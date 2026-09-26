"""The API Agent's own compile gate: `npm install` + `tsc --noEmit` on the generated backend, so a type error is
caught at code time (and fed back to the model) instead of at deploy time on EC2.

Node comes from the `nodejs-wheel-binaries` wheel (bundled with the agent, so the Tool Pod needs no system Node);
a `node` on PATH is the fallback. node_modules are cached per package.json dependency set under
$TMPDIR/api-agent-tsc/ so a fix-up turn only pays for `tsc`, not a second install.

`typecheck_backend(files)` returns {"status": "passed"|"failed"|"skipped", "errors": [...], "output": str,
"duration_s": float}. "skipped" means the gate could not run (no Node, registry unreachable) — never that the
code is fine; callers log it and carry on (the deploy-time build is still the backstop).
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any

logger = logging.getLogger(__name__)

INSTALL_TIMEOUT_S = int(os.environ.get("API_TSC_INSTALL_TIMEOUT_S", "180"))
TSC_TIMEOUT_S = int(os.environ.get("API_TSC_TIMEOUT_S", "120"))
MAX_ERRORS = 25
MAX_OUTPUT = 6000

# tsc diagnostics: "src/routes.ts(193,45): error TS2769: No overload matches this call."
_DIAG_RE = re.compile(r"^(?P<file>[^\s(][^(]*)\((?P<line>\d+),(?P<col>\d+)\): error (?P<code>TS\d+): (?P<msg>.*)$")
# npm failures that are the generated package.json's fault (bad name/version) — feed back, don't skip
_NPM_CODE_FAULT = ("ETARGET", "E404", "EJSONPARSE", "ERESOLVE", "No matching version")

# Known-fix rules: (predicate on the diagnostics text, the hint to give the model). The first entry is the
# medicine-finder failure (deploy run run_01M3E9EC8DJ3JEJ0V93VXM90JX): mongoose's PipelineStage[] does not
# accept a pipeline that mixes $vectorSearch/$search/$searchMeta with other stages.
ATLAS_SEARCH_PIPELINE_HINT = (
    "KNOWN FIX (TS2322/TS2345/TS2769 on an aggregation pipeline with $vectorSearch/$search/$searchMeta): mongoose's "
    "PipelineStage[] type cannot describe Atlas Search stages. Type the pipeline with the official driver's Document "
    "type — `import type { Document } from \"mongodb\";` then `const pipeline: Document[] = [...]` — and run it on the "
    "native collection: `await Model.collection.aggregate(pipeline).toArray()`. Add \"mongodb\": \"^6.9.0\" to "
    "package.json dependencies. Never pass an Atlas Search pipeline to Model.aggregate() and never annotate it "
    "PipelineStage[] or Record<string, unknown>[]."
)
_KNOWN_FIXES: list[tuple[re.Pattern[str], re.Pattern[str], str]] = [
    (re.compile(r"TS2322|TS2345|TS2769"), re.compile(r"\$vectorSearch|\$searchMeta|\$search\b|PipelineStage|VectorSearch"),
     ATLAS_SEARCH_PIPELINE_HINT),
]


def known_fix_hints(text: str) -> list[str]:
    """The known-fix rules whose error signature appears in `text` (tsc output or a deploy FailureReport)."""
    return [hint for codes, sig, hint in _KNOWN_FIXES if codes.search(text or "") and sig.search(text or "")]


def parse_diagnostics(output: str) -> list[str]:
    """The `file(line,col): error TSnnnn: message` lines (first MAX_ERRORS) from tsc output."""
    return [ln.strip() for ln in (output or "").splitlines() if _DIAG_RE.match(ln.strip())][:MAX_ERRORS]


def node_toolchain() -> dict[str, Any] | None:
    """{"bin_dir", "node": [node], "npm": [node, npm-cli.js]} — the bundled wheel first, else node/npm on PATH.
    (The wheel's bin/npm shim does a relative require that breaks outside its own layout, so npm-cli.js is run
    with the bundled node directly.)"""
    try:
        import nodejs_wheel  # type: ignore[import-not-found]
        root = os.path.dirname(nodejs_wheel.__file__)
        node = os.path.join(root, "bin", "node")
        cli = os.path.join(root, "lib", "node_modules", "npm", "bin", "npm-cli.js")
        if os.path.exists(node) and os.path.exists(cli):
            return {"bin_dir": os.path.dirname(node), "node": [node], "npm": [node, cli]}
    except ImportError:
        pass
    node, npm = shutil.which("node"), shutil.which("npm")
    return {"bin_dir": os.path.dirname(node), "node": [node], "npm": [npm]} if node and npm else None


def _deps_key(package_json: str) -> str:
    try:
        pkg = json.loads(package_json)
    except json.JSONDecodeError:
        pkg = {}
    deps = {"dependencies": pkg.get("dependencies", {}), "devDependencies": pkg.get("devDependencies", {})}
    return hashlib.sha256(json.dumps(deps, sort_keys=True).encode()).hexdigest()[:16]


def _run(cmd: list[str], cwd: str, env: dict[str, str], timeout: int) -> tuple[int, str]:
    p = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def typecheck_backend(files: dict[str, str], cache_root: str | None = None, runner: Any = None) -> dict[str, Any]:
    """Install the backend's dependencies (cached) and run `tsc --noEmit -p tsconfig.json`.
    `runner(cmd, cwd, env, timeout) -> (returncode, output)` is injectable for tests."""
    t0 = time.time()
    run = runner or _run

    def done(status: str, errors: list[str] | None = None, output: str = "") -> dict[str, Any]:
        res = {"status": status, "errors": errors or [], "output": output[-MAX_OUTPUT:],
               "duration_s": round(time.time() - t0, 1)}
        logger.info("typecheck %s in %.1fs (%d errors)", status, res["duration_s"], len(res["errors"]))
        return res

    if "package.json" not in files or "tsconfig.json" not in files:
        return done("failed", ["package.json and tsconfig.json are required to type-check the backend"])
    chain = {"bin_dir": None, "node": ["node"], "npm": ["npm"]} if runner else node_toolchain()
    if chain is None:
        return done("skipped", output="no node/npm available in this sandbox")
    root = cache_root or os.path.join(tempfile.gettempdir(), "api-agent-tsc")
    env = {**os.environ, "npm_config_cache": os.path.join(root, "npm-cache"), "npm_config_update_notifier": "false",
           "npm_config_fund": "false", "npm_config_audit": "false"}
    if chain["bin_dir"]:
        env["PATH"] = chain["bin_dir"] + os.pathsep + env.get("PATH", "")
    deps_dir = os.path.join(root, "deps", _deps_key(files["package.json"]))
    try:
        if not os.path.isdir(os.path.join(deps_dir, "node_modules")):
            os.makedirs(deps_dir, exist_ok=True)
            with open(os.path.join(deps_dir, "package.json"), "w", encoding="utf-8") as fh:
                fh.write(files["package.json"])
            code, out = run([*chain["npm"], "install", "--no-audit", "--no-fund", "--ignore-scripts", "--loglevel=error"],
                            deps_dir, env, INSTALL_TIMEOUT_S)
            if code != 0:
                shutil.rmtree(deps_dir, ignore_errors=True)
                if any(s in out for s in _NPM_CODE_FAULT):
                    return done("failed", [f"npm install failed: {out.strip()[-800:]}"], out)
                return done("skipped", output=f"npm install could not run: {out.strip()[-800:]}")
        with tempfile.TemporaryDirectory(dir=root) as work:
            for rel, content in files.items():
                path = os.path.join(work, rel)
                os.makedirs(os.path.dirname(path) or work, exist_ok=True)
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(content)
            os.symlink(os.path.join(deps_dir, "node_modules"), os.path.join(work, "node_modules"))
            tsc = os.path.join(work, "node_modules", "typescript", "bin", "tsc")
            if runner is None and not os.path.exists(tsc):
                return done("failed", ["typescript is not installed — add it to devDependencies"])
            code, out = run([*chain["node"], tsc, "--noEmit", "-p", "tsconfig.json"], work, env, TSC_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        return done("skipped", output=f"typecheck timed out: {e}")
    except OSError as e:
        return done("skipped", output=f"typecheck could not run: {e}")
    if code == 0:
        return done("passed", output=out)
    return done("failed", parse_diagnostics(out) or [out.strip()[-1500:] or f"tsc exited {code}"], out)
