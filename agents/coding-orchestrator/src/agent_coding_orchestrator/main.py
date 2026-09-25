"""
Coding Orchestrator — POC Builder Stage 2 (Spec §6.3), built on the Agent Engine SDK. NO LLM here.

Message contract: every incoming message is an AgentEnvelope request (JSON, text/plain over A2A);
every reply is an AgentEnvelope response (JSON). Tools:
    start_code_run(poc_id, spec_version) · repair_component(poc_id, code_version, component, failure)
    get_code_bundle(poc_id, code_version)

For start/repair the graph runs the three coder agents (skills generate-api / generate-seed /
generate-frontend) in order — contract → seed → backend → frontend — assembles the versioned bundle and
finalizes. Each coder is called SYNCHRONOUSLY as a TOP-LEVEL platform invocation (its own root session with a
fresh token), not an A2A child, because a full 4-coder run (~6 min) outlives the ~5-min OE A2A token. Each
call is one task and one run step. The orchestrator replies `succeeded` at the END; a caller whose own call
times out should find the run by {"stage":"code","poc_id":...} newest first and poll it.

    RUNNER_MODE=aer   -> LangGraph execution (this graph)
    RUNNER_MODE=tool  -> Tool functions below
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Annotated, Any, Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from agent_engine_sdk_langgraph import App

from poc_contracts import ContractError, Envelope, new_id, validate
from poc_shared_tools import platform_invoke
from agent_coding_orchestrator import pipeline
from agent_coding_orchestrator.a2a import invoke_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
logger = logging.getLogger(__name__)
load_dotenv()

APP_NAME = "Coding Orchestrator"
AGENT_NAME = "coding_orchestrator"
app = App(app_name=APP_NAME)
logger.info("✅ App created")

STACK = {"backend": "node-express", "frontend": "react-vite", "database": "mongodb"}
# Coders are called as TOP-LEVEL platform invocations (each its own root session with a fresh token), not
# A2A children, because a full code run (4 coders) outlives the ~5-min OE A2A token. The call is synchronous
# — it blocks for the coder's whole reply (~35-70 s) — so the client timeout is generous.
CODER_INVOKE_TIMEOUT_S = 900


# =============================================================================
# Tools — all in the Tool Pod (S3 + platform DB access)
# =============================================================================

@app.tool(timeout=60)
def orch_start_run(envelope_json: str) -> str:
    """Validate a start_code_run / repair_component envelope, check the code gate, create the run.
    Returns {"run_id", "poc_id", "tool", ...} or {"error": {...}}."""
    from poc_shared_tools import metadata as md
    from poc_shared_tools.errors import ToolError
    try:
        env = validate("agent_envelope", json.loads(envelope_json))["request"]
        p = env["params"]
        poc_id = env["poc_id"]
        if env["tool"] == "start_code_run":
            spec_version = p["spec_version"]
            if not md.check_gate(poc_id, "code", spec_version):
                return json.dumps({"error": {"code": "GATE_NOT_APPROVED", "message": f"spec_approved missing for {spec_version}"}})
            run = md.create_run(poc_id, "code", env.get("caller", "chat_agent"), AGENT_NAME,
                                {"spec_version": spec_version}, env.get("trace_id"))
            md.update_poc_status(poc_id, "coding")
            return json.dumps({"run_id": run["run_id"], "poc_id": poc_id, "tool": env["tool"], "spec_version": spec_version})
        if env["tool"] == "repair_component":
            failure = validate("failure_report", p["failure"])
            run = md.create_run(poc_id, "code", env.get("caller", "deploy_agent"), AGENT_NAME,
                                {"code_version": p["code_version"], "component": failure["component"], "failure": failure},
                                env.get("trace_id"))
            md.update_poc_status(poc_id, "coding")
            return json.dumps({"run_id": run["run_id"], "poc_id": poc_id, "tool": env["tool"]})
        return json.dumps({"error": {"code": "BAD_TOOL", "message": env["tool"]}})
    except (ContractError, KeyError, ValueError) as e:
        return json.dumps({"error": {"code": "INVALID_ENVELOPE", "message": str(e)[:500]}})
    except ToolError as e:
        return json.dumps({"error": {"code": e.code, "message": str(e)[:500], "retryable": e.retryable}})


@app.tool(timeout=120)
def orch_load_inputs(run_id: str) -> str:
    """Allocate the new code_version, resolve spec_version, compute the coder plan, and (on repair) copy the
    unchanged components + contract from the previous version. Stores ctx+plan in runs.outputs. Returns the plan ctx."""
    from poc_shared_tools import metadata as md, s3 as s3t
    try:
        run = md.get_run(run_id)
        poc_id = run["poc_id"]
        inp = run["inputs"]
        code_version = s3t.next_version(poc_id, "code")
        if "spec_version" in inp:                      # start_code_run
            ctx = {"tool": "start_code_run", "poc_id": poc_id, "spec_version": inp["spec_version"],
                   "code_version": code_version}
        else:                                          # repair_component
            prev = inp["code_version"]
            prev_manifest = json.loads(s3t.get_text(f"pocs/{poc_id}/code/{prev}/poc.manifest.json"))
            ctx = {"tool": "repair_component", "poc_id": poc_id, "spec_version": prev_manifest["spec_version"],
                   "code_version": code_version, "prev_version": prev, "failure": inp["failure"]}

        plan = pipeline.build_plan(ctx)

        # On repair, carry forward everything that is NOT being regenerated.
        new_base = f"pocs/{poc_id}/code/{code_version}/"
        if ctx["tool"] == "repair_component":
            prev_base = f"pocs/{poc_id}/code/{ctx['prev_version']}/"
            if plan["copy_contract"]:
                s3t.copy_object(prev_base + "api_contract.yaml", new_base + "api_contract.yaml")
            for comp in plan["copy_components"]:
                _copy_prefix(s3t, prev_base + comp + "/", new_base + comp + "/")

        out = {"ctx": ctx, "plan": plan, "code_version": code_version}
        md._db().runs.update_one({"run_id": run_id},
                                 {"$set": {"outputs.code_version": code_version, "outputs.spec_version": ctx["spec_version"],
                                           "outputs.plan": plan, "outputs.ctx": ctx, "updated_at": md.now()}})
        return json.dumps(out)
    except Exception as e:
        return json.dumps({"error": {"code": getattr(e, "code", "LOAD_FAILED"), "message": str(e)[:500]}})


def _copy_prefix(s3t: Any, src_base: str, dst_base: str) -> None:
    for obj in s3t.list_prefix(src_base):
        rel = obj["key"][len(src_base):]
        if rel:
            s3t.copy_object(obj["key"], dst_base + rel)


@app.tool(timeout=60)
def orch_begin_task(run_id: str, poc_id: str, step_json: str) -> str:
    """Open a task + run step for one coder call. Returns {"task_id"}."""
    from poc_shared_tools import metadata as md
    step = json.loads(step_json)
    task = md.create_task(run_id, poc_id, step["agent"], step["tool"], step.get("mode"))
    md.update_run_step(run_id, f"{step['step']}:{step['tool']}", "running")
    return json.dumps({"task_id": task["task_id"]})


@app.tool(timeout=60)
def orch_end_task(run_id: str, task_id: str, step: str, status: str, artifact_key: str = "",
                  produces: str = "", usage_json: str = "") -> str:
    """Close a task + run step; record the produced artifact key into runs.outputs. Returns {"ok"}."""
    from poc_shared_tools import metadata as md
    usage = json.loads(usage_json) if usage_json else None
    md.finish_task(task_id, status, output_ref=artifact_key or None, token_usage=usage)
    md.update_run_step(run_id, f"{step}:done", status, output_ref=artifact_key or None)
    if status == "succeeded" and artifact_key:
        field = "outputs.contract_key" if produces == "contract" else f"outputs.component_keys.{step}"
        md._db().runs.update_one({"run_id": run_id}, {"$set": {field: artifact_key, "updated_at": md.now()}})
    return json.dumps({"ok": True})


@app.tool(timeout=600)
def orch_assemble(run_id: str) -> str:
    """Download the whole code version, guardrail-scan it, bundle it, write + validate poc.manifest.json.
    Returns {"bundle_key", "contract_key", "manifest_key", "changed_components"} or {"error": {...}}."""
    import tempfile
    from poc_shared_tools import guardrails, metadata as md, s3 as s3t
    try:
        run = md.get_run(run_id)
        poc_id = run["poc_id"]
        o = run["outputs"]
        ctx, plan = o["ctx"], o["plan"]
        code_version, spec_version = o["code_version"], o["spec_version"]
        base = f"pocs/{poc_id}/code/{code_version}/"
        contract_key = base + "api_contract.yaml"

        with tempfile.TemporaryDirectory() as tmp:
            s3t.download_dir(base, tmp)
            scan = guardrails.scan_bundle(tmp)
            if not scan["ok"]:
                return json.dumps({"error": {"code": "GUARDRAIL_VIOLATION", "message": json.dumps(scan["violations"])[:1500]}})
            bundle = s3t.make_bundle(poc_id, run_id, code_version, tmp, AGENT_NAME)

        manifest = pipeline.build_poc_manifest(
            poc_id=poc_id, code_version=code_version, spec_version=spec_version, stack=STACK,
            contract_key=contract_key, bundle_key=bundle["bundle_key"], bundle_sha256=bundle["bundle_sha256"],
            scanned_at=md.now(), violations=scan["violations"], run_id=run_id,
            changed_components=plan["changed_components"], repairs_of=plan["repairs_of"])
        manifest_key = base + "poc.manifest.json"
        s3t.put_object(poc_id, run_id, manifest_key, json.dumps(manifest, indent=2), "application/json", AGENT_NAME)

        md._db().runs.update_one({"run_id": run_id},
                                 {"$set": {"outputs.bundle_key": bundle["bundle_key"], "outputs.contract_key": contract_key,
                                           "outputs.manifest_key": manifest_key,
                                           "outputs.changed_components": plan["changed_components"], "updated_at": md.now()}})
        return json.dumps({"bundle_key": bundle["bundle_key"], "contract_key": contract_key,
                           "manifest_key": manifest_key, "changed_components": plan["changed_components"]})
    except ContractError as e:
        return json.dumps({"error": {"code": "MANIFEST_INVALID", "message": str(e)[:500]}})
    except Exception as e:
        return json.dumps({"error": {"code": getattr(e, "code", "ASSEMBLE_FAILED"), "message": str(e)[:500]}})


@app.tool(timeout=60)
def orch_finalize(run_id: str, ok: bool, error_json: str = "") -> str:
    """Success: set current code version + poc status code_ready + finish run succeeded. Failure: finish run failed."""
    from poc_shared_tools import metadata as md
    run = md.get_run(run_id)
    poc_id = run["poc_id"]
    o = run.get("outputs", {})
    if ok:
        code_version = o["code_version"]
        md.set_current_version(poc_id, "code", code_version)
        md.update_poc_status(poc_id, "code_ready", **{"current_versions.code": code_version})
        outputs = {"code_version": code_version, "changed_components": o.get("changed_components", []),
                   "bundle_key": o.get("bundle_key"), "contract_key": o.get("contract_key")}
        md.finish_run(run_id, "succeeded", outputs=outputs)
        return json.dumps({"run_id": run_id, **outputs})
    err = json.loads(error_json) if error_json else {"code": "CODE_RUN_FAILED", "message": "coding run failed"}
    md.finish_run(run_id, "failed", error=err)
    md.update_poc_status(poc_id, "code_failed")
    return json.dumps({"run_id": run_id, "error": err})


@app.tool(timeout=30)
def orch_get_bundle(poc_id: str, code_version: str) -> str:
    """Return the bundle key + manifest for a code version, or {"error": ...}."""
    from poc_shared_tools import s3 as s3t
    from poc_shared_tools.errors import ToolError
    try:
        manifest = json.loads(s3t.get_text(f"pocs/{poc_id}/code/{code_version}/poc.manifest.json"))
        return json.dumps({"bundle_key": manifest["bundle_key"], "manifest": manifest})
    except ToolError as e:
        return json.dumps({"error": {"code": e.code, "message": str(e)[:500]}})


# =============================================================================
# Graph
# =============================================================================

class _Opt(TypedDict, total=False):
    request: dict[str, Any]
    run_id: str
    poc_id: str
    code_version: str
    plan: dict[str, Any]
    last_error: dict[str, Any]
    result: dict[str, Any]
    done: bool


class OrchState(_Opt):
    messages: Annotated[list[BaseMessage], add_messages]


def _last_human_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    logger.info("Building Coding Orchestrator graph...")
    tools = {t.name: t for t in app.get_tools()}

    def call(name: str, **kw: Any) -> dict[str, Any]:
        out = invoke_tool(tools[name], kw)
        return json.loads(out) if isinstance(out, str) else out

    def reply(state: OrchState, response: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [AIMessage(content=json.dumps(response))], "done": True, "result": response}

    def parse_node(state: OrchState) -> dict[str, Any]:
        text = _last_human_text(state["messages"])
        try:
            env = validate("agent_envelope", json.loads(text))["request"]
        except Exception as e:
            return reply(state, Envelope.failed(new_id("task"), "INVALID_ENVELOPE", f"expected AgentEnvelope JSON: {str(e)[:300]}"))
        return {"request": env, "poc_id": env["poc_id"], "done": False}

    def dispatch(state: OrchState) -> Literal["start", "get_bundle", "bad", "end"]:
        if state.get("done"):
            return "end"
        tool = state["request"]["tool"]
        if tool in ("start_code_run", "repair_component"):
            return "start"
        if tool == "get_code_bundle":
            return "get_bundle"
        return "bad"

    def bad_tool_node(state: OrchState) -> dict[str, Any]:
        req = state["request"]
        return reply(state, Envelope.failed(req["task_id"], "BAD_TOOL", req["tool"]))

    def start_node(state: OrchState) -> dict[str, Any]:
        req = state["request"]
        r = call("orch_start_run", envelope_json=json.dumps({"request": req}))
        if "error" in r:
            return reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"], r["error"].get("retryable", False)))
        return {"run_id": r["run_id"]}

    def load_node(state: OrchState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        r = call("orch_load_inputs", run_id=state["run_id"])
        if "error" in r:
            call("orch_finalize", run_id=state["run_id"], ok=False, error_json=json.dumps(r["error"]))
            return reply(state, Envelope.failed(state["request"]["task_id"], r["error"]["code"], r["error"]["message"]))
        return {"code_version": r["code_version"], "plan": r["plan"]}

    def coders_node(state: OrchState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        req = state["request"]
        run_id, poc_id, code_version = state["run_id"], state["poc_id"], state["code_version"]
        user_id = app.get_current_user_id() or "u_local"
        for step in state["plan"]["steps"]:
            bt = call("orch_begin_task", run_id=run_id, poc_id=poc_id, step_json=json.dumps(step))
            task_id = bt["task_id"]
            params: dict[str, Any] = {"poc_id": poc_id, "code_version": code_version, "inputs": step["inputs"]}
            if step.get("failure"):
                params["failure"] = step["failure"]
            if step.get("previous_source_key"):
                params["previous_source_key"] = step["previous_source_key"]
            env = Envelope.request(poc_id=poc_id, run_id=run_id, caller=AGENT_NAME, agent=step["agent"],
                                   tool=step["tool"], mode=step["mode"], params=params, task_id=task_id,
                                   trace_id=req.get("trace_id"))
            # TOP-LEVEL synchronous invoke of the coder workspace (its own root session, fresh token), not an
            # A2A child: a full 4-coder run outlives the ~5-min OE A2A token, which 401'd the last coder.
            try:
                resp = platform_invoke.invoke_envelope(
                    step["skill"], env, user_id=user_id, session_id=f"code-{run_id}-{step['step']}",
                    timeout_s=CODER_INVOKE_TIMEOUT_S)["response"]
            except Exception as e:
                call("orch_end_task", run_id=run_id, task_id=task_id, step=step["step"], status="failed")
                return _fail_run(state, "CODER_UNAVAILABLE", f"{step['step']} call failed: {str(e)[:300]}")
            if resp.get("status") != "succeeded":
                err = resp.get("error", {})
                call("orch_end_task", run_id=run_id, task_id=task_id, step=step["step"], status="failed")
                return _fail_run(state, err.get("code", "CODER_FAILED"), f"{step['step']}: {err.get('message', 'coder did not succeed')}")
            arts = resp.get("artifacts", [])
            artifact_key = arts[0]["key"] if arts else ""
            call("orch_end_task", run_id=run_id, task_id=task_id, step=step["step"], status="succeeded",
                 artifact_key=artifact_key, produces=step["produces"], usage_json=json.dumps(resp.get("usage") or {}))
        return {}

    def assemble_node(state: OrchState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        r = call("orch_assemble", run_id=state["run_id"])
        if "error" in r:
            return _fail_run(state, r["error"]["code"], r["error"]["message"])
        return {}

    def finalize_node(state: OrchState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        r = call("orch_finalize", run_id=state["run_id"], ok=True)
        req = state["request"]
        arts = []
        if r.get("bundle_key"):
            arts.append(Envelope.artifact("bundle", r["bundle_key"], r["code_version"]))
        if r.get("contract_key"):
            arts.append(Envelope.artifact("contract", r["contract_key"], r["code_version"]))
        return reply(state, Envelope.succeeded(req["task_id"],
                                               {"run_id": state["run_id"], "code_version": r["code_version"],
                                                "changed_components": r.get("changed_components", [])}, arts))

    def _fail_run(state: OrchState, code: str, message: str) -> dict[str, Any]:
        call("orch_finalize", run_id=state["run_id"], ok=False, error_json=json.dumps({"code": code, "message": message}))
        return reply(state, Envelope.failed(state["request"]["task_id"], code, message,
                                            detail={"run_id": state["run_id"]}))

    def get_bundle_node(state: OrchState) -> dict[str, Any]:
        req = state["request"]
        p = req["params"]
        r = call("orch_get_bundle", poc_id=req["poc_id"], code_version=p["code_version"])
        if "error" in r:
            return reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"]))
        arts = [Envelope.artifact("bundle", r["bundle_key"], p["code_version"])]
        return reply(state, Envelope.succeeded(req["task_id"], {"bundle_key": r["bundle_key"], "manifest": r["manifest"]}, arts))

    b = StateGraph(OrchState)
    for name, fn in [("parse", parse_node), ("start", start_node), ("load", load_node), ("coders", coders_node),
                     ("assemble", assemble_node), ("finalize", finalize_node), ("get_bundle", get_bundle_node),
                     ("bad", bad_tool_node)]:
        b.add_node(name, fn)
    b.add_edge(START, "parse")
    b.add_conditional_edges("parse", dispatch, {"start": "start", "get_bundle": "get_bundle", "bad": "bad", "end": END})
    b.add_edge("bad", END)
    b.add_conditional_edges("start", lambda s: "end" if s.get("done") else "load", {"load": "load", "end": END})
    b.add_conditional_edges("load", lambda s: "end" if s.get("done") else "coders", {"coders": "coders", "end": END})
    b.add_conditional_edges("coders", lambda s: "end" if s.get("done") else "assemble", {"assemble": "assemble", "end": END})
    b.add_conditional_edges("assemble", lambda s: "end" if s.get("done") else "finalize", {"finalize": "finalize", "end": END})
    b.add_edge("finalize", END)
    b.add_edge("get_bundle", END)
    graph = b.compile(checkpointer=app.checkpointer())
    logger.info("✅ Coding Orchestrator graph compiled")
    return graph


def main():
    logger.info("=" * 60)
    logger.info(f"🚀 Starting {APP_NAME} (Runner SDK)")
    logger.info("=" * 60)
    app.run()


if __name__ == "__main__":
    main()
