"""Coding Orchestrator — plan logic (pure) and graph (fake SDK + faked platform_invoke coders) tests.
No LLM, no cloud, no platform DB. Coders are STARTED as top-level platform invokes and then POLLED (the ~60s
synchronous-invoke gateway cap 504s while the coder runs on), so the graph tests fake
poc_shared_tools.platform_invoke.start_invoke and drive the coder's task document (which the coder marks in
its root session) through FakeMD; the transport itself (token exchange, 401 -> refresh + retry) is covered
end to end by faking platform_invoke._http. Coverage includes a coder that 504s then completes, and one that
never completes (-> CODER_TIMEOUT)."""
import contextlib
import json

import pytest
from langchain_core.messages import HumanMessage

from poc_contracts import Envelope, new_id, validate

import agent_coding_orchestrator.main as m
from agent_coding_orchestrator import pipeline

POC, TASK = new_id("poc"), new_id("task")

FAILURE = {"poc_id": POC, "deploy_run_id": new_id("run"), "code_version": "v001", "component": "frontend",
           "stage_step": "build_frontend", "failure_class": "BUILD_ERROR", "attempt": 1, "max_attempts": 3}
FAILURE_CONTRACT = dict(FAILURE, component="backend", failure_class="CONTRACT_MISMATCH")


# --- pure plan logic ---------------------------------------------------------

def test_plan_fresh_runs_all_four_in_order():
    plan = pipeline.build_plan({"tool": "start_code_run", "poc_id": POC, "spec_version": "v001", "code_version": "v001"})
    assert [s["step"] for s in plan["steps"]] == ["contract", "seed", "backend", "frontend"]
    assert plan["changed_components"] == ["seed", "backend", "frontend"]
    assert plan["repairs_of"] is None
    backend = next(s for s in plan["steps"] if s["step"] == "backend")
    assert backend["inputs"]["contract_key"].endswith("/code/v001/api_contract.yaml")


def test_plan_repair_single_component():
    plan = pipeline.build_plan({"tool": "repair_component", "poc_id": POC, "spec_version": "v001",
                                "code_version": "v002", "prev_version": "v001", "failure": FAILURE})
    assert [s["step"] for s in plan["steps"]] == ["frontend"]
    assert plan["steps"][0]["mode"] == "repair"
    assert plan["steps"][0]["previous_source_key"].endswith("/code/v001/frontend/")
    assert plan["changed_components"] == ["frontend"]
    assert plan["copy_components"] == ["seed", "backend"]
    assert plan["copy_contract"] is True
    assert plan["repairs_of"] == "v001"


def test_plan_repair_contract_mismatch_rebuilds_three():
    plan = pipeline.build_plan({"tool": "repair_component", "poc_id": POC, "spec_version": "v001",
                                "code_version": "v002", "prev_version": "v001", "failure": FAILURE_CONTRACT})
    assert [s["step"] for s in plan["steps"]] == ["contract", "backend", "frontend"]
    assert [s["mode"] for s in plan["steps"]] == ["contract", "code", "code"]
    assert plan["changed_components"] == ["backend", "frontend"]
    assert plan["copy_components"] == ["seed"]
    assert plan["copy_contract"] is False


def test_build_poc_manifest_validates():
    mani = pipeline.build_poc_manifest(
        poc_id=POC, code_version="v002", spec_version="v001", stack=m.STACK,
        contract_key=f"pocs/{POC}/code/v002/api_contract.yaml", bundle_key=f"pocs/{POC}/code/v002/bundle.tar.gz",
        bundle_sha256="a" * 64, scanned_at="2026-09-24T12:00:00Z", violations=[], run_id=new_id("run"),
        changed_components=["frontend"], repairs_of="v001")
    validate("poc_manifest", mani)
    assert mani["produced_by"]["repairs_of"] == "v001"


# --- graph with a FakeMD + fake S3 + faked A2A coders ------------------------

class FakeMD:
    def __init__(self, approvals):
        self.runs, self.pocs = {}, {POC: {"poc_id": POC, "approvals": approvals, "status": "spec_ready", "current_versions": {}}}
        self.tasks: dict = {}          # task_id -> task doc (coders mark these; the orchestrator polls them)
        self.completion: dict = {}     # task_id -> {"after": n, "status", "output_ref", "error"} for delayed completes
        self._polls: dict = {}         # task_id -> poll count, to resolve a scheduled delayed completion
        self.touches: dict = {}        # run_id -> heartbeat touches

    def now(self):
        return "2026-09-24T12:00:00Z"

    def check_gate(self, poc_id, stage, version):
        return any(a["stage"] == "spec_approved" and a["version"] == version for a in self.pocs[poc_id]["approvals"])

    def create_run(self, poc_id, stage, requested_by, started_by_agent, inputs=None, trace_id=None):
        r = {"run_id": new_id("run"), "poc_id": poc_id, "stage": stage, "status": "queued", "steps": [],
             "inputs": inputs or {}, "outputs": {}}
        self.runs[r["run_id"]] = r
        return r

    def get_run(self, run_id):
        return json.loads(json.dumps(self.runs[run_id]))

    def update_poc_status(self, poc_id, status, **f):
        self.pocs[poc_id]["status"] = status
        _apply_set(self.pocs[poc_id], f)

    def set_current_version(self, poc_id, kind, version):
        self.pocs[poc_id].setdefault("current_versions", {})[kind] = version

    def update_run_step(self, run_id, name, status, output_ref=None, log_key=None, duration_ms=None):
        r = self.runs[run_id]
        r["status"] = "running"
        for s in r["steps"]:
            if s["name"] == name:
                s["status"] = status
                return
        r["steps"].append({"name": name, "status": status})

    def create_task(self, run_id, poc_id, agent, tool, mode=None, input_ref=None, task_id=None, component=None):
        tid = task_id or new_id("task")
        seq = sum(1 for t in self.tasks.values() if t.get("run_id") == run_id) + 1
        self.tasks[tid] = {"task_id": tid, "run_id": run_id, "poc_id": poc_id, "agent": agent, "seq": seq,
                           "tool": tool, "mode": mode, "status": "running", "started_at": _real_now()}
        if component:
            self.tasks[tid]["component"] = component
        return dict(self.tasks[tid])

    def list_tasks(self, run_id):
        return sorted((json.loads(json.dumps(t)) for t in self.tasks.values() if t.get("run_id") == run_id),
                      key=lambda t: t.get("seq", 0))

    def touch_run(self, run_id):
        self.touches[run_id] = self.touches.get(run_id, 0) + 1

    def heartbeat(self, run_id, every_s=30):
        self.touch_run(run_id)
        return contextlib.nullcontext()

    def reopen_run(self, run_id, reason, execution=None):
        r = self.runs[run_id]
        if r["status"] in ("succeeded", "cancelled"):
            from poc_shared_tools.errors import ToolError
            raise ToolError("RUN_NOT_RESUMABLE", run_id)
        r["status"] = "running"
        r.pop("error", None)
        r["executions"] = r.get("executions", 0) + 1
        r.setdefault("continuations", []).append({"reason": reason, "execution": execution})
        return dict(r)

    def finish_task(self, task_id, status, output_ref=None, duration_ms=None, token_usage=None, error=None,
                    component=None):
        t = self.tasks.setdefault(task_id, {"task_id": task_id, "status": "running"})
        t["status"] = status
        if component:
            t["component"] = component
        if output_ref is not None:
            t["output_ref"] = output_ref
        if token_usage is not None:
            t["token_usage"] = token_usage
        if error is not None:
            t["error"] = error

    def mark_coder_task(self, task_id, status, output_ref=None, token_usage=None, error=None, component=None):
        if task_id:
            self.finish_task(task_id, status, output_ref=output_ref, token_usage=token_usage, error=error,
                             component=component)

    def get_task(self, task_id):
        sched = self.completion.get(task_id)  # a coder that finishes only after `after` polls (504-then-completes)
        if sched:
            self._polls[task_id] = self._polls.get(task_id, 0) + 1
            if self._polls[task_id] >= sched["after"]:
                self.finish_task(task_id, sched["status"], output_ref=sched.get("output_ref"), error=sched.get("error"))
                self.completion.pop(task_id)
        if task_id not in self.tasks:
            from poc_shared_tools.errors import ToolError
            raise ToolError("TASK_NOT_FOUND", f"no task {task_id}")
        return json.loads(json.dumps(self.tasks[task_id]))

    def finish_run(self, run_id, status, outputs=None, error=None):
        r = self.runs[run_id]
        r["status"] = status
        if outputs:
            r["outputs"].update(outputs)
        if error:
            r["error"] = error
        return r

    def _db(self):
        md = self

        class Runs:
            def update_one(self, q, u):
                _apply_set(md.runs[q["run_id"]], u["$set"])

        class DB:
            runs = Runs()
        return DB()


def _real_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _apply_set(doc, sets):
    for k, v in sets.items():
        if k == "updated_at":
            continue
        parts = k.split(".")
        cur = doc
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = v


@pytest.fixture
def fake_md(monkeypatch):
    md = FakeMD([{"stage": "spec_approved", "version": "v001"}])
    import poc_shared_tools.metadata as real
    for name in dir(md):
        if not name.startswith("__") and hasattr(real, name):
            monkeypatch.setattr(real, name, getattr(md, name))
    monkeypatch.setattr(real, "_db", md._db)
    return md


@pytest.fixture
def fake_s3(monkeypatch):
    import poc_shared_tools.s3 as s3
    state = {"next": "v001"}
    monkeypatch.setattr(s3, "next_version", lambda poc_id, kind: state["next"])
    monkeypatch.setattr(s3, "get_text", lambda key: json.dumps(
        {"spec_version": "v001", "components": ["seed", "backend", "frontend"]}))
    monkeypatch.setattr(s3, "copy_object", lambda src, dst: None)
    monkeypatch.setattr(s3, "list_prefix", lambda base, max_keys=1000: [{"key": base + "file.txt"}])
    monkeypatch.setattr(s3, "download_dir", lambda base, tmp: [])
    monkeypatch.setattr(s3, "make_bundle", lambda poc_id, run_id, cv, tmp, producer: {
        "bundle_key": f"pocs/{poc_id}/code/{cv}/bundle.tar.gz", "bundle_sha256": "a" * 64, "size": 10})
    monkeypatch.setattr(s3, "put_object", lambda *a, **k: {"key": a[2]})
    return state


def _key_for(env: dict) -> str:
    """The artifact key a coder writes + marks on its task's output_ref (deterministic S3 prefix)."""
    req = env["request"]
    cv = req["params"]["code_version"]
    if req.get("mode") == "contract":
        return f"pocs/{POC}/code/{cv}/api_contract.yaml"
    comp = {"generate_seed": "seed", "generate_frontend": "frontend", "generate_api": "backend"}[req["tool"]]
    return f"pocs/{POC}/code/{cv}/{comp}/"


def _coder_handlers(calls, md):
    """Fire-and-poll: a fired coder marks ITS OWN task done in the platform DB (here, FakeMD) from its root
    session; the orchestrator then polls that task. Each handler records the call and marks the task."""
    def make(tool):
        def handler(env):
            req = env["request"]
            calls.append((tool, req.get("mode")))
            md.finish_task(req["task_id"], "succeeded", output_ref=_key_for(env))
        return handler
    return {"generate-api": make("generate_api"), "generate-seed": make("generate_seed"),
            "generate-frontend": make("generate_frontend")}


def _fake_coder_start(monkeypatch, handlers: dict):
    """Fake platform_invoke.start_invoke(skill, env, ...): run the matching handler (which marks the task,
    like a real coder in its root session) and return {"status": "started"} (a 504/timeout disconnect)."""
    from poc_shared_tools import platform_invoke as pi

    def fake_start(skill, envelope, *, user_id, session_id, client_timeout_s=25):
        if skill in handlers:
            handlers[skill](envelope)
        return {"status": "started"}

    monkeypatch.setattr(pi, "start_invoke", fake_start)


def _fast_poll(monkeypatch):
    monkeypatch.setattr(m, "CODER_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(m, "CODER_POLL_CEILING_S", 2.0)


def _invoke(tool: str, params: dict, handlers: dict | None = None, monkeypatch=None):
    if monkeypatch is not None:
        _fake_coder_start(monkeypatch, handlers or {})
        _fast_poll(monkeypatch)
    graph = m.build_agent()
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="chat_agent",
                           agent="coding_orchestrator", tool=tool, params=params, task_id=TASK)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                       config={"configurable": {"thread_id": "t"}})
    return validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]


def test_happy_path_builds_three_components(fake_md, fake_s3, monkeypatch):
    calls = []
    resp = _invoke("start_code_run", {"poc_id": POC, "spec_version": "v001"}, _coder_handlers(calls, fake_md), monkeypatch)
    assert resp["status"] == "succeeded", resp
    assert resp["result"]["code_version"] == "v001"
    assert resp["result"]["changed_components"] == ["seed", "backend", "frontend"]
    assert [a["kind"] for a in resp["artifacts"]] == ["bundle", "contract"]
    assert calls == [("generate_api", "contract"), ("generate_seed", "code"),
                     ("generate_api", "code"), ("generate_frontend", "code")]
    run = next(r for r in fake_md.runs.values() if r["stage"] == "code")
    assert run["status"] == "succeeded"
    assert fake_md.pocs[POC]["current_versions"]["code"] == "v001"


def test_repair_regenerates_only_failed_component_and_bumps_version(fake_md, fake_s3, monkeypatch):
    fake_s3["next"] = "v002"
    calls = []
    resp = _invoke("repair_component",
                   {"poc_id": POC, "code_version": "v001", "component": "frontend", "failure": FAILURE},
                   _coder_handlers(calls, fake_md), monkeypatch)
    assert resp["status"] == "succeeded", resp
    assert resp["result"]["code_version"] == "v002"
    assert resp["result"]["changed_components"] == ["frontend"]
    assert calls == [("generate_frontend", "repair")]


def test_coder_returns_504_then_completes(fake_md, fake_s3, monkeypatch):
    """Fire-and-poll: the coder's synchronous invoke 504s at the ~60s cap (start returns "started"), and the
    coder marks its task done only after the orchestrator has begun polling — the run still succeeds."""
    _fast_poll(monkeypatch)
    from poc_shared_tools import platform_invoke as pi

    def start(skill, envelope, *, user_id, session_id, client_timeout_s=25):
        req = envelope["request"]
        # 504/disconnect: the coder keeps running server-side and marks its task done only after 3 polls.
        fake_md.completion[req["task_id"]] = {"after": 3, "status": "succeeded", "output_ref": _key_for(envelope)}
        return {"status": "started", "http": 504}

    monkeypatch.setattr(pi, "start_invoke", start)
    graph = m.build_agent()
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="chat_agent",
                           agent="coding_orchestrator", tool="start_code_run",
                           params={"poc_id": POC, "spec_version": "v001"}, task_id=TASK)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                       config={"configurable": {"thread_id": "t504"}})
    resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]
    assert resp["status"] == "succeeded", resp
    assert resp["result"]["changed_components"] == ["seed", "backend", "frontend"]
    assert fake_md.pocs[POC]["current_versions"]["code"] == "v001"


def test_coder_never_completes_fails_run_with_clear_report(fake_md, fake_s3, monkeypatch):
    """A coder that is started but never marks its task done makes the run fail with a clear CODER_TIMEOUT
    report once the per-coder poll ceiling elapses (no infinite wait)."""
    monkeypatch.setattr(m, "CODER_POLL_INTERVAL_S", 0.02)
    monkeypatch.setattr(m, "CODER_POLL_CEILING_S", 0.1)
    from poc_shared_tools import platform_invoke as pi
    monkeypatch.setattr(pi, "start_invoke", lambda skill, envelope, **kw: {"status": "started"})
    graph = m.build_agent()
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="chat_agent",
                           agent="coding_orchestrator", tool="start_code_run",
                           params={"poc_id": POC, "spec_version": "v001"}, task_id=TASK)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                       config={"configurable": {"thread_id": "tto"}})
    resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]
    assert resp["status"] == "failed", resp
    assert resp["error"]["code"] == "CODER_TIMEOUT"
    run = next(r for r in fake_md.runs.values() if r["stage"] == "code")
    assert run["status"] == "failed"
    assert run["error"]["code"] == "CODER_TIMEOUT"
    assert fake_md.pocs[POC]["status"] == "code_failed"


def test_gate_refused(fake_s3, monkeypatch):
    md = FakeMD([])  # no approvals
    import poc_shared_tools.metadata as real
    for name in dir(md):
        if not name.startswith("__") and hasattr(real, name):
            monkeypatch.setattr(real, name, getattr(md, name))
    monkeypatch.setattr(real, "_db", md._db)
    resp = _invoke("start_code_run", {"poc_id": POC, "spec_version": "v009"}, _coder_handlers([], md), monkeypatch)
    assert resp["status"] == "failed" and resp["error"]["code"] == "GATE_NOT_APPROVED"


def test_bad_tool():
    resp = _invoke("nonexistent", {"poc_id": POC})
    assert resp["status"] == "failed" and resp["error"]["code"] == "BAD_TOOL"


def test_invalid_envelope():
    graph = m.build_agent()
    out = graph.invoke({"messages": [HumanMessage(content="not json")]}, config={"configurable": {"thread_id": "t2"}})
    resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]
    assert resp["status"] == "failed" and resp["error"]["code"] == "INVALID_ENVELOPE"


# =============================================================================
# Coder starts go through the shared platform_invoke transport (start_invoke ->
# start_workspace_invoke); a 401 on the invoke POST forces exactly one token
# refresh + retry (the ~5-min OE-token expiry, now on a root session so a fresh
# token is minted, no longer blocks the last coder). The coder marks its task,
# which the orchestrator then polls.
# =============================================================================

def _fake_platform_http(monkeypatch, state):
    """Fake poc_shared_tools.platform_invoke._http end to end: token -> workspaces -> invoke. The invoke
    branch returns a coder `succeeded` envelope built from the request, and 401s on the FIRST invoke to
    exercise the token-refresh-and-retry path. `state` records call counts."""
    from poc_shared_tools import platform_invoke as pi
    pi._tokens.clear()
    pi._ws_map_cache.clear()
    monkeypatch.setenv("PROJECT_ID", "proj_TEST")
    monkeypatch.setenv("POC_PLATFORM_SA_CLIENT_ID", "cid_TEST")
    monkeypatch.setenv("POC_PLATFORM_SA_CLIENT_SECRET", "csecret_TEST")
    monkeypatch.delenv("POC_WORKSPACE_IDS", raising=False)
    monkeypatch.delenv("AGENTIC_PLATFORM_BASE_URL", raising=False)

    def fake_http(method, url, *, headers=None, data=None, timeout=30):
        if url.endswith("/oauth/token"):
            state["token"] += 1
            return 200, json.dumps({"access_token": f"tok-{state['token']}", "expires_in": 3600}).encode()
        if url.endswith("/workspaces?limit=200"):
            return 200, json.dumps({"workspaces": [
                {"workspace_id": "ws-api", "name": "api-agent"},
                {"workspace_id": "ws-seed", "name": "data-seeding-agent"},
                {"workspace_id": "ws-fe", "name": "frontend-agent"},
            ]}).encode()
        if "/invoke" in url:
            state["invoke"] += 1
            if state["invoke"] == 1:  # first coder call: expired token -> 401 -> one refresh + retry
                return 401, b'{"error":"unauthorized"}'
            req = json.loads(json.loads(data)["message"])["request"]
            cv = req["params"]["code_version"]
            key = f"pocs/{POC}/code/{cv}/{req.get('mode', 'code')}/"
            # A real coder marks its own task done in its root session; simulate that here so the
            # orchestrator's poll of the task sees the outcome (start_invoke fired this request).
            import poc_shared_tools.metadata as _md
            _md.finish_task(req["task_id"], "succeeded", output_ref=key)
            inner = Envelope.succeeded(req["task_id"], {"ok": True}, [Envelope.artifact("code", key, cv)])
            return 200, json.dumps({"success": True, "response": json.dumps(inner), "status": "completed"}).encode()
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(pi, "_http", fake_http)


def test_coders_go_through_platform_invoke_and_refresh_token_on_401(fake_md, fake_s3, monkeypatch):
    state = {"token": 0, "invoke": 0}
    _fake_platform_http(monkeypatch, state)
    graph = m.build_agent()
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="chat_agent",
                           agent="coding_orchestrator", tool="start_code_run",
                           params={"poc_id": POC, "spec_version": "v001"}, task_id=TASK)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                       config={"configurable": {"thread_id": "t401"}})
    resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]
    assert resp["status"] == "succeeded", resp
    # 4 coder steps (contract, seed, backend, frontend); the first 401s and retries → 5 invoke attempts.
    assert state["invoke"] == 5
    # exactly two token exchanges: the initial mint + one forced refresh after the 401 (not once per coder).
    assert state["token"] == 2


# =============================================================================
# Resilient runs: continue_run (resume at first step not done), single-call
# bounded waits, self-continuation (hand-over) and the stable task component key.
# =============================================================================

def _env(tool: str, params: dict, mode: str | None = None) -> dict:
    return Envelope.request(poc_id=POC, run_id=new_id("run"), caller="chat_agent", agent="coding_orchestrator",
                            tool=tool, params=params, task_id=TASK, mode=mode)


def _run_graph(env: dict, thread: str = "t") -> dict:
    out = m.build_agent().invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                                 config={"configurable": {"thread_id": thread}})
    return validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]


def _abandoned_run(md, done_steps, *, record_steps=None, extra_tasks=()):
    """A code run killed mid-way: tasks for `done_steps` succeeded (legacy docs: no `component`), run steps
    recorded only for `record_steps`, status still running with a plan already stored."""
    plan = pipeline.build_plan({"tool": "start_code_run", "poc_id": POC, "spec_version": "v001", "code_version": "v001"})
    run = md.create_run(POC, "code", "chat_agent", "coding_orchestrator", {"spec_version": "v001"})
    rid = run["run_id"]
    md.runs[rid].update(status="running", outputs={
        "code_version": "v001", "spec_version": "v001", "plan": plan,
        "ctx": {"tool": "start_code_run", "poc_id": POC, "spec_version": "v001", "code_version": "v001"}})
    by_step = {s["step"]: s for s in plan["steps"]}
    for name in done_steps:
        st = by_step[name]
        t = md.create_task(rid, POC, st["agent"], st["tool"], st["mode"])
        key = f"pocs/{POC}/code/v001/api_contract.yaml" if name == "contract" else f"pocs/{POC}/code/v001/{name}/"
        md.finish_task(t["task_id"], "succeeded", output_ref=key)
        if name in (record_steps if record_steps is not None else done_steps):
            md.update_run_step(rid, f"{name}:done", "succeeded")
            if name == "contract":
                md.runs[rid]["outputs"]["contract_key"] = key
            else:
                md.runs[rid]["outputs"].setdefault("component_keys", {})[name] = key
    for name, status in extra_tasks:
        st = by_step[name]
        t = md.create_task(rid, POC, st["agent"], st["tool"], st["mode"], component=name)
        if status != "running":
            md.finish_task(t["task_id"], status)
    md.pocs[POC]["status"] = "coding"
    return rid


def test_continue_all_coders_done_runs_only_assemble_and_finalize(fake_md, fake_s3, monkeypatch):
    """Today's abandoned run: all four coder tasks succeeded but frontend:done was never recorded. continue_run
    fires NO coder, records the missing frontend step + key, assembles, finalizes → code_ready v001."""
    rid = _abandoned_run(fake_md, ["contract", "seed", "backend", "frontend"],
                         record_steps=["contract", "seed", "backend"])
    fired = []
    _fake_coder_start(monkeypatch, {k: (lambda env, k=k: fired.append(k)) for k in ("generate-api", "generate-seed", "generate-frontend")})
    _fast_poll(monkeypatch)
    resp = _run_graph(_env("continue_run", {"run_id": rid}, mode="continue"))
    assert resp["status"] == "succeeded", resp
    assert fired == []
    run = fake_md.runs[rid]
    assert run["status"] == "succeeded" and run["executions"] == 1
    assert run["outputs"]["component_keys"]["frontend"] == f"pocs/{POC}/code/v001/frontend/"
    assert {"name": "frontend:done", "status": "succeeded"} in run["steps"]
    assert fake_md.pocs[POC]["status"] == "code_ready"
    assert fake_md.pocs[POC]["current_versions"]["code"] == "v001"


def test_continue_resumes_from_first_step_not_done(fake_md, fake_s3, monkeypatch):
    """contract + seed done, backend failed: continue fires backend + frontend only, same code_version."""
    rid = _abandoned_run(fake_md, ["contract", "seed"], extra_tasks=[("backend", "failed")])
    calls = []
    _fake_coder_start(monkeypatch, _coder_handlers(calls, fake_md))
    _fast_poll(monkeypatch)
    resp = _run_graph(_env("continue_run", {"run_id": rid}, mode="continue"))
    assert resp["status"] == "succeeded", resp
    assert calls == [("generate_api", "code"), ("generate_frontend", "code")]
    assert resp["result"]["code_version"] == "v001"


def test_continue_reattaches_to_in_flight_coder_without_refiring(fake_md, fake_s3, monkeypatch):
    rid = _abandoned_run(fake_md, ["contract", "seed", "backend"], extra_tasks=[("frontend", "running")])
    inflight = next(t for t in fake_md.tasks.values() if t.get("component") == "frontend")
    fake_md.completion[inflight["task_id"]] = {"after": 2, "status": "succeeded", "output_ref": f"pocs/{POC}/code/v001/frontend/"}
    fired = []
    _fake_coder_start(monkeypatch, {"generate-frontend": lambda env: fired.append(env)})
    _fast_poll(monkeypatch)
    resp = _run_graph(_env("continue_run", {"run_id": rid}, mode="continue"))
    assert resp["status"] == "succeeded", resp
    assert fired == []


def test_continue_is_idempotent_on_a_succeeded_run(fake_md, fake_s3, monkeypatch):
    rid = _abandoned_run(fake_md, ["contract", "seed", "backend", "frontend"])
    fake_md.runs[rid]["status"] = "succeeded"
    resp = _run_graph(_env("continue_run", {"run_id": rid}, mode="continue"))
    assert resp["status"] == "succeeded" and resp["result"]["already"] == "succeeded"
    assert "executions" not in fake_md.runs[rid]


def test_self_handover_carries_the_run_across_executions(fake_md, fake_s3, monkeypatch):
    """Tool-call budget exceeded at a step boundary → the execution fires continue_run on ITSELF (skill
    code-orchestration) and replies `started`; driving each handed-over envelope finishes the run normally."""
    monkeypatch.setattr(m, "HANDOVER_TOOL_CALLS", 6)
    calls, handovers = [], []
    handlers = _coder_handlers(calls, fake_md)
    handlers["code-orchestration"] = lambda env: handovers.append(env)
    _fake_coder_start(monkeypatch, handlers)
    _fast_poll(monkeypatch)
    resp = _run_graph(_env("start_code_run", {"poc_id": POC, "spec_version": "v001"}), "h0")
    executions = 1
    while resp["status"] == "started":
        env = handovers.pop()
        assert env["request"]["tool"] == "continue_run" and env["request"]["mode"] == "continue"
        resp = _run_graph(env, f"h{executions}")
        executions += 1
    assert resp["status"] == "succeeded", resp
    assert executions >= 2
    assert calls == [("generate_api", "contract"), ("generate_seed", "code"),
                     ("generate_api", "code"), ("generate_frontend", "code")]  # each coder fired exactly once
    run = next(r for r in fake_md.runs.values() if r["stage"] == "code")
    assert run["status"] == "succeeded" and run["executions"] == executions - 1


def test_elapsed_budget_triggers_handover(monkeypatch):
    monkeypatch.setattr(m, "HANDOVER_AFTER_S", 360)
    monkeypatch.setattr(m, "HANDOVER_TOOL_CALLS", 25)
    assert not m.over_budget({"exec_started": 1000.0, "tool_calls": 10, "calls_base": 0}, now=1300.0)
    assert m.over_budget({"exec_started": 1000.0, "tool_calls": 10, "calls_base": 0}, now=1361.0)
    assert m.over_budget({"exec_started": 1000.0, "tool_calls": 26, "calls_base": 0}, now=1001.0)
    assert not m.over_budget({"exec_started": 1000.0, "tool_calls": 30, "calls_base": 10}, now=1001.0)


def test_wait_task_is_one_bounded_call_that_reports_still_running(fake_md, monkeypatch):
    monkeypatch.setattr(m, "CODER_POLL_INTERVAL_S", 0.05)
    run = fake_md.create_run(POC, "code", "chat_agent", "coding_orchestrator", {})
    t = fake_md.create_task(run["run_id"], POC, "frontend_agent", "generate_frontend", "code", component="frontend")
    out = json.loads(m.orch_wait_task(t["task_id"], run["run_id"], 1))
    assert out["status"] == "running"
    assert fake_md.touches[run["run_id"]] >= 1  # heartbeated while waiting
    fake_md.finish_task(t["task_id"], "succeeded", output_ref="k/")
    out = json.loads(m.orch_wait_task(t["task_id"], run["run_id"], 1))
    assert out["status"] == "succeeded" and out["output_ref"] == "k/"


def test_tasks_carry_a_distinct_stable_component_key(fake_md, fake_s3, monkeypatch):
    """contract and backend are both api_agent/generate_api; tasks.component tells them apart."""
    _invoke("start_code_run", {"poc_id": POC, "spec_version": "v001"}, _coder_handlers([], fake_md), monkeypatch)
    comps = [t["component"] for t in sorted(fake_md.tasks.values(), key=lambda t: t["seq"])]
    assert comps == ["contract", "seed", "backend", "frontend"]


def test_legacy_task_component_is_derived_from_mode():
    from poc_shared_tools import metadata as md
    assert md.task_component({"agent": "api_agent", "mode": "contract"}) == "contract"
    assert md.task_component({"agent": "api_agent", "mode": "code"}) == "backend"
    assert md.task_component({"agent": "api_agent", "mode": "repair"}) == "backend"
    assert md.task_component({"agent": "frontend_agent", "mode": "code"}) == "frontend"
    assert md.task_component({"agent": "api_agent", "mode": "code", "component": "backend"}) == "backend"
