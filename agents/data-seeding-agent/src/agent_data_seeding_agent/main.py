"""
Data Seeding Agent — POC Builder Stage 2 sub-agent (Spec §6.4), built on the Agent Engine SDK.

Message contract: every incoming message is an AgentEnvelope request (JSON, text/plain over A2A);
every reply is an AgentEnvelope response (JSON). Entry point / tool:
    generate_seed(poc_id, code_version, mode, inputs{schema_key, query_patterns_key}, failure?, previous_source_key?)

The one LLM generation runs in the Tool Pod (seed_execute); the graph is parse → generate → reply.
The agent loads its inputs from S3 itself and writes its component to S3 itself, then replies
`succeeded` with a single `code` artifact pointing at the component prefix.

    RUNNER_MODE=aer   -> LangGraph execution (this graph)
    RUNNER_MODE=tool  -> Tool functions below
"""
from __future__ import annotations

import json
import logging
import sys
import time
from typing import Annotated, Any, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from agent_engine_sdk_langgraph import App

from poc_contracts import ContractError, Envelope, new_id, validate
from agent_data_seeding_agent.a2a import invoke_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
logger = logging.getLogger(__name__)
load_dotenv()

APP_NAME = "Data Seeding Agent"
AGENT_NAME = "data_seeding_agent"
app = App(app_name=APP_NAME)
logger.info("✅ App created")

KNOWN_TOOLS = {"generate_seed"}


# =============================================================================
# Tools — the LLM generation + S3 I/O run in the Tool Pod
# =============================================================================

@app.tool(timeout=300)
def seed_execute(envelope_json: str) -> str:
    """Validate the generate_seed envelope, load inputs from S3, generate the seed component,
    guardrail-scan and upload it. Returns {"code_version", "component", "artifact_key", "usage"} or {"error": {...}}."""
    from poc_shared_tools import metadata as md, s3 as s3t
    from poc_shared_tools.errors import ToolError
    from agent_data_seeding_agent import pipeline
    task_id: str | None = None
    try:
        env = validate("agent_envelope", json.loads(envelope_json))["request"]
        task_id = env.get("task_id")
        if env["tool"] != "generate_seed":
            out = {"error": {"code": "BAD_TOOL", "message": env["tool"]}}
        else:
            poc_id, run_id = env["poc_id"], env["run_id"]
            p = env["params"]
            code_version = p["code_version"]
            mode = env.get("mode") or p.get("mode") or "code"
            ins = p.get("inputs", {})

            schema_design = json.loads(s3t.get_text(ins["schema_key"]))
            query_patterns = json.loads(s3t.get_text(ins["query_patterns_key"])) if ins.get("query_patterns_key") else None
            gen_inputs = {"schema_design": schema_design, "query_patterns": query_patterns}

            failure = p.get("failure")
            previous_source = None
            if mode == "repair" and p.get("previous_source_key"):
                previous_source = _load_prev(s3t, p["previous_source_key"])

            files, usage = pipeline.generate(gen_inputs, mode, failure=failure, previous_source=previous_source)
            written = pipeline.write_component(poc_id, run_id, code_version, files, AGENT_NAME)
            out = {"code_version": code_version, "component": pipeline.COMPONENT,
                   "artifact_key": written["prefix"], "usage": usage}
    except (ContractError, KeyError, ValueError) as e:
        out = {"error": {"code": "INVALID_ENVELOPE", "message": str(e)[:500]}}
    except pipeline.LLMOutputInvalid as e:
        out = {"error": {"code": "LLM_OUTPUT_INVALID", "message": str(e)[:500], "detail": {"errors": e.errors}}}
    except ToolError as e:
        out = {"error": {"code": e.code, "message": str(e)[:1500], "retryable": e.retryable}}
    except Exception as e:
        out = {"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:500]}}
    # Mark our own task in the platform DB (this is our own root session, started via a top-level invoke): the
    # orchestrator disconnected at the ~60s gateway cap and polls this task for the outcome.
    if "error" in out:
        md.mark_coder_task(task_id, "failed", error=out["error"], component="seed")
    else:
        md.mark_coder_task(task_id, "succeeded", output_ref=out.get("artifact_key"), token_usage=out.get("usage"),
                           component="seed")
    return json.dumps(out)


def _load_prev(s3t: Any, prefix: str) -> dict[str, str]:
    """Read a previously generated component into {relative_path: content} from S3."""
    out: dict[str, str] = {}
    base = prefix.rstrip("/") + "/"
    for obj in s3t.list_prefix(base):
        rel = obj["key"][len(base):]
        if rel:
            out[rel] = s3t.get_object(obj["key"])["body"].decode("utf-8", "ignore")
    return out


# =============================================================================
# Graph
# =============================================================================

class _Opt(TypedDict, total=False):
    request: dict[str, Any]
    done: bool
    result: dict[str, Any]


class SeedAgentState(_Opt):
    messages: Annotated[list[BaseMessage], add_messages]


def _last_human_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    logger.info("Building Data Seeding Agent graph...")
    tools = {t.name: t for t in app.get_tools()}

    def call(name: str, **kw: Any) -> dict[str, Any]:
        out = invoke_tool(tools[name], kw)
        return json.loads(out) if isinstance(out, str) else out

    def reply(state: SeedAgentState, response: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [AIMessage(content=json.dumps(response))], "done": True, "result": response}

    def parse_node(state: SeedAgentState) -> dict[str, Any]:
        text = _last_human_text(state["messages"])
        try:
            env = validate("agent_envelope", json.loads(text))["request"]
        except Exception as e:
            return reply(state, Envelope.failed(new_id("task"), "INVALID_ENVELOPE", f"expected AgentEnvelope JSON: {str(e)[:300]}"))
        if env.get("tool") not in KNOWN_TOOLS:
            return reply(state, Envelope.failed(env["task_id"], "BAD_TOOL", env.get("tool", "")))
        return {"request": env, "done": False}

    def generate_node(state: SeedAgentState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        req = state["request"]
        r = call("seed_execute", envelope_json=json.dumps({"request": req}))
        if "error" in r:
            e = r["error"]
            return reply(state, Envelope.failed(req["task_id"], e["code"], e["message"], e.get("retryable", False), e.get("detail")))
        art = Envelope.artifact("code", r["artifact_key"], r["code_version"])
        usage = r.get("usage")
        return reply(state, Envelope.succeeded(req["task_id"],
                                               {"component": r["component"], "code_version": r["code_version"]},
                                               [art], usage=usage or None))

    b = StateGraph(SeedAgentState)
    b.add_node("parse", parse_node)
    b.add_node("generate", generate_node)
    b.add_edge(START, "parse")
    b.add_conditional_edges("parse", lambda s: "end" if s.get("done") else "generate", {"generate": "generate", "end": END})
    b.add_edge("generate", END)
    graph = b.compile(checkpointer=app.checkpointer())
    logger.info("✅ Data Seeding Agent graph compiled")
    return graph


def main():
    logger.info("=" * 60)
    logger.info(f"🚀 Starting {APP_NAME} (Runner SDK)")
    logger.info("=" * 60)
    app.run()


if __name__ == "__main__":
    main()
