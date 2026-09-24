#!/usr/bin/env bash
# Copies the shared packages into each agent's gitignored vendor/ folder so `agentic dev up`
# and `agentic build` see them inside the agent directory (the image is built from that folder alone).
# Usage: ./scripts/vendor_packages.sh [agent-dir ...]   (default: every agents/*/ that has a pyproject.toml)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PKGS=(poc_contracts shared_tools infra_tools)
targets=("$@")
if [ ${#targets[@]} -eq 0 ]; then
  for d in "$ROOT"/agents/*/; do [ -f "$d/pyproject.toml" ] && targets+=("$d"); done
fi
for a in "${targets[@]}"; do
  a="${a%/}"; mkdir -p "$a/vendor"
  for p in "${PKGS[@]}"; do
    rsync -a --delete --exclude '.venv' --exclude '__pycache__' --exclude '*.egg-info' --exclude '.pytest_cache' \
      "$ROOT/packages/$p/" "$a/vendor/$p/"
  done
  echo "vendored ${PKGS[*]} -> $a/vendor/"
done
