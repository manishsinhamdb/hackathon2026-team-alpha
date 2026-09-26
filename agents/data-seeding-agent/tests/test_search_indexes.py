"""Atlas Search / Vector Search index creation owned by the seed (medicine finder, spec v001: an autocomplete
index for $search/$searchMeta and a vector index on the description embeddings for $vectorSearch)."""
import json
import os
import shutil
import subprocess

import pytest

from agent_data_seeding_agent import pipeline
from tests.test_agent import SCHEMA, FakeLLM, _valid_files

HERE = os.path.dirname(__file__)
MF_QP = json.load(open(os.path.join(HERE, "fixtures", "medicine_finder_query_patterns.json")))

SEED_WITH_INDEXES = (
    "import { MongoClient } from 'mongodb';\n" + pipeline.SEARCH_INDEX_HELPER_JS + """
const SPECS = [
  { collection: "products", name: "autocomplete_index", definition: { mappings: { dynamic: true, fields: {
      brand_name: [{ type: "string" }, { type: "autocomplete" }] } } } },
  { collection: "products", name: "vector_index", type: "vectorSearch", definition: { fields: [
      { type: "vector", path: "vector_embedding", numDimensions: 384, similarity: "cosine" },
      { type: "filter", path: "salt_composition" }] } },
];
await ensureSearchIndexes(db, SPECS);
""")


def test_requirements_from_the_medicine_finder_query_patterns():
    assert pipeline.search_index_requirements(MF_QP) == [
        {"name": "autocomplete_index", "kind": "search", "collection": "products"},
        {"name": "vector_index", "kind": "vectorSearch", "collection": "products"},
    ]


def test_no_requirements_without_search_stages():
    qp = {"patterns": [{"id": "qp-1", "collections": ["orders"], "pseudocode": "db.orders.aggregate([{ $match: {} }])"}]}
    assert pipeline.search_index_requirements(qp) == []
    assert pipeline.search_index_requirements(None) == []
    # a $search with no index name means the "default" index
    qp2 = {"patterns": [{"collections": ["docs"], "pseudocode": "{ $search: { text: { query: q, path: 'title' } } }"}]}
    assert pipeline.search_index_requirements(qp2) == [{"name": "default", "kind": "search", "collection": "docs"}]


def test_prompt_lists_the_indexes_and_carries_the_helper_verbatim():
    human = pipeline.build_messages({"schema_design": SCHEMA, "query_patterns": MF_QP})[1].content
    assert '"autocomplete_index" (search) on collection products' in human
    assert '"vector_index" (vectorSearch) on collection products' in human
    assert pipeline.SEARCH_INDEX_HELPER_JS in human
    assert "Atlas Search / Vector Search" in pipeline.build_messages({"schema_design": SCHEMA})[0].content  # the system rule
    assert "ensureSearchIndexes" not in pipeline.build_messages({"schema_design": SCHEMA})[1].content


def test_validator_requires_every_named_index():
    req = pipeline.search_index_requirements(MF_QP)
    errs = pipeline.validate_files(_valid_files(), req)
    assert any("ensureSearchIndexes" in e for e in errs)
    assert any('"vector_index"' in e for e in errs) and any('"autocomplete_index"' in e for e in errs)
    good = {**_valid_files(), "seed.js": SEED_WITH_INDEXES}
    assert pipeline.validate_files(good, req) == []
    no_type = {**good, "seed.js": SEED_WITH_INDEXES.replace('type: "vectorSearch", ', "")}
    assert any("vectorSearch" in e for e in pipeline.validate_files(no_type, req))
    old_driver = {**good, "package.json": json.dumps({"dependencies": {"mongodb": "^5.9.0"}})}
    assert any("mongodb >= 6" in e for e in pipeline.validate_files(old_driver, req))


def test_generate_rejects_a_seed_without_the_indexes_then_accepts_the_fix():
    llm = FakeLLM([json.dumps({"files": _valid_files()}), json.dumps({"files": {**_valid_files(), "seed.js": SEED_WITH_INDEXES}})])
    files, _ = pipeline.generate({"schema_design": SCHEMA, "query_patterns": MF_QP}, llm=llm)
    assert "ensureSearchIndexes(db, SPECS)" in files["seed.js"]
    assert "vector_index" in llm.calls[1][-1].content   # the rejection named the missing index


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_helper_is_valid_javascript(tmp_path):
    f = tmp_path / "helper.mjs"
    f.write_text(pipeline.SEARCH_INDEX_HELPER_JS + "\nexport { ensureSearchIndexes };\n")
    assert subprocess.run(["node", "--check", str(f)], capture_output=True).returncode == 0
