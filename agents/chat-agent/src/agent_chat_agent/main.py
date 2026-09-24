"""
Chat Agent — POC Builder user-facing front door (Spec §6.1), built on the Agent Engine SDK.

The only user-facing agent: free text in, free text out. It owns the conversation, the human-in-the-loop
gates, run-status reporting, and mapping user intent to stage runs — it never generates specs or code
itself. It drives the specialists over A2A and reads/writes platform state through shared_tools.

Graph is the standard ReAct loop (agent node + tool loop + should_continue). Tools:
  Tool Pod  (is_local=False): chat_create_poc, chat_get_poc, chat_record_approval, chat_get_run_status,
                              chat_find_run, chat_list_artifacts, chat_read_artifact, chat_presign,
                              chat_append_message
  Agent Pod (is_local=True):  chat_call_draft, chat_start_code_run, chat_start_deploy_run,
                              chat_run_tests, chat_teardown  (call the specialists via A2A)

The LLM only ever calls these friendly tools; it never composes AgentEnvelope JSON.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Annotated, Any, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from magenta_sdklanggraph import App

from poc_contracts import Envelope, new_id
from agent_chat_agent.a2a import A2AClient, invoke_tool
from agent_chat_agent.llm import build_llm

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
logger = logging.getLogger(__name__)
load_dotenv()

APP_NAME = "Chat Agent"
AGENT_NAME = "chat_agent"
app = App(app_name=APP_NAME)
logger.info("✅ App created")


SYSTEM_PROMPT = """You are the Chat Agent of POC Builder — the only part of the system the user talks to.
You turn a meeting transcript into a running, tested proof-of-concept web app by driving specialist agents
through your tools. You never write specs or code yourself; you coordinate and report.

The pipeline and its human-in-the-loop GATES (follow exactly):
1. NEW POC. When the user pastes or uploads a transcript, call chat_create_poc(title, transcript_text) to
   create the POC and store the transcript. Derive a short title from the transcript. Then call
   chat_call_draft(poc_id) to draft the spec.
2. CLARIFICATION LOOP. If chat_call_draft returns status "needs_clarification", ask the user the questions
   VERBATIM (question text + the example suggestions), one message, and wait. When they answer, call
   chat_call_draft(poc_id, answers_json=<[{"question_id","answer"}] as JSON>). Do at most 3 rounds; if
   still incomplete after the 3rd, call chat_call_draft(poc_id, force_assumptions=true) and tell the user
   you proceeded with stated assumptions. When status is "succeeded" the spec is ready (note spec_version).
3. SHOW BEFORE APPROVE. Before recording ANY approval, show the user what they are approving: for a spec,
   read poc_spec.md with chat_read_artifact and give a short summary + the spec_version; for code, name the
   code_version. Only when the user clearly says go-ahead (e.g. "go ahead", "build it", "deploy it") do you
   record it. To approve a spec: chat_record_approval(poc_id, "spec_approved", spec_version). To approve
   code: chat_record_approval(poc_id, "code_approved", code_version, implicit=false). "Deploy without
   reviewing the code" means chat_record_approval(poc_id, "code_approved", code_version, implicit=true).
4. GATES ARE MANDATORY. Never start a stage whose approval is not yet recorded. A code run needs
   spec_approved for the current spec_version; a deploy run needs code_approved for the current
   code_version. If the gate is missing, say exactly what is missing and do not start the stage.
5. ONE RUN PER STAGE. Before starting a stage, call chat_find_run(poc_id, stage) and refuse to start a
   second run of the same stage while one is queued/running.
6. START STAGES. After spec_approved: chat_start_code_run(poc_id, spec_version). After code_approved:
   chat_start_deploy_run(poc_id, code_version, options_json) with options
   {"db_mode":"shared_db","run_tests":true,"ttl_hours":4} unless the user says otherwise. Re-run tests with
   chat_run_tests(poc_id, deployment_run_id). Tear down with chat_teardown(poc_id).
7. PROGRESS + ERRORS. Answer "how's it going?" from chat_get_run_status(run_id) (find the run first with
   chat_find_run). If a run failed, read the error and offer the next action (retry, tear down, or fix).
8. STATUS. code run done -> poc status code_ready; deploy+test done -> tested; teardown -> torn_down. Read
   chat_get_poc(poc_id) to confirm status/versions when unsure.
9. Keep a transcript of the conversation: after the user's message and again before your final reply, call
   chat_append_message(poc_id, role, content) for the user turn and your reply. Always finish a turn with a
   natural-language reply to the user — tool results are not shown to them.

What the user can say (examples):
- "Here is the meeting transcript: <text>"  -> create POC + draft
- "Answer q1-1: ...  Answer q1-2: ..."       -> re-draft with answers
- "Show me the spec"                          -> read + summarise poc_spec.md
- "Looks good, go ahead and build it"         -> record spec_approved, start code run
- "How's it going?"                           -> report run status
- "Deploy it, I don't need to review the code"-> record code_approved (implicit), start deploy run
- "Tear it down"                              -> teardown
"""


# =============================================================================
# Tool Pod tools (is_local=False) — platform DB + S3
# =============================================================================

@app.tool(is_local=False, timeout=60)
def chat_create_poc(title: str, transcript_text: str) -> str:
    """Create a new POC (pocs document), store the transcript under pocs/{poc_id}/input/, return {"poc_id"}."""
    from poc_shared_tools import metadata as md, s3 as s3t
    owner = app.get_current_user_id() or "u_local"
    poc = md.create_poc(title=title, owner_user_id=owner)
    poc_id = poc["poc_id"]
    run_id = new_id("run")
    key = f"pocs/{poc_id}/input/transcript.txt"
    r = s3t.put_object(poc_id, run_id, key, transcript_text or "", "text/plain", AGENT_NAME)
    meta = {"filename": "transcript.txt", "uploaded_by": owner, "uploaded_at": Envelope.now(),
            "sha256": r["sha256"], "bytes": r["size"]}
    s3t.put_object(poc_id, run_id, f"pocs/{poc_id}/input/transcript.meta.json",
                   json.dumps(meta, indent=2), "application/json", AGENT_NAME)
    return json.dumps({"poc_id": poc_id, "title": title, "transcript_key": key})


@app.tool(is_local=False, timeout=30)
def chat_get_poc(poc_id: str) -> str:
    """Return the POC document (status, current_versions, approvals) with DB secrets masked."""
    from poc_shared_tools import metadata as md
    poc = md.get_poc(poc_id)
    return json.dumps(_mask_secrets(poc), default=str)


@app.tool(is_local=False, timeout=30)
def chat_record_approval(poc_id: str, stage: str, version: str, implicit: bool = False) -> str:
    """Record a HITL gate: stage is 'spec_approved' or 'code_approved'; version is the vNNN being approved."""
    from poc_shared_tools import metadata as md
    owner = app.get_current_user_id() or "u_local"
    entry = md.record_approval(poc_id, stage, version, approved_by=owner, implicit=implicit)
    return json.dumps(entry, default=str)


@app.tool(is_local=False, timeout=30)
def chat_get_run_status(run_id: str) -> str:
    """Return {run_id, status, current_step, steps, error, outputs} for a run."""
    from poc_shared_tools import metadata as md
    return json.dumps(md.get_run_status(run_id), default=str)


@app.tool(is_local=False, timeout=30)
def chat_find_run(poc_id: str, stage: str) -> str:
    """Return the newest run of a stage (draft|code|deploy|test|teardown) for a POC, or {"error": ...}."""
    from poc_shared_tools import metadata as md
    try:
        doc = md._db().runs.find_one({"poc_id": poc_id, "stage": stage}, sort=[("started_at", -1)])
    except Exception as e:
        return json.dumps({"error": {"code": "DB_ERROR", "message": str(e)[:400]}})
    if not doc:
        return json.dumps({"error": {"code": "NO_RUN", "message": f"no {stage} run for {poc_id}"}})
    doc.pop("_id", None)
    return json.dumps({k: doc.get(k) for k in ("run_id", "poc_id", "stage", "status", "current_step",
                                               "error", "outputs", "started_at")}, default=str)


@app.tool(is_local=False, timeout=30)
def chat_list_artifacts(poc_id: str, prefix: str = "") -> str:
    """List S3 artifacts under pocs/{poc_id}/{prefix}. Returns [{key, size, last_modified}]."""
    from poc_shared_tools import s3 as s3t
    full = prefix if prefix.startswith("pocs/") else f"pocs/{poc_id}/{prefix.lstrip('/')}"
    return json.dumps(s3t.list_prefix(full), default=str)


@app.tool(is_local=False, timeout=30)
def chat_read_artifact(key: str) -> str:
    """Read an S3 text artifact (truncated to 6000 chars). Returns {"key", "text", "truncated"}."""
    from poc_shared_tools import s3 as s3t
    text = s3t.get_text(key)
    return json.dumps({"key": key, "text": text[:6000], "truncated": len(text) > 6000})


@app.tool(is_local=False, timeout=30)
def chat_presign(key: str) -> str:
    """Return a presigned GET url for an artifact: {"url"}."""
    from poc_shared_tools import s3 as s3t
    return json.dumps({"url": s3t.presign_get(key)})


@app.tool(is_local=False, timeout=30)
def chat_append_message(poc_id: str, role: str, content: str) -> str:
    """Append a message to the POC conversation history. role is user|assistant|tool."""
    from poc_shared_tools import metadata as md
    seq = md.append_message(poc_id, role, content)
    return json.dumps({"seq": seq})


# =============================================================================
# Agent Pod tools (is_local=True) — call the specialists over A2A
# =============================================================================

@app.tool(is_local=True, timeout=290)
def chat_call_draft(poc_id: str, answers_json: str = "", force_assumptions: bool = False) -> str:
    """Draft or refine the spec (sync). Returns the Draft Agent's response JSON
    (status succeeded with spec_version, or needs_clarification with questions, or failed)."""
    params: dict[str, Any] = {"poc_id": poc_id}
    if answers_json:
        try:
            params["answers"] = json.loads(answers_json)
        except json.JSONDecodeError:
            return json.dumps({"status": "failed", "error": {"code": "BAD_ANSWERS_JSON", "message": answers_json[:200]}})
    if force_assumptions:
        params["force_assumptions"] = True
    resp = _a2a("draft-spec", "draft_agent", "draft_spec", poc_id, params)
    return json.dumps(resp, default=str)


@app.tool(is_local=True, timeout=290)
def chat_start_code_run(poc_id: str, spec_version: str) -> str:
    """Start the Stage-2 code run (background). Returns {"status", "run_id"}."""
    return json.dumps(_a2a_run("code-orchestration", "coding_orchestrator", "start_code_run", poc_id,
                               {"poc_id": poc_id, "spec_version": spec_version}, "code"), default=str)


@app.tool(is_local=True, timeout=290)
def chat_start_deploy_run(poc_id: str, code_version: str, options_json: str = "") -> str:
    """Start the Stage-3/4 deploy run (background). options_json e.g.
    {"db_mode":"shared_db","run_tests":true,"ttl_hours":4}. Returns {"status", "run_id"}."""
    options: dict[str, Any] = {}
    if options_json:
        try:
            options = json.loads(options_json)
        except json.JSONDecodeError:
            return json.dumps({"status": "failed", "error": {"code": "BAD_OPTIONS_JSON", "message": options_json[:200]}})
    return json.dumps(_a2a_run("deploy-operations", "deploy_agent", "start_deploy_run", poc_id,
                               {"poc_id": poc_id, "code_version": code_version, "options": options}, "deploy"),
                      default=str)


@app.tool(is_local=True, timeout=290)
def chat_run_tests(poc_id: str, deployment_run_id: str = "") -> str:
    """Re-run the e2e tests on the live deployment (background). Returns {"status", "run_id"}."""
    params: dict[str, Any] = {"poc_id": poc_id}
    if deployment_run_id:
        params["deployment_run_id"] = deployment_run_id
    return json.dumps(_a2a_run("e2e-tests", "test_agent", "run_e2e", poc_id, params, "test"), default=str)


@app.tool(is_local=True, timeout=290)
def chat_teardown(poc_id: str) -> str:
    """Tear down the POC's cloud resources (background). Returns {"status", "run_id"}."""
    return json.dumps(_a2a_run("deploy-operations", "deploy_agent", "teardown_poc", poc_id,
                               {"poc_id": poc_id}, "teardown"), default=str)


# --- A2A helpers -------------------------------------------------------------

def _a2a(skill: str, agent_name: str, tool: str, poc_id: str, params: dict[str, Any],
         timeout_s: int = 285) -> dict[str, Any]:
    """Invoke a specialist over A2A; return the inner AgentEnvelope response dict."""
    env = Envelope.request(poc_id=poc_id, run_id=new_id("run"), caller="chat_agent", agent=agent_name,
                           tool=tool, params=params, task_id=new_id("task"))
    client = A2AClient(app)
    agent = client.find_agent(skill)
    resp = client.invoke(agent, env, timeout_s=timeout_s)
    return resp.get("response", resp) if isinstance(resp, dict) else {"status": "failed",
                                                                       "error": {"code": "A2A_BAD_REPLY", "message": str(resp)[:300]}}


def _a2a_run(skill: str, agent_name: str, tool: str, poc_id: str, params: dict[str, Any],
             stage: str) -> dict[str, Any]:
    """Start a background stage over A2A. On any A2A failure/timeout, fall back to the newest run of that
    stage (the callee writes its runs document before doing long work). Returns {"status", "run_id"}."""
    try:
        resp = _a2a(skill, agent_name, tool, poc_id, params)
        run_id = _extract_run_id(resp)
        if run_id:
            return {"status": resp.get("status", "started"), "run_id": run_id, "response": resp}
    except Exception as e:  # A2A discovery/timeout/transport
        logger.warning("A2A %s.%s failed, falling back to run lookup: %s", agent_name, tool, e)
        resp = {"status": "started", "error": {"code": "A2A_FALLBACK", "message": str(e)[:200]}}
    found = _newest_run(poc_id, stage)
    if found:
        return {"status": found.get("status", "started"), "run_id": found.get("run_id"), "response": resp}
    return {"status": resp.get("status", "started"), "run_id": None, "response": resp}


def _extract_run_id(resp: dict[str, Any]) -> str | None:
    if not isinstance(resp, dict):
        return None
    result = resp.get("result") or {}
    rid = result.get("run_id")
    return rid if isinstance(rid, str) else None


def _newest_run(poc_id: str, stage: str) -> dict[str, Any] | None:
    from poc_shared_tools import metadata as md
    try:
        doc = md._db().runs.find_one({"poc_id": poc_id, "stage": stage}, sort=[("started_at", -1)])
        if doc:
            doc.pop("_id", None)
        return doc
    except Exception:
        return None


def _mask_secrets(poc: dict[str, Any]) -> dict[str, Any]:
    db = poc.get("database")
    if isinstance(db, dict):
        db = dict(db)
        if db.get("connection_uri"):
            db["connection_uri"] = "***masked***"
        atlas = db.get("atlas")
        if isinstance(atlas, dict) and atlas.get("db_password"):
            atlas = dict(atlas)
            atlas["db_password"] = "***masked***"
            db["atlas"] = atlas
        poc = dict(poc)
        poc["database"] = db
    return poc


# =============================================================================
# Graph — ReAct loop (agent node + tool loop + should_continue)
# =============================================================================

class ChatState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def _memory_context(user_id: str, query: str) -> str:
    """Best-effort semantic recall; empty string when memory is not configured (§5.1)."""
    mem = getattr(app, "memory", None)
    if mem is None:
        return ""
    try:
        ctx = mem.build_context(query=query, user_id=user_id)
        text = getattr(ctx, "formatted_context", ctx)
        return text if isinstance(text, str) else ""
    except Exception:
        return ""


@app.entrypoint
def build_agent(llm: Any = None) -> CompiledStateGraph:
    logger.info("Building Chat Agent graph...")
    tool_objs = app.get_tools()
    tools_by_name = {t.name: t for t in tool_objs}
    model = llm if llm is not None else build_llm(temperature=0)
    model_with_tools = model.bind_tools(tool_objs) if tool_objs else model

    def agent_node(state: ChatState) -> dict[str, Any]:
        history = [m for m in state["messages"] if not isinstance(m, SystemMessage)]
        user_id = app.get_current_user_id() or "u_local"
        last_user = next((m.content for m in reversed(history) if isinstance(m, HumanMessage)), "")
        recalled = _memory_context(user_id, last_user if isinstance(last_user, str) else "")
        system = SYSTEM_PROMPT + (f"\n\n## Relevant memory\n{recalled}" if recalled else "")
        resp = model_with_tools.invoke([SystemMessage(content=system)] + history)
        return {"messages": [resp]}

    def should_continue(state: ChatState) -> str:
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else "end"

    def tool_node(state: ChatState) -> dict[str, Any]:
        """SDK-agnostic ToolNode: run each tool call in the last AI message, append a ToolMessage."""
        last = state["messages"][-1]
        out: list[BaseMessage] = []
        for tc in getattr(last, "tool_calls", []) or []:
            name = tc.get("name")
            args = tc.get("args", {}) or {}
            tool = tools_by_name.get(name)
            if tool is None:
                content = json.dumps({"error": {"code": "UNKNOWN_TOOL", "message": name}})
            else:
                try:
                    result = invoke_tool(tool, args)
                    content = result if isinstance(result, str) else json.dumps(result, default=str)
                except Exception as e:
                    content = json.dumps({"error": {"code": "TOOL_FAILED", "message": str(e)[:400]}})
            out.append(ToolMessage(content=content, tool_call_id=tc.get("id", new_id("task"))))
        return {"messages": out}

    b = StateGraph(ChatState)
    b.add_node("agent", agent_node)
    b.add_node("tools", tool_node)
    b.add_edge(START, "agent")
    b.add_conditional_edges("agent", should_continue, {"tools": "tools", "end": END})
    b.add_edge("tools", "agent")
    graph = b.compile(checkpointer=app.checkpointer())
    logger.info("✅ Chat Agent graph compiled")
    return graph


def main():
    logger.info("=" * 60)
    logger.info(f"🚀 Starting {APP_NAME} (Runner SDK)")
    logger.info("=" * 60)
    app.run()


if __name__ == "__main__":
    main()
