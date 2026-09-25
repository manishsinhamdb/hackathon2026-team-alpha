"""
Test Agent — POC Builder Stage 4 (Spec §6.8), built on the Agent Engine SDK.

Message contract: every incoming message is an AgentEnvelope request (JSON, text/plain over A2A);
every reply is an AgentEnvelope response (JSON). Entry point: run_e2e(deployment_run_id, scope?).

No LLM calls anywhere in this agent. All work runs in the Tool Pod.

Graph: parse → start → load → plan → smoke → browser → report → reply
Each node calls one tool via invoke_tool(); any {"error"} short-circuits to fail_node.
The whole run is time-boxed at 15 minutes (900 s); breach → TIMEOUT.

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
from langgraph.graph.state import CompiledStateGraph
from langgraph.graph.message import add_messages
from agent_engine_sdk_langgraph import App

from poc_contracts import Envelope, ContractError, new_id, validate
from agent_test_agent.a2a import invoke_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
logger = logging.getLogger(__name__)
load_dotenv()

APP_NAME = "Test Agent"
AGENT_NAME = "test_agent"
TIMEOUT_S = 900  # 15 minutes

app = App(app_name=APP_NAME)
logger.info("✅ App created")


# =============================================================================
# Tools — all in the Tool Pod (cloud + platform DB access)
# =============================================================================

@app.tool(timeout=60)
def test_start_run(envelope_json: str) -> str:
    """Validate a run_e2e envelope, create the runs document (stage 'test'), update poc status.
    Returns {"run_id", "poc_id", "deployment_run_id", "scope"} or {"error": {...}}."""
    from poc_shared_tools import metadata as md
    try:
        env = validate("agent_envelope", json.loads(envelope_json))["request"]
        if env["tool"] != "run_e2e":
            return json.dumps({"error": {"code": "BAD_TOOL", "message": env["tool"]}})
        p = env["params"]
        poc_id = env["poc_id"]
        deployment_run_id = p.get("deployment_run_id") or env.get("run_id", "")
        scope = p.get("scope", "all")
        run = md.create_run(
            poc_id, "test",
            env.get("caller", "deploy_agent"), AGENT_NAME,
            {"deployment_run_id": deployment_run_id, "scope": scope},
            env.get("trace_id"),
        )
        md.update_poc_status(poc_id, "testing")
        return json.dumps({"run_id": run["run_id"], "poc_id": poc_id,
                           "deployment_run_id": deployment_run_id, "scope": scope})
    except (ContractError, KeyError, ValueError) as e:
        return json.dumps({"error": {"code": "INVALID_ENVELOPE", "message": str(e)[:500]}})
    except Exception as e:
        return json.dumps({"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:500]}})


@app.tool(timeout=60)
def test_load_context(run_id: str) -> str:
    """Load deployment.json, poc.manifest.json, poc_spec.md front matter, api_contract.yaml;
    store compact context in runs.outputs.context. Returns context dict or {"error": {...}}."""
    from poc_shared_tools import metadata as md, s3 as s3t
    import yaml
    try:
        run = md.get_run(run_id)
        poc_id = run["poc_id"]
        deployment_run_id = run["inputs"]["deployment_run_id"]

        # 1. deployment.json
        dep_key = f"pocs/{poc_id}/deploy/{deployment_run_id}/deployment.json"
        try:
            dep = validate("deployment", json.loads(s3t.get_text(dep_key)))
        except Exception as e:
            return json.dumps({"error": {"code": "DEPLOYMENT_NOT_FOUND", "message": str(e)[:500]}})

        code_version = dep["code_version"]

        # 2. poc.manifest.json
        try:
            manifest = json.loads(s3t.get_text(f"pocs/{poc_id}/code/{code_version}/poc.manifest.json"))
            spec_version = manifest["spec_version"]
            contract_key = manifest["contract_key"]
        except Exception as e:
            return json.dumps({"error": {"code": "SPEC_NOT_FOUND", "message": f"poc.manifest.json: {e}"}})

        # 3. poc_spec.md front matter
        try:
            spec_text = s3t.get_text(f"pocs/{poc_id}/spec/{spec_version}/poc_spec.md")
            parts = spec_text.split("---", 2)
            fm = yaml.safe_load(parts[1]) if len(parts) >= 3 else {}
            validate("poc_spec_frontmatter", fm)
        except Exception as e:
            return json.dumps({"error": {"code": "SPEC_NOT_FOUND", "message": f"poc_spec.md: {e}"}})

        # 4. api_contract.yaml
        try:
            contract = yaml.safe_load(s3t.get_text(contract_key))
        except Exception as e:
            return json.dumps({"error": {"code": "CONTRACT_NOT_FOUND", "message": f"api_contract.yaml: {e}"}})

        from agent_test_agent.pipeline import parse_operations
        operations = parse_operations(contract)

        ctx: dict[str, Any] = {
            "base_url": dep["urls"]["app"],
            "api_url": dep["urls"]["api"],
            "health_url": dep["urls"]["health"],
            "code_version": code_version,
            "spec_version": spec_version,
            "instance_id": dep["instance"]["instance_id"],
            "public_ip": dep["instance"]["public_ip"],
            "user_stories": fm.get("user_stories", []),
            "success_criteria": fm.get("success_criteria", []),
            "operations": operations,
        }
        md._db().runs.update_one({"run_id": run_id},
                                 {"$set": {"outputs.context": ctx, "updated_at": md.now()}})
        return json.dumps(ctx)
    except Exception as e:
        return json.dumps({"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:500]}})


@app.tool(timeout=60)
def test_generate_plan(run_id: str) -> str:
    """Build test_plan.json from context, write to S3, store in runs.outputs.test_plan.
    Returns test_plan dict or {"error": {...}}."""
    from poc_shared_tools import metadata as md, s3 as s3t
    from agent_test_agent.pipeline import generate_plan
    try:
        run = md.get_run(run_id)
        poc_id = run["poc_id"]
        ctx = run["outputs"]["context"]
        plan = generate_plan(ctx["user_stories"], ctx["success_criteria"], ctx["operations"])
        key = f"pocs/{poc_id}/test/{run_id}/test_plan.json"
        s3t.put_object(poc_id, run_id, key, json.dumps(plan, indent=2), "application/json", AGENT_NAME)
        md._db().runs.update_one({"run_id": run_id},
                                 {"$set": {"outputs.test_plan": plan, "outputs.test_plan_key": key,
                                           "updated_at": md.now()}})
        return json.dumps(plan)
    except Exception as e:
        return json.dumps({"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:500]}})


@app.tool(timeout=60)
def test_run_api_smoke(run_id: str) -> str:
    """Execute API smoke tests and p95 latency checks via HTTP inside the Tool Pod.
    Returns results list or {"error": {...}}."""
    from poc_shared_tools import metadata as md
    from agent_test_agent.pipeline import run_api_smoke_tests
    try:
        run = md.get_run(run_id)
        ctx = run["outputs"]["context"]
        plan = run["outputs"]["test_plan"]
        results = run_api_smoke_tests(ctx["api_url"], ctx["health_url"], plan)
        md._db().runs.update_one({"run_id": run_id},
                                 {"$set": {"outputs.api_results": results, "updated_at": md.now()}})
        return json.dumps(results)
    except Exception as e:
        return json.dumps({"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:500]}})


@app.tool(timeout=900)
def test_run_browser(run_id: str) -> str:
    """Run Playwright journeys on the POC's EC2 instance via SSM; parse results; upload artifacts.
    Returns browser results list or {"error": {...}}."""
    from poc_shared_tools import metadata as md
    from agent_test_agent.pipeline import run_browser_tests
    try:
        run = md.get_run(run_id)
        ctx = run["outputs"]["context"]
        plan = run["outputs"]["test_plan"]
        scope = run["inputs"].get("scope", "all")
        results = run_browser_tests(run["poc_id"], run_id, ctx, plan, scope)
        md._db().runs.update_one({"run_id": run_id},
                                 {"$set": {"outputs.browser_results": results, "updated_at": md.now()}})
        return json.dumps(results)
    except Exception as e:
        return json.dumps({"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:500]}})


@app.tool(timeout=60)
def test_write_report(run_id: str) -> str:
    """Assemble and validate test_report.json, write to S3, finish the run.
    Returns result dict (design decision 4) or {"error": {...}}."""
    from poc_shared_tools import metadata as md, s3 as s3t
    from agent_test_agent.pipeline import assemble_report
    t0 = time.perf_counter()
    try:
        run = md.get_run(run_id)
        poc_id = run["poc_id"]
        ctx = run["outputs"]["context"]
        plan = run["outputs"]["test_plan"]
        api_results = run["outputs"].get("api_results", [])
        browser_results = run["outputs"].get("browser_results", [])
        deployment_run_id = run["inputs"]["deployment_run_id"]

        report = assemble_report(poc_id, run_id, deployment_run_id, ctx, api_results, browser_results, plan)
        validate("test_report", report)

        report_key = f"pocs/{poc_id}/test/{run_id}/test_report.json"
        s3t.put_object(poc_id, run_id, report_key, json.dumps(report, indent=2), "application/json", AGENT_NAME)

        s = report["summary"]
        md._db().pocs.update_one(
            {"poc_id": poc_id},
            {"$set": {"last_test": {"run_id": run_id, "passed": s["passed"], "failed": s["failed"],
                                    "report_key": report_key},
                      "updated_at": md.now()}},
        )

        first_failure = next((r for r in report["results"] if r["status"] == "failed"), None)
        suspected_component = first_failure.get("suspected_component") if first_failure else None
        failed_ids = [r["id"] for r in report["results"] if r["status"] == "failed"]

        result = {
            "run_id": run_id,
            "passed": s["passed"],
            "failed": s["failed"],
            "skipped": s["skipped"],
            "not_automatable": s["not_automatable"],
            "failed_ids": failed_ids,
            "suspected_component": suspected_component,
            "report_key": report_key,
        }

        # Record the step before finishing the run so the terminal status is not reset to "running".
        md.update_run_step(run_id, "write_report", "succeeded", duration_ms=int((time.perf_counter() - t0) * 1000))
        md.finish_run(run_id, "succeeded", outputs=result)
        poc_status = "tested" if s["failed"] == 0 else "deployed"
        md.update_poc_status(poc_id, poc_status)

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:500]}})


# =============================================================================
# Graph
# =============================================================================

class _Opt(TypedDict, total=False):
    request: dict[str, Any]
    run_id: str
    poc_id: str
    scope: str
    start_time: float
    last_error: dict[str, Any]
    result: dict[str, Any]
    done: bool


class TestAgentState(_Opt):
    messages: Annotated[list[BaseMessage], add_messages]


def _last_human_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    logger.info("Building Test Agent graph...")
    tools = {t.name: t for t in app.get_tools()}

    def call(name: str, **kw: Any) -> Any:
        out = invoke_tool(tools[name], kw)
        return json.loads(out) if isinstance(out, str) else out

    def reply(state: TestAgentState, response: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [AIMessage(content=json.dumps(response))], "done": True, "result": response}

    def _timed_out(state: TestAgentState) -> bool:
        st = state.get("start_time")
        return st is not None and (time.time() - st) > TIMEOUT_S

    def _fail(state: TestAgentState, code: str, message: str) -> dict[str, Any]:
        req = state.get("request") or {}
        task_id = req.get("task_id") or new_id("task")
        run_id = state.get("run_id")
        if run_id:
            try:
                from poc_shared_tools import metadata as md
                md.finish_run(run_id, "failed", error={"code": code, "message": message})
            except Exception:
                pass
        return reply(state, Envelope.failed(task_id, code, message))

    def step(run_id: str | None, name: str, status: str, t0: float | None = None) -> None:
        """Mirror the pipeline onto runs.steps (like the Deploy Agent), best-effort."""
        if not run_id:
            return
        try:
            from poc_shared_tools import metadata as md
            kw: dict[str, Any] = {}
            if t0 is not None:
                kw["duration_ms"] = int((time.time() - t0) * 1000)
            md.update_run_step(run_id, name, status, **kw)
        except Exception:
            pass

    # --- nodes ---

    def parse_node(state: TestAgentState) -> dict[str, Any]:
        text = _last_human_text(state["messages"])
        try:
            env = validate("agent_envelope", json.loads(text))["request"]
        except Exception as e:
            return reply(state, Envelope.failed(new_id("task"), "INVALID_ENVELOPE",
                                                f"expected AgentEnvelope JSON: {str(e)[:300]}"))
        return {"request": env, "done": False, "start_time": time.time()}

    def start_node(state: TestAgentState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        if _timed_out(state):
            return _fail(state, "TIMEOUT", "exceeded 15-minute time limit at start")
        req = state["request"]
        t0 = time.time()
        r = call("test_start_run", envelope_json=json.dumps({"request": req}))
        if "error" in r:
            return reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"]))
        step(r["run_id"], "start", "succeeded", t0)
        return {"run_id": r["run_id"], "poc_id": r["poc_id"], "scope": r["scope"]}

    def load_node(state: TestAgentState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        if _timed_out(state):
            return _fail(state, "TIMEOUT", "exceeded 15-minute time limit at load")
        run_id = state["run_id"]
        t0 = time.time()
        step(run_id, "load_context", "running")
        r = call("test_load_context", run_id=run_id)
        if "error" in r:
            step(run_id, "load_context", "failed", t0)
            req = state["request"]
            return reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"]))
        step(run_id, "load_context", "succeeded", t0)
        return {}

    def plan_node(state: TestAgentState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        if _timed_out(state):
            return _fail(state, "TIMEOUT", "exceeded 15-minute time limit at plan")
        run_id = state["run_id"]
        t0 = time.time()
        step(run_id, "generate_plan", "running")
        r = call("test_generate_plan", run_id=run_id)
        if "error" in r:
            step(run_id, "generate_plan", "failed", t0)
            req = state["request"]
            return reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"]))
        step(run_id, "generate_plan", "succeeded", t0)
        return {}

    def smoke_node(state: TestAgentState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        if _timed_out(state):
            return _fail(state, "TIMEOUT", "exceeded 15-minute time limit at smoke")
        run_id = state["run_id"]
        t0 = time.time()
        step(run_id, "api_smoke", "running")
        r = call("test_run_api_smoke", run_id=run_id)
        if isinstance(r, dict) and "error" in r and not isinstance(r, list):
            step(run_id, "api_smoke", "failed", t0)
            req = state["request"]
            return reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"]))
        step(run_id, "api_smoke", "succeeded", t0)
        return {}

    def browser_node(state: TestAgentState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        if _timed_out(state):
            return _fail(state, "TIMEOUT", "exceeded 15-minute time limit at browser")
        run_id = state["run_id"]
        scope = state.get("scope", "all")
        if scope == "smoke":
            step(run_id, "browser", "skipped")
            return {}
        t0 = time.time()
        step(run_id, "browser", "running")
        r = call("test_run_browser", run_id=run_id)
        if isinstance(r, dict) and "error" in r and not isinstance(r, list):
            step(run_id, "browser", "failed", t0)
            req = state["request"]
            return reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"]))
        step(run_id, "browser", "succeeded", t0)
        return {}

    def report_node(state: TestAgentState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        if _timed_out(state):
            return _fail(state, "TIMEOUT", "exceeded 15-minute time limit at report")
        run_id = state["run_id"]
        t0 = time.time()
        step(run_id, "write_report", "running")
        r = call("test_write_report", run_id=run_id)
        if "error" in r:
            step(run_id, "write_report", "failed", t0)
            req = state["request"]
            return reply(state, Envelope.failed(req["task_id"], r["error"]["code"], r["error"]["message"]))
        # NB: the "write_report" succeeded step is recorded inside the tool *before* finish_run, so it
        # does not reset the run's terminal status here.
        # Success reply
        req = state["request"]
        report_key = r.get("report_key", "")
        arts = [Envelope.artifact("report", report_key)] if report_key else []
        return reply(state, Envelope.succeeded(req["task_id"], r, arts))

    # --- graph ---
    b = StateGraph(TestAgentState)
    for name, fn in [("parse", parse_node), ("start", start_node), ("load", load_node),
                     ("plan", plan_node), ("smoke", smoke_node), ("browser", browser_node),
                     ("report", report_node)]:
        b.add_node(name, fn)

    b.add_edge(START, "parse")
    b.add_conditional_edges("parse", lambda s: "end" if s.get("done") else "start",
                            {"start": "start", "end": END})
    b.add_conditional_edges("start",  lambda s: "end" if s.get("done") else "load",   {"load":   "load",   "end": END})
    b.add_conditional_edges("load",   lambda s: "end" if s.get("done") else "plan",   {"plan":   "plan",   "end": END})
    b.add_conditional_edges("plan",   lambda s: "end" if s.get("done") else "smoke",  {"smoke":  "smoke",  "end": END})
    b.add_conditional_edges("smoke",  lambda s: "end" if s.get("done") else "browser",{"browser":"browser","end": END})
    b.add_conditional_edges("browser",lambda s: "end" if s.get("done") else "report", {"report": "report", "end": END})
    b.add_edge("report", END)

    graph = b.compile(checkpointer=app.checkpointer())
    logger.info("✅ Test Agent graph compiled")
    return graph


def main():
    logger.info("=" * 60)
    logger.info(f"🚀 Starting {APP_NAME} (Runner SDK)")
    logger.info("=" * 60)
    app.run()


if __name__ == "__main__":
    main()
