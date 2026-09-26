"""Exercises the Deploy Agent graph end to end with every cloud step faked at the pipeline boundary.
Cross-agent calls (test agent, repair via the coding orchestrator, self-handover) are FIRED through
platform_invoke.start_invoke; the fakes simulate the callee by writing its run document into FakeMD, which the
deploy agent's single-call waits (deploy_wait_test_run / deploy_wait_code_run) then find."""
import contextlib
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
        self.touches = {}
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
    def touch_run(self, run_id): self.touches[run_id] = self.touches.get(run_id, 0) + 1
    def heartbeat(self, run_id, every_s=30):
        self.touch_run(run_id); return contextlib.nullcontext()
    def reopen_run(self, run_id, reason, execution=None):
        r = self.runs[run_id]
        if r["status"] in ("succeeded", "cancelled"):
            from poc_shared_tools.errors import ToolError
            raise ToolError("RUN_NOT_RESUMABLE", run_id)
        r["status"] = "running"; r.pop("error", None); r["executions"] = r.get("executions", 0) + 1
        r.setdefault("continuations", []).append({"reason": reason, "execution": execution}); return dict(r)
    def add_callee_run(self, stage, inputs, status="succeeded", outputs=None):
        """What a fired callee (test agent / orchestrator) does in its own root session: write its run doc."""
        r = {"run_id": new_id("run"), "poc_id": POC, "stage": stage, "status": status, "steps": [], "inputs": inputs,
             "outputs": outputs or {}, "repair_attempts": {}, "started_at": _real_now()}
        self.runs[r["run_id"]] = r; return r
    def _db(self):
        md = self
        class Runs:
            def update_one(self, q, u):
                r = md.runs[q["run_id"]]
                for k, v in u["$set"].items():
                    if k.startswith("outputs."): r["outputs"][k[8:]] = v
            def find_one(self, q, sort=None):
                hits = [r for r in md.runs.values() if all(_match(r, k, v) for k, v in q.items())]
                hits.sort(key=lambda r: r.get("started_at", ""), reverse=True)
                return json.loads(json.dumps(hits[0])) if hits else None
        class DB: runs = Runs()
        return DB()


def _real_now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _match(doc, dotted, want):
    cur = doc
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    if isinstance(want, dict) and "$gte" in want:
        return cur >= want["$gte"]
    return cur == want


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
    """Route platform_invoke.start_invoke(skill, env, ...) — a FIRE — to the matching handler, which simulates
    the callee (writes its run doc into FakeMD). Returns {"status": "started"} like a 504/timeout disconnect."""
    from poc_shared_tools import platform_invoke as pi
    fired = []

    def fake_start(skill, envelope, *, user_id, session_id, client_timeout_s=25):
        fired.append((skill, envelope, session_id))
        if skill in handlers:
            handlers[skill](envelope)
        return {"status": "started"}

    monkeypatch.setattr(pi, "start_invoke", fake_start)
    return fired


def _test_agent(md, failed=0, suspected=None):
    def handler(env):
        rid_outs = {"failed": failed, "report_key": f"pocs/{POC}/test/x/test_report.json"}
        if suspected: rid_outs["suspected_component"] = suspected
        md.add_callee_run("test", {"deployment_run_id": env["request"]["params"]["deployment_run_id"]},
                          "succeeded" if failed == 0 else "failed", rid_outs)
    return handler


def _orchestrator(md, new_version="v002"):
    def handler(env):
        assert env["request"]["tool"] == "repair_component"
        f = validate("failure_report", env["request"]["params"]["failure"])
        md.add_callee_run("code", {"code_version": f["code_version"], "component": f["component"], "failure": f},
                          "succeeded", {"code_version": new_version})
    return handler


def _invoke(req_tool: str, params: dict, handlers: dict | None = None, monkeypatch=None, out_executions: list | None = None):
    """Run one request; follow self-handovers (continue_run fired at skill deploy-operations) like the platform
    would — each continuation is a fresh graph execution — until a terminal reply."""
    handovers = []
    if monkeypatch is not None:
        _fake_cross_agent(monkeypatch, dict(handlers or {}, **{"deploy-operations": handovers.append}))
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="chat_agent", agent="deploy_agent", tool=req_tool, params=params, task_id=TASK)
    n = 0
    while True:
        out = m.build_agent().invoke({"messages": [HumanMessage(content=json.dumps(env))]}, config={"configurable": {"thread_id": f"t{n}"}})
        resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]
        n += 1
        if resp["status"] != "started" or not handovers:
            break
        env = handovers.pop()
        assert env["request"]["tool"] == "continue_run" and env["request"]["mode"] == "continue"
    if out_executions is not None:
        out_executions.append(n)
    return resp


def test_happy_path_with_tests(fake_md, monkeypatch):
    counter = _fake_steps(monkeypatch)
    resp = _invoke("start_deploy_run", {"code_version": "v001", "options": {}}, {"e2e-tests": _test_agent(fake_md)}, monkeypatch)
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
    orchestrator = _orchestrator(fake_md)
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
    orchestrator = _orchestrator(fake_md)
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
            # the fired test agent writes its run doc (root session); the deploy agent's wait finds it
            import poc_shared_tools.metadata as _md
            _md._db  # FakeMD is installed; write through the fixture's instance
            state["md"].add_callee_run("test", {"deployment_run_id": req["params"]["deployment_run_id"]}, "succeeded",
                                       {"failed": 0, "report_key": f"pocs/{POC}/test/x/test_report.json"})
            inner = Envelope.started(req["task_id"], new_id("run"))
            return 200, json.dumps({"success": True, "response": json.dumps(inner), "status": "completed"}).encode()
        raise AssertionError(f"unexpected URL {url}")

    monkeypatch.setattr(pi, "_http", fake_http)


def test_deploy_test_goes_through_platform_invoke_and_refreshes_token_on_401(fake_md, monkeypatch):
    counter = _fake_steps(monkeypatch)
    state = {"token": 0, "invoke": 0, "md": fake_md}
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


# =============================================================================
# Resilient runs: self-handover, continue_run of an abandoned run, single-call
# bounded waits, heartbeat inside long steps.
# =============================================================================

def test_self_handover_carries_a_deploy_across_executions(fake_md, monkeypatch):
    monkeypatch.setattr(m, "HANDOVER_TOOL_CALLS", 5)
    counter = _fake_steps(monkeypatch)
    execs = []
    resp = _invoke("start_deploy_run", {"code_version": "v001", "options": {}}, {"e2e-tests": _test_agent(fake_md)},
                   monkeypatch, out_executions=execs)
    assert resp["status"] == "succeeded" and resp["result"]["test_passed"] is True, resp
    assert execs[0] >= 2
    assert all(v == 1 for v in counter.values()), counter          # no step re-executed across executions
    run = next(r for r in fake_md.runs.values() if r["stage"] == "deploy")
    assert run["executions"] == execs[0] - 1
    assert len([r for r in fake_md.runs.values() if r["stage"] == "test"]) == 1  # tests fired once


def _abandoned_deploy(md, last_done: str, *, inflight=None, with_index=True):
    run = md.create_run(POC, "deploy", "chat_agent", "deploy_agent", {"code_version": "v001", "options": {}})
    rid = run["run_id"]
    for st in pipeline.STEPS[:pipeline.STEPS.index(last_done) + 1]:
        md.update_run_step(rid, st, "succeeded")
    md.runs[rid]["outputs"].update({"code_version": "v001", "urls": {"app": "http://1.2.3.4/"},
                                    "deployment_key": f"pocs/{POC}/deploy/x/deployment.json"})
    if with_index and last_done != "run_tests":
        md.runs[rid]["outputs"]["resume_step_index"] = pipeline.STEPS.index(last_done) + 1
    if inflight:
        md.runs[rid]["outputs"]["inflight"] = inflight
    md.pocs[POC]["status"] = "deploying"
    return rid


def test_continue_reattaches_to_the_in_flight_test_run(fake_md, monkeypatch):
    """The 2026-09-25 failure: the execution died at run_tests while the (already fired) test run went on to
    pass. continue_run re-executes NO step and fires NO new test run — it finds the verdict and finishes."""
    counter = _fake_steps(monkeypatch)
    rid = _abandoned_deploy(fake_md, "run_tests", inflight={"kind": "test", "fired_at": _real_now()}, with_index=False)
    fake_md.add_callee_run("test", {"deployment_run_id": rid}, "succeeded", {"failed": 0, "report_key": f"pocs/{POC}/test/x/test_report.json"})
    fired = []
    resp = _invoke("continue_run", {"run_id": rid}, {"e2e-tests": fired.append}, monkeypatch)
    assert resp["status"] == "succeeded", resp
    assert fired == [] and counter.get("run_tests", 0) == 0
    assert set(counter) == {"finalize"}
    assert fake_md.runs[rid]["status"] == "succeeded" and fake_md.runs[rid]["executions"] == 1


def test_continue_resumes_at_first_step_not_done(fake_md, monkeypatch):
    counter = _fake_steps(monkeypatch)
    rid = _abandoned_deploy(fake_md, "build_backend")
    resp = _invoke("continue_run", {"run_id": rid}, {"e2e-tests": _test_agent(fake_md)}, monkeypatch)
    assert resp["status"] == "succeeded", resp
    assert set(counter) == {"start_backend", "build_frontend", "publish_frontend", "write_deployment", "run_tests", "finalize"}


def test_legacy_run_without_resume_index_does_not_skip_unverified_tests():
    run = {"steps": [{"name": s, "status": "succeeded"} for s in pipeline.STEPS[:pipeline.STEPS.index("run_tests") + 1]],
           "outputs": {}}
    assert m.resume_index(run) == pipeline.STEPS.index("run_tests")
    run["outputs"]["test_passed"] = True
    assert m.resume_index(run) == pipeline.STEPS.index("finalize")


def test_continue_is_idempotent_on_a_succeeded_deploy(fake_md, monkeypatch):
    _fake_steps(monkeypatch)
    rid = _abandoned_deploy(fake_md, "finalize")
    fake_md.runs[rid]["status"] = "succeeded"
    resp = _invoke("continue_run", {"run_id": rid}, {}, monkeypatch)
    assert resp["status"] == "succeeded" and resp["result"]["already"] == "succeeded"


def test_long_step_headroom_hands_over_early(monkeypatch):
    monkeypatch.setattr(m, "HANDOVER_AFTER_S", 360)
    st = {"exec_started": 1000.0, "tool_calls": 3, "calls_base": 0}
    assert not m.over_budget(st, "seed_data", now=1200.0)
    assert m.over_budget(st, "launch_instance", now=1200.0)       # 200 s in: a 2-4 min launch would overrun
    assert m.over_budget(st, None, now=1361.0)


def test_wait_test_run_is_one_bounded_call(fake_md, monkeypatch):
    monkeypatch.setattr(m, "POLL_S", 0.05)
    rid = _abandoned_deploy(fake_md, "write_deployment")
    t = fake_md.add_callee_run("test", {"deployment_run_id": rid}, "running")
    w = json.loads(m.deploy_wait_test_run(rid, _real_now(), 1))
    assert w["status"] == "running" and fake_md.touches[rid] >= 1
    fake_md.runs[t["run_id"]]["status"] = "succeeded"
    assert json.loads(m.deploy_wait_test_run(rid, _real_now(), 1))["status"] == "succeeded"
    monkeypatch.setattr(m, "CALLEE_APPEAR_S", 0)
    other = _abandoned_deploy(fake_md, "write_deployment")
    assert json.loads(m.deploy_wait_test_run(other, "2026-09-26T00:00:00Z", 1))["status"] == "missing"


def test_execute_step_heartbeats_the_run(fake_md, monkeypatch):
    _fake_steps(monkeypatch)
    rid = _abandoned_deploy(fake_md, "check_gate")
    json.loads(m.deploy_execute_step(rid, "provision_db"))
    assert fake_md.touches[rid] >= 1
    assert fake_md.runs[rid]["outputs"]["resume_step_index"] == pipeline.STEPS.index("provision_db") + 1
