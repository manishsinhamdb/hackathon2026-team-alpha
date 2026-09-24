#!/usr/bin/env python3
"""Expiry reaper (Spec §5.7).

Lists expired entries in the platform `cloud_resources` ledger, groups them by POC, and tears each POC
down through the Deploy Agent's own teardown logic (`agent_deploy_agent.pipeline.teardown`), so there is
exactly one code path that deletes cloud resources. Never deletes a user-provided database (the deploy
teardown already refuses that). S3 assets are retained.

The spec says a *platform scheduled job* runs this; until that exists, schedule it with cron, e.g.:

    */30 * * * *  cd /path/to/repo/agents/deploy-agent && set -a && . ../../.env && set +a && \
                  uv run python ../../scripts/reaper.py            # real teardown
    # add --dry-run first to see what it would touch.

Run it from the deploy-agent venv (it needs infra_tools + the platform DB URI). Use --dry-run to list
without deleting.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from typing import Any, Callable

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
log = logging.getLogger("reaper")


def _group_by_poc(resources: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in resources:
        out.setdefault(r.get("poc_id", "unknown"), []).append(r)
    return out


def reap(
    list_expired: Callable[[], list[dict[str, Any]]],
    teardown_fn: Callable[[str, str], dict[str, Any]],
    create_run: Callable[[str], str],
    dry_run: bool = True,
    log_fn: Callable[[str], None] = log.info,
) -> dict[str, Any]:
    """Core loop, fully injectable for testing.

    `list_expired` -> ledger rows; `create_run(poc_id) -> run_id`; `teardown_fn(poc_id, run_id) -> result`.
    Returns a summary {pocs, torn_down, skipped, errors}.
    """
    resources = list_expired()
    by_poc = _group_by_poc(resources)
    summary: dict[str, Any] = {"pocs": len(by_poc), "resources": len(resources),
                               "torn_down": [], "errors": {}, "dry_run": dry_run}
    for poc_id, rows in by_poc.items():
        kinds = sorted({r.get("type", "?") for r in rows})
        log_fn(f"expired POC {poc_id}: {len(rows)} resource(s) {kinds}")
        if dry_run:
            continue
        try:
            run_id = create_run(poc_id)
            result = teardown_fn(poc_id, run_id)
            summary["torn_down"].append({"poc_id": poc_id, "run_id": run_id, "result": result})
            log_fn(f"tore down {poc_id} via run {run_id}")
        except Exception as e:  # keep going; one bad POC must not block the rest
            summary["errors"][poc_id] = str(e)[:400]
            log_fn(f"ERROR tearing down {poc_id}: {e}")
    return summary


# --- production wiring (imported lazily so the unit test needs no cloud) ------

def _default_list_expired() -> list[dict[str, Any]]:
    from poc_shared_tools import metadata as md
    return md.list_expired_resources()


def _default_create_run(poc_id: str) -> str:
    from poc_shared_tools import metadata as md
    run = md.create_run(poc_id, "teardown", requested_by="reaper", started_by_agent="reaper")
    return run["run_id"]


def _default_teardown(poc_id: str, run_id: str) -> dict[str, Any]:
    from agent_deploy_agent import pipeline as deploy_pipeline
    return deploy_pipeline.teardown(poc_id, run_id)


def main() -> int:
    ap = argparse.ArgumentParser(description="POC Builder expiry reaper (§5.7)")
    ap.add_argument("--dry-run", action="store_true", help="list expired resources, delete nothing")
    args = ap.parse_args()
    summary = reap(_default_list_expired, _default_teardown, _default_create_run, dry_run=args.dry_run)
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not summary["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
