"""Coding Orchestrator — plan logic (pure) and graph (fake SDK + faked A2A coders) tests.
No LLM, no cloud, no platform DB."""
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

    def create_task(self, run_id, poc_id, agent, tool, mode=None, input_ref=None, task_id=None):
        return {"task_id": task_id or new_id("task"), "run_id": run_id, "agent": agent, "tool": tool}

    def finish_task(self, task_id, status, output_ref=None, duration_ms=None, token_usage=None):
        pass

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


def _coder_handlers(calls):
    def api(env):
        req = env["request"]
        calls.append((req["tool"], req.get("mode")))
        cv = req["params"]["code_version"]
        if req.get("mode") == "contract":
            key = f"pocs/{POC}/code/{cv}/api_contract.yaml"
            return Envelope.succeeded(req["task_id"], {"contract_key": key}, [Envelope.artifact("contract", key, cv)])
        key = f"pocs/{POC}/code/{cv}/backend/"
        return Envelope.succeeded(req["task_id"], {"component": "backend"}, [Envelope.artifact("code", key, cv)])

    def seed(env):
        req = env["request"]
        calls.append(("generate_seed", req.get("mode")))
        cv = req["params"]["code_version"]
        key = f"pocs/{POC}/code/{cv}/seed/"
        return Envelope.succeeded(req["task_id"], {"component": "seed"}, [Envelope.artifact("code", key, cv)])

    def frontend(env):
        req = env["request"]
        calls.append(("generate_frontend", req.get("mode")))
        cv = req["params"]["code_version"]
        key = f"pocs/{POC}/code/{cv}/frontend/"
        return Envelope.succeeded(req["task_id"], {"component": "frontend"}, [Envelope.artifact("code", key, cv)])

    return {"generate-api": api, "generate-seed": seed, "generate-frontend": frontend}


def _invoke(tool: str, params: dict, handlers: dict | None = None):
    m.app.a2a_handlers = handlers or {}
    graph = m.build_agent()
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="chat_agent",
                           agent="coding_orchestrator", tool=tool, params=params, task_id=TASK)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                       config={"configurable": {"thread_id": "t"}})
    return validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]


def test_happy_path_builds_three_components(fake_md, fake_s3):
    calls = []
    resp = _invoke("start_code_run", {"poc_id": POC, "spec_version": "v001"}, _coder_handlers(calls))
    assert resp["status"] == "succeeded", resp
    assert resp["result"]["code_version"] == "v001"
    assert resp["result"]["changed_components"] == ["seed", "backend", "frontend"]
    assert [a["kind"] for a in resp["artifacts"]] == ["bundle", "contract"]
    assert calls == [("generate_api", "contract"), ("generate_seed", "code"),
                     ("generate_api", "code"), ("generate_frontend", "code")]
    run = next(r for r in fake_md.runs.values() if r["stage"] == "code")
    assert run["status"] == "succeeded"
    assert fake_md.pocs[POC]["current_versions"]["code"] == "v001"


def test_repair_regenerates_only_failed_component_and_bumps_version(fake_md, fake_s3):
    fake_s3["next"] = "v002"
    calls = []
    resp = _invoke("repair_component",
                   {"poc_id": POC, "code_version": "v001", "component": "frontend", "failure": FAILURE},
                   _coder_handlers(calls))
    assert resp["status"] == "succeeded", resp
    assert resp["result"]["code_version"] == "v002"
    assert resp["result"]["changed_components"] == ["frontend"]
    assert calls == [("generate_frontend", "repair")]


def test_gate_refused(fake_s3, monkeypatch):
    md = FakeMD([])  # no approvals
    import poc_shared_tools.metadata as real
    for name in dir(md):
        if not name.startswith("__") and hasattr(real, name):
            monkeypatch.setattr(real, name, getattr(md, name))
    monkeypatch.setattr(real, "_db", md._db)
    resp = _invoke("start_code_run", {"poc_id": POC, "spec_version": "v009"}, _coder_handlers([]))
    assert resp["status"] == "failed" and resp["error"]["code"] == "GATE_NOT_APPROVED"


def test_bad_tool():
    resp = _invoke("nonexistent", {"poc_id": POC})
    assert resp["status"] == "failed" and resp["error"]["code"] == "BAD_TOOL"


def test_invalid_envelope():
    m.app.a2a_handlers = {}
    graph = m.build_agent()
    out = graph.invoke({"messages": [HumanMessage(content="not json")]}, config={"configurable": {"thread_id": "t2"}})
    resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]
    assert resp["status"] == "failed" and resp["error"]["code"] == "INVALID_ENVELOPE"


# =============================================================================
# A2A token refresh on stale discovery (long-run 401 -> re-mint and retry)
# =============================================================================

def _stub_app_that_refreshes(calls, first_registry, second_registry):
    """A minimal app whose a2a_tools() returns a discovery tool; the registry it reports depends on how
    many times a2a_tools() has been called — call 1 mimics an expired token (empty), call 2 the fresh one."""
    import json as _json

    class _T:
        def __init__(self, fn, name):
            self.fn, self.name, self.args = fn, name, {}
        def invoke(self, call):
            return self.fn(**(call.get("args", {}) if isinstance(call, dict) else {}))

    class _App:
        def a2a_tools(self):
            calls["n"] += 1
            registry = first_registry if calls["n"] == 1 else second_registry

            def discover_available_agents():
                return _json.dumps(registry)
            return [_T(discover_available_agents, "discover_available_agents")]
    return _App()


def test_find_agent_refreshes_token_when_discovery_is_stale():
    """First discovery is empty (expired A2A token -> 401 -> nothing); find_agent must re-mint the token
    (a fresh a2a_tools()) and retry, then match. This is the frontend-coder-not-found case on a long run."""
    from agent_coding_orchestrator.a2a import A2AClient
    calls = {"n": 0}
    full = [{"name": "frontend-agent", "id": "ws-fe", "skills": ["generate-frontend"]}]
    c = A2AClient(_stub_app_that_refreshes(calls, first_registry=[], second_registry=full))
    agent = c.find_agent("generate-frontend")
    assert agent["id"] == "ws-fe"
    assert calls["n"] == 2  # refreshed exactly once after the empty first discovery


def test_find_agent_refreshes_only_once_then_raises_for_absent_agent():
    from agent_coding_orchestrator.a2a import A2AClient
    calls = {"n": 0}
    c = A2AClient(_stub_app_that_refreshes(calls, first_registry=[], second_registry=[]))
    try:
        c.find_agent("generate-frontend")
        assert False, "expected RuntimeError for a genuinely absent agent"
    except RuntimeError as e:
        assert "generate-frontend" in str(e)
    assert calls["n"] == 2  # one refresh, then give up
