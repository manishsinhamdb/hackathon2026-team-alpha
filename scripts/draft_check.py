#!/usr/bin/env python3
"""Live acceptance check for the Draft Agent (Spec §6.2), chained into the Stage-2 generation check.

With the REAL LLM (source .env first) it runs the Draft Agent's pipeline functions directly (NOT S3):

  A) fixtures/transcripts/recsys_meeting.txt -> analyze -> generate the three spec artifacts, and assert:
       - poc_spec front matter validates (poc_spec_frontmatter)
       - 3-7 user stories, each with >= 1 testid
       - >= 2 automatable success criteria
       - schema_design validates, >= 2 collections, every collection has a seed count
       - query_patterns validates, every pattern maps to an existing user story id
  B) fixtures/transcripts/vague_meeting.txt -> analyze -> generate_questions, assert >= 2 questions
       covering success_criteria AND data_entities.
  C) Chain: feed the drafted recsys spec into the Stage-2 pipelines (contract -> seed -> backend ->
       frontend) and confirm the generated code builds (npm install + build, node --check, testids
       present in the built frontend) — the same checks gen_golden_check.py runs on the golden spec.

Run it from an agent venv that has the shared packages + langchain + pyyaml, e.g.:
    cd agents/draft-agent && set -a && . ../../.env && set +a && \
      uv run python ../../scripts/draft_check.py

Options: --skip-npm (generation + static checks only), --skip-chain (only A and B), --keep (keep temp dir).
Exit code 0 iff every check passes.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXT = ROOT / "fixtures" / "transcripts"
AGENTS = ROOT / "agents"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


DRAFT = _load(AGENTS / "draft-agent/src/agent_draft_agent/pipeline.py", "draft_pipeline")
LLM = _load(AGENTS / "draft-agent/src/agent_draft_agent/llm.py", "draft_llm")
SEED = _load(AGENTS / "data-seeding-agent/src/agent_data_seeding_agent/pipeline.py", "seed_pipeline")
API = _load(AGENTS / "api-agent/src/agent_api_agent/pipeline.py", "api_pipeline")
FE = _load(AGENTS / "frontend-agent/src/agent_frontend_agent/pipeline.py", "fe_pipeline")

from poc_contracts import new_id, validate  # noqa: E402  (after sys.path is set by the agent venv)


class Table:
    def __init__(self):
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, note: str = "") -> bool:
        self.rows.append((name, ok, note))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {note}" if note else ""))
        return ok

    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.rows)

    def render(self) -> str:
        w = max(len(n) for n, _, _ in self.rows)
        out = ["", f"{'CHECK'.ljust(w)}  RESULT  NOTE", f"{'-' * w}  ------  ----"]
        for n, ok, note in self.rows:
            out.append(f"{n.ljust(w)}  {'PASS' if ok else 'FAIL':<6}  {note}")
        return "\n".join(out)


def run(cmd: list[str], cwd: Path, timeout: int = 900) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    if p.returncode != 0:
        tail = (p.stderr or p.stdout).strip().splitlines()[-8:]
        return False, "\n      ".join(tail)
    return True, ""


def check_recsys(t: Table, llm, code: Path) -> dict[str, Any] | None:
    poc_id = new_id("poc")
    transcript = (FIXT / "recsys_meeting.txt").read_text()
    try:
        analysis, _ = DRAFT.analyze(transcript, [], llm=llm)
        t.add("recsys analyze", True, f"missing={analysis['missing']}")
    except Exception as e:
        t.add("recsys analyze", False, str(e)[:200])
        return None
    try:
        result, _ = DRAFT.generate(analysis["extraction"], poc_id, "v001", llm=llm)
        t.add("recsys generate 3 artifacts", True, "ok")
    except Exception as e:
        t.add("recsys generate 3 artifacts", False, str(e)[:200])
        return None

    files = result["files"]
    (code / "spec").mkdir(parents=True, exist_ok=True)
    for name, content in files.items():
        (code / "spec" / name).write_text(content)

    fm = yaml.safe_load(files["poc_spec.md"].split("---", 2)[1])
    t.add("front matter validates", _is_valid("poc_spec_frontmatter", fm))
    stories = fm.get("user_stories", [])
    t.add("3-7 user stories, each with >=1 testid",
          3 <= len(stories) <= 7 and all(s.get("testids") for s in stories), f"{len(stories)} stories")
    n_auto = sum(1 for c in fm.get("success_criteria", []) if c.get("automatable"))
    t.add(">=2 automatable success criteria", n_auto >= 2, f"{n_auto} automatable")

    schema = json.loads(files["schema_design.json"])
    ok_schema = _is_valid("schema_design", schema) and len(schema.get("collections", [])) >= 2 and \
        all(isinstance(c.get("seed", {}).get("count"), int) for c in schema.get("collections", []))
    t.add("schema_design valid, >=2 collections w/ seed counts", ok_schema,
          f"{len(schema.get('collections', []))} collections")

    qp = json.loads(files["query_patterns.json"])
    ids = {s["id"] for s in stories}
    ok_qp = _is_valid("query_patterns", qp) and \
        all(set(p.get("user_story_ids", [])) <= ids for p in qp.get("patterns", []))
    t.add("query_patterns valid + all mapped to a user story", ok_qp,
          f"{len(qp.get('patterns', []))} patterns")

    # inputs for the Stage-2 chain
    return {"spec": fm, "schema_design": schema, "query_patterns": qp}


def check_vague(t: Table, llm) -> None:
    transcript = (FIXT / "vague_meeting.txt").read_text()
    try:
        analysis, _ = DRAFT.analyze(transcript, [], llm=llm)
    except Exception as e:
        t.add("vague analyze", False, str(e)[:200])
        return
    missing = analysis["missing"]
    t.add("vague analyze finds missing fields", bool(missing), f"missing={missing}")
    try:
        questions, _ = DRAFT.generate_questions(analysis["extraction"], missing, round_no=1, llm=llm)
    except Exception as e:
        t.add("vague generate_questions", False, str(e)[:200])
        return
    fields = {q["field"] for q in questions}
    t.add("vague returns >=2 questions", len(questions) >= 2, f"{len(questions)} questions")
    t.add("questions cover success_criteria + data_entities",
          {"success_criteria", "data_entities"} <= fields, f"fields={sorted(fields)}")


def chain_stage2(t: Table, inputs: dict[str, Any], llm, code: Path, skip_npm: bool) -> None:
    """The same generation + build checks gen_golden_check.py runs, on the DRAFTED spec."""
    try:
        contract_files, _ = API.generate(
            {"spec": inputs["spec"], "schema_design": inputs["schema_design"], "query_patterns": inputs["query_patterns"]},
            "contract", llm=llm)
        (code / "api_contract.yaml").write_text(contract_files["api_contract.yaml"])
        t.add("chain: generate contract", True)
    except Exception as e:
        t.add("chain: generate contract", False, str(e)[:200])
        return
    contract_yaml = contract_files["api_contract.yaml"]

    for label, fn in (
        ("seed", lambda: SEED.generate({"schema_design": inputs["schema_design"],
                                        "query_patterns": inputs["query_patterns"]}, "code", llm=llm)),
        ("backend", lambda: API.generate({"contract_yaml": contract_yaml, "schema_design": inputs["schema_design"],
                                          "query_patterns": inputs["query_patterns"]}, "code", llm=llm)),
        ("frontend", lambda: FE.generate({"spec": inputs["spec"], "contract_yaml": contract_yaml}, "code", llm=llm)),
    ):
        try:
            files, _ = fn()
            {"seed": SEED, "backend": API, "frontend": FE}[label].write_files_to_dir(str(code / label), files)
            t.add(f"chain: generate {label}", True)
        except Exception as e:
            t.add(f"chain: generate {label}", False, str(e)[:200])

    from poc_shared_tools import guardrails
    scan = guardrails.scan_bundle(str(code))
    t.add("chain: guardrail scan clean", scan["ok"], "" if scan["ok"] else json.dumps(scan["violations"])[:200])

    if (code / "seed" / "seed.js").exists():
        ok, note = run(["node", "--check", "seed.js"], code / "seed", timeout=60)
        t.add("chain: node --check seed.js", ok, note)

    if skip_npm:
        return
    if (code / "backend" / "package.json").exists():
        ok, note = run(["npm", "install", "--no-audit", "--no-fund"], code / "backend")
        if ok:
            ok, note = run(["npm", "run", "build"], code / "backend")
            if ok and not (code / "backend" / "dist" / "server.js").exists():
                ok, note = False, "dist/server.js not produced"
        t.add("chain: backend npm install + build", ok, note)
    if (code / "frontend" / "package.json").exists():
        ok, note = run(["npm", "install", "--no-audit", "--no-fund"], code / "frontend")
        if ok:
            ok, note = run(["npm", "run", "build"], code / "frontend")
            if ok and not (code / "frontend" / "dist" / "index.html").exists():
                ok, note = False, "dist/index.html not produced"
        t.add("chain: frontend npm install + build", ok, note)
        dist = code / "frontend" / "dist"
        blob = ""
        if dist.exists():
            for f in dist.rglob("*"):
                if f.is_file() and f.suffix in (".js", ".html", ".css"):
                    blob += f.read_text(errors="ignore")
        missing = [tid for tid in FE.all_testids(inputs["spec"]) if tid not in blob]
        t.add("chain: all spec testids in built frontend", not missing, "" if not missing else f"missing {missing}")


def _is_valid(kind: str, obj: Any) -> bool:
    try:
        validate(kind, obj)
        return True
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-npm", action="store_true")
    ap.add_argument("--skip-chain", action="store_true")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="draft_check_"))
    code = workdir / "code"
    code.mkdir(parents=True, exist_ok=True)
    print(f"working dir: {workdir}")

    llm = LLM.build_llm(temperature=0)
    t = Table()

    print("\nA) recsys_meeting.txt -> draft")
    inputs = check_recsys(t, llm, code)

    print("\nB) vague_meeting.txt -> clarifying questions")
    check_vague(t, llm)

    if not args.skip_chain and inputs is not None:
        print("\nC) chain drafted spec -> Stage-2 code (build)")
        chain_stage2(t, inputs, llm, code, args.skip_npm)

    print(t.render())
    if not args.keep:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        print(f"\nkept working dir: {workdir}")
    print(f"\nOVERALL: {'PASS' if t.ok() else 'FAIL'}")
    return 0 if t.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
