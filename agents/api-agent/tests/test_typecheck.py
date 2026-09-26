"""The backend compile gate and the Atlas Search pipeline-typing rule (medicine-finder deploy run
run_01M3E9EC8DJ3JEJ0V93VXM90JX failed build_backend on TS2769: a $vectorSearch pipeline passed to mongoose's
Model.aggregate(), whose PipelineStage[] type cannot describe it). No npm, no LLM — both are injected."""
import json
import os

import pytest

from agent_api_agent import pipeline, typecheck as tc
from tests.test_agent import CONTRACT_YAML, SCHEMA, FakeLLM, _backend_files

# The exact compiler output from the failed deploy (trimmed to the first diagnostic).
TS2769_VECTOR = (
    "src/routes.ts(355,47): error TS2769: No overload matches this call.\n"
    "  Overload 1 of 2, '(pipeline?: PipelineStage[] | undefined, options?: AggregateOptions | undefined): "
    "Aggregate<any[]>', gave the following error.\n"
    "        Property '$vectorSearch' is missing in type 'Record<string, unknown>' but required in type 'VectorSearch'.\n"
)

BAD_ROUTES = """import { Router } from "express";
import { ProductModel } from "./models.js";
export const api = Router();
api.get("/alt", async (_req, res) => {
  const pipeline = [{ $vectorSearch: { index: "vector_index", path: "v", queryVector: [0.1], numCandidates: 10, limit: 5 } }, { $sort: { price: 1 } }];
  res.json(await ProductModel.aggregate(pipeline));
});
"""

GOOD_ROUTES = """import { Router } from "express";
import type { Document } from "mongodb";
import { ProductModel } from "./models.js";
export const api = Router();
api.get("/alt", async (_req, res) => {
  const pipeline: Document[] = [{ $vectorSearch: { index: "vector_index", path: "v", queryVector: [0.1], numCandidates: 10, limit: 5 } }, { $sort: { price: 1 } }];
  res.json(await ProductModel.collection.aggregate(pipeline).toArray());
});
"""


def _with_mongodb_dep(files):
    pkg = json.loads(files["package.json"])
    pkg.setdefault("dependencies", {})["mongodb"] = "^6.9.0"
    return {**files, "package.json": json.dumps(pkg)}


# --- the rule in the generator prompt and the repair prompt ------------------

def test_backend_system_prompt_carries_the_pipeline_typing_rule():
    sys_msg = pipeline.build_messages({"contract_yaml": CONTRACT_YAML, "schema_design": SCHEMA}, "code")[0].content
    assert 'import type { Document } from "mongodb"' in sys_msg
    assert "const pipeline: Document[]" in sys_msg
    assert "Model.collection.aggregate(pipeline).toArray()" in sys_msg or ".collection.aggregate(pipeline).toArray()" in sys_msg
    assert "tsc --noEmit" in sys_msg
    assert "PipelineStage[]" in sys_msg   # named as the thing NOT to use


def test_known_fix_hint_matches_the_real_deploy_error_only():
    assert tc.known_fix_hints(TS2769_VECTOR) == [tc.ATLAS_SEARCH_PIPELINE_HINT]
    assert tc.known_fix_hints("src/a.ts(1,1): error TS2769: No overload matches this call. foo(bar)") == []
    assert tc.known_fix_hints("src/a.ts(3,9): error TS2322: $search stage in PipelineStage") != []
    assert tc.known_fix_hints("") == []


def test_repair_prompt_includes_the_known_fix_for_a_ts2769_failure():
    failure = {"step": "build_backend", "component": "backend", "stdout_tail": TS2769_VECTOR}
    human = pipeline.build_messages({"contract_yaml": CONTRACT_YAML, "schema_design": SCHEMA}, "repair",
                                    failure=failure, previous_source={"src/routes.ts": BAD_ROUTES})[1].content
    assert "Known fix for this failure" in human
    assert "Document[]" in human
    # an unrelated failure gets no hint
    human2 = pipeline.build_messages({"contract_yaml": CONTRACT_YAML}, "repair",
                                     failure={"step": "start_backend", "stdout_tail": "EADDRINUSE"})[1].content
    assert "Known fix" not in human2


# --- the rule checked statically in the generator output ---------------------

def test_lint_rejects_atlas_search_pipeline_without_driver_document_type():
    files = {**_backend_files(), "src/routes.ts": BAD_ROUTES}
    errs = pipeline.validate_backend(files)
    assert any("Document" in e and "src/routes.ts" in e for e in errs)
    assert any('"mongodb"' in e for e in errs)


def test_lint_rejects_pipelinestage_annotation_on_search_pipeline():
    src = GOOD_ROUTES.replace("import type { Document }", "import type { Document } from \"mongodb\";\nimport { PipelineStage }")
    src = src.replace('from "mongodb";\nimport { PipelineStage } from "mongodb"', 'from "mongodb";\nimport { PipelineStage } from "mongoose"')
    errs = pipeline.lint_atlas_search_typing(_with_mongodb_dep({**_backend_files(), "src/routes.ts": src}))
    assert any("PipelineStage" in e for e in errs)


def test_lint_accepts_document_typed_pipeline_and_ignores_plain_pipelines():
    assert pipeline.lint_atlas_search_typing(_with_mongodb_dep({**_backend_files(), "src/routes.ts": GOOD_ROUTES})) == []
    plain = {**_backend_files(), "src/routes.ts": 'const p = [{ $match: { a: 1 } }]; // no search stage\n'}
    assert pipeline.lint_atlas_search_typing(plain) == []
    assert pipeline.validate_backend(_with_mongodb_dep({**_backend_files(), "src/routes.ts": GOOD_ROUTES})) == []


# --- the compile gate inside generate() --------------------------------------

def test_generate_feeds_tsc_errors_back_and_merges_only_changed_files():
    first = _with_mongodb_dep({**_backend_files(), "src/routes.ts": GOOD_ROUTES})
    fixed_routes = GOOD_ROUTES + "// fixed\n"
    llm = FakeLLM([json.dumps({"files": first}), json.dumps({"files": {"src/routes.ts": fixed_routes}})])
    seen = []

    def fake_check(files):
        seen.append(files["src/routes.ts"])
        if len(seen) == 1:
            return {"status": "failed", "errors": TS2769_VECTOR.splitlines()[:1] + [
                "src/routes.ts(5,9): error TS2322: $vectorSearch is not assignable to PipelineStage"], "duration_s": 1}
        return {"status": "passed", "errors": [], "duration_s": 1}

    report = {}
    files, usage = pipeline.generate({"contract_yaml": CONTRACT_YAML, "schema_design": SCHEMA}, "code", llm=llm,
                                     typecheck=fake_check, report=report)
    assert files["src/routes.ts"] == fixed_routes           # the fix-up turn's file
    assert files["src/server.ts"] == first["src/server.ts"]  # unchanged files kept from the first turn
    assert report == {"attempts": 2, "typecheck": {"status": "passed", "errors": [], "duration_s": 1}}
    feedback = llm.calls[1][-1].content
    assert "does not compile" in feedback and "TS2322" in feedback
    assert "KNOWN FIX" in feedback and "Document[]" in feedback
    assert "ONLY the files you change" in feedback
    assert usage["input_tokens"] == 10


def test_generate_accepts_when_typecheck_is_skipped():
    llm = FakeLLM([json.dumps({"files": _backend_files()})])
    report = {}
    pipeline.generate({"contract_yaml": CONTRACT_YAML}, "code", llm=llm,
                      typecheck=lambda f: {"status": "skipped", "errors": [], "duration_s": 0}, report=report)
    assert report["typecheck"]["status"] == "skipped" and report["attempts"] == 1


def test_generate_stops_at_the_budget():
    llm = FakeLLM([json.dumps({"files": _backend_files()})] * 3)
    fail = lambda f: {"status": "failed", "errors": ["src/routes.ts(1,1): error TS1005: ';' expected."], "duration_s": 0}
    with pytest.raises(pipeline.LLMOutputInvalid) as e:
        pipeline.generate({"contract_yaml": CONTRACT_YAML}, "code", llm=llm, typecheck=fail, budget_s=-1)
    assert len(llm.calls) == 1
    assert any("budget" in x for x in e.value.errors) and any("TS1005" in x for x in e.value.errors)


def test_contract_mode_never_typechecks():
    llm = FakeLLM([json.dumps({"files": {"api_contract.yaml": CONTRACT_YAML}})])
    boom = lambda f: (_ for _ in ()).throw(AssertionError("typecheck must not run for contracts"))
    pipeline.generate({"schema_design": SCHEMA}, "contract", llm=llm, typecheck=boom)


# --- typecheck_backend with a fake npm/tsc runner ----------------------------

class FakeRunner:
    def __init__(self, install=(0, ""), tsc=(0, "")):
        self.install, self.tsc, self.calls = install, tsc, []

    def __call__(self, cmd, cwd, env, timeout):
        self.calls.append(cmd)
        if cmd[0] == "npm":
            if self.install[0] == 0:
                os.makedirs(os.path.join(cwd, "node_modules"), exist_ok=True)
            return self.install
        assert cmd[-3:] == ["--noEmit", "-p", "tsconfig.json"]
        assert os.path.exists(os.path.join(cwd, "src", "routes.ts"))
        return self.tsc


def test_typecheck_parses_tsc_diagnostics(tmp_path):
    r = FakeRunner(tsc=(2, TS2769_VECTOR))
    res = tc.typecheck_backend(_backend_files(), cache_root=str(tmp_path), runner=r)
    assert res["status"] == "failed"
    assert res["errors"] == ["src/routes.ts(355,47): error TS2769: No overload matches this call."]
    assert tc.known_fix_hints(res["output"])


def test_typecheck_caches_node_modules_per_dependency_set(tmp_path):
    r = FakeRunner()
    assert tc.typecheck_backend(_backend_files(), cache_root=str(tmp_path), runner=r)["status"] == "passed"
    assert tc.typecheck_backend(_backend_files(), cache_root=str(tmp_path), runner=r)["status"] == "passed"
    assert [c[0] for c in r.calls].count("npm") == 1   # second check reused the install
    tc.typecheck_backend(_with_mongodb_dep(_backend_files()), cache_root=str(tmp_path), runner=r)
    assert [c[0] for c in r.calls].count("npm") == 2   # a new dependency set installs again


def test_typecheck_npm_code_fault_fails_but_network_fault_skips(tmp_path):
    bad = FakeRunner(install=(1, "npm ERR! code ETARGET\nnpm ERR! notarget No matching version for mongoose@^99"))
    res = tc.typecheck_backend(_backend_files(), cache_root=str(tmp_path / "a"), runner=bad)
    assert res["status"] == "failed" and "ETARGET" in res["errors"][0]
    net = FakeRunner(install=(1, "npm ERR! code ECONNREFUSED"))
    assert tc.typecheck_backend(_backend_files(), cache_root=str(tmp_path / "b"), runner=net)["status"] == "skipped"


def test_typecheck_requires_package_and_tsconfig(tmp_path):
    files = {k: v for k, v in _backend_files().items() if k != "tsconfig.json"}
    assert tc.typecheck_backend(files, cache_root=str(tmp_path), runner=FakeRunner())["status"] == "failed"


# --- opt-in: the real compiler on the real failure (needs Node + npm registry) --

@pytest.mark.skipif(os.environ.get("API_TSC_IT") != "1" or not tc.node_toolchain(),
                    reason="set API_TSC_IT=1 to run npm install + tsc for real")
def test_real_tsc_rejects_model_aggregate_and_accepts_document_pipeline(tmp_path):
    base = {**_backend_files(),
            "tsconfig.json": pipeline.GOLDEN_TSCONFIG, "package.json": pipeline.GOLDEN_BACKEND_PKG,
            "src/server.ts": "export {};\n",
            "src/models.ts": 'import mongoose, { Schema } from "mongoose";\n'
                             'export const ProductModel = mongoose.model("Product", new Schema({ price: Number, v: [Number] }));\n'}
    bad = tc.typecheck_backend({**base, "src/routes.ts": BAD_ROUTES}, cache_root=str(tmp_path))
    assert bad["status"] == "failed" and any("TS2769" in e for e in bad["errors"]), bad
    assert tc.known_fix_hints(bad["output"])
    good = tc.typecheck_backend(_with_mongodb_dep({**base, "src/routes.ts": GOOD_ROUTES}), cache_root=str(tmp_path))
    assert good["status"] == "passed", good
