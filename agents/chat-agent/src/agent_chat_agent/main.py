"""
Chat Agent — POC Builder user-facing front door (Spec §6.1), built on the Agent Engine SDK.

The only user-facing agent: free text in, free text out. It owns the conversation, the human-in-the-loop
gates, run-status reporting, and mapping user intent to stage runs — it never generates specs or code
itself. Every stage (draft, code, deploy, test, teardown) is STARTED as a TOP-LEVEL platform invoke of the
specialist's workspace — its own root session that survives the chat turn ending and the ~60 s synchronous
gateway cap — and then polled through the specialist's runs document. No agent-to-agent (A2A) call remains
in the chat agent. It reads/writes platform state through shared_tools.

Graph is the standard ReAct loop (agent node + tool loop + should_continue). Tools:
  Tool Pod  (is_local=False): chat_create_poc, chat_get_poc, chat_record_approval, chat_get_run_status,
                              chat_find_run, chat_list_artifacts, chat_read_artifact, chat_presign,
                              chat_append_message
  Agent Pod (is_local=True):  chat_call_draft, chat_start_code_run, chat_start_deploy_run,
                              chat_run_tests, chat_teardown  (START a specialist via a top-level invoke)

The LLM only ever calls these friendly tools; it never composes AgentEnvelope JSON.
"""
from __future__ import annotations

import contextvars
import json
import logging
import sys
import threading
import time
from typing import Annotated, Any, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from agent_engine_sdk_langgraph import App

from poc_contracts import Envelope, new_id
from agent_chat_agent import platform_invoke
from agent_chat_agent.a2a import invoke_tool
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
   chat_call_draft(poc_id) to START drafting the spec. Drafting runs in the BACKGROUND (a rich transcript
   can take a few minutes): chat_call_draft returns {"status","run_id"} as soon as the draft run is
   registered — it does NOT wait for the spec. Reply with a short ack: say drafting has started, give the
   run_id, and invite the user to ask "how's it going?". NEVER present a spec or questions from
   chat_call_draft's own return value — it only starts the run.
2. DRAFT PROGRESS. When the user asks "how's it going?" (or "show me the spec" / "any questions?") while a
   draft is in flight, look at BOTH the draft run and the POC: call chat_find_run(poc_id,"draft"), then
   chat_get_run_status(run_id), and chat_get_poc(poc_id). Decide from that:
   - POC status is spec_ready  -> the spec is READY. Read poc_spec.md with chat_read_artifact and give a
     short summary + the spec_version (exactly as after a synchronous draft). Then follow SHOW BEFORE APPROVE.
   - draft run status succeeded AND its outputs.needs_clarification is true -> present the questions in
     outputs.questions to the user VERBATIM (question text + the example suggestions), in one message, and
     wait. This is the clarification flow.
   - draft run status is queued/running (and POC not yet spec_ready) -> tell the user it's still drafting and
     to check back shortly.
   - draft run status failed -> show the error and offer to retry ("retry drafting").
3. CLARIFICATION LOOP. When the user answers clarification questions, call
   chat_call_draft(poc_id, answers_json=<[{"question_id","answer"}] as JSON>) — this STARTS a new draft run
   the same way (ack + "how's it going?"). Do at most 3 rounds; if a draft still returns questions after the
   3rd, call chat_call_draft(poc_id, force_assumptions=true) and tell the user you proceeded with stated
   assumptions. "retry drafting" / "retry" re-fires chat_call_draft(poc_id) (add force_assumptions=true only
   if the user asks to proceed with assumptions).
4. SHOW BEFORE APPROVE. Before recording ANY approval, show the user what they are approving: for a spec,
   read poc_spec.md with chat_read_artifact and give a short summary + the spec_version; for code, name the
   code_version. Only when the user clearly says go-ahead (e.g. "go ahead", "build it", "deploy it") do you
   record it. To approve a spec: chat_record_approval(poc_id, "spec_approved", spec_version). To approve
   code: chat_record_approval(poc_id, "code_approved", code_version, implicit=false). "Deploy without
   reviewing the code" means chat_record_approval(poc_id, "code_approved", code_version, implicit=true).
5. GATES ARE MANDATORY. Never start a stage whose approval is not yet recorded. A code run needs
   spec_approved for the current spec_version; a deploy run needs code_approved for the current
   code_version. If the gate is missing, say exactly what is missing and do not start the stage.
6. ONE RUN PER STAGE. Before starting a stage, call chat_find_run(poc_id, stage) and refuse to start a
   second run of the same stage while one is queued/running.
7. START STAGES. After spec_approved: chat_start_code_run(poc_id, spec_version). After code_approved:
   chat_start_deploy_run(poc_id, code_version, options_json) with options
   {"db_mode":"shared_db","run_tests":true,"ttl_hours":4} unless the user says otherwise. Re-run tests with
   chat_run_tests(poc_id, deployment_run_id). Tear down with chat_teardown(poc_id). Like chat_call_draft,
   these start tools return as soon as the run is registered (status "started"/"running" + a run_id) — they
   do NOT wait for the stage to finish. As soon as you have the run_id, reply right away: tell them it
   started, give the run_id, and invite them to ask "how's it going?". Never imply a stage is done just
   because the start tool returned.
8. PROGRESS + ERRORS. Answer "how's it going?" for code/deploy/test/teardown from chat_get_run_status(run_id)
   (find the run first with chat_find_run). Runs finish in the background, so a stage may still be running or
   already terminal when asked. If a run failed, read the error and offer the next action (retry, tear down,
   or fix). For draft, follow rule 2.
9. STATUS. draft done -> poc status spec_ready; code run done -> code_ready; deploy+test done -> tested;
   teardown -> torn_down. Read chat_get_poc(poc_id) to confirm status/versions when unsure.
10. Keep a transcript of the conversation: after the user's message and again before your final reply, call
   chat_append_message(poc_id, role, content) for the user turn and your reply. Always finish a turn with a
   natural-language reply to the user — tool results are not shown to them.

What the user can say (examples):
- "Here is the meeting transcript: <text>"  -> create POC + START draft, ack with run_id
- "How's it going?" (while drafting)         -> read draft run + POC: spec ready? questions? still running?
- "Answer q1-1: ...  Answer q1-2: ..."       -> start a new draft run with answers
- "Retry drafting"                            -> re-fire chat_call_draft
- "Show me the spec"                          -> if spec_ready, read + summarise poc_spec.md
- "Looks good, go ahead and build it"         -> record spec_approved, start code run
- "How's it going?" (after a code/deploy run) -> report run status
- "Deploy it, I don't need to review the code"-> record code_approved (implicit), start deploy run
- "Tear it down"                              -> teardown
"""


# =============================================================================
# Tool Pod tools (is_local=False) — platform DB + S3
# =============================================================================

@app.tool(timeout=60)
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


@app.tool(timeout=30)
def chat_get_poc(poc_id: str) -> str:
    """Return the POC document (status, current_versions, approvals) with DB secrets masked."""
    from poc_shared_tools import metadata as md
    poc = md.get_poc(poc_id)
    return json.dumps(_mask_secrets(poc), default=str)


@app.tool(timeout=30)
def chat_record_approval(poc_id: str, stage: str, version: str, implicit: bool = False) -> str:
    """Record a HITL gate: stage is 'spec_approved' or 'code_approved'; version is the vNNN being approved."""
    from poc_shared_tools import metadata as md
    owner = app.get_current_user_id() or "u_local"
    entry = md.record_approval(poc_id, stage, version, approved_by=owner, implicit=implicit)
    return json.dumps(entry, default=str)


@app.tool(timeout=30)
def chat_get_run_status(run_id: str) -> str:
    """Return {run_id, status, current_step, steps, error, outputs} for a run."""
    from poc_shared_tools import metadata as md
    return json.dumps(md.get_run_status(run_id), default=str)


@app.tool(timeout=30)
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


@app.tool(timeout=30)
def chat_list_artifacts(poc_id: str, prefix: str = "") -> str:
    """List S3 artifacts under pocs/{poc_id}/{prefix}. Returns [{key, size, last_modified}]."""
    from poc_shared_tools import s3 as s3t
    full = prefix if prefix.startswith("pocs/") else f"pocs/{poc_id}/{prefix.lstrip('/')}"
    return json.dumps(s3t.list_prefix(full), default=str)


@app.tool(timeout=30)
def chat_read_artifact(key: str) -> str:
    """Read an S3 text artifact (truncated to 6000 chars). Returns {"key", "text", "truncated"}."""
    from poc_shared_tools import s3 as s3t
    text = s3t.get_text(key)
    return json.dumps({"key": key, "text": text[:6000], "truncated": len(text) > 6000})


@app.tool(timeout=30)
def chat_presign(key: str) -> str:
    """Return a presigned GET url for an artifact: {"url"}."""
    from poc_shared_tools import s3 as s3t
    return json.dumps({"url": s3t.presign_get(key)})


@app.tool(timeout=30)
def chat_append_message(poc_id: str, role: str, content: str) -> str:
    """Append a message to the POC conversation history. role is user|assistant|tool."""
    from poc_shared_tools import metadata as md
    seq = md.append_message(poc_id, role, content)
    return json.dumps({"seq": seq})


# =============================================================================
# Agent Pod tools (is_local=True) — START the specialists via a top-level platform invoke
# =============================================================================

@app.tool(timeout=290)
def chat_call_draft(poc_id: str, answers_json: str = "", force_assumptions: bool = False) -> str:
    """Start drafting/refining the spec in the BACKGROUND (the Draft Agent runs as its own root session and
    registers a draft run; a rich transcript can take a few minutes). Returns {"status","run_id"} as soon as
    the run is registered — it does NOT wait for the draft to finish. Report progress from the draft run +
    the POC via "how's it going?" (chat_find_run(poc_id,"draft") + chat_get_run_status + chat_get_poc).
    Pass answers_json to resume a clarification round; force_assumptions=true to draft despite gaps."""
    params: dict[str, Any] = {"poc_id": poc_id}
    if answers_json:
        try:
            params["answers"] = json.loads(answers_json)
        except json.JSONDecodeError:
            return json.dumps({"status": "failed", "error": {"code": "BAD_ANSWERS_JSON", "message": answers_json[:200]}})
    if force_assumptions:
        params["force_assumptions"] = True
    return json.dumps(_invoke_run("draft-spec", "draft_agent", "draft_spec", poc_id, params, "draft"), default=str)


@app.tool(timeout=290)
def chat_start_code_run(poc_id: str, spec_version: str) -> str:
    """Start the Stage-2 code run (background). Returns {"status", "run_id"}."""
    return json.dumps(_invoke_run("code-orchestration", "coding_orchestrator", "start_code_run", poc_id,
                                  {"poc_id": poc_id, "spec_version": spec_version}, "code"), default=str)


@app.tool(timeout=290)
def chat_start_deploy_run(poc_id: str, code_version: str, options_json: str = "") -> str:
    """Start the Stage-3/4 deploy run (background). options_json e.g.
    {"db_mode":"shared_db","run_tests":true,"ttl_hours":4}. Returns {"status", "run_id"}."""
    options: dict[str, Any] = {}
    if options_json:
        try:
            options = json.loads(options_json)
        except json.JSONDecodeError:
            return json.dumps({"status": "failed", "error": {"code": "BAD_OPTIONS_JSON", "message": options_json[:200]}})
    return json.dumps(_invoke_run("deploy-operations", "deploy_agent", "start_deploy_run", poc_id,
                                  {"poc_id": poc_id, "code_version": code_version, "options": options}, "deploy"),
                      default=str)


@app.tool(timeout=290)
def chat_run_tests(poc_id: str, deployment_run_id: str = "") -> str:
    """Re-run the e2e tests on the live deployment (background). Returns {"status", "run_id"}."""
    params: dict[str, Any] = {"poc_id": poc_id}
    if deployment_run_id:
        params["deployment_run_id"] = deployment_run_id
    return json.dumps(_invoke_run("e2e-tests", "test_agent", "run_e2e", poc_id, params, "test"), default=str)


@app.tool(timeout=290)
def chat_teardown(poc_id: str) -> str:
    """Tear down the POC's cloud resources (background). Returns {"status", "run_id"}."""
    return json.dumps(_invoke_run("deploy-operations", "deploy_agent", "teardown_poc", poc_id,
                                  {"poc_id": poc_id}, "teardown"), default=str)


# --- stage-start helpers -----------------------------------------------------

# A stage-start turn must return quickly. Every specialist (draft included) registers a runs document at
# graph start and keeps executing in its own root session after this call returns, so we wait only long
# enough to learn the run_id — a short ack window, then a brief poll for the run doc to appear — never for
# the whole stage. The user then polls progress with chat_get_run_status ("how's it going?"). We fire the
# HTTP invoke on a context-copied daemon thread; INVOKE_FIRE_TIMEOUT_S only bounds how long the daemon holds
# the connection (the run may take minutes and continues server-side after we disconnect), not the run.
RUN_APPEAR_TIMEOUT_S = 25
RUN_APPEAR_POLL_S = 2
INVOKE_ACK_TIMEOUT_S = 8
INVOKE_FIRE_TIMEOUT_S = 120


def _invoke_run(skill: str, agent_name: str, tool: str, poc_id: str, params: dict[str, Any],
                stage: str) -> dict[str, Any]:
    """Start a stage with a TOP-LEVEL platform invocation (a root session, independent of this chat turn),
    then return as soon as its run is registered — do NOT block for the stage to finish. Fire on a
    context-copied daemon thread, wait a short ack, then watch for the freshly-created runs document.
    Returns {"status", "run_id", "response"}.

    Every cross-agent start now goes through here (draft, code, deploy, teardown, tests): an A2A call is a
    child of this turn and the platform cancels it when the turn ends (capped at 300 s), and a synchronous
    turn hits the ~60 s gateway cap. A top-level invoke keeps running server-side after we disconnect, which
    is what a minutes-long stage — including a rich transcript's draft — needs."""
    env = Envelope.request(poc_id=poc_id, run_id=new_id("run"), caller="chat_agent", agent=agent_name,
                           tool=tool, params=params, task_id=new_id("task"))
    user_id = app.get_current_user_id() or "u_local"
    session_id = f"{stage}-{env['request']['run_id']}"  # a fresh session so the run is its own, not this turn's

    # Resolve the workspace id up front so a resolution/token error surfaces synchronously (before we claim
    # the stage started). This is a fast API lookup; the long-running invoke happens off-thread below.
    try:
        workspace_id = platform_invoke.resolve_workspace_id(skill)
    except Exception as e:
        logger.warning("invoke %s: workspace resolution failed: %s", stage, e)
        return {"status": "failed", "run_id": None,
                "error": {"code": "WORKSPACE_UNRESOLVED", "message": str(e)[:300]}}

    before = (_newest_run(poc_id, stage) or {}).get("run_id")
    box: dict[str, Any] = {}

    def _call() -> None:
        try:
            box["resp"] = platform_invoke.invoke_workspace(
                workspace_id, json.dumps(env), session_id=session_id, user_id=user_id,
                timeout_s=INVOKE_FIRE_TIMEOUT_S)
        except Exception as e:  # a short-timeout disconnect is expected for a long run; the run doc recovers it
            box["error"] = str(e)

    # Copy the current context so any scope/auth carried on contextvars is preserved off the main thread.
    ctx = contextvars.copy_context()
    th = threading.Thread(target=lambda: ctx.run(_call), name=f"invoke-{stage}", daemon=True)
    th.start()
    th.join(INVOKE_ACK_TIMEOUT_S)

    if "resp" in box:  # the specialist acked within the ack window — use its run_id if present
        run_id = _extract_invoke_run_id(box["resp"])
        if run_id:
            return {"status": "started", "run_id": run_id, "response": box["resp"]}
    if th.is_alive():
        logger.info("invoke %s.%s still running after %ss ack window; polling for the run document",
                    agent_name, tool, INVOKE_ACK_TIMEOUT_S)
    resp: dict[str, Any] = box.get("resp") or {
        "status": "started",
        "error": {"code": "INVOKE_ACK_TIMEOUT", "message": box.get("error", f"no ack within {INVOKE_ACK_TIMEOUT_S}s")},
    }
    found = _await_new_run(poc_id, stage, before)
    if found:
        return {"status": found.get("status", "started"), "run_id": found.get("run_id"), "response": resp}
    return {"status": resp.get("status", "started"), "run_id": None, "response": resp}


def _extract_invoke_run_id(resp: dict[str, Any]) -> str | None:
    """Pull a run_id out of a platform invoke response {success, response, ...} whose `response` carries the
    specialist's AgentEnvelope (possibly OE-wrapped). Returns None for the common case of a long run that has
    not replied yet (we then poll the runs document instead)."""
    if not isinstance(resp, dict):
        return None
    from agent_chat_agent.a2a import _unwrap_envelope
    inner = resp.get("response")
    if isinstance(inner, str):
        try:
            inner = json.loads(inner)
        except json.JSONDecodeError:
            return None
    if not isinstance(inner, dict):
        return None
    envelope = _unwrap_envelope(inner).get("response", {})
    rid = (envelope.get("result") or {}).get("run_id") if isinstance(envelope, dict) else None
    return rid if isinstance(rid, str) else None


def _await_new_run(poc_id: str, stage: str, before_run_id: str | None) -> dict[str, Any] | None:
    """Poll for a runs document of this stage that is newer than before_run_id (the durable callee writes it
    at graph start). Returns the run doc, or None if none appeared within RUN_APPEAR_TIMEOUT_S."""
    deadline = time.time() + RUN_APPEAR_TIMEOUT_S
    while time.time() < deadline:
        doc = _newest_run(poc_id, stage)
        if doc and doc.get("run_id") != before_run_id:
            return doc
        time.sleep(RUN_APPEAR_POLL_S)
    doc = _newest_run(poc_id, stage)
    return doc if doc and doc.get("run_id") != before_run_id else None


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
