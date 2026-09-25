"""Draft Agent — pipeline (fake LLM) and graph (fake SDK) tests. No cloud, no real LLM."""
import json

import pytest
from langchain_core.messages import HumanMessage

from poc_contracts import Envelope, new_id, validate

import agent_draft_agent.main as m
from agent_draft_agent import pipeline, rag

POC, TASK = new_id("poc"), new_id("task")


class FakeResp:
    def __init__(self, content):
        self.content = content
        self.usage_metadata = {"input_tokens": 5, "output_tokens": 6}


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        return FakeResp(self.outputs.pop(0))


# --- fixtures for a valid artifacts object -----------------------------------

def _good_obj():
    return {
        "title": "Test recommendations POC",
        "user_stories": [
            {"id": "us-01", "title": "A", "actor": "user", "acceptance": ["x"], "testids": ["us-01-a"]},
            {"id": "us-02", "title": "B", "actor": "user", "acceptance": ["y"], "testids": ["us-02-b"]},
            {"id": "us-03", "title": "C", "actor": "manager", "acceptance": ["z"], "testids": ["us-03-c"]},
        ],
        "success_criteria": [
            {"id": "sc-01", "statement": "p95 < 300ms", "automatable": True},
            {"id": "sc-02", "statement": ">=5 results", "automatable": True},
            {"id": "sc-03", "statement": "founders happy", "automatable": False},
        ],
        "seed_requirements": {"max_docs_per_collection": 9000, "realism": "synthetic en-IN"},
        "assumptions": ["no auth in POC", "synthetic data"],
        "schema_design": {"collections": [
            {"name": "products", "description": "catalog",
             "fields": [{"name": "sku", "type": "string", "required": True}],
             "indexes": [{"keys": {"sku": 1}, "unique": True}],
             "seed": {"count": 5000, "generator_hints": "brands"}},
            {"name": "orders", "description": "orders",
             "fields": [{"name": "ordered_at", "type": "date", "required": True}],
             "seed": {"count": 20000, "generator_hints": "clusters"}},
        ]},
        "query_patterns": {"patterns": [
            {"id": "qp-01", "user_story_ids": ["us-01"], "name": "recs", "description": "d",
             "collections": ["orders", "products"], "pseudocode": "p"},
            {"id": "qp-02", "user_story_ids": ["us-02"], "name": "browse", "description": "d",
             "collections": ["products"], "pseudocode": "p"},
            {"id": "qp-03", "user_story_ids": ["us-03"], "name": "top", "description": "d",
             "collections": ["orders"], "pseudocode": "p"},
        ]},
        "sections": {"executive_summary": "A recommendations POC.", "business_objective": "Lift basket."},
        "poc_definitions": {"poc_summary": "s", "poc_goal": "g", "poc_success_criteria": "sc",
                            "poc_domain": "ecommerce", "poc_key_entities": "products, orders",
                            "poc_user_stories": "us-01..us-03"},
    }


# =============================================================================
# analyze
# =============================================================================

def test_analyze_extracts_and_filters_missing():
    out = {"extraction": {"title": "T"}, "missing": ["success_criteria", "data_entities", "bogus_field"]}
    llm = FakeLLM([json.dumps(out)])
    result, usage = pipeline.analyze("some transcript", [], llm=llm)
    assert result["extraction"]["title"] == "T"
    assert result["missing"] == ["success_criteria", "data_entities"]  # bogus filtered out
    assert usage["input_tokens"] == 5


def test_analyze_retries_on_bad_json_then_succeeds():
    good = {"extraction": {"title": "T"}, "missing": []}
    llm = FakeLLM(["not json at all", json.dumps(good)])
    result, _ = pipeline.analyze("t", [], llm=llm)
    assert result["missing"] == []
    assert len(llm.calls) == 2


def test_analyze_folds_prior_answers_into_prompt():
    rounds = [{"round": 1, "questions": [{"question_id": "q1-1", "field": "success_criteria",
                                          "question": "target?", "answer": "300ms p95"}]}]
    llm = FakeLLM([json.dumps({"extraction": {}, "missing": []})])
    pipeline.analyze("t", rounds, llm=llm)
    human = llm.calls[0][1].content
    assert "300ms p95" in human


# =============================================================================
# questions
# =============================================================================

def test_generate_questions_caps_at_5_and_ids_and_covers_fields():
    missing = ["title", "business_objective", "target_users", "user_stories",
               "success_criteria", "data_entities", "timeline_constraints"]
    llm = FakeLLM([json.dumps({"questions": []})])  # LLM adds nothing → template fallback per field
    questions, _ = pipeline.generate_questions({"title": ""}, missing, round_no=1, llm=llm)
    assert len(questions) == 5
    assert all(q["question_id"] == f"q1-{i}" for i, q in enumerate(questions, 1))
    fields = {q["field"] for q in questions}
    # first-5 of the ordered missing list → success_criteria is 5th, must be present
    assert "success_criteria" in fields


def test_generate_questions_covers_success_criteria_and_data_entities_for_vague():
    missing = ["success_criteria", "data_entities"]
    llm = FakeLLM([json.dumps({"questions": [
        {"field": "success_criteria", "question": "What target?", "why_it_matters": "tests", "suggestions": ["300ms"]},
        {"field": "data_entities", "question": "What data?", "why_it_matters": "schema", "suggestions": ["pings"]},
    ]})])
    questions, _ = pipeline.generate_questions({}, missing, round_no=2, llm=llm)
    assert len(questions) == 2
    assert {q["field"] for q in questions} == {"success_criteria", "data_entities"}
    assert questions[0]["question_id"] == "q2-1"


def test_generate_questions_fallback_when_llm_garbage():
    missing = ["success_criteria", "data_entities"]
    llm = FakeLLM(["garbage not json"])
    questions, _ = pipeline.generate_questions({}, missing, round_no=1, llm=llm)
    assert {q["field"] for q in questions} == {"success_criteria", "data_entities"}
    assert all(q["question"] and q["suggestions"] for q in questions)


# =============================================================================
# artifacts
# =============================================================================

def test_generate_artifacts_valid_and_deterministic_fields():
    llm = FakeLLM([json.dumps(_good_obj())])
    result, _ = pipeline.generate({"title": "T"}, POC, "v001", llm=llm)
    files = result["files"]
    assert set(files) == {"poc_spec.md", "schema_design.json", "query_patterns.json"}
    # front matter validates
    fm = __import__("yaml").safe_load(files["poc_spec.md"].split("---", 2)[1])
    validate("poc_spec_frontmatter", fm)
    assert fm["poc_id"] == POC and fm["spec_version"] == "v001" and fm["approved"] is False
    schema = json.loads(files["schema_design.json"])
    validate("schema_design", schema)
    assert schema["database_name"] == POC
    assert schema["seed_requirements"]["deterministic_seed"] == 42
    assert schema["seed_requirements"]["max_docs_per_collection"] == 9000
    # the 20000 order count is clamped to the cap
    assert all(c["seed"]["count"] <= 9000 for c in schema["collections"])
    qp = json.loads(files["query_patterns.json"])
    validate("query_patterns", qp)
    assert result["user_story_count"] == 3
    # every pattern maps to an existing story
    ids = {s["id"] for s in fm["user_stories"]}
    assert all(set(p["user_story_ids"]) <= ids for p in qp["patterns"])


def test_generate_retries_once_on_semantic_error():
    bad = _good_obj()
    bad["success_criteria"] = [{"id": "sc-01", "statement": "x", "automatable": True}]  # only 1 automatable
    llm = FakeLLM([json.dumps(bad), json.dumps(_good_obj())])
    result, _ = pipeline.generate({}, POC, "v001", llm=llm)
    assert result["user_story_count"] == 3
    assert len(llm.calls) == 2


def test_generate_raises_on_connection_string_leak():
    leak = _good_obj()
    leak["sections"] = {"executive_summary": "uses mongodb+srv://user:pw@host/db"}
    llm = FakeLLM([json.dumps(leak), json.dumps(leak)])
    with pytest.raises(pipeline.LLMOutputInvalid):
        pipeline.generate({}, POC, "v001", llm=llm)


def test_generate_drops_malformed_aggregation_sketch():
    """LLM sometimes emits aggregation_sketch as strings; the optional field is dropped, not fatal."""
    obj = _good_obj()
    obj["query_patterns"]["patterns"][0]["aggregation_sketch"] = ["$match: { x: 1 }", "$unwind: '$items'"]
    obj["query_patterns"]["patterns"][1]["supporting_indexes"] = ["items.sku_1"]  # wrong shape too
    llm = FakeLLM([json.dumps(obj)])
    result, _ = pipeline.generate({}, POC, "v001", llm=llm)
    qp = json.loads(result["files"]["query_patterns.json"])
    validate("query_patterns", qp)  # must still validate
    assert "aggregation_sketch" not in qp["patterns"][0]
    assert "supporting_indexes" not in qp["patterns"][1]


def test_chunk_spec_by_section():
    llm = FakeLLM([json.dumps(_good_obj())])
    result, _ = pipeline.generate({}, POC, "v001", llm=llm)
    chunks = pipeline.chunk_spec_by_section(result["files"]["poc_spec.md"])
    sections = {c["section"] for c in chunks}
    assert "Executive summary" in sections
    assert all(c["text"] for c in chunks)


# =============================================================================
# rag
# =============================================================================

def test_hash_embedder_deterministic_unit_norm():
    e = rag.HashEmbedder()
    v1 = e.embed("hello")
    v2 = e.embed("hello")
    assert v1 == v2 and len(v1) == rag.EMBED_DIM
    assert abs(sum(x * x for x in v1) ** 0.5 - 1.0) < 1e-6
    assert e.embed("hello") != e.embed("world")


# =============================================================================
# graph
# =============================================================================

def _request(params: dict):
    return Envelope.request(poc_id=POC, run_id=new_id("run"), caller="chat_agent", agent="draft_agent",
                            tool="draft_spec", params=params, task_id=TASK)


def _invoke(params: dict):
    graph = m.build_agent()
    env = _request(params)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                       config={"configurable": {"thread_id": "t"}})
    return validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]


@pytest.fixture
def fake_meta(monkeypatch):
    """Fake the platform-DB run/POC calls the graph makes (start_run, finish_run, finalize side effects).
    Returns a log capturing the run lifecycle so tests can assert registration + finish."""
    import poc_shared_tools.metadata as metadata
    log: dict[str, list] = {"created": [], "finished": []}

    def fake_create_run(poc_id, stage, requested_by, started_by_agent, inputs=None, trace_id=None):
        log["created"].append({"poc_id": poc_id, "stage": stage, "inputs": inputs, "by": requested_by})
        return {"run_id": "run_test", "poc_id": poc_id, "stage": stage, "status": "queued"}

    def fake_finish_run(run_id, status, outputs=None, error=None):
        log["finished"].append({"run_id": run_id, "status": status, "outputs": outputs, "error": error})
        return {"run_id": run_id, "status": status}

    monkeypatch.setattr(metadata, "create_run", fake_create_run)
    monkeypatch.setattr(metadata, "set_run_status", lambda *a, **k: None)
    monkeypatch.setattr(metadata, "finish_run", fake_finish_run)
    monkeypatch.setattr(metadata, "set_current_version", lambda *a, **k: None)
    monkeypatch.setattr(metadata, "update_poc_status", lambda *a, **k: None)
    monkeypatch.setattr(metadata, "get_poc", lambda poc_id: {"owner_user_id": "u_test"})
    return log


@pytest.fixture
def fake_io(monkeypatch, fake_meta):
    """Fake S3 + metadata + rag so the Tool Pod tools run without cloud.
    Returns {"puts": [(key, body)], "meta": <run lifecycle log>}."""
    import poc_shared_tools.s3 as s3
    from poc_shared_tools.errors import ToolError

    puts: list[tuple[str, str]] = []

    def fake_get_text(key):
        if key.endswith("transcript.txt"):
            return "A meeting transcript about a recommendations POC."
        if "pending" in key:
            raise ToolError("S3_NOT_FOUND", "no pending file")
        if key.endswith("poc_spec.md"):
            return "---\ntitle: t\n---\n\n# Executive summary\nHello.\n"
        return "{}"

    def fake_put_object(poc_id, run_id, key, body, content_type, producer):
        puts.append((key, body))
        return {"key": key, "sha256": "x", "size": len(body)}

    monkeypatch.setattr(s3, "get_text", fake_get_text)
    monkeypatch.setattr(s3, "put_object", fake_put_object)
    monkeypatch.setattr(s3, "next_version", lambda poc_id, kind: "v001")
    monkeypatch.setattr(rag, "upsert_spec_embeddings", lambda *a, **k: 3)
    return {"puts": puts, "meta": fake_meta}


def test_graph_needs_clarification(fake_io, monkeypatch):
    monkeypatch.setattr(pipeline, "analyze",
                        lambda *a, **k: ({"extraction": {"title": "t"},
                                          "missing": ["success_criteria", "data_entities"]},
                                         {"input_tokens": 1, "output_tokens": 1}))
    monkeypatch.setattr(pipeline, "generate_questions",
                        lambda *a, **k: ([
                            {"question_id": "q1-1", "field": "success_criteria", "question": "target?",
                             "why_it_matters": "tests", "suggestions": ["300ms"]},
                            {"question_id": "q1-2", "field": "data_entities", "question": "data?",
                             "why_it_matters": "schema", "suggestions": ["pings"]},
                        ], {"input_tokens": 1, "output_tokens": 1}))
    resp = _invoke({"poc_id": POC})
    assert resp["status"] == "needs_clarification", resp
    qs = resp["result"]["questions"]
    assert len(qs) == 2 and len(qs) <= 5
    fields = {q["field"] for q in qs}
    assert {"success_criteria", "data_entities"} <= fields
    # persisted to the pending clarifications file
    pending = [(k, b) for k, b in fake_io["puts"] if "pending" in k]
    assert pending, "questions must be persisted"
    doc = json.loads(pending[-1][1])
    assert doc["rounds"][-1]["round"] == 1


def test_graph_needs_clarification_finishes_run_with_questions(fake_io, monkeypatch):
    """The draft run is registered at start and finished `succeeded` carrying the questions in outputs, so
    the caller's 'how's it going?' can surface them."""
    monkeypatch.setattr(pipeline, "analyze",
                        lambda *a, **k: ({"extraction": {"title": "t"},
                                          "missing": ["success_criteria", "data_entities"]},
                                         {"input_tokens": 1, "output_tokens": 1}))
    monkeypatch.setattr(pipeline, "generate_questions",
                        lambda *a, **k: ([
                            {"question_id": "q1-1", "field": "success_criteria", "question": "target?",
                             "why_it_matters": "tests", "suggestions": ["300ms"]},
                        ], {"input_tokens": 1, "output_tokens": 1}))
    _invoke({"poc_id": POC})
    meta = fake_io["meta"]
    assert len(meta["created"]) == 1 and meta["created"][0]["stage"] == "draft"
    assert len(meta["finished"]) == 1
    fin = meta["finished"][0]
    assert fin["run_id"] == "run_test" and fin["status"] == "succeeded"
    assert fin["outputs"]["needs_clarification"] is True
    assert fin["outputs"]["questions"][0]["question_id"] == "q1-1"
    assert fin["outputs"]["round"] == 1


def test_graph_force_assumptions_drafts_despite_missing(fake_io, monkeypatch):
    monkeypatch.setattr(pipeline, "analyze",
                        lambda *a, **k: ({"extraction": {}, "missing": ["success_criteria"]},
                                         {"input_tokens": 1, "output_tokens": 1}))
    monkeypatch.setattr(pipeline, "generate", lambda *a, **k: (_gen_result(), {"input_tokens": 2, "output_tokens": 2}))
    resp = _invoke({"poc_id": POC, "force_assumptions": True})
    assert resp["status"] == "succeeded", resp
    assert len(resp["artifacts"]) == 4


def test_graph_drafted_writes_four_artifacts(fake_io, monkeypatch):
    monkeypatch.setattr(pipeline, "analyze",
                        lambda *a, **k: ({"extraction": {}, "missing": []}, {"input_tokens": 1, "output_tokens": 1}))
    monkeypatch.setattr(pipeline, "generate", lambda *a, **k: (_gen_result(), {"input_tokens": 2, "output_tokens": 2}))
    resp = _invoke({"poc_id": POC})
    assert resp["status"] == "succeeded", resp
    kinds = [a["kind"] for a in resp["artifacts"]]
    assert kinds == ["spec", "schema", "query_patterns", "clarifications"]
    assert resp["result"]["spec_version"] == "v001"
    assert resp["result"]["user_story_count"] == 3
    # the four spec files were written
    written = [k for k, _ in fake_io["puts"] if "/spec/v001/" in k]
    assert any(k.endswith("poc_spec.md") for k in written)
    assert any(k.endswith("clarifications.json") for k in written)


def test_graph_drafted_registers_and_finishes_run_succeeded(fake_io, monkeypatch):
    """A drafted spec registers a draft run at start and finishes it `succeeded` with the spec_version."""
    monkeypatch.setattr(pipeline, "analyze",
                        lambda *a, **k: ({"extraction": {}, "missing": []}, {"input_tokens": 1, "output_tokens": 1}))
    monkeypatch.setattr(pipeline, "generate", lambda *a, **k: (_gen_result(), {"input_tokens": 2, "output_tokens": 2}))
    _invoke({"poc_id": POC})
    meta = fake_io["meta"]
    assert len(meta["created"]) == 1
    created = meta["created"][0]
    assert created["stage"] == "draft" and created["poc_id"] == POC
    assert len(meta["finished"]) == 1
    fin = meta["finished"][0]
    assert fin["status"] == "succeeded" and fin["outputs"]["spec_version"] == "v001"
    assert any("/spec/v001/" in k for k, _ in fake_io["puts"])


def test_graph_invalid_transcript(fake_meta, monkeypatch):
    import poc_shared_tools.s3 as s3
    from poc_shared_tools.errors import ToolError

    def raise_nf(key):
        raise ToolError("S3_NOT_FOUND", f"no object at {key}")

    monkeypatch.setattr(s3, "get_text", raise_nf)
    resp = _invoke({"poc_id": POC})
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "INVALID_TRANSCRIPT"
    # the run was registered at start, then finished failed with the same error
    assert len(fake_meta["created"]) == 1
    assert len(fake_meta["finished"]) == 1
    fin = fake_meta["finished"][0]
    assert fin["status"] == "failed" and fin["error"]["code"] == "INVALID_TRANSCRIPT"


def test_graph_invalid_envelope():
    graph = m.build_agent()
    out = graph.invoke({"messages": [HumanMessage(content="not json")]}, config={"configurable": {"thread_id": "t2"}})
    resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]
    assert resp["status"] == "failed" and resp["error"]["code"] == "INVALID_ENVELOPE"


def test_graph_bad_tool():
    graph = m.build_agent()
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="chat_agent", agent="draft_agent",
                           tool="nope", params={}, task_id=TASK)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]}, config={"configurable": {"thread_id": "t3"}})
    resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]
    assert resp["status"] == "failed" and resp["error"]["code"] == "BAD_TOOL"


def _gen_result():
    """A fake pipeline.generate() return value (already-validated artifacts)."""
    return {
        "files": {
            "poc_spec.md": "---\ntitle: t\n---\n\n# Executive summary\nHi.\n",
            "schema_design.json": json.dumps({"database_name": POC, "collections": []}),
            "query_patterns.json": json.dumps({"patterns": []}),
        },
        "assumptions": ["no auth"],
        "user_story_count": 3,
        "poc_definitions": {"poc_summary": "s"},
        "front_matter": {"user_stories": [1, 2, 3]},
    }
