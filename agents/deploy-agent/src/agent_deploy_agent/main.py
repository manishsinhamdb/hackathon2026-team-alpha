"""
Deploy Agent — POC Builder Stages 3–5 (Spec §6.7), built on the Agent Engine SDK.

Message contract: every incoming message is an AgentEnvelope request (JSON, text/plain over A2A);
every reply is an AgentEnvelope response (JSON). Tools:
    start_deploy_run(code_version, options) · resume_run(run_id) · teardown_poc() · get_deployment() · get_run_status(run_id)

The graph is the step pipeline: check_gate → provision_db → store_secret → launch_instance → fetch_bundle
→ seed_data → build_backend → start_backend → build_frontend → publish_frontend → write_deployment → run_tests → finalize.
Every step executes in the Tool Pod (deploy_execute_step) and records itself in the `runs` collection, so a run
can be resumed from its last completed step. Failures in repairable steps call the Coding Orchestrator's
repair_component over A2A (max 3 per component); tests go to the Test Agent over A2A. Both callees reply
`started` and are polled through `runs`.

    RUNNER_MODE=aer   -> LangGraph execution (this graph)
    RUNNER_MODE=tool  -> Tool functions below
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Annotated, Any, Literal, Optional, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from agent_engine_sdk_langgraph import App

from poc_contracts import Envelope, ContractError, new_id, validate
from agent_deploy_agent import pipeline
from agent_deploy_agent.a2a import A2AClient, invoke_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
logger = logging.getLogger(__name__)
load_dotenv()

APP_NAME = "Deploy Agent"
AGENT_NAME = "deploy_agent"
app = App(app_name=APP_NAME)
logger.info("✅ App created")

POLL_S = 15
REPAIR_WAIT_S = 20 * 60
TEST_WAIT_S = 20 * 60


# =============================================================================
# Tools — all in the Tool Pod (cloud + platform DB access)
# =============================================================================

@app.tool(timeout=60)
def deploy_start_run(envelope_json: str) -> str:
    """Validate a start_deploy_run / teardown request envelope, check the deploy gate and create the run document.
    Returns {"run_id", "poc_id"} or {"error": {...}}."""
    from poc_shared_tools import metadata as md
    from poc_shared_tools.errors import ToolError
    try:
        env = validate("agent_envelope", json.loads(envelope_json))["request"]
        p = env["params"]
        if env["tool"] == "start_deploy_run":
            code_version = p["code_version"]
            if not md.check_gate(env["poc_id"], "deploy", code_version):
                return json.dumps({"error": {"code": "GATE_NOT_APPROVED", "message": f"code_approved missing for {code_version}"}})
            run = md.create_run(env["poc_id"], "deploy", env.get("caller", "chat_agent"), AGENT_NAME,
                                {"code_version": code_version, "options": p.get("options", {})}, env.get("trace_id"))
            md.update_poc_status(env["poc_id"], "deploying")
        elif env["tool"] == "teardown_poc":
            run = md.create_run(env["poc_id"], "teardown", env.get("caller", "chat_agent"), AGENT_NAME, {}, env.get("trace_id"))
        else:
            return json.dumps({"error": {"code": "BAD_TOOL", "message": env["tool"]}})
        return json.dumps({"run_id": run["run_id"], "poc_id": env["poc_id"]})
    except (ContractError, KeyError, ValueError) as e:
        return json.dumps({"error": {"code": "INVALID_ENVELOPE", "message": str(e)[:500]}})
    except ToolError as e:
        return json.dumps({"error": {"code": e.code, "message": str(e)[:500], "retryable": e.retryable}})


@app.tool(timeout=1800)
def deploy_execute_step(run_id: str, step: str) -> str:
    """Execute one pipeline step for a deploy run and persist its outputs into runs.outputs.
    Returns {"ok", "step", "outputs", "error"}."""
    from poc_shared_tools import metadata as md
    t0 = time.time()
    run = md.get_run(run_id)
    md.update_run_step(run_id, step, "running")
    try:
        r = pipeline.run_step(run, step)
    except Exception as e:  # any tool/infra exception becomes a structured failure
        code = getattr(e, "code", "STEP_EXCEPTION")
        r = {"ok": False, "outputs": {}, "error": {"code": code, "message": str(e)[:2000], "retryable": bool(getattr(e, "retryable", False))}}
    dur = int((time.time() - t0) * 1000)
    if r["ok"]:
        if r["outputs"]:
            md._db().runs.update_one({"run_id": run_id}, {"$set": {f"outputs.{k}": v for k, v in r["outputs"].items()} | {"updated_at": md.now()}})
        md.update_run_step(run_id, step, "succeeded", log_key=None, duration_ms=dur)
    else:
        md.update_run_step(run_id, step, "failed", log_key=r["error"].get("log_key"), duration_ms=dur)
    return json.dumps({"ok": r["ok"], "step": step, "outputs": r["outputs"], "error": r["error"]}, default=str)


@app.tool(timeout=30)
def deploy_get_run(run_id: str) -> str:
    """Return the runs document for a run_id (status, steps, outputs, error, repair_attempts)."""
    from poc_shared_tools import metadata as md
    return json.dumps(md.get_run(run_id), default=str)


@app.tool(timeout=30)
def deploy_find_test_run(deployment_run_id: str) -> str:
    """Find the most recent test run that was started for a given deployment_run_id, or {"error": ...}.
    Used to recover after an A2A call to the Test Agent times out or fails transiently."""
    from poc_shared_tools import metadata as md
    try:
        doc = md._db().runs.find_one(
            {"stage": "test", "inputs.deployment_run_id": deployment_run_id},
            sort=[("started_at", -1)],
        )
        if not doc:
            return json.dumps({"error": {"code": "TEST_RUN_NOT_FOUND",
                                         "message": f"no test run for deployment {deployment_run_id}"}})
        doc.pop("_id", None)
        return json.dumps(doc, default=str)
    except Exception as e:
        return json.dumps({"error": {"code": "DB_ERROR", "message": str(e)[:500]}})


@app.tool(timeout=30)
def deploy_get_deployment(poc_id: str) -> str:
    """Return the latest deployment.json for a POC, or {"error": ...}."""
    from poc_shared_tools import metadata as md, s3 as s3t
    poc = md.get_poc(poc_id)
    key = (poc.get("deployment") or {}).get("deployment_key")
    if not key:
        return json.dumps({"error": {"code": "NOT_DEPLOYED", "message": f"{poc_id} has no deployment"}})
    return s3t.get_text(key)


@app.tool(timeout=900)
def deploy_teardown(poc_id: str, run_id: str) -> str:
    """Tear down every cloud resource of a POC (instance, access-list entry, DB/user or flex cluster, secret)."""
    from poc_shared_tools import metadata as md
    md.update_run_step(run_id, "teardown", "running")
    try:
        r = pipeline.teardown(poc_id, run_id)
        md.update_run_step(run_id, "teardown", "succeeded" if not r["errors"] else "failed")
        md.finish_run(run_id, "succeeded" if not r["errors"] else "failed", outputs=r,
                      error=None if not r["errors"] else {"code": "TEARDOWN_PARTIAL", "message": json.dumps(r["errors"])[:1000]})
        return json.dumps(r, default=str)
    except Exception as e:
        md.finish_run(run_id, "failed", error={"code": "TEARDOWN_FAILED", "message": str(e)[:1000]})
        return json.dumps({"error": {"code": "TEARDOWN_FAILED", "message": str(e)[:1000]}})


@app.tool(timeout=60)
def deploy_build_failure_report(run_id: str, step: str, error_json: str) -> str:
    """Classify a step failure into a FailureReport (§8.9), bump the component's repair attempt, and store it under repairs/."""
    from poc_shared_tools import metadata as md, s3 as s3t
    run = md.get_run(run_id)
    error = json.loads(error_json)
    component = error.get("component") or pipeline.REPAIRABLE.get(step, "backend")
    attempt = md.bump_repair_attempt(run_id, component)
    if attempt > pipeline.MAX_REPAIRS:
        return json.dumps({"exhausted": True, "component": component, "attempt": attempt})
    fr = pipeline.build_failure_report(run, step, error, attempt)
    task_id = new_id("task")
    key = f"pocs/{run['poc_id']}/repairs/{run_id}/{task_id}/failure.json"
    s3t.put_object(run["poc_id"], run_id, key, json.dumps(fr, indent=2), "application/json", AGENT_NAME)
    return json.dumps({"exhausted": False, "failure_report": fr, "failure_key": key, "task_id": task_id})


@app.tool(timeout=60)
def deploy_record_repair_result(run_id: str, new_code_version: str) -> str:
    """After a repair run succeeds, point the deploy run at the new code version and refresh bundle keys."""
    from poc_shared_tools import metadata as md, s3 as s3t
    run = md.get_run(run_id)
    pm = json.loads(s3t.get_text(f"pocs/{run['poc_id']}/code/{new_code_version}/poc.manifest.json"))
    md._db().runs.update_one({"run_id": run_id}, {"$set": {"outputs.code_version": new_code_version, "outputs.bundle_key": pm["bundle_key"],
                                                         "outputs.contract_key": pm["contract_key"], "updated_at": md.now()}})
    return json.dumps({"code_version": new_code_version})


# =============================================================================
# Graph
# =============================================================================

class _Opt(TypedDict, total=False):
    request: dict[str, Any]
    run_id: str
    poc_id: str
    step_index: int
    last_error: dict[str, Any]
    result: dict[str, Any]
    done: bool


class DeployState(_Opt):
    messages: Annotated[list[BaseMessage], add_messages]


def _last_human_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    logger.info("Building Deploy Agent graph...")
    tools = {t.name: t for t in app.get_tools()}
    a2a = A2AClient(app)

    def call(name: str, **kw: Any) -> dict[str, Any]:
        out = invoke_tool(tools[name], kw)
        return json.loads(out) if isinstance(out, str) else out

    def reply(state: DeployState, response: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [AIMessage(content=json.dumps(response))], "done": True, "result": response}

    def parse_node(state: DeployState) -> dict[str, Any]:
        text = _last_human_text(state["messages"])
        try:
            env = validate("agent_envelope", json.loads(text))["request"]
        except Exception as e:
            fake_task = new_id("task")
            return reply(state, Envelope.failed(fake_task, "INVALID_ENVELOPE", f"expected AgentEnvelope JSON: {str(e)[:300]}"))
        return {"request": env, "poc_id": env["poc_id"], "done": False}

    def dispatch(state: DeployState) -> Literal["start", "resume", "teardown", "get_deployment", "get_run_status", "end"]:
        if state.get("done"):
            return "end"
        tool = state["request"]["tool"]
        return {"start_deploy_run": "start", "resume_run": "resume", "teardown_poc": "teardown",
                "get_deployment": "get_deployment", "get_run_status": "get_run_status"}.get(tool, "end")

    def start_node(state: DeployState) -> dict[str, Any]:
        req = state["request"]
        r = call("deploy_start_run", envelope_json=json.dumps({"request": req}))
        if "error" in r:
            return reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"], r["error"].get("retryable", False)))
        return {"run_id": r["run_id"], "step_index": 0}

    def resume_node(state: DeployState) -> dict[str, Any]:
        req = state["request"]
        run = call("deploy_get_run", run_id=req["params"]["run_id"])
        if run.get("stage") != "deploy":
            return reply(state, Envelope.failed(req["task_id"], "NOT_A_DEPLOY_RUN", run.get("stage", "?")))
        done = {s["name"] for s in run.get("steps", []) if s["status"] == "succeeded"}
        idx = next((i for i, s in enumerate(pipeline.STEPS) if s not in done), len(pipeline.STEPS))
        return {"run_id": run["run_id"], "step_index": idx}

    def step_node(state: DeployState) -> dict[str, Any]:
        idx = state["step_index"]
        step = pipeline.STEPS[idx]
        r = call("deploy_execute_step", run_id=state["run_id"], step=step)
        if r["ok"]:
            return {"step_index": idx + 1, "last_error": {}}
        return {"last_error": r["error"] | {"step": step}}

    def after_step(state: DeployState) -> Literal["next", "tests", "repair", "fail", "finish"]:
        if state.get("last_error"):
            step = state["last_error"]["step"]
            return "repair" if step in pipeline.REPAIRABLE else "fail"
        idx = state["step_index"]
        if idx >= len(pipeline.STEPS):
            return "finish"
        if pipeline.STEPS[idx] == "run_tests":
            return "tests"
        return "next"

    def _poll_run(run_id: str, wait_s: int) -> dict[str, Any]:
        deadline = time.time() + wait_s
        while time.time() < deadline:
            run = call("deploy_get_run", run_id=run_id)
            if run.get("status") in ("succeeded", "failed", "cancelled"):
                return run
            time.sleep(POLL_S)
        return {"status": "timeout", "run_id": run_id}

    def repair_node(state: DeployState) -> dict[str, Any]:
        err = state["last_error"]
        step = err["step"]
        fr = call("deploy_build_failure_report", run_id=state["run_id"], step=step, error_json=json.dumps(err))
        if fr.get("exhausted"):
            return {"last_error": err | {"exhausted": True}}
        req = state["request"]
        env = Envelope.request(poc_id=state["poc_id"], run_id=state["run_id"], caller=AGENT_NAME, agent="coding_orchestrator",
                               tool="repair_component", task_id=fr["task_id"], trace_id=req.get("trace_id"),
                               params={"code_version": fr["failure_report"]["code_version"], "component": fr["failure_report"]["component"],
                                       "failure": fr["failure_report"]})
        try:
            resp = a2a.invoke(a2a.find_agent("code-orchestration"), env)["response"]
        except Exception as e:
            return {"last_error": err | {"exhausted": True, "message": f"repair call failed: {e}"}}
        if resp["status"] not in ("started", "succeeded"):
            return {"last_error": err | {"exhausted": True, "message": resp.get("error", {}).get("message", "repair refused")}}
        repair_run = _poll_run(resp["result"]["run_id"], REPAIR_WAIT_S) if resp["status"] == "started" else {"status": "succeeded", "outputs": resp.get("result", {})}
        if repair_run.get("status") != "succeeded":
            return {"last_error": err | {"exhausted": True, "message": f"repair run {repair_run.get('status')}"}}
        new_v = (repair_run.get("outputs") or {}).get("code_version")
        if not new_v:
            return {"last_error": err | {"exhausted": True, "message": "repair run produced no code_version"}}
        call("deploy_record_repair_result", run_id=state["run_id"], new_code_version=new_v)
        # a repaired component needs the new bundle on the box: rewind to fetch_bundle, then re-run from there
        return {"last_error": {}, "step_index": pipeline.STEPS.index("fetch_bundle")}

    def after_repair(state: DeployState) -> Literal["next", "fail"]:
        return "fail" if state.get("last_error", {}).get("exhausted") else "next"

    def tests_node(state: DeployState) -> dict[str, Any]:
        idx = state["step_index"]
        r = call("deploy_execute_step", run_id=state["run_id"], step="run_tests")
        if r["outputs"].get("tests_skipped"):
            return {"step_index": idx + 1}
        req = state["request"]
        env = Envelope.request(poc_id=state["poc_id"], run_id=state["run_id"], caller=AGENT_NAME, agent="test_agent", tool="run_e2e",
                               trace_id=req.get("trace_id"), params={"deployment_run_id": state["run_id"], "scope": "all"})
        resp = None
        try:
            resp = a2a.invoke(a2a.find_agent("e2e-tests"), env)["response"]
        except Exception as invoke_exc:
            # A2A call failed or timed out — look up the test run by deployment_run_id;
            # the Test Agent may have already created it (design decision 3).
            fr = call("deploy_find_test_run", deployment_run_id=state["run_id"])
            if "error" not in fr:
                test_run = _poll_run(fr["run_id"], 120)
                if test_run.get("status") in ("succeeded", "failed"):
                    outs = test_run.get("outputs") or {}
                    passed = test_run.get("status") == "succeeded" and int(outs.get("failed", 1)) == 0
                    call("deploy_set_outputs", run_id=state["run_id"],
                         outputs_json=json.dumps({"test_run_id": test_run.get("run_id", ""),
                                                  "test_passed": passed,
                                                  "test_report_key": outs.get("report_key")}))
                    if passed:
                        return {"step_index": idx + 1, "last_error": {}}
                    comp = outs.get("suspected_component") or "unknown"
                    err = {"step": "run_tests", "code": "TEST_FAILURE",
                           "message": f"{outs.get('failed', '?')} test(s) failed", "component": comp,
                           "test_result_ids": outs.get("failed_ids", [])}
                    if comp in ("seed", "backend", "frontend"):
                        err["step"] = {"seed": "seed_data", "backend": "build_backend", "frontend": "build_frontend"}[comp]
                        return {"last_error": err}
                    return {"last_error": err | {"exhausted": True}}
            # No run found within 120 s — give up
            return {"last_error": {"step": "run_tests", "code": "TEST_AGENT_UNAVAILABLE",
                                   "message": str(invoke_exc)[:500]}}
        if resp["status"] == "started":
            test_run = _poll_run(resp["result"]["run_id"], TEST_WAIT_S)
        elif resp["status"] == "succeeded":
            test_run = {"status": "succeeded", "run_id": resp.get("result", {}).get("run_id", ""), "outputs": resp.get("result", {})}
        else:
            return {"last_error": {"step": "run_tests", "code": "TEST_FAILURE", "message": resp.get("error", {}).get("message", "test agent failed")}}
        outs = test_run.get("outputs") or {}
        passed = test_run.get("status") == "succeeded" and int(outs.get("failed", 1)) == 0
        call("deploy_set_outputs", run_id=state["run_id"], outputs_json=json.dumps({"test_run_id": test_run.get("run_id", ""), "test_passed": passed, "test_report_key": outs.get("report_key")}))
        if passed:
            return {"step_index": idx + 1, "last_error": {}}
        comp = outs.get("suspected_component") or "unknown"
        err = {"step": "run_tests", "code": "TEST_FAILURE", "message": f"{outs.get('failed', '?')} test(s) failed", "component": comp,
               "test_result_ids": outs.get("failed_ids", [])}
        if comp in ("seed", "backend", "frontend"):
            err["step"] = {"seed": "seed_data", "backend": "build_backend", "frontend": "build_frontend"}[comp]
            return {"last_error": err}
        return {"last_error": err | {"exhausted": True}}

    def after_tests(state: DeployState) -> Literal["next", "repair", "fail"]:
        e = state.get("last_error") or {}
        if not e:
            return "next"
        if e.get("exhausted") or e["step"] not in pipeline.REPAIRABLE:
            return "fail"
        return "repair"

    def fail_node(state: DeployState) -> dict[str, Any]:
        err = state.get("last_error") or {"code": "UNKNOWN", "message": "unknown failure", "step": "?"}
        code = err.get("code", "STEP_FAILED")
        run = call("deploy_get_run", run_id=state["run_id"])
        call("deploy_finish_run", run_id=state["run_id"], status="failed",
             error_json=json.dumps({"code": code, "component": err.get("component"), "message": err.get("message", ""), "failure_key": err.get("log_key")}))
        req = state["request"]
        return reply(state, Envelope.failed(req["task_id"], code, f"deploy run {state['run_id']} failed at {err.get('step')}: {err.get('message', '')[:400]}",
                                            detail={"run_id": state["run_id"], "step": err.get("step"), "repair_attempts": run.get("repair_attempts", {})}))

    def finish_node(state: DeployState) -> dict[str, Any]:
        run = call("deploy_get_run", run_id=state["run_id"])
        o = run.get("outputs", {})
        call("deploy_finish_run", run_id=state["run_id"], status="succeeded",
             error_json="", outputs_json=json.dumps({"deployment_key": o.get("deployment_key"), "urls": o.get("urls"), "code_version": o.get("code_version"),
                                                     "test_run_id": o.get("test_run_id"), "test_passed": o.get("test_passed")}))
        req = state["request"]
        arts = [Envelope.artifact("deployment", o["deployment_key"])] if o.get("deployment_key") else []
        if o.get("test_report_key"):
            arts.append(Envelope.artifact("report", o["test_report_key"]))
        return reply(state, Envelope.succeeded(req["task_id"], {"run_id": state["run_id"], "urls": o.get("urls"), "code_version": o.get("code_version"),
                                                                "final_status": o.get("final_status"), "test_passed": o.get("test_passed")}, arts))

    def teardown_node(state: DeployState) -> dict[str, Any]:
        req = state["request"]
        r = call("deploy_start_run", envelope_json=json.dumps({"request": req}))
        if "error" in r:
            return reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"]))
        t = call("deploy_teardown", poc_id=state["poc_id"], run_id=r["run_id"])
        if "error" in t:
            return reply(state, Envelope.failed(req["task_id"], t["error"]["code"], t["error"]["message"], detail={"run_id": r["run_id"]}))
        return reply(state, Envelope.succeeded(req["task_id"], {"run_id": r["run_id"], **t}))

    def get_deployment_node(state: DeployState) -> dict[str, Any]:
        req = state["request"]
        r = call("deploy_get_deployment", poc_id=state["poc_id"])
        if "error" in r:
            return reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"]))
        return reply(state, Envelope.succeeded(req["task_id"], {"deployment": r}, [Envelope.artifact("deployment", f"pocs/{state['poc_id']}/deploy/{r['run_id']}/deployment.json")]))

    def get_run_status_node(state: DeployState) -> dict[str, Any]:
        req = state["request"]
        run = call("deploy_get_run", run_id=req["params"]["run_id"])
        slim = {k: run.get(k) for k in ("run_id", "poc_id", "stage", "status", "current_step", "error", "repair_attempts")}
        slim["steps"] = [{"name": s["name"], "status": s["status"]} for s in run.get("steps", [])]
        slim["urls"] = (run.get("outputs") or {}).get("urls")
        return reply(state, Envelope.succeeded(req["task_id"], slim))

    b = StateGraph(DeployState)
    for name, fn in [("parse", parse_node), ("start", start_node), ("resume", resume_node), ("step", step_node), ("repair", repair_node),
                     ("tests", tests_node), ("fail", fail_node), ("finish", finish_node), ("teardown", teardown_node),
                     ("get_deployment", get_deployment_node), ("get_run_status", get_run_status_node)]:
        b.add_node(name, fn)
    b.add_edge(START, "parse")
    b.add_conditional_edges("parse", dispatch, {"start": "start", "resume": "resume", "teardown": "teardown",
                                                "get_deployment": "get_deployment", "get_run_status": "get_run_status", "end": END})
    b.add_conditional_edges("start", lambda s: "end" if s.get("done") else "step", {"step": "step", "end": END})
    b.add_conditional_edges("resume", lambda s: "end" if s.get("done") else "step", {"step": "step", "end": END})
    b.add_conditional_edges("step", after_step, {"next": "step", "tests": "tests", "repair": "repair", "fail": "fail", "finish": "finish"})
    b.add_conditional_edges("repair", after_repair, {"next": "step", "fail": "fail"})
    b.add_conditional_edges("tests", after_tests, {"next": "step", "repair": "repair", "fail": "fail"})
    for n in ("fail", "finish", "teardown", "get_deployment", "get_run_status"):
        b.add_edge(n, END)
    graph = b.compile(checkpointer=app.checkpointer())
    logger.info("✅ Deploy Agent graph compiled")
    return graph


# Two small persistence tools the graph uses (declared after the graph for readability; registration is by decorator).

@app.tool(timeout=30)
def deploy_set_outputs(run_id: str, outputs_json: str) -> str:
    """Merge a JSON object into runs.outputs for a run."""
    from poc_shared_tools import metadata as md
    outputs = json.loads(outputs_json)
    md._db().runs.update_one({"run_id": run_id}, {"$set": {f"outputs.{k}": v for k, v in outputs.items()} | {"updated_at": md.now()}})
    return json.dumps({"ok": True})


@app.tool(timeout=30)
def deploy_finish_run(run_id: str, status: str, error_json: str = "", outputs_json: str = "") -> str:
    """Finish a run with status succeeded|failed, optional error and outputs (JSON strings).

    A failed deploy run releases the POC from the transient "deploying" status back to "code_ready"
    so the operator can start a fresh deploy (or resume) without the status being stuck. deploy_start_run
    flips pocs.status to "deploying"; this is the counterpart that undoes it on failure. Only deploy-stage
    runs are affected — teardown finishes via metadata.finish_run directly, not this tool."""
    from poc_shared_tools import metadata as md
    err = json.loads(error_json) if error_json else None
    outs = json.loads(outputs_json) if outputs_json else None
    r = md.finish_run(run_id, status, outputs=outs, error=err)
    if status == "failed" and r.get("stage") == "deploy":
        md.update_poc_status(r["poc_id"], "code_ready")
    return json.dumps({"run_id": r["run_id"], "status": r["status"]})


def main():
    logger.info("=" * 60)
    logger.info(f"🚀 Starting {APP_NAME} (Runner SDK)")
    logger.info("=" * 60)
    app.run()


if __name__ == "__main__":
    main()
