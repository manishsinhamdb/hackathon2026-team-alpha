"""
Coding Orchestrator — POC Builder Stage 2 (Spec §6.3), built on the Agent Engine SDK. NO LLM here.

Message contract: every incoming message is an AgentEnvelope request (JSON, text/plain over A2A);
every reply is an AgentEnvelope response (JSON). Tools:
    start_code_run(poc_id, spec_version) · repair_component(poc_id, code_version, component, failure)
    get_code_bundle(poc_id, code_version)

For start/repair the graph runs the three coder agents (skills generate-api / generate-seed /
generate-frontend) in order — contract → seed → backend → frontend — assembles the versioned bundle and
finalizes.

Each coder is STARTED as a TOP-LEVEL platform invocation (its own root session with a fresh token), not an
A2A child, and then POLLED for completion — a fire-and-poll pattern. Two platform caps force this: (1) a full
4-coder run (~6 min) outlives the ~5-min OE A2A token, so A2A is out; (2) the synchronous invoke gateway
caps a turn at ~60 s and returns 504 while the callee keeps running to completion (a seed coder took 82 s
past a 504), so a *synchronous* invoke of a >60 s coder fails caller-side even though the coder succeeds.
So the orchestrator opens one task per coder (orch_begin_task), FIRES the coder with a short client timeout
(504/timeout == "started"), and then polls that task document (orch_task_status) — the coder marks its own
task done/failed in the platform DB from its root session when it finishes. Poll interval ~10 s, per-coder
ceiling 15 min; a coder that never completes fails the run with a clear CODER_TIMEOUT report. Each coder is
one task and one run step. The orchestrator replies `succeeded` at the END; a caller whose own call times
out should find the run by {"stage":"code","poc_id":...} newest first and poll it.

Resilient runs (docs/06 "Resilient runs"): the platform kills an execution after ~10 min wall clock, so
  * every tool that works for a while heartbeats runs.heartbeat_at (>= every 30 s);
  * waiting on a coder is ONE tool call (orch_wait_task) that polls internally for a bounded window
    (<= ORCH_WAIT_WINDOW_S) and returns terminal or "running"; the graph loops;
  * the run is resumable: continue_run(run_id) (mode "continue") re-enters at the first step not done,
    reusing every task/artifact already recorded (idempotent);
  * at each step/wait boundary, past ORCH_HANDOVER_AFTER_S elapsed or ORCH_HANDOVER_TOOL_CALLS tool calls,
    the execution hands itself over: it fires a new root session of itself with continue_run and returns
    `started`.

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
# Coders are STARTED as TOP-LEVEL platform invocations (each its own root session with a fresh token), not
# A2A children (a full 4-coder run outlives the ~5-min OE A2A token), and then POLLED — the synchronous
# invoke gateway 504s at ~60 s while a coder (seed reliably ~80 s) keeps running to completion, so a
# synchronous wait fails caller-side. We fire with a short client timeout (a 504/timeout means "started",
# the coder runs on server-side), then poll the coder's task document, which the coder marks done/failed.
CODER_START_TIMEOUT_S = 25       # short: disconnect well before the ~60s gateway cap; the coder runs on
CODER_POLL_INTERVAL_S = 10       # seconds between task-status polls
CODER_POLL_CEILING_S = 15 * 60   # per-coder ceiling; a coder that never completes fails the run
# Self-continuation (docs/06 "Resilient runs"). Defaults keep one execution well under the ~10-min platform cap.
HANDOVER_AFTER_S = int(os.getenv("ORCH_HANDOVER_AFTER_S", "360"))      # elapsed seconds in this execution
HANDOVER_TOOL_CALLS = int(os.getenv("ORCH_HANDOVER_TOOL_CALLS", "25"))  # tool calls in this execution
WAIT_WINDOW_S = min(240, int(os.getenv("ORCH_WAIT_WINDOW_S", "240")))   # one orch_wait_task call, <= 4 min


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


@app.tool(timeout=60)
def orch_continue_run(envelope_json: str) -> str:
    """Validate a continue_run envelope and reopen the run for this execution (records a continuation and
    bumps runs.executions). Idempotent: an already-succeeded run is reported as such, not re-run.
    Returns {"run_id", "poc_id", "tool": "continue_run", "already": "succeeded"?} or {"error": {...}}."""
    from poc_shared_tools import metadata as md
    from poc_shared_tools.errors import ToolError
    try:
        env = validate("agent_envelope", json.loads(envelope_json))["request"]
        run_id = env["params"]["run_id"]
        run = md.get_run(run_id)
        if run["stage"] != "code" or run["poc_id"] != env["poc_id"]:
            return json.dumps({"error": {"code": "BAD_RUN", "message": f"{run_id} is not a code run of {env['poc_id']}"}})
        base = {"run_id": run_id, "poc_id": run["poc_id"], "tool": "continue_run"}
        if run["status"] == "succeeded":
            return json.dumps(base | {"already": "succeeded", "outputs": run.get("outputs", {})})
        md.reopen_run(run_id, env["params"].get("reason", "continue"), env["params"].get("execution"))
        md.update_poc_status(run["poc_id"], "coding")
        return json.dumps(base)
    except (ContractError, KeyError, ValueError) as e:
        return json.dumps({"error": {"code": "INVALID_ENVELOPE", "message": str(e)[:500]}})
    except ToolError as e:
        return json.dumps({"error": {"code": e.code, "message": str(e)[:500], "retryable": e.retryable}})


@app.tool(timeout=120)
def orch_load_inputs(run_id: str) -> str:
    """Allocate the new code_version, resolve spec_version, compute the coder plan, and (on repair) copy the
    unchanged components + contract from the previous version. Stores ctx+plan in runs.outputs. Returns the plan ctx.
    Idempotent: a run that already has a plan (a continued run) reuses it — no new version is allocated."""
    from poc_shared_tools import metadata as md, s3 as s3t
    try:
        run = md.get_run(run_id)
        poc_id = run["poc_id"]
        inp = run["inputs"]
        o = run.get("outputs") or {}
        if o.get("plan") and o.get("ctx") and o.get("code_version"):
            md.touch_run(run_id)
            return json.dumps({"ctx": o["ctx"], "plan": o["plan"], "code_version": o["code_version"], "reused": True})
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
    """Open a task + run step for one coder call. The task carries component = the plan step name
    (contract|seed|backend|frontend) — a stable key, since contract and backend share agent+tool. Returns {"task_id"}."""
    from poc_shared_tools import metadata as md
    step = json.loads(step_json)
    task = md.create_task(run_id, poc_id, step["agent"], step["tool"], step.get("mode"), component=step["step"])
    md.update_run_step(run_id, f"{step['step']}:{step['tool']}", "running")
    return json.dumps({"task_id": task["task_id"]})


@app.tool(timeout=60)
def orch_end_task(run_id: str, task_id: str, step: str, status: str, artifact_key: str = "",
                  produces: str = "", usage_json: str = "") -> str:
    """Close a task + run step; record the produced artifact key into runs.outputs. Returns {"ok"}."""
    _end_task(run_id, task_id, step, status, artifact_key, produces, json.loads(usage_json) if usage_json else None)
    return json.dumps({"ok": True})


def _end_task(run_id: str, task_id: str, step: str, status: str, artifact_key: str = "", produces: str = "",
              usage: dict[str, int] | None = None, finish: bool = True) -> None:
    """Record a coder step's end on the run (step + output key). `finish=False` re-records an already-finished
    task on resume without touching the task document, so its original ended_at/duration survive."""
    from poc_shared_tools import metadata as md
    if finish:
        md.finish_task(task_id, status, output_ref=artifact_key or None, token_usage=usage, component=step)
    md.update_run_step(run_id, f"{step}:done", status, output_ref=artifact_key or None)
    if status == "succeeded" and artifact_key:
        field = "outputs.contract_key" if produces == "contract" else f"outputs.component_keys.{step}"
        md._db().runs.update_one({"run_id": run_id}, {"$set": {field: artifact_key, "updated_at": md.now()}})


@app.tool(timeout=30)
def orch_task_status(task_id: str) -> str:
    """Read one coder task the coder marks in its own root session (fire-and-poll). Returns
    {"status", "output_ref", "error", "usage"}; a still-running or not-yet-created task is reported
    running (never an error) so the poll loop keeps waiting until its ceiling."""
    from poc_shared_tools import metadata as md
    from poc_shared_tools.errors import ToolError
    try:
        t = md.get_task(task_id)
    except ToolError:
        return json.dumps({"status": "running", "output_ref": "", "error": None, "usage": None})
    return json.dumps({"status": t.get("status", "running"), "output_ref": t.get("output_ref", ""),
                       "error": t.get("error"), "usage": t.get("token_usage")})


@app.tool(timeout=60)
def orch_step_state(run_id: str, step: str) -> str:
    """Where does plan step `step` stand in this run (resume point)? Looks at the newest task for the step
    (tasks.component, or derived for legacy tasks). Returns one of
      {"state": "done", "task_id", "output_ref"}   — succeeded; its end is (re)recorded idempotently
      {"state": "running", "task_id"}             — in flight (a coder fired by an earlier execution)
      {"state": "start"}                           — nothing usable yet (missing, failed, or stuck past ceiling)."""
    from poc_shared_tools import metadata as md
    md.touch_run(run_id)
    tasks = [t for t in md.list_tasks(run_id) if md.task_component(t) == step]
    if not tasks:
        return json.dumps({"state": "start"})
    t = tasks[-1]
    if t.get("status") == "succeeded":
        run = md.get_run(run_id)
        o = run.get("outputs") or {}
        recorded = any(s_["name"] == f"{step}:done" and s_["status"] == "succeeded" for s_ in run.get("steps", []))
        key_field = "contract_key" if step == "contract" else None
        has_key = (o.get(key_field) if key_field else (o.get("component_keys") or {}).get(step))
        if not recorded or (t.get("output_ref") and not has_key):
            _end_task(run_id, t["task_id"], step, "succeeded", t.get("output_ref", ""),
                      "contract" if step == "contract" else "component", t.get("token_usage"), finish=False)
        return json.dumps({"state": "done", "task_id": t["task_id"], "output_ref": t.get("output_ref", "")})
    if t.get("status") == "running" and not _past_ceiling(t, md):
        return json.dumps({"state": "running", "task_id": t["task_id"]})
    if t.get("status") == "running":  # a coder that never reported back — close it, start afresh
        md.finish_task(t["task_id"], "failed", error={"code": "CODER_TIMEOUT", "message": "no completion within ceiling"})
    return json.dumps({"state": "start"})


def _past_ceiling(task: dict[str, Any], md: Any) -> bool:
    started = md._parse_ts(task.get("started_at") or task.get("created_at"))
    return bool(started) and (time.time() - started.timestamp()) > CODER_POLL_CEILING_S


@app.tool(timeout=300)
def orch_wait_task(task_id: str, run_id: str, window_s: int = WAIT_WINDOW_S) -> str:
    """ONE tool call that waits for a coder task: polls the task document internally every
    CODER_POLL_INTERVAL_S for up to `window_s` (<= 240 s), heartbeating the run, and returns
    {"status": "succeeded"|"failed"|"timeout"|"running", "output_ref", "error", "usage"}. "running" means
    the window closed with the coder still working — the graph loops (or hands over). "timeout" means the
    task is past its CODER_POLL_CEILING_S since it started."""
    from poc_shared_tools import metadata as md
    from poc_shared_tools.errors import ToolError
    window_s = max(1, min(int(window_s), 240))
    deadline = time.time() + window_s
    with md.heartbeat(run_id):
        while True:
            try:
                t = md.get_task(task_id)
            except ToolError:
                t = {"status": "running"}
            st = t.get("status", "running")
            if st in ("succeeded", "failed"):
                return json.dumps({"status": st, "output_ref": t.get("output_ref", ""), "error": t.get("error"),
                                   "usage": t.get("token_usage")})
            if t.get("started_at") and _past_ceiling(t, md):
                return json.dumps({"status": "timeout"})
            if time.time() + CODER_POLL_INTERVAL_S > deadline:
                return json.dumps({"status": "running"})
            time.sleep(CODER_POLL_INTERVAL_S)


@app.tool(timeout=60)
def orch_record_handover(run_id: str) -> str:
    """Touch the run before this execution hands itself over; returns {"executions"} (count so far)."""
    from poc_shared_tools import metadata as md
    md.touch_run(run_id)
    return json.dumps({"executions": int(md.get_run(run_id).get("executions") or 0)})


@app.tool(timeout=600)
def orch_assemble(run_id: str) -> str:
    """Download the whole code version, guardrail-scan it, bundle it, write + validate poc.manifest.json.
    Returns {"bundle_key", "contract_key", "manifest_key", "changed_components"} or {"error": {...}}.
    Idempotent: a run whose manifest + bundle are already recorded (a continued run) reuses them."""
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
        if o.get("manifest_key") and o.get("bundle_key"):
            md.update_run_step(run_id, "assemble", "succeeded", output_ref=o["bundle_key"])
            return json.dumps({"bundle_key": o["bundle_key"], "contract_key": o.get("contract_key", contract_key),
                               "manifest_key": o["manifest_key"], "changed_components": plan["changed_components"],
                               "reused": True})
        md.update_run_step(run_id, "assemble", "running")

        with md.heartbeat(run_id), tempfile.TemporaryDirectory() as tmp:
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
        md.update_run_step(run_id, "assemble", "succeeded", output_ref=bundle["bundle_key"])
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
    step_idx: int                 # index into plan["steps"] of the step being worked
    task_id: str                  # in-flight coder task of that step
    exec_started: float           # wall clock when THIS execution began (self-continuation budget)
    calls_base: int               # tool_calls at the start of this execution
    handover: bool
    last_error: dict[str, Any]
    result: dict[str, Any]
    done: bool


class OrchState(_Opt):
    messages: Annotated[list[BaseMessage], add_messages]
    tool_calls: Annotated[int, operator.add]


def _last_human_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def over_budget(state: dict[str, Any], now: float | None = None) -> bool:
    """Self-continuation boundary test: this execution has run past HANDOVER_AFTER_S or made more than
    HANDOVER_TOOL_CALLS tool calls."""
    elapsed = (now or time.time()) - state.get("exec_started", time.time())
    calls = int(state.get("tool_calls") or 0) - int(state.get("calls_base") or 0)
    return elapsed > HANDOVER_AFTER_S or calls > HANDOVER_TOOL_CALLS


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    logger.info("Building Coding Orchestrator graph...")
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

    def reply(state: OrchState, response: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [AIMessage(content=json.dumps(response))], "done": True, "result": response}

    def parse_node(state: OrchState) -> dict[str, Any]:
        base = {"exec_started": time.time(), "calls_base": int(state.get("tool_calls") or 0), "handover": False}
        text = _last_human_text(state["messages"])
        try:
            env = validate("agent_envelope", json.loads(text))["request"]
        except Exception as e:
            return base | reply(state, Envelope.failed(new_id("task"), "INVALID_ENVELOPE", f"expected AgentEnvelope JSON: {str(e)[:300]}"))
        return base | {"request": env, "poc_id": env["poc_id"], "done": False, "step_idx": 0, "task_id": ""}

    def dispatch(state: OrchState) -> Literal["start", "continue", "get_bundle", "bad", "end"]:
        if state.get("done"):
            return "end"
        tool = state["request"]["tool"]
        if tool in ("start_code_run", "repair_component"):
            return "start"
        if tool == "continue_run":
            return "continue"
        if tool == "get_code_bundle":
            return "get_bundle"
        return "bad"

    def bad_tool_node(state: OrchState) -> dict[str, Any]:
        req = state["request"]
        return reply(state, Envelope.failed(req["task_id"], "BAD_TOOL", req["tool"]))

    def start_node(state: OrchState) -> dict[str, Any]:
        c = Calls()
        req = state["request"]
        r = c("orch_start_run", envelope_json=json.dumps({"request": req}))
        if "error" in r:
            return c.out(reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"], r["error"].get("retryable", False))))
        return c.out({"run_id": r["run_id"]})

    def continue_node(state: OrchState) -> dict[str, Any]:
        """continue_run(run_id): resume an existing run at its first step not done (idempotent)."""
        c = Calls()
        req = state["request"]
        r = c("orch_continue_run", envelope_json=json.dumps({"request": req}))
        if "error" in r:
            return c.out(reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"], r["error"].get("retryable", False))))
        if r.get("already") == "succeeded":
            o = r.get("outputs") or {}
            return c.out(reply(state, Envelope.succeeded(req["task_id"], {"run_id": r["run_id"], "code_version": o.get("code_version"),
                                                                         "changed_components": o.get("changed_components", []),
                                                                         "already": "succeeded"})))
        logger.info("▶ continue_run %s (reason=%s)", r["run_id"], req["params"].get("reason", "continue"))
        return c.out({"run_id": r["run_id"]})

    def load_node(state: OrchState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        c = Calls()
        r = c("orch_load_inputs", run_id=state["run_id"])
        if "error" in r:
            c("orch_finalize", run_id=state["run_id"], ok=False, error_json=json.dumps(r["error"]))
            return c.out(reply(state, Envelope.failed(state["request"]["task_id"], r["error"]["code"], r["error"]["message"])))
        return c.out({"code_version": r["code_version"], "plan": r["plan"], "step_idx": 0, "task_id": ""})

    def step_node(state: OrchState) -> dict[str, Any]:
        """Resume point for plan step `step_idx`: advance past a done step, re-attach to an in-flight coder,
        or begin a task and FIRE the coder."""
        c = Calls()
        req = state["request"]
        run_id, poc_id, code_version = state["run_id"], state["poc_id"], state["code_version"]
        step = state["plan"]["steps"][state["step_idx"]]
        st = c("orch_step_state", run_id=run_id, step=step["step"])
        if st["state"] == "done":
            return c.out({"step_idx": state["step_idx"] + 1, "task_id": ""})
        if st["state"] == "running":
            return c.out({"task_id": st["task_id"]})
        bt = c("orch_begin_task", run_id=run_id, poc_id=poc_id, step_json=json.dumps(step))
        task_id = bt["task_id"]
        params: dict[str, Any] = {"poc_id": poc_id, "code_version": code_version, "inputs": step["inputs"]}
        if step.get("failure"):
            params["failure"] = step["failure"]
        if step.get("previous_source_key"):
            params["previous_source_key"] = step["previous_source_key"]
        env = Envelope.request(poc_id=poc_id, run_id=run_id, caller=AGENT_NAME, agent=step["agent"],
                               tool=step["tool"], mode=step["mode"], params=params, task_id=task_id,
                               trace_id=req.get("trace_id"))
        # FIRE: start the coder as a TOP-LEVEL invoke (its own root session, fresh token) and disconnect
        # before the ~60s gateway cap; a 504/timeout means "started" and the coder runs on server-side. A
        # non-504 error status is a real start failure. The coder marks task_id done/failed in the DB.
        try:
            platform_invoke.start_invoke(step["skill"], env, user_id=app.get_current_user_id() or "u_local",
                                         session_id=f"code-{run_id}-{step['step']}-{task_id[-6:].lower()}",
                                         client_timeout_s=CODER_START_TIMEOUT_S)
        except Exception as e:
            c("orch_end_task", run_id=run_id, task_id=task_id, step=step["step"], status="failed")
            return c.out(_fail_run(state, c, "CODER_UNAVAILABLE", f"{step['step']} start failed: {str(e)[:300]}"))
        return c.out({"task_id": task_id})

    def wait_node(state: OrchState) -> dict[str, Any]:
        """ONE bounded wait call on the in-flight coder; the window shrinks to end at the hand-over budget."""
        c = Calls()
        run_id = state["run_id"]
        step = state["plan"]["steps"][state["step_idx"]]
        left = HANDOVER_AFTER_S - (time.time() - state.get("exec_started", time.time()))
        window = int(max(15, min(WAIT_WINDOW_S, left)))
        w = c("orch_wait_task", task_id=state["task_id"], run_id=run_id, window_s=window)
        if w["status"] == "running":
            return c.out({})
        if w["status"] == "succeeded":
            c("orch_end_task", run_id=run_id, task_id=state["task_id"], step=step["step"], status="succeeded",
              artifact_key=w.get("output_ref", ""), produces=step["produces"], usage_json=json.dumps(w.get("usage") or {}))
            return c.out({"step_idx": state["step_idx"] + 1, "task_id": ""})
        c("orch_end_task", run_id=run_id, task_id=state["task_id"], step=step["step"], status="failed")
        if w["status"] == "timeout":
            return c.out(_fail_run(state, c, "CODER_TIMEOUT", f"{step['step']} did not complete within {CODER_POLL_CEILING_S}s"))
        err = w.get("error") or {}
        return c.out(_fail_run(state, c, err.get("code", "CODER_FAILED"), f"{step['step']}: {err.get('message', 'coder did not succeed')}"))

    def route_step(state: OrchState) -> Literal["handover", "step", "wait", "assemble", "end"]:
        """Boundary after every step/wait: finished → end; over budget → hand over; else keep going."""
        if state.get("done"):
            return "end"
        if over_budget(state):
            return "handover"
        if state.get("task_id"):
            return "wait"
        if state["step_idx"] >= len(state["plan"]["steps"]):
            return "assemble"
        return "step"

    def handover_node(state: OrchState) -> dict[str, Any]:
        """Self-continuation: everything is already persisted (tasks, steps, outputs), so fire a NEW root session
        of this agent with continue_run and return `started`. If the fire itself fails, carry on here."""
        c = Calls()
        req, run_id = state["request"], state["run_id"]
        h = c("orch_record_handover", run_id=run_id)
        n = int(h.get("executions") or 0) + 1
        session = f"code-{run_id}-cont{n}-{new_id('task')[-6:].lower()}"
        env = Envelope.request(poc_id=state["poc_id"], run_id=run_id, caller=AGENT_NAME, agent=AGENT_NAME,
                               tool="continue_run", mode="continue", params={"run_id": run_id, "reason": "handover",
                                                                            "execution": session},
                               trace_id=req.get("trace_id"))
        elapsed = int(time.time() - state.get("exec_started", time.time()))
        calls = int(state.get("tool_calls") or 0) - int(state.get("calls_base") or 0) + c.n
        try:
            platform_invoke.start_invoke("code-orchestration", env, user_id=app.get_current_user_id() or "u_local",
                                         session_id=session, client_timeout_s=CODER_START_TIMEOUT_S)
        except Exception as e:
            logger.warning("⚠ handover of %s failed (%s) — continuing in this execution", run_id, str(e)[:200])
            return c.out({"exec_started": time.time(), "calls_base": int(state.get("tool_calls") or 0) + c.n})
        logger.info("↪ HANDOVER run=%s after %ss / %s tool calls → new execution session=%s (step_idx=%s)",
                    run_id, elapsed, calls, session, state.get("step_idx"))
        return c.out({"handover": True, **reply(state, Envelope.started(req["task_id"], run_id))})

    def assemble_node(state: OrchState) -> dict[str, Any]:
        c = Calls()
        r = c("orch_assemble", run_id=state["run_id"])
        if "error" in r:
            return c.out(_fail_run(state, c, r["error"]["code"], r["error"]["message"]))
        return c.out({})

    def finalize_node(state: OrchState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        c = Calls()
        r = c("orch_finalize", run_id=state["run_id"], ok=True)
        req = state["request"]
        arts = []
        if r.get("bundle_key"):
            arts.append(Envelope.artifact("bundle", r["bundle_key"], r["code_version"]))
        if r.get("contract_key"):
            arts.append(Envelope.artifact("contract", r["contract_key"], r["code_version"]))
        return c.out(reply(state, Envelope.succeeded(req["task_id"],
                                                     {"run_id": state["run_id"], "code_version": r["code_version"],
                                                      "changed_components": r.get("changed_components", [])}, arts)))

    def _fail_run(state: OrchState, c: Calls, code: str, message: str) -> dict[str, Any]:
        c("orch_finalize", run_id=state["run_id"], ok=False, error_json=json.dumps({"code": code, "message": message}))
        return reply(state, Envelope.failed(state["request"]["task_id"], code, message,
                                            detail={"run_id": state["run_id"]}))

    def get_bundle_node(state: OrchState) -> dict[str, Any]:
        c = Calls()
        req = state["request"]
        p = req["params"]
        r = c("orch_get_bundle", poc_id=req["poc_id"], code_version=p["code_version"])
        if "error" in r:
            return c.out(reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"])))
        arts = [Envelope.artifact("bundle", r["bundle_key"], p["code_version"])]
        return c.out(reply(state, Envelope.succeeded(req["task_id"], {"bundle_key": r["bundle_key"], "manifest": r["manifest"]}, arts)))

    b = StateGraph(OrchState)
    for name, fn in [("parse", parse_node), ("start", start_node), ("continue", continue_node), ("load", load_node),
                     ("step", step_node), ("wait", wait_node), ("handover", handover_node),
                     ("assemble", assemble_node), ("finalize", finalize_node), ("get_bundle", get_bundle_node),
                     ("bad", bad_tool_node)]:
        b.add_node(name, fn)
    loop = {"handover": "handover", "step": "step", "wait": "wait", "assemble": "assemble", "end": END}
    b.add_edge(START, "parse")
    b.add_conditional_edges("parse", dispatch, {"start": "start", "continue": "continue", "get_bundle": "get_bundle",
                                                "bad": "bad", "end": END})
    b.add_edge("bad", END)
    b.add_conditional_edges("start", lambda s: "end" if s.get("done") else "load", {"load": "load", "end": END})
    b.add_conditional_edges("continue", lambda s: "end" if s.get("done") else "load", {"load": "load", "end": END})
    b.add_conditional_edges("load", route_step, loop)
    b.add_conditional_edges("step", route_step, loop)
    b.add_conditional_edges("wait", route_step, loop)
    b.add_conditional_edges("handover", route_step, loop)
    b.add_conditional_edges("assemble", lambda s: "end" if s.get("done") else "finalize", {"finalize": "finalize", "end": END})
    b.add_edge("finalize", END)
    b.add_edge("get_bundle", END)
    # Each loop pass is a superstep; the hand-over budget (<= HANDOVER_TOOL_CALLS calls) bounds an execution,
    # so lift LangGraph's default recursion limit (25) comfortably above it.
    graph = b.compile(checkpointer=app.checkpointer()).with_config(recursion_limit=4 * HANDOVER_TOOL_CALLS + 40)
    logger.info("✅ Coding Orchestrator graph compiled")
    return graph


def main():
    logger.info("=" * 60)
    logger.info(f"🚀 Starting {APP_NAME} (Runner SDK)")
    logger.info("=" * 60)
    app.run()


if __name__ == "__main__":
    main()
