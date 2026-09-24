"""Chat Agent — tool behaviour (fake SDK, monkeypatched shared_tools) and a scripted ReAct graph run
(fake LLM emits the happy-path tool calls; fake A2A handlers return canned envelopes). No cloud, no LLM."""
import json

from langchain_core.messages import AIMessage, HumanMessage

from poc_contracts import new_id

import agent_chat_agent.main as m

POC = new_id("poc")


# =============================================================================
# tool behaviour
# =============================================================================

def test_chat_create_poc_writes_transcript_and_doc(monkeypatch):
    import poc_shared_tools.metadata as md
    import poc_shared_tools.s3 as s3
    puts = []
    monkeypatch.setattr(md, "create_poc", lambda title, owner_user_id: {"poc_id": POC, "title": title})
    monkeypatch.setattr(s3, "put_object",
                        lambda poc_id, run_id, key, body, ct, producer: puts.append((key, body)) or {"sha256": "abc", "size": len(body)})
    out = json.loads(m.chat_create_poc("My POC", "the transcript text"))
    assert out["poc_id"] == POC
    keys = [k for k, _ in puts]
    assert f"pocs/{POC}/input/transcript.txt" in keys
    assert f"pocs/{POC}/input/transcript.meta.json" in keys


def test_chat_record_approval_uses_current_user(monkeypatch):
    import poc_shared_tools.metadata as md
    seen = {}
    monkeypatch.setattr(md, "record_approval",
                        lambda poc_id, stage, version, approved_by, implicit=False: seen.update(
                            {"stage": stage, "version": version, "by": approved_by, "implicit": implicit}) or {"stage": stage})
    m.chat_record_approval(POC, "spec_approved", "v001")
    assert seen == {"stage": "spec_approved", "version": "v001", "by": "u_test", "implicit": False}


def test_chat_get_poc_masks_secrets(monkeypatch):
    import poc_shared_tools.metadata as md
    monkeypatch.setattr(md, "get_poc", lambda poc_id: {
        "poc_id": poc_id, "status": "deployed",
        "database": {"connection_uri": "mongodb+srv://u:pw@h/db", "atlas": {"db_password": "s3cr3t"}}})
    out = json.loads(m.chat_get_poc(POC))
    assert out["database"]["connection_uri"] == "***masked***"
    assert out["database"]["atlas"]["db_password"] == "***masked***"


def test_chat_find_run_returns_newest(monkeypatch):
    import poc_shared_tools.metadata as md

    class FakeRuns:
        def find_one(self, q, sort=None):
            assert q == {"poc_id": POC, "stage": "code"}
            return {"_id": 1, "run_id": "run_X", "poc_id": POC, "stage": "code", "status": "running"}

    class FakeDB:
        runs = FakeRuns()

    monkeypatch.setattr(md, "_db", lambda: FakeDB())
    out = json.loads(m.chat_find_run(POC, "code"))
    assert out["run_id"] == "run_X" and out["status"] == "running"


# =============================================================================
# scripted graph
# =============================================================================

class FakeResp(AIMessage):
    pass


class ScriptedLLM:
    """Returns pre-programmed AIMessages, one per invoke; ignores the prompt. bind_tools -> self."""
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.calls += 1
        if self.script:
            return self.script.pop(0)
        return AIMessage(content="All done.")


def _ai_tool(name, args):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": new_id("task"), "type": "tool_call"}])


def _run_graph(llm, user_text=" go "):
    graph = m.build_agent(llm=llm)
    out = graph.invoke({"messages": [HumanMessage(content=user_text)]},
                       config={"configurable": {"thread_id": "t"}})
    return out


def _install_fakes(monkeypatch):
    """Monkeypatch shared_tools + register fake A2A handlers; return the ordered event log + an invariant guard."""
    import poc_shared_tools.metadata as md
    import poc_shared_tools.s3 as s3
    events = []

    monkeypatch.setattr(md, "create_poc", lambda title, owner_user_id: {"poc_id": POC})
    monkeypatch.setattr(s3, "put_object",
                        lambda *a, **k: {"sha256": "x", "size": 1})
    monkeypatch.setattr(s3, "get_text", lambda key: "# Executive summary\nA recs POC.\n")
    monkeypatch.setattr(md, "record_approval",
                        lambda poc_id, stage, version, approved_by, implicit=False:
                        events.append(("approval", stage, version, implicit)) or
                        {"stage": stage, "version": version, "implicit": implicit})

    def draft_handler(env):
        return {"response": {"task_id": env["request"]["task_id"], "status": "succeeded",
                             "result": {"spec_version": "v001", "user_story_count": 5, "assumptions": ["a"]}}}

    def make_run_handler(stage, gate):
        def handler(env):
            approved = any(e[0] == "approval" and e[1] == gate for e in events)
            assert approved, f"{stage} run started without {gate}!"
            events.append(("run", stage))
            return {"response": {"task_id": env["request"]["task_id"], "status": "started",
                                 "result": {"run_id": new_id("run")}}}
        return handler

    m.app.a2a_handlers = {
        "draft-spec": draft_handler,
        "code-orchestration": make_run_handler("code", "spec_approved"),
        "deploy-operations": make_run_handler("deploy", "code_approved"),
    }
    return events


def test_happy_path_records_gates_before_runs(monkeypatch):
    events = _install_fakes(monkeypatch)
    script = [
        _ai_tool("chat_create_poc", {"title": "Recs", "transcript_text": "t"}),
        _ai_tool("chat_call_draft", {"poc_id": POC}),
        _ai_tool("chat_record_approval", {"poc_id": POC, "stage": "spec_approved", "version": "v001"}),
        _ai_tool("chat_start_code_run", {"poc_id": POC, "spec_version": "v001"}),
        _ai_tool("chat_record_approval", {"poc_id": POC, "stage": "code_approved", "version": "v001", "implicit": True}),
        _ai_tool("chat_start_deploy_run", {"poc_id": POC, "code_version": "v001",
                                           "options_json": json.dumps({"db_mode": "shared_db", "run_tests": True, "ttl_hours": 4})}),
        AIMessage(content="Deploy started; I'll report when it's tested."),
    ]
    _run_graph(ScriptedLLM(script))
    kinds = [(e[0], e[1]) for e in events]
    # gate recorded before its run, in order
    assert kinds.index(("approval", "spec_approved")) < kinds.index(("run", "code"))
    assert kinds.index(("approval", "code_approved")) < kinds.index(("run", "deploy"))
    # implicit code approval carried through
    assert any(e == ("approval", "code_approved", "v001", True) for e in events)


def test_no_run_starts_without_gate(monkeypatch):
    """If the LLM tries to start a code run before approval, the fake orchestrator's invariant trips."""
    events = _install_fakes(monkeypatch)
    script = [
        _ai_tool("chat_create_poc", {"title": "Recs", "transcript_text": "t"}),
        _ai_tool("chat_start_code_run", {"poc_id": POC, "spec_version": "v001"}),  # gate NOT recorded
        AIMessage(content="done"),
    ]
    _run_graph(ScriptedLLM(script))
    # the code-orchestration handler asserts spec_approved exists; the tool swallows the failure into an
    # error result, so no ("run","code") event was appended.
    assert not any(e[0] == "run" for e in events)


def test_clarification_path_asks_and_starts_nothing(monkeypatch):
    events = _install_fakes(monkeypatch)

    def draft_needs(env):
        return {"response": {"task_id": env["request"]["task_id"], "status": "needs_clarification",
                             "result": {"questions": [
                                 {"question_id": "q1-1", "field": "success_criteria", "question": "target?",
                                  "why_it_matters": "tests", "suggestions": ["300ms"]}]}}}

    m.app.a2a_handlers["draft-spec"] = draft_needs
    script = [
        _ai_tool("chat_create_poc", {"title": "Recs", "transcript_text": "t"}),
        _ai_tool("chat_call_draft", {"poc_id": POC}),
        AIMessage(content="I need one clarification: What is the target? e.g. 300ms"),
    ]
    out = _run_graph(ScriptedLLM(script))
    assert not events  # nothing approved, nothing started
    assert "clarification" in out["messages"][-1].content.lower() or "target" in out["messages"][-1].content.lower()


def test_plain_reply_no_tools(monkeypatch):
    _install_fakes(monkeypatch)
    out = _run_graph(ScriptedLLM([AIMessage(content="Hello! Paste a transcript to begin.")]))
    assert out["messages"][-1].content.startswith("Hello")
