"""Every §8 example from the spec must validate; a few deliberately broken ones must not."""
import pytest
from poc_contracts import validate, ContractError, Envelope, new_id, next_version, is_id, KINDS, schema_for
from poc_contracts.fakes import FakeDraftAgent, FakeApiAgent, FakeCodingOrchestrator, FakeTestAgent

POC = new_id("poc"); RUN = new_id("run"); TASK = new_id("task")
PRE = f"pocs/{POC}/"
NOW = "2026-09-24T10:00:00Z"


def test_all_schemas_load():
    for k in KINDS:
        assert schema_for(k)["$schema"].endswith("2020-12/schema")


def test_ids():
    assert is_id(POC) and is_id(RUN) and is_id(TASK)
    assert not is_id("poc_short")
    assert next_version(None) == "v001" and next_version("v003") == "v004"
    with pytest.raises(ValueError): new_id("job")


def test_envelope_roundtrip():
    req = Envelope.request(poc_id=POC, run_id=RUN, caller="chat_agent", agent="draft_agent",
                           tool="draft_spec", params={"transcript_key": PRE + "input/transcript.txt"}, max_tokens=200000)
    assert req["request"]["task_id"].startswith("task_")
    Envelope.succeeded(TASK, {"x": 1}, [Envelope.artifact("spec", PRE + "spec/v001/poc_spec.md", "v001")])
    Envelope.started(TASK, RUN)
    Envelope.failed(TASK, "INVALID_TRANSCRIPT", "empty")
    with pytest.raises(ContractError):
        validate("agent_envelope", {"response": {"task_id": TASK, "status": "failed"}})  # failed needs error
    with pytest.raises(ContractError):
        validate("agent_envelope", {"request": {"poc_id": "nope", "run_id": RUN, "task_id": TASK,
                                                "caller": "a", "agent": "b", "tool": "c", "params": {}}})


def test_spec_frontmatter():
    validate("poc_spec_frontmatter", {
        "poc_id": POC, "spec_version": "v002", "title": "Real-time recommendation engine",
        "stack": {"backend": "node-express", "frontend": "react-vite", "database": "mongodb"},
        "user_stories": [{"id": "us-01", "title": "Shopper sees recommendations", "actor": "shopper",
                          "acceptance": ["≥ 5 recommendations render"], "testids": ["us-01-reco-list", "us-01-reco-item"]}],
        "success_criteria": [{"id": "sc-01", "statement": "p95 < 300 ms", "automatable": True}],
        "seed_requirements": {"max_docs_per_collection": 10000, "realism": "e-commerce, en-IN locale"},
        "assumptions": ["…"], "approved": False})


def test_schema_design_and_query_patterns():
    validate("schema_design", {
        "database_name": POC, "collections": [{
            "name": "products", "description": "Catalog items",
            "fields": [{"name": "sku", "type": "string", "required": True, "example": "SKU-1001"},
                       {"name": "embedding", "type": "array<double>", "dims": 1024, "optional": True}],
            "indexes": [{"keys": {"sku": 1}, "unique": True}, {"keys": {"categories": 1, "price": -1}}],
            "relationships": [{"field": "brand_id", "references": "brands._id", "embed_or_reference": "reference"}],
            "seed": {"count": 5000, "generator_hints": "realistic product names"}}],
        "seed_requirements": {"max_docs_per_collection": 10000, "deterministic_seed": 42}})
    validate("query_patterns", {"patterns": [{
        "id": "qp-01", "user_story_ids": ["us-01"], "name": "Top recommendations", "description": "co-purchased",
        "collections": ["orders", "products"], "pseudocode": "match → unwind → group → sort → limit",
        "aggregation_sketch": [{"$match": {"items.sku": "<sku>"}}, {"$limit": 10}],
        "expected_latency_ms_p95": 300, "supporting_indexes": [{"collection": "orders", "keys": {"items.sku": 1}}]}]})


def test_manifests():
    validate("component_manifest", {"component": "backend", "runtime": "node20", "workdir": "backend",
        "entrypoints": {"install": ["npm ci"], "build": ["npm run build"], "start": ["node dist/server.js"],
                        "healthcheck": {"type": "http", "url": "http://localhost:8080/api/health", "expect": 200}},
        "env": {"required": ["MONGODB_URI", "PORT"], "optional": ["API_KEY"]}, "ports": [8080]})
    validate("component_manifest", {"component": "seed", "runtime": "node20", "workdir": "seed",
        "entrypoints": {"install": ["npm ci"], "seed": ["node seed.js"]},
        "env": {"required": ["MONGODB_URI", "SEED_MAX_DOCS"]}, "expected_output": "json_summary_last_line"})
    validate("component_manifest", {"component": "frontend", "runtime": "node20", "workdir": "frontend",
        "entrypoints": {"install": ["npm ci"], "build": ["npm run build"]},
        "env": {"required": ["VITE_API_BASE_URL"]}, "static_dir": "dist",
        "publish": {"type": "nginx_static", "api_proxy": {"path": "/api", "upstream": "http://127.0.0.1:8080"}}})
    with pytest.raises(ContractError):  # backend without healthcheck
        validate("component_manifest", {"component": "backend", "runtime": "node20", "workdir": "backend",
            "entrypoints": {"install": ["npm ci"], "build": ["x"], "start": ["y"]}, "env": {"required": []}})
    validate("poc_manifest", {"poc_id": POC, "code_version": "v003", "spec_version": "v002",
        "stack": {"backend": "node-express", "frontend": "react-vite", "database": "mongodb"},
        "components": ["seed", "backend", "frontend"], "contract_key": PRE + "code/v003/api_contract.yaml",
        "bundle_key": PRE + "code/v003/bundle.tar.gz", "bundle_sha256": "a" * 64,
        "guardrail_scan": {"ok": True, "scanned_at": NOW},
        "produced_by": {"run_id": RUN, "repairs_of": "v002", "changed_components": ["frontend"]}})


def test_deployment_report_failure():
    validate("deployment", {"poc_id": POC, "run_id": RUN, "code_version": "v003", "status": "deployed",
        "instance": {"instance_id": "i-0abc123def", "instance_type": "t3.medium", "public_ip": "13.233.1.2",
                     "region": "ap-south-1", "ttl_expires_at": NOW},
        "database": {"mode": "shared_db", "cluster_name": "poc-sandbox", "database_name": POC,
                     "secret_arn": "arn:aws:secretsmanager:ap-south-1:1:secret:x"},
        "urls": {"app": "http://13.233.1.2/", "api": "http://13.233.1.2/api", "health": "http://13.233.1.2/api/health"},
        "env": {"PORT": "8080", "VITE_API_BASE_URL": "/api", "SEED_MAX_DOCS": "10000"},
        "seed_summary": {"products": 5000, "orders": 8000},
        "steps": [{"name": "seed_data", "status": "succeeded", "log_key": PRE + "deploy/x/logs/seed_data.stdout.txt", "duration_ms": 41200}]})
    validate("test_report", {"poc_id": POC, "run_id": RUN, "deployment_run_id": RUN, "base_url": "http://13.233.1.2/",
        "summary": {"total": 14, "passed": 12, "failed": 1, "skipped": 0, "not_automatable": 1, "duration_ms": 183000},
        "results": [{"id": "api-getRecommendations", "kind": "api_smoke", "status": "passed", "duration_ms": 220},
                    {"id": "us-01", "kind": "user_story", "status": "failed", "suspected_component": "frontend",
                     "evidence": {"error": "locator not found", "console_errors": ["TypeError"], "failed_requests": []},
                     "artifacts": {"screenshot": PRE + "test/x/artifacts/us-01.png"}}],
        "success_criteria": [{"id": "sc-01", "status": "passed", "measured": {"p95_ms": 212}}, {"id": "sc-02", "status": "not_automatable"}]})
    with pytest.raises(ContractError):  # failed result without suspected_component
        validate("test_report", {"poc_id": POC, "run_id": RUN, "deployment_run_id": RUN, "base_url": "http://x/",
            "summary": {"total": 1, "passed": 0, "failed": 1, "skipped": 0, "not_automatable": 0, "duration_ms": 1},
            "results": [{"id": "us-01", "kind": "user_story", "status": "failed"}], "success_criteria": []})
    validate("failure_report", {"poc_id": POC, "deploy_run_id": RUN, "code_version": "v003", "component": "frontend",
        "stage_step": "build_frontend", "failure_class": "BUILD_ERROR", "exit_code": 1,
        "stdout_key": PRE + "deploy/x/logs/build_frontend.stdout.txt", "stderr_excerpt": "TS2339",
        "test_result_ids": ["us-01"], "attempt": 1, "max_attempts": 3, "hints": ["contract unchanged"]})


def test_platform_documents():
    validate("poc_document", {"poc_id": POC, "title": "t", "owner_user_id": "u_123", "status": "spec_ready",
        "stack": {"backend": "node-express", "frontend": "react-vite", "database": "mongodb"}, "s3_prefix": PRE,
        "current_versions": {"spec": "v002"},
        "approvals": [{"stage": "spec_approved", "version": "v002", "approved_by": "u_123", "at": NOW, "implicit": False}],
        "created_at": NOW, "updated_at": NOW})
    validate("run_document", {"run_id": RUN, "poc_id": POC, "stage": "deploy", "status": "running", "requested_by": "u_123",
        "started_by_agent": "chat_agent", "current_step": "build_frontend",
        "steps": [{"name": "provision_db", "status": "succeeded", "started_at": NOW, "ended_at": NOW}],
        "repair_attempts": {"frontend": 2}, "created_at": NOW, "updated_at": NOW})
    validate("task_document", {"task_id": TASK, "run_id": RUN, "poc_id": POC, "seq": 7, "agent": "api_agent", "mode": "repair",
        "tool": "generate_backend", "status": "succeeded", "duration_ms": 48211, "created_at": NOW, "updated_at": NOW})


def test_fakes_return_valid_envelopes():
    req = Envelope.request(poc_id=POC, run_id=RUN, caller="chat_agent", agent="draft_agent", tool="draft_spec",
                           params={"transcript_key": PRE + "input/transcript.txt"})
    d = FakeDraftAgent()
    r1 = d.draft_spec(req); assert r1["response"]["status"] == "needs_clarification"
    r2 = d.draft_spec(req); assert r2["response"]["status"] == "succeeded" and len(r2["response"]["artifacts"]) == 4
    api = FakeApiAgent()
    c = api.generate_api(Envelope.request(poc_id=POC, run_id=RUN, caller="coding_orchestrator", agent="api_agent",
                                          tool="generate_api", mode="contract", params={"code_version": "v001"}))
    assert c["response"]["artifacts"][0]["kind"] == "contract"
    assert FakeCodingOrchestrator().start_code_run(req)["response"]["status"] == "started"
    assert FakeTestAgent().run_e2e(req)["response"]["result"]["passed"] == 3
