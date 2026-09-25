"""
Draft Agent — POC Builder Stage 1 (Spec §6.2), built on the Agent Engine SDK.

Message contract: every incoming message is an AgentEnvelope request (JSON, text/plain over A2A);
every reply is an AgentEnvelope response (JSON). Entry point / tool:
    draft_spec(poc_id, transcript_key?, answers?[{question_id, answer}], template_poc_id?, force_assumptions?)

A transcript becomes spec/v{NNN}/{poc_spec.md, schema_design.json, query_patterns.json, clarifications.json},
or the agent replies needs_clarification with <=5 questions.

The LLM generation + all S3 / platform-DB I/O run in the Tool Pod (draft_load / draft_analyze /
draft_questions / draft_generate / draft_finalize). The graph orchestrates:
    parse -> load -> analyze -> (questions | generate -> finalize) -> reply
and records run steps under stage "draft" like the other agents.

    RUNNER_MODE=aer   -> LangGraph execution (this graph)
    RUNNER_MODE=tool  -> Tool functions below
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Annotated, Any, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from agent_engine_sdk_langgraph import App

from poc_contracts import ContractError, Envelope, new_id, validate
from agent_draft_agent.a2a import invoke_tool

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
logger = logging.getLogger(__name__)
load_dotenv()

APP_NAME = "Draft Agent"
AGENT_NAME = "draft_agent"
app = App(app_name=APP_NAME)
logger.info("✅ App created")

KNOWN_TOOLS = {"draft_spec"}
PENDING_KEY = "pocs/{poc_id}/spec/pending/clarifications.json"


# =============================================================================
# Tools — LLM generation + S3 / platform-DB I/O run in the Tool Pod
# =============================================================================

@app.tool(timeout=120)
def draft_load(envelope_json: str) -> str:
    """Load the transcript from S3 and any pending clarification rounds; fold in params.answers.
    Returns {"transcript", "prior_rounds", "round_no"} or {"error": {...}} (INVALID_TRANSCRIPT)."""
    from poc_shared_tools import s3 as s3t
    from poc_shared_tools.errors import ToolError
    try:
        env = validate("agent_envelope", json.loads(envelope_json))["request"]
        poc_id = env["poc_id"]
        p = env["params"]
        transcript_key = p.get("transcript_key") or f"pocs/{poc_id}/input/transcript.txt"
        try:
            transcript = s3t.get_text(transcript_key)
        except ToolError as e:
            return json.dumps({"error": {"code": "INVALID_TRANSCRIPT", "message": f"{transcript_key}: {e}"[:400]}})
        if not transcript or not transcript.strip():
            return json.dumps({"error": {"code": "INVALID_TRANSCRIPT", "message": f"{transcript_key} is empty"}})

        rounds = _load_pending_rounds(s3t, poc_id)
        _fold_answers(rounds, p.get("answers") or [])
        round_no = len(rounds) + 1
        return json.dumps({"transcript": transcript, "prior_rounds": rounds, "round_no": round_no})
    except (ContractError, KeyError, ValueError) as e:
        return json.dumps({"error": {"code": "INVALID_ENVELOPE", "message": str(e)[:400]}})
    except ToolError as e:
        return json.dumps({"error": {"code": e.code, "message": str(e)[:800], "retryable": e.retryable}})
    except Exception as e:  # pragma: no cover - defensive
        return json.dumps({"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:400]}})


@app.tool(timeout=300)
def draft_analyze(envelope_json: str, transcript: str, prior_rounds_json: str) -> str:
    """Completeness analysis (LLM, JSON only). Returns {"extraction", "missing", "usage"} or {"error"}."""
    from agent_draft_agent import pipeline
    try:
        prior_rounds = json.loads(prior_rounds_json) if prior_rounds_json else []
        result, usage = pipeline.analyze(transcript, prior_rounds)
        return json.dumps({"extraction": result["extraction"], "missing": result["missing"], "usage": usage})
    except pipeline.LLMOutputInvalid as e:
        return json.dumps({"error": {"code": "LLM_OUTPUT_INVALID", "message": str(e)[:400], "detail": {"errors": e.errors}}})
    except Exception as e:  # pragma: no cover - defensive
        return json.dumps({"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:400]}})


@app.tool(timeout=300)
def draft_questions(envelope_json: str, extraction_json: str, missing_json: str, round_no: int,
                    prior_rounds_json: str) -> str:
    """Generate <=5 clarifying questions, persist them to the pending clarifications file.
    Returns {"questions", "usage"} or {"error"}."""
    from poc_shared_tools import s3 as s3t
    from poc_shared_tools.errors import ToolError
    from agent_draft_agent import pipeline
    try:
        env = validate("agent_envelope", json.loads(envelope_json))["request"]
        poc_id, run_id = env["poc_id"], env["run_id"]
        extraction = json.loads(extraction_json)
        missing = json.loads(missing_json)
        prior_rounds = json.loads(prior_rounds_json) if prior_rounds_json else []
        questions, usage = pipeline.generate_questions(extraction, missing, int(round_no))
        rounds = list(prior_rounds) + [{"round": int(round_no), "questions": questions}]
        s3t.put_object(poc_id, run_id, PENDING_KEY.format(poc_id=poc_id),
                       json.dumps({"rounds": rounds}, indent=2), "application/json", AGENT_NAME)
        return json.dumps({"questions": questions, "usage": usage})
    except (ContractError, KeyError, ValueError) as e:
        return json.dumps({"error": {"code": "INVALID_ENVELOPE", "message": str(e)[:400]}})
    except pipeline.LLMOutputInvalid as e:
        return json.dumps({"error": {"code": "LLM_OUTPUT_INVALID", "message": str(e)[:400], "detail": {"errors": e.errors}}})
    except ToolError as e:
        return json.dumps({"error": {"code": e.code, "message": str(e)[:800], "retryable": e.retryable}})
    except Exception as e:  # pragma: no cover - defensive
        return json.dumps({"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:400]}})


@app.tool(timeout=300)
def draft_generate(envelope_json: str, extraction_json: str, prior_rounds_json: str) -> str:
    """Allocate the next spec version, generate + validate the three artifacts, write all four spec files.
    Returns {"spec_version", "spec_key", "keys", "assumptions", "user_story_count", "poc_definitions", "usage"}
    or {"error"}."""
    from poc_shared_tools import s3 as s3t
    from poc_shared_tools.errors import ToolError
    from agent_draft_agent import pipeline
    try:
        env = validate("agent_envelope", json.loads(envelope_json))["request"]
        poc_id, run_id = env["poc_id"], env["run_id"]
        extraction = json.loads(extraction_json)
        prior_rounds = json.loads(prior_rounds_json) if prior_rounds_json else []

        spec_version = s3t.next_version(poc_id, "spec")
        result, usage = pipeline.generate(extraction, poc_id, spec_version)
        base = f"pocs/{poc_id}/spec/{spec_version}"
        files = result["files"]
        keys = {
            "spec": f"{base}/poc_spec.md",
            "schema": f"{base}/schema_design.json",
            "query_patterns": f"{base}/query_patterns.json",
            "clarifications": f"{base}/clarifications.json",
        }
        s3t.put_object(poc_id, run_id, keys["spec"], files["poc_spec.md"], "text/markdown", AGENT_NAME)
        s3t.put_object(poc_id, run_id, keys["schema"], files["schema_design.json"], "application/json", AGENT_NAME)
        s3t.put_object(poc_id, run_id, keys["query_patterns"], files["query_patterns.json"], "application/json", AGENT_NAME)
        s3t.put_object(poc_id, run_id, keys["clarifications"],
                       json.dumps({"rounds": prior_rounds}, indent=2), "application/json", AGENT_NAME)
        return json.dumps({
            "spec_version": spec_version, "spec_key": keys["spec"], "keys": keys,
            "assumptions": result["assumptions"], "user_story_count": result["user_story_count"],
            "poc_definitions": result["poc_definitions"], "usage": usage,
        })
    except (ContractError, KeyError, ValueError) as e:
        return json.dumps({"error": {"code": "INVALID_ENVELOPE", "message": str(e)[:400]}})
    except pipeline.LLMOutputInvalid as e:
        return json.dumps({"error": {"code": "LLM_OUTPUT_INVALID", "message": str(e)[:400], "detail": {"errors": e.errors}}})
    except ToolError as e:
        return json.dumps({"error": {"code": e.code, "message": str(e)[:800], "retryable": e.retryable}})
    except Exception as e:  # pragma: no cover - defensive
        return json.dumps({"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:400]}})


@app.tool(timeout=120)
def draft_finalize(envelope_json: str, spec_version: str, spec_key: str, poc_definitions_json: str) -> str:
    """Side effects (§6.2 step 5): set current spec version + status spec_ready; RAG-index the spec into
    spec_embeddings (best-effort); tombstone the pending clarifications file. Returns {"ok", "indexed"}."""
    from poc_shared_tools import metadata, s3 as s3t
    from poc_shared_tools.errors import ToolError
    from agent_draft_agent import pipeline, rag
    try:
        env = validate("agent_envelope", json.loads(envelope_json))["request"]
        poc_id, run_id = env["poc_id"], env["run_id"]

        metadata.set_current_version(poc_id, "spec", spec_version)
        metadata.update_poc_status(poc_id, "spec_ready")

        indexed = 0
        try:
            owner = metadata.get_poc(poc_id).get("owner_user_id", "u_local")
            spec_md = s3t.get_text(spec_key)
            chunks = pipeline.chunk_spec_by_section(spec_md)
            indexed = rag.upsert_spec_embeddings(poc_id, spec_version, chunks, owner)
        except Exception as e:  # RAG is best-effort (§5.5 fallback)
            logger.warning("spec RAG indexing skipped: %s", e)

        # shared_tools forbids deletes outside deploy/test; tombstone the pending file instead.
        try:
            s3t.put_object(poc_id, run_id, PENDING_KEY.format(poc_id=poc_id),
                           json.dumps({"resolved_into": spec_version}), "application/json", AGENT_NAME)
        except ToolError:
            pass
        return json.dumps({"ok": True, "indexed": indexed})
    except (ContractError, KeyError, ValueError) as e:
        return json.dumps({"error": {"code": "INVALID_ENVELOPE", "message": str(e)[:400]}})
    except ToolError as e:
        return json.dumps({"error": {"code": e.code, "message": str(e)[:800], "retryable": e.retryable}})
    except Exception as e:  # pragma: no cover - defensive
        return json.dumps({"error": {"code": getattr(e, "code", "RUNNER_FAILED"), "message": str(e)[:400]}})


# --- tool helpers ------------------------------------------------------------

def _load_pending_rounds(s3t: Any, poc_id: str) -> list[dict[str, Any]]:
    from poc_shared_tools.errors import ToolError
    try:
        raw = s3t.get_text(PENDING_KEY.format(poc_id=poc_id))
    except ToolError:
        return []
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if doc.get("resolved_into"):  # a previous draft already consumed this file
        return []
    return doc.get("rounds", []) or []


def _fold_answers(rounds: list[dict[str, Any]], answers: list[dict[str, Any]]) -> None:
    by_id = {a.get("question_id"): a.get("answer") for a in answers if a.get("question_id")}
    for rnd in rounds:
        for q in rnd.get("questions", []):
            if q.get("question_id") in by_id and by_id[q["question_id"]] is not None:
                q["answer"] = by_id[q["question_id"]]


# =============================================================================
# Graph
# =============================================================================

class _Opt(TypedDict, total=False):
    request: dict[str, Any]
    transcript: str
    prior_rounds: list[dict[str, Any]]
    round_no: int
    extraction: dict[str, Any]
    missing: list[str]
    spec_version: str
    spec_key: str
    poc_definitions: dict[str, Any]
    usage: dict[str, int]
    done: bool
    result: dict[str, Any]


class DraftState(_Opt):
    messages: Annotated[list[BaseMessage], add_messages]


def _last_human_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


def _add_usage(a: dict[str, int] | None, b: dict[str, int] | None) -> dict[str, int]:
    a = a or {}
    b = b or {}
    return {k: int(a.get(k, 0)) + int(b.get(k, 0)) for k in ("input_tokens", "output_tokens")}


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    logger.info("Building Draft Agent graph...")
    tools = {t.name: t for t in app.get_tools()}

    def call(name: str, **kw: Any) -> dict[str, Any]:
        out = invoke_tool(tools[name], kw)
        return json.loads(out) if isinstance(out, str) else out

    def reply(state: DraftState, response: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [AIMessage(content=json.dumps(response))], "done": True, "result": response}

    def _envelope_json(state: DraftState) -> str:
        return json.dumps({"request": state["request"]})

    # -- parse ---------------------------------------------------------------
    def parse_node(state: DraftState) -> dict[str, Any]:
        text = _last_human_text(state["messages"])
        try:
            env = validate("agent_envelope", json.loads(text))["request"]
        except Exception as e:
            return reply(state, Envelope.failed(new_id("task"), "INVALID_ENVELOPE", f"expected AgentEnvelope JSON: {str(e)[:300]}"))
        if env.get("tool") not in KNOWN_TOOLS:
            return reply(state, Envelope.failed(env["task_id"], "BAD_TOOL", env.get("tool", "")))
        return {"request": env, "done": False, "usage": {"input_tokens": 0, "output_tokens": 0}}

    # -- load ----------------------------------------------------------------
    def load_node(state: DraftState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        req = state["request"]
        r = call("draft_load", envelope_json=_envelope_json(state))
        if "error" in r:
            e = r["error"]
            return reply(state, Envelope.failed(req["task_id"], e["code"], e["message"], e.get("retryable", False)))
        return {"transcript": r["transcript"], "prior_rounds": r["prior_rounds"], "round_no": r["round_no"]}

    # -- analyze -------------------------------------------------------------
    def analyze_node(state: DraftState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        req = state["request"]
        r = call("draft_analyze", envelope_json=_envelope_json(state),
                 transcript=state["transcript"], prior_rounds_json=json.dumps(state["prior_rounds"]))
        if "error" in r:
            e = r["error"]
            return reply(state, Envelope.failed(req["task_id"], e["code"], e["message"], e.get("retryable", False),
                                                e.get("detail")))
        return {"extraction": r["extraction"], "missing": r["missing"],
                "usage": _add_usage(state.get("usage"), r.get("usage"))}

    # -- branch --------------------------------------------------------------
    def decide(state: DraftState) -> str:
        if state.get("done"):
            return "end"
        from agent_draft_agent.pipeline import MAX_CLARIFICATION_ROUNDS
        force = bool(state["request"]["params"].get("force_assumptions"))
        if state["missing"] and not force and state["round_no"] < MAX_CLARIFICATION_ROUNDS:
            return "questions"
        return "generate"

    # -- questions -----------------------------------------------------------
    def questions_node(state: DraftState) -> dict[str, Any]:
        req = state["request"]
        r = call("draft_questions", envelope_json=_envelope_json(state),
                 extraction_json=json.dumps(state["extraction"]), missing_json=json.dumps(state["missing"]),
                 round_no=state["round_no"], prior_rounds_json=json.dumps(state["prior_rounds"]))
        if "error" in r:
            e = r["error"]
            return reply(state, Envelope.failed(req["task_id"], e["code"], e["message"], e.get("retryable", False),
                                                e.get("detail")))
        usage = _add_usage(state.get("usage"), r.get("usage"))
        resp = Envelope.needs_clarification(req["task_id"], r["questions"])
        resp["response"]["usage"] = usage
        return {"messages": [AIMessage(content=json.dumps(resp))], "done": True, "result": resp}

    # -- generate ------------------------------------------------------------
    def generate_node(state: DraftState) -> dict[str, Any]:
        req = state["request"]
        r = call("draft_generate", envelope_json=_envelope_json(state),
                 extraction_json=json.dumps(state["extraction"]), prior_rounds_json=json.dumps(state["prior_rounds"]))
        if "error" in r:
            e = r["error"]
            return reply(state, Envelope.failed(req["task_id"], e["code"], e["message"], e.get("retryable", False),
                                                e.get("detail")))
        return {"spec_version": r["spec_version"], "spec_key": r["spec_key"],
                "poc_definitions": r["poc_definitions"],
                "usage": _add_usage(state.get("usage"), r.get("usage")),
                "result": {"_gen": r}}  # stash the full generate result for finalize

    # -- finalize ------------------------------------------------------------
    def finalize_node(state: DraftState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        req = state["request"]
        gen = state["result"]["_gen"]
        r = call("draft_finalize", envelope_json=_envelope_json(state), spec_version=state["spec_version"],
                 spec_key=state["spec_key"], poc_definitions_json=json.dumps(state["poc_definitions"]))
        if "error" in r:
            e = r["error"]
            return reply(state, Envelope.failed(req["task_id"], e["code"], e["message"], e.get("retryable", False)))
        keys = gen["keys"]
        version = state["spec_version"]
        artifacts = [
            Envelope.artifact("spec", keys["spec"], version),
            Envelope.artifact("schema", keys["schema"], version),
            Envelope.artifact("query_patterns", keys["query_patterns"], version),
            Envelope.artifact("clarifications", keys["clarifications"], version),
        ]
        result = {"spec_version": version, "assumptions": gen["assumptions"],
                  "user_story_count": gen["user_story_count"]}
        return reply(state, Envelope.succeeded(req["task_id"], result, artifacts, usage=state.get("usage") or None))

    b = StateGraph(DraftState)
    b.add_node("parse", parse_node)
    b.add_node("load", load_node)
    b.add_node("analyze", analyze_node)
    b.add_node("questions", questions_node)
    b.add_node("generate", generate_node)
    b.add_node("finalize", finalize_node)
    b.add_edge(START, "parse")
    b.add_conditional_edges("parse", lambda s: "end" if s.get("done") else "load", {"load": "load", "end": END})
    b.add_conditional_edges("load", lambda s: "end" if s.get("done") else "analyze", {"analyze": "analyze", "end": END})
    b.add_conditional_edges("analyze", decide, {"questions": "questions", "generate": "generate", "end": END})
    b.add_edge("questions", END)
    b.add_conditional_edges("generate", lambda s: "end" if s.get("done") else "finalize", {"finalize": "finalize", "end": END})
    b.add_edge("finalize", END)
    graph = b.compile(checkpointer=app.checkpointer())
    logger.info("✅ Draft Agent graph compiled")
    return graph


def main():
    logger.info("=" * 60)
    logger.info(f"🚀 Starting {APP_NAME} (Runner SDK)")
    logger.info("=" * 60)
    app.run()


if __name__ == "__main__":
    main()
