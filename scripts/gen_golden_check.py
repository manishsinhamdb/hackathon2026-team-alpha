#!/usr/bin/env python3
"""Live acceptance check for the four coding agents (this session's acceptance test).

Using the agents' pipeline functions directly with the REAL LLM (source .env first), generate
contract -> seed -> backend -> frontend from fixtures/golden/spec/v001 into a local temp dir (NOT S3),
then: validate every manifest, run guardrails.scan_bundle, run `npm install && npm run build` for the
backend and frontend, `node --check seed.js`, and grep the built frontend for every testid in the spec.
Prints a short pass/fail table. Exit code 0 iff every check passes.

Run it with an agent venv that has the shared packages + langchain + pyyaml, e.g.:
    cd agents/api-agent && set -a && . ./.env && set +a && \
      uv run python ../../scripts/gen_golden_check.py

Options:
    --keep            keep the temp working dir (printed) instead of deleting it
    --skip-npm        skip the npm install/build steps (generation + static checks only)
    --outdir DIR      generate into DIR instead of a fresh temp dir
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
GOLDEN = ROOT / "fixtures" / "golden"
AGENTS = ROOT / "agents"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


SEED = _load(AGENTS / "data-seeding-agent/src/agent_data_seeding_agent/pipeline.py", "seed_pipeline")
API = _load(AGENTS / "api-agent/src/agent_api_agent/pipeline.py", "api_pipeline")
FE = _load(AGENTS / "frontend-agent/src/agent_frontend_agent/pipeline.py", "fe_pipeline")
LLM = _load(AGENTS / "api-agent/src/agent_api_agent/llm.py", "api_llm")


class Table:
    def __init__(self):
        self.rows: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, note: str = "") -> bool:
        self.rows.append((name, ok, note))
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {note}" if note else ""))
        return ok

    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.rows)

    def render(self) -> str:
        w = max(len(n) for n, _, _ in self.rows)
        out = ["", f"{'CHECK'.ljust(w)}  RESULT  NOTE", f"{'-' * w}  ------  ----"]
        for n, ok, note in self.rows:
            out.append(f"{n.ljust(w)}  {'PASS' if ok else 'FAIL':<6}  {note}")
        return "\n".join(out)


def load_inputs() -> dict[str, Any]:
    spec_text = (GOLDEN / "spec/v001/poc_spec.md").read_text()
    fm = yaml.safe_load(spec_text.split("---", 2)[1])
    schema = json.loads((GOLDEN / "spec/v001/schema_design.json").read_text())
    qp = json.loads((GOLDEN / "spec/v001/query_patterns.json").read_text())
    return {"spec": fm, "schema_design": schema, "query_patterns": qp}


def run(cmd: list[str], cwd: Path, timeout: int = 900) -> tuple[bool, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    if p.returncode != 0:
        tail = (p.stderr or p.stdout).strip().splitlines()[-8:]
        return False, "\n      ".join(tail)
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--skip-npm", action="store_true")
    ap.add_argument("--no-gen", action="store_true", help="reuse code already in --outdir; skip the LLM generation")
    ap.add_argument("--outdir", default=None)
    args = ap.parse_args()
    if args.no_gen and not args.outdir:
        print("--no-gen requires --outdir"); return 2

    workdir = Path(args.outdir) if args.outdir else Path(tempfile.mkdtemp(prefix="gen_golden_"))
    code = workdir / "code"
    code.mkdir(parents=True, exist_ok=True)
    print(f"working dir: {workdir}")

    inputs = load_inputs()
    testids = FE.all_testids(inputs["spec"])
    t = Table()
    u1 = u2 = u3 = u4 = {}

    # --- generation (real LLM) ---
    if not args.no_gen:
        llm = LLM.build_llm(temperature=0)
        print("\nGenerating (real LLM)…")
        try:
            contract_files, u1 = API.generate(
                {"spec": inputs["spec"], "schema_design": inputs["schema_design"], "query_patterns": inputs["query_patterns"]},
                "contract", llm=llm)
            (code / "api_contract.yaml").write_text(contract_files["api_contract.yaml"])
            t.add("generate contract", True, "ok")
        except Exception as e:
            t.add("generate contract", False, str(e)[:200])
            print(t.render()); return 1
        contract_yaml = contract_files["api_contract.yaml"]

        try:
            seed_files, u2 = SEED.generate(
                {"schema_design": inputs["schema_design"], "query_patterns": inputs["query_patterns"]}, "code", llm=llm)
            SEED.write_files_to_dir(str(code / "seed"), seed_files)
            t.add("generate seed", True, "ok")
        except Exception as e:
            t.add("generate seed", False, str(e)[:200])

        try:
            backend_files, u3 = API.generate(
                {"contract_yaml": contract_yaml, "schema_design": inputs["schema_design"], "query_patterns": inputs["query_patterns"]},
                "code", llm=llm)
            API.write_files_to_dir(str(code / "backend"), backend_files)
            t.add("generate backend", True, "ok")
        except Exception as e:
            t.add("generate backend", False, str(e)[:200])

        try:
            frontend_files, u4 = FE.generate({"spec": inputs["spec"], "contract_yaml": contract_yaml}, "code", llm=llm)
            FE.write_files_to_dir(str(code / "frontend"), frontend_files)
            t.add("generate frontend", True, "ok")
        except Exception as e:
            t.add("generate frontend", False, str(e)[:200])
    else:
        print("\n--no-gen: reusing code in", code)

    # --- static validation (from disk, so --no-gen behaves identically) ---
    from poc_contracts.validate import is_valid

    def read(rel: str) -> str | None:
        p = code / rel
        return p.read_text() if p.exists() else None

    for comp in ("seed", "backend", "frontend"):
        mani = read(f"{comp}/component.manifest.json")
        ok = bool(mani) and is_valid("component_manifest", json.loads(mani))
        t.add(f"{comp} manifest valid", ok)

    contract_text = read("api_contract.yaml")
    try:
        doc = yaml.safe_load(contract_text) if contract_text else None
        ok = isinstance(doc, dict) and {"openapi", "paths", "components"} <= set(doc) and "/health" in doc.get("paths", {})
        t.add("contract parses + has health", ok)
    except Exception as e:
        t.add("contract parses + has health", False, str(e)[:120])

    from poc_shared_tools import guardrails
    scan = guardrails.scan_bundle(str(code))
    t.add("guardrail scan clean", scan["ok"], "" if scan["ok"] else json.dumps(scan["violations"])[:200])

    # --- build / check ---
    if (code / "seed" / "seed.js").exists():
        ok, note = run(["node", "--check", "seed.js"], code / "seed", timeout=60)
        t.add("node --check seed.js", ok, note)
    else:
        t.add("node --check seed.js", False, "seed.js missing")

    if not args.skip_npm:
        if (code / "backend" / "package.json").exists():
            ok, note = run(["npm", "install", "--no-audit", "--no-fund"], code / "backend")
            if ok:
                ok, note = run(["npm", "run", "build"], code / "backend")
                if ok and not (code / "backend" / "dist" / "server.js").exists():
                    ok, note = False, "dist/server.js not produced"
            t.add("backend npm install + build", ok, note)
        else:
            t.add("backend npm install + build", False, "package.json missing")

        if (code / "frontend" / "package.json").exists():
            ok, note = run(["npm", "install", "--no-audit", "--no-fund"], code / "frontend")
            if ok:
                ok, note = run(["npm", "run", "build"], code / "frontend")
                dist = code / "frontend" / "dist"
                if ok and not (dist / "index.html").exists():
                    ok, note = False, "dist/index.html not produced"
            t.add("frontend npm install + build", ok, note)

            # grep the built frontend for every testid declared in the spec
            dist = code / "frontend" / "dist"
            blob = ""
            if dist.exists():
                for f in dist.rglob("*"):
                    if f.is_file() and f.suffix in (".js", ".html", ".css"):
                        blob += f.read_text(errors="ignore")
            missing = [tid for tid in testids if tid not in blob]
            t.add("all spec testids in built frontend", not missing,
                  "" if not missing else f"missing {missing}")
        else:
            t.add("frontend npm install + build", False, "package.json missing")

    print(t.render())
    total_in = sum(u.get("input_tokens", 0) for u in (u1, u2, u3, u4))
    total_out = sum(u.get("output_tokens", 0) for u in (u1, u2, u3, u4))
    print(f"\ntokens: in={total_in} out={total_out}")

    if not args.keep and not args.outdir:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
    else:
        print(f"kept working dir: {workdir}")

    result = "PASS" if t.ok() else "FAIL"
    print(f"\nOVERALL: {result}")
    return 0 if t.ok() else 1


if __name__ == "__main__":
    sys.exit(main())
