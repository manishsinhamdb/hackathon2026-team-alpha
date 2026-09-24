"""Static guardrails on generated code (§5.7, §7.9). Run by the Coding Orchestrator before upload
and by the Deploy Agent before execution. Pure Python, no network."""
import os
import re
from typing import Any

from .config import get_config

RULES: list[tuple[str, re.Pattern]] = [
    ("rm_rf_root", re.compile(r"\brm\s+(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/(\s|$|\*)")),
    ("fork_bomb", re.compile(r":\(\)\s*\{\s*:\|:&\s*\};:")),
    ("curl_pipe_sh", re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba)?sh\b")),
    ("netcat_exec", re.compile(r"\bnc\b[^\n]*\s-e\s")),
    ("base64_exec", re.compile(r"base64\s+(-d|--decode)[^\n|]*\|\s*(sudo\s+)?(ba)?sh\b")),
    ("mongodb_uri_literal", re.compile(r"mongodb(\+srv)?://[^\s'\"<]+@")),
    ("aws_access_key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("aws_secret_key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}")),
    ("crypto_miner", re.compile(r"(?i)\b(xmrig|minerd|cpuminer|stratum\+tcp)\b")),
    ("eval_remote", re.compile(r"(?i)\beval\s*\(\s*(await\s+)?(fetch|require\(['\"]https?)")),
]

SCAN_EXTS = {".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".sh", ".yaml", ".yml", ".env", ".html", ".css", ".md", ".txt", ""}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".venv"}


def scan_script(text: str, file: str = "<inline>") -> list[dict[str, Any]]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        for rule, rx in RULES:
            if rx.search(line):
                out.append({"file": file, "line": i, "rule": rule, "snippet": line.strip()[:160]})
    return out


def scan_bundle(local_dir: str, seed_max_docs: int | None = None) -> dict[str, Any]:
    """Returns {ok, violations[], files_scanned, total_bytes}. Also enforces the bundle-size cap."""
    cfg = get_config()
    violations, files, total = [], 0, 0
    for root, dirs, names in os.walk(local_dir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for n in names:
            path = os.path.join(root, n)
            total += os.path.getsize(path)
            if os.path.splitext(n)[1].lower() not in SCAN_EXTS:
                continue
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            files += 1
            rel = os.path.relpath(path, local_dir)
            violations += scan_script(text, rel)
    if total > cfg.bundle_max_bytes:
        violations.append({"file": ".", "line": 0, "rule": "bundle_too_large", "snippet": f"{total} bytes > {cfg.bundle_max_bytes}"})
    return {"ok": not violations, "violations": violations, "files_scanned": files, "total_bytes": total}


def clamp_seed_max(requested: int | None) -> int:
    """SEED_MAX_DOCS: default 10 000, spec-configurable up to 100 000 (§5.7)."""
    cfg = get_config()
    if requested is None:
        return cfg.seed_max_docs_default
    return max(1, min(int(requested), 100_000))
