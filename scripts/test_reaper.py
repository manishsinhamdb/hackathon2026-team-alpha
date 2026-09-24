"""Unit test for scripts/reaper.py with a fake ledger. No cloud, no platform DB.

Run from any agent venv that has pytest, e.g.:
    cd agents/deploy-agent && uv run python -m pytest -q ../../scripts/test_reaper.py
"""
import importlib.util
from pathlib import Path

REAPER = Path(__file__).resolve().parent / "reaper.py"
spec = importlib.util.spec_from_file_location("reaper", REAPER)
reaper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reaper)


LEDGER = [
    {"resource_id": "i-1", "type": "ec2_instance", "poc_id": "poc_A"},
    {"resource_id": "clu-1", "type": "atlas_flex_cluster", "poc_id": "poc_A"},
    {"resource_id": "i-2", "type": "ec2_instance", "poc_id": "poc_B"},
]


def test_dry_run_tears_down_nothing():
    calls = []
    summary = reaper.reap(
        list_expired=lambda: list(LEDGER),
        teardown_fn=lambda poc_id, run_id: calls.append((poc_id, run_id)) or {"errors": []},
        create_run=lambda poc_id: f"run_{poc_id}",
        dry_run=True,
        log_fn=lambda m: None,
    )
    assert summary["pocs"] == 2 and summary["resources"] == 3
    assert calls == []  # nothing torn down
    assert summary["torn_down"] == []


def test_real_run_tears_down_once_per_poc():
    calls = []
    summary = reaper.reap(
        list_expired=lambda: list(LEDGER),
        teardown_fn=lambda poc_id, run_id: calls.append((poc_id, run_id)) or {"errors": []},
        create_run=lambda poc_id: f"run_{poc_id}",
        dry_run=False,
        log_fn=lambda m: None,
    )
    assert sorted(p for p, _ in calls) == ["poc_A", "poc_B"]  # grouped: one teardown per POC
    assert len(summary["torn_down"]) == 2
    assert summary["errors"] == {}


def test_one_bad_poc_does_not_block_others():
    def teardown(poc_id, run_id):
        if poc_id == "poc_A":
            raise RuntimeError("atlas throttled")
        return {"errors": []}

    summary = reaper.reap(
        list_expired=lambda: list(LEDGER),
        teardown_fn=teardown,
        create_run=lambda poc_id: f"run_{poc_id}",
        dry_run=False,
        log_fn=lambda m: None,
    )
    assert "poc_A" in summary["errors"]
    assert [t["poc_id"] for t in summary["torn_down"]] == ["poc_B"]
