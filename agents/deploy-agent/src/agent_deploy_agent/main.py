"""
Deploy Agent — POC Builder Stages 3–5 (Spec §6.7), built on the Agent Engine SDK.

Message contract: every incoming message is an AgentEnvelope request (JSON, text/plain over A2A);
every reply is an AgentEnvelope response (JSON). Tools:
    start_deploy_run(code_version, options) · resume_run(run_id) · teardown_poc() · get_deployment() · get_run_status(run_id)

The graph is the step pipeline: check_gate → provision_db → store_secret → launch_instance → fetch_bundle
→ seed_data → build_backend → start_backend → build_frontend → publish_frontend → write_deployment → run_tests → finalize.
Every step executes in the Tool Pod (deploy_execute_step) and records itself in the `runs` collection, so a run
can be resumed from its last completed step. Failures in repairable steps call the Coding Orchestrator's
repair_component (max 3 per component); tests go to the Test Agent. Both are FIRED as TOP-LEVEL platform
invocations (poc_shared_tools.platform_invoke.start_invoke — own root session, fresh token) and then waited
on through the platform DB (the callee's run document), not called synchronously.

Resilient runs (docs/06 "Resilient runs"): the platform kills an execution after ~10 min wall clock, so
  * deploy_execute_step / deploy_teardown heartbeat runs.heartbeat_at while they work;
  * the run is resumable: continue_run(run_id) (mode "continue"; resume_run is the same path) re-enters at
    outputs.resume_step_index, re-attaching to an in-flight test or repair run instead of re-firing it;
  * waiting on the test agent / a repair is ONE tool call (deploy_wait_test_run / deploy_wait_code_run) that
    polls internally for <= 4 min and returns terminal or "running"; the graph loops;
  * at each step/wait boundary, past DEPLOY_HANDOVER_AFTER_S or DEPLOY_HANDOVER_TOOL_CALLS, the execution
    fires continue_run on itself (skill deploy-operations) and replies `started`.

    RUNNER_MODE=aer   -> LangGraph execution (this graph)
    RUNNER_MODE=tool  -> Tool functions below
"""
from __future__ import annotations

import json
import logging
import operator
import os
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
from poc_shared_tools import platform_invoke
from agent_deploy_agent import pipeline
from agent_deploy_agent.a2a import invoke_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
logger = logging.getLogger(__name__)
load_dotenv()

APP_NAME = "Deploy Agent"
AGENT_NAME = "deploy_agent"
app = App(app_name=APP_NAME)
logger.info("✅ App created")

POLL_S = 15
REPAIR_WAIT_S = 20 * 60          # ceiling on one repair code run, measured from when it was fired
TEST_WAIT_S = 20 * 60            # ceiling on one test run, measured from when it was fired
CALLEE_APPEAR_S = 5 * 60         # a fired callee whose run doc never appears within this is unavailable
# run_tests (-> test agent) and repair_component (-> coding orchestrator) happen late in a long deploy run,
# past the ~5-min OE A2A token, so they are FIRED as TOP-LEVEL platform invocations (each its own root
# session with a fresh token) with a short client timeout (a 504/timeout means "started"), then waited on.
START_TIMEOUT_S = 25
# Self-continuation (docs/06 "Resilient runs"). Defaults keep one execution well under the ~10-min cap.
HANDOVER_AFTER_S = int(os.getenv("DEPLOY_HANDOVER_AFTER_S", "360"))
HANDOVER_TOOL_CALLS = int(os.getenv("DEPLOY_HANDOVER_TOOL_CALLS", "25"))
WAIT_WINDOW_S = min(240, int(os.getenv("DEPLOY_WAIT_WINDOW_S", "240")))
# Steps that can take minutes on their own: hand over BEFORE one if less than this headroom is left.
LONG_STEPS = {"provision_db": 240, "launch_instance": 240}


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


@app.tool(timeout=60)
def deploy_continue_run(envelope_json: str) -> str:
    """Validate a continue_run / resume_run envelope and reopen the deploy run for this execution (records a
    continuation, bumps runs.executions, POC back to deploying). Returns {"run_id", "poc_id", "step_index",
    "inflight": {"kind": "test"|"repair", ...} | None} or {"already": "succeeded"} or {"error": {...}}."""
    from poc_shared_tools import metadata as md
    from poc_shared_tools.errors import ToolError
    try:
        env = validate("agent_envelope", json.loads(envelope_json))["request"]
        run_id = env["params"]["run_id"]
        run = md.get_run(run_id)
        if run["stage"] != "deploy" or run["poc_id"] != env["poc_id"]:
            return json.dumps({"error": {"code": "NOT_A_DEPLOY_RUN", "message": f"{run_id} is not a deploy run of {env['poc_id']}"}})
        o = run.get("outputs") or {}
        if run["status"] == "succeeded":
            return json.dumps({"run_id": run_id, "poc_id": run["poc_id"], "already": "succeeded",
                               "outputs": {k: o.get(k) for k in ("urls", "code_version", "test_passed", "deployment_key")}})
        md.reopen_run(run_id, env["params"].get("reason", "continue"), env["params"].get("execution"))
        md.update_poc_status(run["poc_id"], "deploying")
        return json.dumps({"run_id": run_id, "poc_id": run["poc_id"], "step_index": resume_index(run),
                           "inflight": o.get("inflight") or None})
    except (ContractError, KeyError, ValueError) as e:
        return json.dumps({"error": {"code": "INVALID_ENVELOPE", "message": str(e)[:500]}})
    except ToolError as e:
        return json.dumps({"error": {"code": e.code, "message": str(e)[:500], "retryable": e.retryable}})


def resume_index(run: dict[str, Any]) -> int:
    """Index of the first pipeline step still to do. outputs.resume_step_index is authoritative (it also
    captures a repair's rewind to fetch_bundle); legacy runs fall back to the succeeded steps — except
    run_tests, whose step entry is recorded before the tests actually pass."""
    o = run.get("outputs") or {}
    if isinstance(o.get("resume_step_index"), int):
        return o["resume_step_index"]
    done = {s["name"] for s in run.get("steps", []) if s["status"] == "succeeded"}
    if not o.get("test_passed"):
        done.discard("run_tests")
    return next((i for i, s in enumerate(pipeline.STEPS) if s not in done), len(pipeline.STEPS))


@app.tool(timeout=1800)
def deploy_execute_step(run_id: str, step: str) -> str:
    """Execute one pipeline step for a deploy run and persist its outputs into runs.outputs (heartbeating the
    run while the step works). Returns {"ok", "step", "outputs", "error"}."""
    from poc_shared_tools import metadata as md
    t0 = time.time()
    run = md.get_run(run_id)
    md.update_run_step(run_id, step, "running")
    with md.heartbeat(run_id):
        try:
            r = pipeline.run_step(run, step)
        except Exception as e:  # any tool/infra exception becomes a structured failure
            code = getattr(e, "code", "STEP_EXCEPTION")
            r = {"ok": False, "outputs": {}, "error": {"code": code, "message": str(e)[:2000], "retryable": bool(getattr(e, "retryable", False))}}
    dur = int((time.time() - t0) * 1000)
    if r["ok"]:
        outs = dict(r["outputs"] or {})
        if step != "run_tests":  # run_tests only completes once the test run passes (tests_wait)
            outs["resume_step_index"] = pipeline.STEPS.index(step) + 1
        md._db().runs.update_one({"run_id": run_id}, {"$set": {f"outputs.{k}": v for k, v in outs.items()} | {"updated_at": md.now()}})
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


def _age_s(ts: str | None) -> float:
    from poc_shared_tools import metadata as md
    d = md._parse_ts(ts)
    return (time.time() - d.timestamp()) if d else 0.0


def _wait_for_run(query: dict[str, Any], run_id_hint: str, fired_at: str, window_s: int, ceiling_s: int) -> dict[str, Any]:
    """Shared single-call wait: find the callee's run (by query), poll it every POLL_S for <= window_s while
    heartbeating the deploy run, return {"status": succeeded|failed|cancelled|running|missing|timeout, "run"}."""
    from poc_shared_tools import metadata as md
    window_s = max(1, min(int(window_s), 240))
    deadline = time.time() + window_s
    with md.heartbeat(run_id_hint):
        while True:
            doc = md._db().runs.find_one(query, sort=[("started_at", -1)])
            if doc:
                doc.pop("_id", None)
                if doc.get("status") in ("succeeded", "failed", "cancelled"):
                    return {"status": doc["status"], "run": doc}
                if _age_s(fired_at) > ceiling_s:
                    return {"status": "timeout", "run": doc}
            elif _age_s(fired_at) > CALLEE_APPEAR_S:
                return {"status": "missing", "run": None}
            if time.time() + POLL_S > deadline:
                return {"status": "running", "run": doc}
            time.sleep(POLL_S)


@app.tool(timeout=300)
def deploy_wait_test_run(deployment_run_id: str, fired_at: str, window_s: int = WAIT_WINDOW_S) -> str:
    """ONE tool call waiting on the test run started for `deployment_run_id` (fired at `fired_at`): polls the
    test run document internally for <= window_s (<= 240 s) and returns {"status": succeeded|failed|
    running|missing|timeout, "run"}. "running" → the graph loops (or hands over)."""
    from datetime import datetime, timedelta, timezone
    from poc_shared_tools import metadata as md
    fired = md._parse_ts(fired_at) or datetime.now(timezone.utc)
    since = (fired - timedelta(seconds=120)).isoformat(timespec="seconds").replace("+00:00", "Z")  # pod clock skew
    q = {"stage": "test", "inputs.deployment_run_id": deployment_run_id, "started_at": {"$gte": since}}
    return json.dumps(_wait_for_run(q, deployment_run_id, fired_at, window_s, TEST_WAIT_S), default=str)


@app.tool(timeout=300)
def deploy_wait_code_run(poc_id: str, deployment_run_id: str, component: str, attempt: int, fired_at: str,
                         window_s: int = WAIT_WINDOW_S) -> str:
    """ONE tool call waiting on the repair code run fired for (deployment_run_id, component, attempt):
    polls it internally for <= window_s and returns {"status", "run"} like deploy_wait_test_run."""
    q = {"stage": "code", "poc_id": poc_id, "inputs.failure.deploy_run_id": deployment_run_id,
         "inputs.failure.component": component, "inputs.failure.attempt": int(attempt)}
    return json.dumps(_wait_for_run(q, deployment_run_id, fired_at, window_s, REPAIR_WAIT_S), default=str)


@app.tool(timeout=60)
def deploy_record_handover(run_id: str) -> str:
    """Touch the run before this execution hands itself over; returns {"executions"} (count so far)."""
    from poc_shared_tools import metadata as md
    md.touch_run(run_id)
    return json.dumps({"executions": int(md.get_run(run_id).get("executions") or 0)})


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
        with md.heartbeat(run_id):
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
    # a repaired component needs the new bundle on the box: rewind to fetch_bundle and clear the in-flight marker
    md._db().runs.update_one({"run_id": run_id}, {"$set": {"outputs.code_version": new_code_version, "outputs.bundle_key": pm["bundle_key"],
                                                         "outputs.contract_key": pm["contract_key"], "outputs.inflight": None,
                                                         "outputs.resume_step_index": pipeline.STEPS.index("fetch_bundle"),
                                                         "updated_at": md.now()}})
    return json.dumps({"code_version": new_code_version})


# =============================================================================
# Graph
# =============================================================================

class _Opt(TypedDict, total=False):
    request: dict[str, Any]
    run_id: str
    poc_id: str
    step_index: int
    inflight: dict[str, Any]      # {"kind": "test"|"repair", "fired_at", ...} — a callee we are waiting on
    exec_started: float           # wall clock when THIS execution began (self-continuation budget)
    calls_base: int
    last_error: dict[str, Any]
    result: dict[str, Any]
    done: bool


class DeployState(_Opt):
    messages: Annotated[list[BaseMessage], add_messages]
    tool_calls: Annotated[int, operator.add]


def _last_human_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def over_budget(state: dict[str, Any], next_step: str | None = None, now: float | None = None) -> bool:
    """Self-continuation boundary test: past HANDOVER_AFTER_S elapsed (less the headroom a long next step
    needs) or more than HANDOVER_TOOL_CALLS tool calls in this execution."""
    elapsed = (now or time.time()) - state.get("exec_started", time.time())
    calls = int(state.get("tool_calls") or 0) - int(state.get("calls_base") or 0)
    headroom = LONG_STEPS.get(next_step or "", 0)
    return elapsed > HANDOVER_AFTER_S - headroom or calls > HANDOVER_TOOL_CALLS


def _test_outcome(test_run: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    outs = test_run.get("outputs") or {}
    return test_run.get("status") == "succeeded" and int(outs.get("failed", 1)) == 0, outs


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    logger.info("Building Deploy Agent graph...")
    tools = {t.name: t for t in app.get_tools()}

    class Calls:
        """Per-node tool caller that counts calls into state["tool_calls"] (an add-reducer)."""
        def __init__(self) -> None:
            self.n = 0

        def __call__(self, name: str, **kw: Any) -> dict[str, Any]:
            self.n += 1
            out = invoke_tool(tools[name], kw)
            return json.loads(out) if isinstance(out, str) else out

        def out(self, update: dict[str, Any] | None = None) -> dict[str, Any]:
            return {**(update or {}), "tool_calls": self.n}

    def reply(state: DeployState, response: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [AIMessage(content=json.dumps(response))], "done": True, "result": response}

    def user_id() -> str:
        return app.get_current_user_id() or "u_local"

    def parse_node(state: DeployState) -> dict[str, Any]:
        base = {"exec_started": time.time(), "calls_base": int(state.get("tool_calls") or 0)}
        text = _last_human_text(state["messages"])
        try:
            env = validate("agent_envelope", json.loads(text))["request"]
        except Exception as e:
            fake_task = new_id("task")
            return base | reply(state, Envelope.failed(fake_task, "INVALID_ENVELOPE", f"expected AgentEnvelope JSON: {str(e)[:300]}"))
        return base | {"request": env, "poc_id": env["poc_id"], "done": False, "inflight": {}, "last_error": {}}

    def dispatch(state: DeployState) -> Literal["start", "resume", "teardown", "get_deployment", "get_run_status", "end"]:
        if state.get("done"):
            return "end"
        tool = state["request"]["tool"]
        return {"start_deploy_run": "start", "resume_run": "resume", "continue_run": "resume", "teardown_poc": "teardown",
                "get_deployment": "get_deployment", "get_run_status": "get_run_status"}.get(tool, "end")

    def start_node(state: DeployState) -> dict[str, Any]:
        c = Calls()
        req = state["request"]
        r = c("deploy_start_run", envelope_json=json.dumps({"request": req}))
        if "error" in r:
            return c.out(reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"], r["error"].get("retryable", False))))
        return c.out({"run_id": r["run_id"], "step_index": 0})

    def resume_node(state: DeployState) -> dict[str, Any]:
        """continue_run / resume_run: re-enter at the first step not done, re-attaching to an in-flight callee."""
        c = Calls()
        req = state["request"]
        r = c("deploy_continue_run", envelope_json=json.dumps({"request": req}))
        if "error" in r:
            return c.out(reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"], r["error"].get("retryable", False))))
        if r.get("already") == "succeeded":
            return c.out(reply(state, Envelope.succeeded(req["task_id"], {"run_id": r["run_id"], **r["outputs"], "already": "succeeded"})))
        logger.info("▶ continue_run %s at step %s inflight=%s (reason=%s)", r["run_id"], r["step_index"],
                    (r.get("inflight") or {}).get("kind"), req["params"].get("reason", "continue"))
        return c.out({"run_id": r["run_id"], "step_index": r["step_index"], "inflight": r.get("inflight") or {}})

    def step_node(state: DeployState) -> dict[str, Any]:
        c = Calls()
        idx = state["step_index"]
        step = pipeline.STEPS[idx]
        r = c("deploy_execute_step", run_id=state["run_id"], step=step)
        if r["ok"]:
            return c.out({"step_index": idx + 1, "last_error": {}})
        return c.out({"last_error": r["error"] | {"step": step}})

    def route(state: DeployState) -> Literal["handover", "step", "tests", "tests_wait", "repair", "repair_wait", "fail", "finish", "end"]:
        """Boundary after every node: done → end; failure → repair/fail; over budget → hand over; else next."""
        if state.get("done"):
            return "end"
        e = state.get("last_error") or {}
        if e:
            if e.get("exhausted") or e.get("step") not in pipeline.REPAIRABLE:
                return "fail"
            return "repair"
        kind = (state.get("inflight") or {}).get("kind")
        idx = state["step_index"]
        nxt = pipeline.STEPS[idx] if idx < len(pipeline.STEPS) else None
        if over_budget(state, None if kind else nxt):
            return "handover"
        if kind == "test":
            return "tests_wait"
        if kind == "repair":
            return "repair_wait"
        if nxt is None:
            return "finish"
        return "tests" if nxt == "run_tests" else "step"

    def repair_node(state: DeployState) -> dict[str, Any]:
        """Build the failure report and FIRE repair_component on the coding orchestrator (its own root session)."""
        c = Calls()
        err = state["last_error"]
        step = err["step"]
        fr = c("deploy_build_failure_report", run_id=state["run_id"], step=step, error_json=json.dumps(err))
        if fr.get("exhausted"):
            return c.out({"last_error": err | {"exhausted": True}})
        req = state["request"]
        rep = fr["failure_report"]
        env = Envelope.request(poc_id=state["poc_id"], run_id=state["run_id"], caller=AGENT_NAME, agent="coding_orchestrator",
                               tool="repair_component", task_id=fr["task_id"], trace_id=req.get("trace_id"),
                               params={"code_version": rep["code_version"], "component": rep["component"], "failure": rep})
        inflight = {"kind": "repair", "component": rep["component"], "attempt": int(rep["attempt"]),
                    "fired_at": Envelope.now(), "task_id": fr["task_id"]}
        c("deploy_set_outputs", run_id=state["run_id"], outputs_json=json.dumps({"inflight": inflight}))
        try:
            platform_invoke.start_invoke("code-orchestration", env, user_id=user_id(),
                                         session_id=f"repair-{state['run_id']}-{fr['task_id']}", client_timeout_s=START_TIMEOUT_S)
        except Exception as e:
            c("deploy_set_outputs", run_id=state["run_id"], outputs_json=json.dumps({"inflight": None}))
            return c.out({"last_error": err | {"exhausted": True, "message": f"repair call failed: {e}"}, "inflight": {}})
        return c.out({"last_error": {}, "inflight": inflight})

    def repair_wait_node(state: DeployState) -> dict[str, Any]:
        c = Calls()
        inf = state["inflight"]
        w = c("deploy_wait_code_run", poc_id=state["poc_id"], deployment_run_id=state["run_id"], component=inf["component"],
              attempt=inf["attempt"], fired_at=inf["fired_at"], window_s=_window(state))
        if w["status"] == "running":
            return c.out({})
        err = {"step": pipeline.REPAIR_STEP_FOR.get(inf["component"], "build_backend"), "code": "REPAIR_FAILED",
               "component": inf["component"], "exhausted": True}
        if w["status"] != "succeeded":
            c("deploy_set_outputs", run_id=state["run_id"], outputs_json=json.dumps({"inflight": None}))
            return c.out({"inflight": {}, "last_error": err | {"message": f"repair run {w['status']}"}})
        new_v = ((w.get("run") or {}).get("outputs") or {}).get("code_version")
        if not new_v:
            c("deploy_set_outputs", run_id=state["run_id"], outputs_json=json.dumps({"inflight": None}))
            return c.out({"inflight": {}, "last_error": err | {"message": "repair run produced no code_version"}})
        c("deploy_record_repair_result", run_id=state["run_id"], new_code_version=new_v)  # rewinds to fetch_bundle
        return c.out({"inflight": {}, "last_error": {}, "step_index": pipeline.STEPS.index("fetch_bundle")})

    def _window(state: DeployState) -> int:
        left = HANDOVER_AFTER_S - (time.time() - state.get("exec_started", time.time()))
        return int(max(15, min(WAIT_WINDOW_S, left)))

    def tests_node(state: DeployState) -> dict[str, Any]:
        """Record the run_tests step and FIRE the test agent (its own root session); tests_wait waits on it."""
        c = Calls()
        idx = state["step_index"]
        r = c("deploy_execute_step", run_id=state["run_id"], step="run_tests")
        if r["outputs"].get("tests_skipped"):
            c("deploy_set_outputs", run_id=state["run_id"], outputs_json=json.dumps({"resume_step_index": idx + 1}))
            return c.out({"step_index": idx + 1})
        req = state["request"]
        env = Envelope.request(poc_id=state["poc_id"], run_id=state["run_id"], caller=AGENT_NAME, agent="test_agent", tool="run_e2e",
                               trace_id=req.get("trace_id"), params={"deployment_run_id": state["run_id"], "scope": "all"})
        inflight = {"kind": "test", "fired_at": Envelope.now()}
        c("deploy_set_outputs", run_id=state["run_id"], outputs_json=json.dumps({"inflight": inflight}))
        try:
            platform_invoke.start_invoke("e2e-tests", env, user_id=user_id(), session_id=f"test-{state['run_id']}-{new_id('task')[-6:].lower()}",
                                         client_timeout_s=START_TIMEOUT_S)
        except Exception as e:
            c("deploy_set_outputs", run_id=state["run_id"], outputs_json=json.dumps({"inflight": None}))
            return c.out({"last_error": {"step": "run_tests", "code": "TEST_AGENT_UNAVAILABLE", "message": str(e)[:500]}})
        return c.out({"inflight": inflight})

    def tests_wait_node(state: DeployState) -> dict[str, Any]:
        """ONE bounded wait on the test run; on a verdict record it and move on (or route a repair)."""
        c = Calls()
        idx = pipeline.STEPS.index("run_tests")
        w = c("deploy_wait_test_run", deployment_run_id=state["run_id"], fired_at=state["inflight"]["fired_at"],
              window_s=_window(state))
        if w["status"] == "running":
            return c.out({})
        if w["status"] in ("missing", "timeout"):
            c("deploy_set_outputs", run_id=state["run_id"], outputs_json=json.dumps({"inflight": None}))
            return c.out({"inflight": {}, "last_error": {"step": "run_tests", "code": "TEST_AGENT_UNAVAILABLE",
                                                         "message": f"test run {w['status']}"}})
        test_run = w["run"] or {}
        passed, outs = _test_outcome(test_run)
        upd = {"test_run_id": test_run.get("run_id", ""), "test_passed": passed, "test_report_key": outs.get("report_key"),
               "inflight": None}
        if passed:
            upd["resume_step_index"] = idx + 1
        c("deploy_set_outputs", run_id=state["run_id"], outputs_json=json.dumps(upd))
        if passed:
            return c.out({"inflight": {}, "step_index": idx + 1, "last_error": {}})
        comp = outs.get("suspected_component") or "unknown"
        err = {"step": "run_tests", "code": "TEST_FAILURE", "message": f"{outs.get('failed', '?')} test(s) failed", "component": comp,
               "test_result_ids": outs.get("failed_ids", [])}
        if comp in pipeline.REPAIR_STEP_FOR:
            return c.out({"inflight": {}, "last_error": err | {"step": pipeline.REPAIR_STEP_FOR[comp]}})
        return c.out({"inflight": {}, "last_error": err | {"exhausted": True}})

    def handover_node(state: DeployState) -> dict[str, Any]:
        """Self-continuation: all progress is persisted (steps, outputs.resume_step_index, outputs.inflight), so
        fire a NEW root session of this agent with continue_run and return `started`. If that fire fails, carry on."""
        c = Calls()
        req, run_id = state["request"], state["run_id"]
        h = c("deploy_record_handover", run_id=run_id)
        n = int(h.get("executions") or 0) + 1
        session = f"deploy-{run_id}-cont{n}-{new_id('task')[-6:].lower()}"
        env = Envelope.request(poc_id=state["poc_id"], run_id=run_id, caller=AGENT_NAME, agent=AGENT_NAME, tool="continue_run",
                               mode="continue", params={"run_id": run_id, "reason": "handover", "execution": session},
                               trace_id=req.get("trace_id"))
        elapsed = int(time.time() - state.get("exec_started", time.time()))
        calls = int(state.get("tool_calls") or 0) - int(state.get("calls_base") or 0) + c.n
        try:
            platform_invoke.start_invoke("deploy-operations", env, user_id=user_id(), session_id=session,
                                         client_timeout_s=START_TIMEOUT_S)
        except Exception as e:
            logger.warning("⚠ handover of %s failed (%s) — continuing in this execution", run_id, str(e)[:200])
            return c.out({"exec_started": time.time(), "calls_base": int(state.get("tool_calls") or 0) + c.n})
        logger.info("↪ HANDOVER run=%s after %ss / %s tool calls → new execution session=%s (step_index=%s, inflight=%s)",
                    run_id, elapsed, calls, session, state.get("step_index"), (state.get("inflight") or {}).get("kind"))
        return c.out(reply(state, Envelope.started(req["task_id"], run_id)))

    def fail_node(state: DeployState) -> dict[str, Any]:
        c = Calls()
        err = state.get("last_error") or {"code": "UNKNOWN", "message": "unknown failure", "step": "?"}
        code = err.get("code", "STEP_FAILED")
        run = c("deploy_get_run", run_id=state["run_id"])
        c("deploy_finish_run", run_id=state["run_id"], status="failed",
          error_json=json.dumps({"code": code, "component": err.get("component"), "message": err.get("message", ""), "failure_key": err.get("log_key")}))
        req = state["request"]
        return c.out(reply(state, Envelope.failed(req["task_id"], code, f"deploy run {state['run_id']} failed at {err.get('step')}: {err.get('message', '')[:400]}",
                                                  detail={"run_id": state["run_id"], "step": err.get("step"), "repair_attempts": run.get("repair_attempts", {})})))

    def finish_node(state: DeployState) -> dict[str, Any]:
        c = Calls()
        run = c("deploy_get_run", run_id=state["run_id"])
        o = run.get("outputs", {})
        c("deploy_finish_run", run_id=state["run_id"], status="succeeded",
          error_json="", outputs_json=json.dumps({"deployment_key": o.get("deployment_key"), "urls": o.get("urls"), "code_version": o.get("code_version"),
                                                  "test_run_id": o.get("test_run_id"), "test_passed": o.get("test_passed")}))
        req = state["request"]
        arts = [Envelope.artifact("deployment", o["deployment_key"])] if o.get("deployment_key") else []
        if o.get("test_report_key"):
            arts.append(Envelope.artifact("report", o["test_report_key"]))
        return c.out(reply(state, Envelope.succeeded(req["task_id"], {"run_id": state["run_id"], "urls": o.get("urls"), "code_version": o.get("code_version"),
                                                                      "final_status": o.get("final_status"), "test_passed": o.get("test_passed")}, arts)))

    def teardown_node(state: DeployState) -> dict[str, Any]:
        c = Calls()
        req = state["request"]
        r = c("deploy_start_run", envelope_json=json.dumps({"request": req}))
        if "error" in r:
            return c.out(reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"])))
        t = c("deploy_teardown", poc_id=state["poc_id"], run_id=r["run_id"])
        if "error" in t:
            return c.out(reply(state, Envelope.failed(req["task_id"], t["error"]["code"], t["error"]["message"], detail={"run_id": r["run_id"]})))
        return c.out(reply(state, Envelope.succeeded(req["task_id"], {"run_id": r["run_id"], **t})))

    def get_deployment_node(state: DeployState) -> dict[str, Any]:
        c = Calls()
        req = state["request"]
        r = c("deploy_get_deployment", poc_id=state["poc_id"])
        if "error" in r:
            return c.out(reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"])))
        return c.out(reply(state, Envelope.succeeded(req["task_id"], {"deployment": r}, [Envelope.artifact("deployment", f"pocs/{state['poc_id']}/deploy/{r['run_id']}/deployment.json")])))

    def get_run_status_node(state: DeployState) -> dict[str, Any]:
        c = Calls()
        req = state["request"]
        run = c("deploy_get_run", run_id=req["params"]["run_id"])
        slim = {k: run.get(k) for k in ("run_id", "poc_id", "stage", "status", "current_step", "error", "repair_attempts")}
        slim["steps"] = [{"name": s["name"], "status": s["status"]} for s in run.get("steps", [])]
        slim["urls"] = (run.get("outputs") or {}).get("urls")
        return c.out(reply(state, Envelope.succeeded(req["task_id"], slim)))

    b = StateGraph(DeployState)
    for name, fn in [("parse", parse_node), ("start", start_node), ("resume", resume_node), ("step", step_node), ("repair", repair_node),
                     ("repair_wait", repair_wait_node), ("tests", tests_node), ("tests_wait", tests_wait_node),
                     ("handover", handover_node), ("fail", fail_node), ("finish", finish_node), ("teardown", teardown_node),
                     ("get_deployment", get_deployment_node), ("get_run_status", get_run_status_node)]:
        b.add_node(name, fn)
    loop = {k: k for k in ("handover", "step", "tests", "tests_wait", "repair", "repair_wait", "fail", "finish")} | {"end": END}
    b.add_edge(START, "parse")
    b.add_conditional_edges("parse", dispatch, {"start": "start", "resume": "resume", "teardown": "teardown",
                                                "get_deployment": "get_deployment", "get_run_status": "get_run_status", "end": END})
    for n in ("start", "resume", "step", "repair", "repair_wait", "tests", "tests_wait", "handover"):
        b.add_conditional_edges(n, route, loop)
    for n in ("fail", "finish", "teardown", "get_deployment", "get_run_status"):
        b.add_edge(n, END)
    # Each loop pass is a superstep; the hand-over budget bounds an execution, so lift LangGraph's default
    # recursion limit (25) comfortably above it.
    graph = b.compile(checkpointer=app.checkpointer()).with_config(recursion_limit=4 * HANDOVER_TOOL_CALLS + 40)
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
