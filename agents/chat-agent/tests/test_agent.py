"""Chat Agent — tool behaviour (fake SDK, monkeypatched shared_tools) and a scripted ReAct graph run
(fake LLM emits the happy-path tool calls; fake A2A handlers return canned envelopes). No cloud, no LLM."""
import json

from langchain_core.messages import AIMessage, HumanMessage

from poc_contracts import new_id

import agent_chat_agent.main as m

POC = new_id("poc")


# =============================================================================
# A2A reply unwrapping (the OE wraps the callee's envelope under result/status)
# =============================================================================

def test_unwrap_envelope_handles_oe_wrapper():
    from agent_chat_agent.a2a import _unwrap_envelope
    inner = {"task_id": "task_x", "status": "succeeded", "result": {"spec_version": "v001"}}
    # OE shape: {"status":"completed","result":"<json string of {response: ...}>","error":null}
    wrapped = {"status": "completed", "result": json.dumps({"response": inner}), "error": None}
    assert _unwrap_envelope(wrapped)["response"] == inner
    # already-normalised shape passes through
    assert _unwrap_envelope({"response": inner})["response"] == inner
    # a bare response envelope gets wrapped
    assert _unwrap_envelope(inner)["response"] == inner
    # result as a dict carrying response
    assert _unwrap_envelope({"result": {"response": inner}})["response"] == inner


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
# draft start (top-level invoke, background) — start-ack, answers/force, retry
# =============================================================================

def _fake_draft_invoke(monkeypatch, *, run_id="run_DRAFT"):
    """Fake the draft top-level invoke: resolve a ws id, ack synchronously with a run_id embedded in the
    response envelope (so _extract_invoke_run_id finds it — no run-doc polling in the test). Returns a log
    of every invoke's (message, session_id)."""
    log: list[dict] = []

    def fake_iw(workspace_id, message, *, session_id, user_id, timeout_s=120):
        log.append({"workspace_id": workspace_id, "message": message, "session_id": session_id, "user_id": user_id})
        env = json.loads(message)
        envelope = {"response": {"task_id": env["request"]["task_id"], "status": "started",
                                 "result": {"run_id": run_id}}}
        return {"success": True, "response": json.dumps(envelope), "status": "completed"}

    monkeypatch.setattr(m.platform_invoke, "resolve_workspace_id", lambda skill: f"ws-{skill}")
    monkeypatch.setattr(m.platform_invoke, "invoke_workspace", fake_iw)
    return log


def test_chat_call_draft_starts_background_run_and_acks(monkeypatch):
    """chat_call_draft STARTS drafting as a top-level invoke of the draft workspace and returns started +
    run_id — it does not wait for the spec, and it never returns spec/questions itself."""
    log = _fake_draft_invoke(monkeypatch)
    out = json.loads(m.chat_call_draft(POC))
    assert out["status"] == "started" and out["run_id"] == "run_DRAFT"
    assert len(log) == 1
    env = json.loads(log[0]["message"])
    assert env["request"]["tool"] == "draft_spec" and env["request"]["agent"] == "draft_agent"
    assert env["request"]["params"] == {"poc_id": POC}
    assert log[0]["session_id"].startswith("draft-")  # its own root session, not the chat turn's
    assert log[0]["user_id"]  # required for durable identity


def test_chat_call_draft_passes_answers_and_force(monkeypatch):
    """Clarification answers + force_assumptions flow into the draft envelope params."""
    log = _fake_draft_invoke(monkeypatch)
    answers = [{"question_id": "q1-1", "answer": "p95 < 300ms"}]
    out = json.loads(m.chat_call_draft(POC, answers_json=json.dumps(answers), force_assumptions=True))
    assert out["status"] == "started"
    params = json.loads(log[0]["message"])["request"]["params"]
    assert params["answers"] == answers and params["force_assumptions"] is True


def test_chat_call_draft_bad_answers_json_fails_fast(monkeypatch):
    _fake_draft_invoke(monkeypatch)
    out = json.loads(m.chat_call_draft(POC, answers_json="{not json"))
    assert out["status"] == "failed" and out["error"]["code"] == "BAD_ANSWERS_JSON"


def test_chat_call_draft_retry_starts_a_fresh_run(monkeypatch):
    """A 'retry drafting' re-fires chat_call_draft, which starts a brand-new draft run (fresh session)."""
    log = _fake_draft_invoke(monkeypatch)
    a = json.loads(m.chat_call_draft(POC))
    b = json.loads(m.chat_call_draft(POC))
    assert a["status"] == "started" and b["status"] == "started"
    assert len(log) == 2
    assert log[0]["session_id"] != log[1]["session_id"]  # each retry is its own root session


# =============================================================================
# draft progress ("how's it going?") reads the draft run + POC
# =============================================================================

def test_chat_find_run_draft_surfaces_questions(monkeypatch):
    """A finished draft run that returned clarification questions carries them in outputs, so the agent can
    present them from chat_find_run(poc_id,'draft')."""
    import poc_shared_tools.metadata as md
    run_doc = {"_id": 1, "run_id": "run_D", "poc_id": POC, "stage": "draft", "status": "succeeded",
               "outputs": {"needs_clarification": True, "round": 1,
                           "questions": [{"question_id": "q1-1", "field": "success_criteria",
                                          "question": "What target?", "suggestions": ["p95 < 300ms"]}]},
               "started_at": "t"}

    class FakeRuns:
        def find_one(self, q, sort=None):
            assert q == {"poc_id": POC, "stage": "draft"}
            return dict(run_doc)

    class FakeDB:
        runs = FakeRuns()

    monkeypatch.setattr(md, "_db", lambda: FakeDB())
    out = json.loads(m.chat_find_run(POC, "draft"))
    assert out["stage"] == "draft" and out["status"] == "succeeded"
    assert out["outputs"]["needs_clarification"] is True
    assert out["outputs"]["questions"][0]["question_id"] == "q1-1"


def test_chat_status_spec_ready_reads_poc_and_spec(monkeypatch):
    """When drafting finished into a spec, the POC is spec_ready and poc_spec.md is readable to summarise."""
    import poc_shared_tools.metadata as md
    import poc_shared_tools.s3 as s3
    monkeypatch.setattr(md, "get_poc", lambda poc_id: {"poc_id": poc_id, "status": "spec_ready",
                                                       "current_versions": {"spec": "v001"}})
    monkeypatch.setattr(s3, "get_text", lambda key: "---\ntitle: t\n---\n\n# Executive summary\nThree views.\n")
    poc = json.loads(m.chat_get_poc(POC))
    assert poc["status"] == "spec_ready" and poc["current_versions"]["spec"] == "v001"
    art = json.loads(m.chat_read_artifact(f"pocs/{POC}/spec/v001/poc_spec.md"))
    assert "Executive summary" in art["text"]


# =============================================================================
# top-level invoke start path (HTTP faked): token exchange, URL, body
# =============================================================================

def _fake_http_transport(monkeypatch, *, token_status=200, invoke_run_id="run_INV"):
    """Fake platform_invoke._http; record every call; drive token -> workspaces -> invoke. Returns the log."""
    import agent_chat_agent.platform_invoke as pi
    pi._tokens.clear()
    pi._ws_map_cache.clear()
    monkeypatch.setenv("PROJECT_ID", "proj_TEST")
    monkeypatch.setenv("POC_PLATFORM_SA_CLIENT_ID", "cid_TEST")
    monkeypatch.setenv("POC_PLATFORM_SA_CLIENT_SECRET", "csecret_TEST")
    monkeypatch.delenv("POC_WORKSPACE_IDS", raising=False)
    monkeypatch.delenv("AGENTIC_PLATFORM_BASE_URL", raising=False)
    calls = []

    def fake_http(method, url, *, headers=None, data=None, timeout=30):
        calls.append({"method": method, "url": url, "headers": headers or {}, "data": data})
        if url.endswith("/api/v1/oauth/token"):
            if token_status != 200:
                return token_status, b'{"error":"invalid_client"}'
            return 200, json.dumps({"access_token": "tok-1", "token_type": "bearer", "expires_in": 3600}).encode()
        if url.endswith("/workspaces?limit=200"):
            return 200, json.dumps({"workspaces": [
                {"workspace_id": "ws-CODE", "name": "coding-orchestrator"},
                {"workspace_id": "ws-DEPLOY", "name": "deploy-agent"},
            ]}).encode()
        if "/invoke" in url:
            envelope = {"response": {"status": "started", "result": {"run_id": invoke_run_id}}}
            return 200, json.dumps({"success": True, "response": json.dumps(envelope), "status": "completed"}).encode()
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(pi, "_http", fake_http)
    return calls


def test_invoke_run_exchanges_token_resolves_ws_and_posts_envelope(monkeypatch):
    calls = _fake_http_transport(monkeypatch)
    out = m._invoke_run("code-orchestration", "coding_orchestrator", "start_code_run", POC,
                        {"poc_id": POC, "spec_version": "v001"}, "code")
    assert out["status"] == "started" and out["run_id"] == "run_INV"

    # token exchange: form-encoded client_credentials with our client id + secret
    tok = next(c for c in calls if c["url"].endswith("/oauth/token"))
    assert tok["method"] == "POST"
    form = dict(p.split("=", 1) for p in tok["data"].decode().split("&"))
    assert form["grant_type"] == "client_credentials"
    assert form["client_id"] == "cid_TEST" and form["client_secret"] == "csecret_TEST"

    # invoke URL carries the project id and the workspace id resolved by name from the workspaces API
    inv = next(c for c in calls if "/invoke" in c["url"])
    assert inv["url"] == "https://agentic-platform.mongodb.com/api/v1/projects/proj_TEST/workspaces/ws-CODE/invoke"
    assert inv["headers"]["Authorization"] == "Bearer tok-1"

    # body: the AgentEnvelope as `message`, plus session_id + user_id
    body = json.loads(inv["data"])
    assert set(("message", "session_id", "user_id")) <= set(body)
    env = json.loads(body["message"])
    assert env["request"]["tool"] == "start_code_run"
    assert env["request"]["agent"] == "coding_orchestrator"
    assert env["request"]["params"]["spec_version"] == "v001"
    assert body["session_id"].startswith("code-")  # a fresh, run-scoped session (not the chat turn's)
    assert body["user_id"]  # required for durable identity


def test_invoke_run_refreshes_token_on_401(monkeypatch):
    """A 401 on invoke forces one token refresh and a retry (then succeeds)."""
    import agent_chat_agent.platform_invoke as pi
    calls = _fake_http_transport(monkeypatch)
    state = {"invoke_calls": 0}
    real_http = pi._http

    def flaky_http(method, url, *, headers=None, data=None, timeout=30):
        if "/invoke" in url:
            state["invoke_calls"] += 1
            if state["invoke_calls"] == 1:
                calls.append({"method": method, "url": url, "headers": headers or {}, "data": data})
                return 401, b'{"error":"unauthorized"}'
        return real_http(method, url, headers=headers, data=data, timeout=timeout)

    monkeypatch.setattr(pi, "_http", flaky_http)
    out = m._invoke_run("deploy-operations", "deploy_agent", "start_deploy_run", POC,
                        {"poc_id": POC, "code_version": "v001", "options": {}}, "deploy")
    assert out["status"] == "started" and out["run_id"] == "run_INV"
    # two token exchanges (initial + forced refresh after 401) and two invoke attempts
    assert sum(1 for c in calls if c["url"].endswith("/oauth/token")) == 2
    assert state["invoke_calls"] == 2


def test_invoke_run_reports_failure_when_workspace_unresolved(monkeypatch):
    """If resolution fails, the start tool returns failed synchronously (it never claims the stage started)."""
    import agent_chat_agent.platform_invoke as pi
    monkeypatch.setattr(pi, "resolve_workspace_id", lambda skill: (_ for _ in ()).throw(RuntimeError("boom")))
    out = m._invoke_run("code-orchestration", "coding_orchestrator", "start_code_run", POC,
                        {"poc_id": POC, "spec_version": "v001"}, "code")
    assert out["status"] == "failed" and out["run_id"] is None
    assert out["error"]["code"] == "WORKSPACE_UNRESOLVED"


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

    # Callees ack synchronously with a run_id in these tests, so the run-doc poll fallback is only reached
    # when a start is (correctly) refused; keep that path fast.
    monkeypatch.setattr(m, "RUN_APPEAR_TIMEOUT_S", 0.2)
    monkeypatch.setattr(m, "RUN_APPEAR_POLL_S", 0)

    monkeypatch.setattr(md, "create_poc", lambda title, owner_user_id: {"poc_id": POC})
    monkeypatch.setattr(s3, "put_object",
                        lambda *a, **k: {"sha256": "x", "size": 1})
    monkeypatch.setattr(s3, "get_text", lambda key: "# Executive summary\nA recs POC.\n")
    monkeypatch.setattr(md, "record_approval",
                        lambda poc_id, stage, version, approved_by, implicit=False:
                        events.append(("approval", stage, version, implicit)) or
                        {"stage": stage, "version": version, "implicit": implicit})

    # Every stage — draft included — is started with a top-level platform invoke (its own root session). The
    # invoke's AgentEnvelope `message` names the tool, from which we derive the stage + the gate that must
    # already be recorded. We ack synchronously with a run_id embedded in the response so
    # _extract_invoke_run_id finds it (no run-document polling needed in the test).
    tool_stage_gate = {
        "draft_spec": ("draft", None),
        "start_code_run": ("code", "spec_approved"),
        "start_deploy_run": ("deploy", "code_approved"),
        "teardown_poc": ("teardown", None),
    }

    def fake_invoke_workspace(workspace_id, message, *, session_id, user_id, timeout_s=120):
        env = json.loads(message)
        stage, gate = tool_stage_gate[env["request"]["tool"]]
        if gate is not None:
            assert any(e[0] == "approval" and e[1] == gate for e in events), f"{stage} run started without {gate}!"
        events.append(("run", stage))
        envelope = {"response": {"task_id": env["request"]["task_id"], "status": "started",
                                 "result": {"run_id": new_id("run")}}}
        return {"success": True, "response": json.dumps(envelope), "status": "completed"}

    monkeypatch.setattr(m.platform_invoke, "resolve_workspace_id", lambda skill: f"ws-fake-{skill}")
    monkeypatch.setattr(m.platform_invoke, "invoke_workspace", fake_invoke_workspace)
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


def test_draft_start_records_no_gates_or_downstream_runs(monkeypatch):
    """Starting a draft only starts the draft run: it records no approvals and no code/deploy/teardown runs."""
    events = _install_fakes(monkeypatch)
    script = [
        _ai_tool("chat_create_poc", {"title": "Recs", "transcript_text": "t"}),
        _ai_tool("chat_call_draft", {"poc_id": POC}),
        AIMessage(content="Drafting started. Ask me 'how's it going?' for progress."),
    ]
    out = _run_graph(ScriptedLLM(script))
    assert events == [("run", "draft")]  # only the draft run; no approvals, no downstream stage
    assert "draft" in out["messages"][-1].content.lower()


def test_plain_reply_no_tools(monkeypatch):
    _install_fakes(monkeypatch)
    out = _run_graph(ScriptedLLM([AIMessage(content="Hello! Paste a transcript to begin.")]))
    assert out["messages"][-1].content.startswith("Hello")
