"""Exercises the Deploy Agent graph end to end with every cloud step faked at the pipeline boundary."""
import json
import pytest
from langchain_core.messages import HumanMessage
from poc_contracts import Envelope, new_id, validate

import agent_deploy_agent.main as m
from agent_deploy_agent import pipeline

POC, TASK = new_id("poc"), new_id("task")


class FakeMD:
    """In-memory stand-in for poc_shared_tools.metadata used by the tools."""
    def __init__(self):
        self.runs, self.pocs = {}, {POC: {"poc_id": POC, "approvals": [{"stage": "code_approved", "version": "v001"}], "status": "code_ready"}}
    def now(self): return "2026-09-24T12:00:00Z"
    def check_gate(self, poc_id, stage, version): return any(a["version"] == version for a in self.pocs[poc_id]["approvals"])
    def create_run(self, poc_id, stage, requested_by, started_by_agent, inputs=None, trace_id=None):
        r = {"run_id": new_id("run"), "poc_id": poc_id, "stage": stage, "status": "queued", "steps": [], "inputs": inputs or {}, "outputs": {}, "repair_attempts": {}}
        self.runs[r["run_id"]] = r; return r
    def get_run(self, run_id): return json.loads(json.dumps(self.runs[run_id]))
    def get_poc(self, poc_id): return self.pocs[poc_id]
    def update_poc_status(self, poc_id, status, **f): self.pocs[poc_id]["status"] = status; self.pocs[poc_id].update(f)
    def update_run_step(self, run_id, name, status, output_ref=None, log_key=None, duration_ms=None):
        r = self.runs[run_id]; r["status"] = "running"; r["current_step"] = name
        for s in r["steps"]:
            if s["name"] == name: s["status"] = status; return
        r["steps"].append({"name": name, "status": status})
    def bump_repair_attempt(self, run_id, component):
        r = self.runs[run_id]; r["repair_attempts"][component] = r["repair_attempts"].get(component, 0) + 1; return r["repair_attempts"][component]
    def finish_run(self, run_id, status, outputs=None, error=None):
        r = self.runs[run_id]; r["status"] = status
        if outputs: r["outputs"].update(outputs)
        if error: r["error"] = error
        return r
    def _db(self):
        md = self
        class Runs:
            def update_one(self, q, u):
                r = md.runs[q["run_id"]]
                for k, v in u["$set"].items():
                    if k.startswith("outputs."): r["outputs"][k[8:]] = v
        class DB: runs = Runs()
        return DB()


@pytest.fixture
def fake_md(monkeypatch):
    md = FakeMD()
    import poc_shared_tools.metadata as real
    for name in dir(md):
        if not name.startswith("__") and hasattr(real, name):
            monkeypatch.setattr(real, name, getattr(md, name))
    monkeypatch.setattr(real, "_db", md._db)
    return md


def _fake_steps(monkeypatch, fail_at: dict | None = None, counter: dict | None = None):
    fail_at = fail_at or {}
    counter = counter if counter is not None else {}
    def run_step(run, step):
        counter[step] = counter.get(step, 0) + 1
        if step in fail_at and counter[step] <= fail_at[step]:
            return {"ok": False, "outputs": {}, "error": {"code": "BUILD_ERROR", "message": "TS2339", "component": "frontend"}}
        outs = {"check_gate": {"code_version": run["inputs"]["code_version"], "bundle_key": f"pocs/{POC}/code/v001/bundle.tar.gz"},
                "launch_instance": {"instance_id": "i-1", "public_ip": "1.2.3.4", "instance_type": "t3.medium"},
                "publish_frontend": {"urls": {"app": "http://1.2.3.4/", "api": "http://1.2.3.4/api", "health": "http://1.2.3.4/api/health"}},
                "write_deployment": {"deployment_key": f"pocs/{POC}/deploy/x/deployment.json"},
                "run_tests": {"tests_skipped": True} if not run["inputs"].get("options", {}).get("run_tests", True) else {"tests_requested": True},
                "finalize": {"final_status": "tested"}}.get(step, {})
        return {"ok": True, "outputs": outs, "error": None}
    monkeypatch.setattr(pipeline, "run_step", run_step)
    monkeypatch.setattr(m, "POLL_S", 0)
    return counter


def _fake_cross_agent(monkeypatch, handlers: dict):
    """Route platform_invoke.invoke_envelope(skill, env, ...) to the matching synchronous handler. Deploy now
    calls the test agent ('e2e-tests') and the orchestrator ('code-orchestration') as SYNCHRONOUS top-level
    invokes; each handler returns Envelope.succeeded(...) == {"response": {...}}, the shape invoke_envelope
    yields (the platform reply's `response` field carries the callee's AgentEnvelope)."""
    from poc_shared_tools import platform_invoke as pi

    def fake_invoke_envelope(skill, envelope, *, user_id, session_id, timeout_s=900):
        return handlers[skill](envelope)

    monkeypatch.setattr(pi, "invoke_envelope", fake_invoke_envelope)


def _invoke(req_tool: str, params: dict, handlers: dict | None = None, monkeypatch=None):
    if monkeypatch is not None:
        _fake_cross_agent(monkeypatch, handlers or {})
    graph = m.build_agent()
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="chat_agent", agent="deploy_agent", tool=req_tool, params=params, task_id=TASK)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]}, config={"configurable": {"thread_id": "t"}})
    return validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]


def test_happy_path_with_tests(fake_md, monkeypatch):
    counter = _fake_steps(monkeypatch)
    def test_agent(env):
        # The test agent runs the whole suite in its own root session and replies `succeeded` synchronously
        # (no fast-ack) with the report in `result`.
        rid = new_id("run")
        report_key = f"pocs/{POC}/test/{rid}/test_report.json"
        return Envelope.succeeded(env["request"]["task_id"],
                                  {"run_id": rid, "failed": 0, "report_key": report_key},
                                  [Envelope.artifact("report", report_key)])
    resp = _invoke("start_deploy_run", {"code_version": "v001", "options": {}}, {"e2e-tests": test_agent}, monkeypatch)
    assert resp["status"] == "succeeded", resp
    assert resp["result"]["urls"]["app"] == "http://1.2.3.4/" and resp["result"]["test_passed"] is True
    assert [a["kind"] for a in resp["artifacts"]] == ["deployment", "report"]
    run = next(r for r in fake_md.runs.values() if r["stage"] == "deploy")
    assert run["status"] == "succeeded" and [s["name"] for s in run["steps"]] == pipeline.STEPS
    assert counter["build_frontend"] == 1


def test_gate_refused(fake_md, monkeypatch):
    _fake_steps(monkeypatch)
    resp = _invoke("start_deploy_run", {"code_version": "v009", "options": {}}, monkeypatch=monkeypatch)
    assert resp["status"] == "failed" and resp["error"]["code"] == "GATE_NOT_APPROVED"


def test_repair_loop_then_success(fake_md, monkeypatch):
    counter = _fake_steps(monkeypatch, fail_at={"build_frontend": 1})
    def orchestrator(env):
        # Repair is a SYNCHRONOUS top-level invoke: the orchestrator runs the repair code run to completion
        # in its own root session and replies `succeeded` with the new code_version.
        assert env["request"]["tool"] == "repair_component"
        validate("failure_report", env["request"]["params"]["failure"])
        rid = new_id("run")
        return Envelope.succeeded(env["request"]["task_id"], {"run_id": rid, "code_version": "v002"})
    import poc_shared_tools.s3 as s3
    monkeypatch.setattr(s3, "put_object", lambda *a, **k: {"key": a[2]})
    monkeypatch.setattr(s3, "get_text", lambda key: json.dumps({"bundle_key": key.replace("poc.manifest.json", "bundle.tar.gz"), "contract_key": key.replace("poc.manifest.json", "api_contract.yaml")}))
    resp = _invoke("start_deploy_run", {"code_version": "v001", "options": {"run_tests": False}}, {"code-orchestration": orchestrator}, monkeypatch)
    assert resp["status"] == "succeeded", resp
    run = next(r for r in fake_md.runs.values() if r["stage"] == "deploy")
    assert run["repair_attempts"] == {"frontend": 1} and run["outputs"]["code_version"] == "v002"
    assert counter["fetch_bundle"] == 2 and counter["build_frontend"] == 2  # rewound to fetch_bundle after repair


def test_repair_exhausted_fails_cleanly(fake_md, monkeypatch):
    _fake_steps(monkeypatch, fail_at={"build_frontend": 99})
    def orchestrator(env):
        rid = new_id("run")
        return Envelope.succeeded(env["request"]["task_id"], {"run_id": rid, "code_version": "v002"})
    import poc_shared_tools.s3 as s3
    monkeypatch.setattr(s3, "put_object", lambda *a, **k: {"key": a[2]})
    monkeypatch.setattr(s3, "get_text", lambda key: json.dumps({"bundle_key": "pocs/x/code/v002/bundle.tar.gz", "contract_key": "pocs/x/code/v002/api_contract.yaml"}))
    resp = _invoke("start_deploy_run", {"code_version": "v001", "options": {"run_tests": False}}, {"code-orchestration": orchestrator}, monkeypatch)
    assert resp["status"] == "failed" and resp["error"]["code"] == "BUILD_ERROR"
    assert resp["error"]["detail"]["repair_attempts"] == {"frontend": 4}  # 3 repairs used, 4th bump marks exhausted


def test_failed_deploy_resets_poc_status(fake_md, monkeypatch):
    # A deploy that fails at a non-repairable step must release pocs.status from the
    # transient "deploying" back to "code_ready" (deploy_start_run set it to "deploying").
    _fake_steps(monkeypatch, fail_at={"provision_db": 99})
    assert fake_md.pocs[POC]["status"] == "code_ready"
    resp = _invoke("start_deploy_run", {"code_version": "v001", "options": {"run_tests": False}}, monkeypatch=monkeypatch)
    assert resp["status"] == "failed"
    run = next(r for r in fake_md.runs.values() if r["stage"] == "deploy")
    assert run["status"] == "failed"
    assert fake_md.pocs[POC]["status"] == "code_ready"


def test_publish_frontend_urls_use_public_dns(monkeypatch):
    # The public-facing URLs must use the EC2 public DNS name (as the golden deployment does), not the raw
    # public IP — the egress proxy refuses raw-IP hosts and the DNS name is the stable public address.
    from poc_infra_tools import ssm
    monkeypatch.setattr(ssm, "publish_frontend_nginx", lambda *a, **k: {"exit_code": 0, "stdout": "", "stderr": ""})
    monkeypatch.setattr(ssm, "public_healthcheck_via_ssm", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(ssm, "http_healthcheck", lambda *a, **k: {"ok": True})
    run = {"poc_id": POC, "run_id": "run_x", "inputs": {"code_version": "v001", "options": {}},
           "outputs": {"code_version": "v001", "instance_id": "i-1", "public_ip": "1.2.3.4",
                       "public_dns": "ec2-1-2-3-4.ap-south-1.compute.amazonaws.com",
                       "frontend_manifest": {"workdir": "frontend", "static_dir": "dist",
                                             "publish": {"api_proxy": {"path": "/api", "upstream": "http://127.0.0.1:8080"}}}}}
    r = pipeline.run_step(run, "publish_frontend")
    assert r["ok"], r
    urls = r["outputs"]["urls"]
    assert urls["app"] == "http://ec2-1-2-3-4.ap-south-1.compute.amazonaws.com/"
    assert urls["api"] == "http://ec2-1-2-3-4.ap-south-1.compute.amazonaws.com/api"
    assert "1.2.3.4" not in json.dumps(urls)  # never the raw IP


def test_invalid_envelope():
    graph = m.build_agent()
    out = graph.invoke({"messages": [HumanMessage(content="deploy it please")]}, config={"configurable": {"thread_id": "t2"}})
    resp = json.loads(out["messages"][-1].content)["response"]
    assert resp["status"] == "failed" and resp["error"]["code"] == "INVALID_ENVELOPE"


def test_get_run_status(fake_md, monkeypatch):
    _fake_steps(monkeypatch)
    r = fake_md.create_run(POC, "deploy", "u", "deploy_agent", {"code_version": "v001"})
    fake_md.update_run_step(r["run_id"], "check_gate", "succeeded")
    resp = _invoke("get_run_status", {"run_id": r["run_id"]}, monkeypatch=monkeypatch)
    assert resp["status"] == "succeeded" and resp["result"]["steps"] == [{"name": "check_gate", "status": "succeeded"}]


# =============================================================================
# Deploy's cross-agent calls go through the shared platform_invoke transport,
# with a 401 forcing exactly one token refresh + retry (deploy -> test late in a
# long run, past the ~5-min OE token — now a root session, so a fresh token).
# =============================================================================

def _fake_platform_http(monkeypatch, state):
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
            return 200, json.dumps({"workspaces": [{"workspace_id": "ws-te", "name": "test-agent"}]}).encode()
        if "/invoke" in url:
            state["invoke"] += 1
            if state["invoke"] == 1:  # first call: expired token -> 401 -> one refresh + retry
                return 401, b'{"error":"unauthorized"}'
            req = json.loads(json.loads(data)["message"])["request"]
            rid = new_id("run")
            report_key = f"pocs/{POC}/test/{rid}/test_report.json"
            inner = Envelope.succeeded(req["task_id"], {"run_id": rid, "failed": 0, "report_key": report_key},
                                       [Envelope.artifact("report", report_key)])
            return 200, json.dumps({"success": True, "response": json.dumps(inner), "status": "completed"}).encode()
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(pi, "_http", fake_http)


def test_deploy_test_goes_through_platform_invoke_and_refreshes_token_on_401(fake_md, monkeypatch):
    counter = _fake_steps(monkeypatch)
    state = {"token": 0, "invoke": 0}
    _fake_platform_http(monkeypatch, state)
    graph = m.build_agent()
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="chat_agent", agent="deploy_agent",
                           tool="start_deploy_run", params={"code_version": "v001", "options": {}}, task_id=TASK)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                       config={"configurable": {"thread_id": "tinv"}})
    resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]
    assert resp["status"] == "succeeded" and resp["result"]["test_passed"] is True, resp
    assert state["invoke"] == 2   # one 401, then the retry succeeds
    assert state["token"] == 2    # initial mint + exactly one forced refresh
