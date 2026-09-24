"""Test Agent — 5 acceptance tests (no LLM, no cloud).

1. Plan generation from golden fixtures — 6 smoke entries, 3 journeys, sc-01 mapped with p95.
2. Path-param resolution with a fake HTTP responder.
3. Generated journeys.spec.js has one test per story and every declared testid.
4. Report assembly validates against "test_report" for mixed pass/fail input.
5. Full graph run with all six tools monkeypatched replies succeeded with design-4 shape.
"""
from __future__ import annotations

import json
import pathlib
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from unittest.mock import patch

import pytest
import yaml
from langchain_core.messages import HumanMessage
from poc_contracts import Envelope, new_id, validate

import agent_test_agent.main as m
from agent_test_agent import pipeline

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------
ROOT = pathlib.Path(__file__).parent.parent.parent.parent  # repo root
SPEC_MD = ROOT / "fixtures/golden/spec/v001/poc_spec.md"
CONTRACT_YAML = ROOT / "fixtures/golden/code/v001/api_contract.yaml"

POC = "poc_01K5ZGF1XTVREG0000000000A1"
DEPLOY_RUN = new_id("run")


def _load_golden():
    spec_text = SPEC_MD.read_text()
    parts = spec_text.split("---", 2)
    fm = yaml.safe_load(parts[1])
    contract = yaml.safe_load(CONTRACT_YAML.read_text())
    return fm, contract


# ---------------------------------------------------------------------------
# Test 1 — plan generation from golden fixtures
# ---------------------------------------------------------------------------

def test_plan_from_golden_fixtures():
    fm, contract = _load_golden()
    operations = pipeline.parse_operations(contract)
    plan = pipeline.generate_plan(fm["user_stories"], fm["success_criteria"], operations)

    # 6 smoke entries (one per operation in the contract)
    assert len(plan["api_smoke"]) == 6, f"expected 6, got {len(plan['api_smoke'])}: {[e['operationId'] for e in plan['api_smoke']]}"
    op_ids = {e["operationId"] for e in plan["api_smoke"]}
    assert op_ids == {"getHealth", "listCategories", "listProducts", "getProduct",
                      "getRecommendations", "getTopProducts"}

    # 3 journeys with the exact testids from the golden spec
    assert len(plan["journeys"]) == 3
    journey_by_id = {j["id"]: j for j in plan["journeys"]}
    assert journey_by_id["us-01"]["testids"] == ["us-01-product-name", "us-01-reco-list", "us-01-reco-item"]
    assert journey_by_id["us-02"]["testids"] == ["us-02-category-list", "us-02-category-item", "us-02-product-list", "us-02-product-item"]
    assert journey_by_id["us-03"]["testids"] == ["us-03-top-list", "us-03-top-item"]

    # sc-01 mapped to getRecommendations with p95_target_ms=300
    criteria_by_id = {c["id"]: c for c in plan["criteria"]}
    sc01 = criteria_by_id["sc-01"]
    assert sc01["mapped_operation_id"] == "getRecommendations", sc01
    assert sc01["p95_target_ms"] == 300, sc01
    assert sc01["automatable"] is True

    # sc-04 not automatable
    sc04 = criteria_by_id["sc-04"]
    assert sc04["automatable"] is False
    assert sc04["mapped_operation_id"] is None


# ---------------------------------------------------------------------------
# Test 2 — path-param resolution with a fake HTTP responder
# ---------------------------------------------------------------------------

class _FakeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if "/products" in self.path and "/recommendations" not in self.path and "{" not in self.path:
            body = json.dumps({"items": [{"sku": "SKU-TEST-001", "name": "Test Product", "category": "snacks", "brand": "B", "price_inr": 50}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


def test_path_param_resolution():
    server = HTTPServer(("127.0.0.1", 0), _FakeHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever)
    t.daemon = True
    t.start()

    try:
        fm, contract = _load_golden()
        operations = pipeline.parse_operations(contract)
        api_url = f"http://127.0.0.1:{port}/api"

        val = pipeline.resolve_path_param(api_url, "sku", operations)
        assert val == "SKU-TEST-001", f"expected SKU-TEST-001, got {val!r}"
    finally:
        server.shutdown()
        t.join(timeout=2)


# ---------------------------------------------------------------------------
# Test 3 — journeys.spec.js has one test per story and every declared testid
# ---------------------------------------------------------------------------

def test_generated_spec_js():
    fm, contract = _load_golden()
    operations = pipeline.parse_operations(contract)
    plan = pipeline.generate_plan(fm["user_stories"], fm["success_criteria"], operations)
    js = pipeline.generate_js_spec(plan, "http://example.com")

    # One test per user story
    for us in fm["user_stories"]:
        assert us["id"] in js, f"Story {us['id']} not found in generated spec"

    # Every declared testid appears in the JS
    for us in fm["user_stories"]:
        for tid in us.get("testids", []):
            assert tid in js, f"testid {tid!r} not found in generated spec"

    # Sanity: @playwright/test is required
    assert "@playwright/test" in js


# ---------------------------------------------------------------------------
# Test 4 — report assembly validates against "test_report" for mixed pass/fail
# ---------------------------------------------------------------------------

def test_report_assembly_validates():
    fm, contract = _load_golden()
    operations = pipeline.parse_operations(contract)
    plan = pipeline.generate_plan(fm["user_stories"], fm["success_criteria"], operations)

    # Fake api_results: getRecommendations passed with p95, getHealth failed
    api_results = [
        {"id": "api-getHealth", "kind": "api_smoke", "status": "failed", "duration_ms": 10,
         "suspected_component": "backend", "evidence": {"http_status": 503, "error": "db down"}},
        {"id": "api-listCategories", "kind": "api_smoke", "status": "passed", "duration_ms": 50},
        {"id": "api-listProducts", "kind": "api_smoke", "status": "passed", "duration_ms": 45},
        {"id": "api-getProduct", "kind": "api_smoke", "status": "skipped"},
        {"id": "api-getRecommendations", "kind": "api_smoke", "status": "passed", "duration_ms": 120,
         "measured": {"p95_ms": 210}},
        {"id": "api-getTopProducts", "kind": "api_smoke", "status": "passed", "duration_ms": 60},
    ]
    browser_results = [
        {"id": "us-01", "kind": "user_story", "status": "passed", "duration_ms": 3000},
        {"id": "us-02", "kind": "user_story", "status": "failed", "duration_ms": 2000,
         "suspected_component": "frontend",
         "evidence": {"error": "Missing testid us-02-category-list", "failed_requests": []}},
        {"id": "us-03", "kind": "user_story", "status": "passed", "duration_ms": 2500},
    ]

    ctx = {
        "base_url": "http://1.2.3.4/",
        "api_url": "http://1.2.3.4/api",
        "health_url": "http://1.2.3.4/api/health",
        "instance_id": "i-abc123",
        "public_ip": "1.2.3.4",
        "code_version": "v001",
        "spec_version": "v001",
        "user_stories": fm["user_stories"],
        "success_criteria": fm["success_criteria"],
        "operations": operations,
    }
    run_id = new_id("run")
    report = pipeline.assemble_report(POC, run_id, DEPLOY_RUN, ctx, api_results, browser_results, plan)

    # Must validate against the schema
    validate("test_report", report)

    s = report["summary"]
    assert s["failed"] == 2  # api-getHealth + us-02
    assert s["passed"] >= 4  # at least api-listCategories, api-listProducts, api-getRecommendations, us-01, us-03
    assert s["skipped"] == 1  # api-getProduct
    # sc-01 should be passed (p95=210 <= 300)
    sc_by_id = {sc["id"]: sc for sc in report["success_criteria"]}
    assert sc_by_id["sc-01"]["status"] == "passed", sc_by_id["sc-01"]
    assert sc_by_id["sc-01"]["measured"]["p95_ms"] == 210
    assert sc_by_id["sc-04"]["status"] == "not_automatable"


# ---------------------------------------------------------------------------
# Test 5 — full graph run with all six tools monkeypatched replies succeeded
# ---------------------------------------------------------------------------

class _FakeMD:
    def __init__(self):
        self._runs: dict[str, dict] = {}
        self._pocs: dict[str, dict] = {POC: {"poc_id": POC, "status": "deployed"}}

    def now(self):
        return "2026-09-24T12:00:00Z"

    def create_run(self, poc_id, stage, requested_by, started_by_agent, inputs=None, trace_id=None):
        r = {"run_id": new_id("run"), "poc_id": poc_id, "stage": stage, "status": "queued",
             "inputs": inputs or {}, "outputs": {}, "steps": [], "repair_attempts": {}}
        self._runs[r["run_id"]] = r
        return r

    def get_run(self, run_id):
        return json.loads(json.dumps(self._runs[run_id]))

    def update_poc_status(self, poc_id, status, **fields):
        self._pocs[poc_id]["status"] = status

    def finish_run(self, run_id, status, outputs=None, error=None):
        r = self._runs[run_id]
        r["status"] = status
        if outputs:
            r["outputs"].update(outputs)
        if error:
            r["error"] = error
        return r

    def _db(self):
        md = self

        class _Pocs:
            def update_one(self, flt, upd, **kw):
                pass

        class _Runs:
            def update_one(self, flt, upd, **kw):
                r = md._runs[flt["run_id"]]
                for k, v in upd.get("$set", {}).items():
                    if k.startswith("outputs."):
                        r["outputs"][k[8:]] = v
                    elif k != "updated_at":
                        r[k] = v

        class _DB:
            runs = _Runs()
            pocs = _Pocs()

        return _DB()


def _make_canned_tools(fake_md: _FakeMD):
    """Return a dict of tool-name -> callable that monkeypatches all 6 test tools."""
    fm, contract = _load_golden()
    operations = pipeline.parse_operations(contract)
    plan = pipeline.generate_plan(fm["user_stories"], fm["success_criteria"], operations)

    def fake_start_run(envelope_json: str) -> str:
        env = json.loads(envelope_json)["request"]
        run = fake_md.create_run(
            env["poc_id"], "test", env.get("caller", "deploy_agent"), "test_agent",
            {"deployment_run_id": env["params"].get("deployment_run_id", ""), "scope": env["params"].get("scope", "all")},
        )
        return json.dumps({"run_id": run["run_id"], "poc_id": env["poc_id"],
                           "deployment_run_id": env["params"].get("deployment_run_id", ""),
                           "scope": env["params"].get("scope", "all")})

    def fake_load_context(run_id: str) -> str:
        r = fake_md._runs[run_id]
        ctx = {
            "base_url": "http://1.2.3.4/", "api_url": "http://1.2.3.4/api",
            "health_url": "http://1.2.3.4/api/health", "code_version": "v001", "spec_version": "v001",
            "instance_id": "i-abc123", "public_ip": "1.2.3.4",
            "user_stories": fm["user_stories"], "success_criteria": fm["success_criteria"],
            "operations": operations,
        }
        r["outputs"]["context"] = ctx
        return json.dumps(ctx)

    def fake_generate_plan(run_id: str) -> str:
        r = fake_md._runs[run_id]
        r["outputs"]["test_plan"] = plan
        return json.dumps(plan)

    def fake_run_api_smoke(run_id: str) -> str:
        results = [{"id": f"api-{op['operationId']}", "kind": "api_smoke", "status": "passed", "duration_ms": 50}
                   for op in operations]
        fake_md._runs[run_id]["outputs"]["api_results"] = results
        return json.dumps(results)

    def fake_run_browser(run_id: str) -> str:
        results = [{"id": us["id"], "kind": "user_story", "status": "passed", "duration_ms": 1000}
                   for us in fm["user_stories"]]
        fake_md._runs[run_id]["outputs"]["browser_results"] = results
        return json.dumps(results)

    def fake_write_report(run_id: str) -> str:
        r = fake_md._runs[run_id]
        report_key = f"pocs/{POC}/test/{run_id}/test_report.json"
        result = {
            "run_id": run_id, "passed": 9, "failed": 0, "skipped": 0, "not_automatable": 2,
            "failed_ids": [], "suspected_component": None, "report_key": report_key,
        }
        fake_md.finish_run(run_id, "succeeded", outputs=result)
        fake_md.update_poc_status(r["poc_id"], "tested")
        return json.dumps(result)

    return {
        "test_start_run": fake_start_run,
        "test_load_context": fake_load_context,
        "test_generate_plan": fake_generate_plan,
        "test_run_api_smoke": fake_run_api_smoke,
        "test_run_browser": fake_run_browser,
        "test_write_report": fake_write_report,
    }


def test_full_graph_run_succeeds():
    from agent_test_agent.a2a import invoke_tool as real_invoke_tool

    fake_md = _FakeMD()
    canned = _make_canned_tools(fake_md)

    # Patch app tools with canned versions
    original_tools = dict(m.app._tools)
    try:
        from tests.conftest import _Tool
        for name, fn in canned.items():
            m.app._tools[name] = _Tool(fn, name)

        graph = m.build_agent()
        task_id = new_id("task")
        env = Envelope.request(
            poc_id=POC, run_id=DEPLOY_RUN,
            caller="deploy_agent", agent="test_agent", tool="run_e2e",
            params={"deployment_run_id": DEPLOY_RUN, "scope": "all"},
            task_id=task_id,
        )
        out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                           config={"configurable": {"thread_id": "graph_test_1"}})
        resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]

        assert resp["status"] == "succeeded", resp
        r = resp["result"]
        # Design decision 4 shape
        assert "run_id" in r
        assert "passed" in r
        assert "failed" in r
        assert "skipped" in r
        assert "not_automatable" in r
        assert "failed_ids" in r
        assert "suspected_component" in r
        assert "report_key" in r
        assert r["failed"] == 0
        assert isinstance(r["failed_ids"], list)
        # Artifact
        assert any(a["kind"] == "report" for a in resp.get("artifacts", []))
    finally:
        m.app._tools.clear()
        m.app._tools.update(original_tools)
