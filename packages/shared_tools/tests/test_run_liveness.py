"""Resilient runs (docs/06): heartbeat, stale detection, abandon/reopen bookkeeping, stable task keys.
Pure Python: the runs collection is a tiny in-memory fake (no MongoDB)."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from poc_shared_tools import metadata as md


def _iso(dt):
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


class _Runs:
    def __init__(self, docs):
        self.docs = {d["run_id"]: d for d in docs}
        self.touches = 0

    def update_one(self, q, u):
        d = self.docs.get(q["run_id"])
        if d is not None:
            if set(u.get("$set", {})) == {"heartbeat_at"}:
                self.touches += 1
            d.update(u.get("$set", {}))

    def find_one(self, q):
        d = self.docs.get(q["run_id"])
        return dict(d) if d else None

    def find(self, q):
        return [dict(d) for d in self.docs.values()
                if d["status"] in q["status"]["$in"] and all(d.get(k) == v for k, v in q.items() if k != "status")]

    def find_one_and_update(self, q, u, return_document=None):
        d = self.docs.get(q["run_id"])
        if d is None or d["status"] not in q["status"]["$in"]:
            return None
        d.update(u.get("$set", {}))
        for k in u.get("$unset", {}):
            d.pop(k, None)
        for k, v in u.get("$push", {}).items():
            d.setdefault(k, []).append(v)
        for k, v in u.get("$inc", {}).items():
            d[k] = d.get(k, 0) + v
        return dict(d)


@pytest.fixture
def runs(monkeypatch):
    now = datetime.now(timezone.utc)
    col = _Runs([
        {"run_id": "run_fresh", "stage": "code", "poc_id": "p", "status": "running", "heartbeat_at": _iso(now)},
        {"run_id": "run_stale", "stage": "code", "poc_id": "p", "status": "running",
         "heartbeat_at": _iso(now - timedelta(minutes=5)), "updated_at": _iso(now - timedelta(minutes=5))},
        {"run_id": "run_legacy", "stage": "deploy", "poc_id": "p", "status": "running",
         "started_at": _iso(now - timedelta(hours=20)), "updated_at": _iso(now - timedelta(hours=19))},
        {"run_id": "run_done", "stage": "code", "poc_id": "p", "status": "succeeded",
         "heartbeat_at": _iso(now - timedelta(hours=3))},
    ])

    class DB:
        pass
    db = DB()
    db.runs = col
    monkeypatch.setattr(md, "_db", lambda: db)
    return col


def test_run_is_stale_uses_newest_liveness_stamp():
    at = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
    run = {"status": "running", "heartbeat_at": "2026-09-26T11:58:00Z", "updated_at": "2026-09-26T11:00:00Z"}
    assert not md.run_is_stale(run, 180, at)          # 2 min old heartbeat
    assert md.run_is_stale(run, 60, at)
    assert md.run_is_stale({"status": "running", "updated_at": "2026-09-26T11:50:00Z"}, 180, at)  # legacy run
    assert not md.run_is_stale({"status": "failed", "heartbeat_at": "2026-09-20T00:00:00Z"}, 180, at)
    assert md.STALE_AFTER_S == 180 and md.HEARTBEAT_EVERY_S <= 30


def test_find_stale_runs_filters_by_stage_and_age(runs):
    assert [r["run_id"] for r in md.find_stale_runs("code", 180)] == ["run_stale"]
    assert [r["run_id"] for r in md.find_stale_runs("deploy", 180)] == ["run_legacy"]
    assert {r["run_id"] for r in md.find_stale_runs(None, 180)} == {"run_stale", "run_legacy"}


def test_touch_run_sets_heartbeat(runs):
    runs.docs["run_stale"]["heartbeat_at"] = "2020-01-01T00:00:00Z"
    md.touch_run("run_stale")
    assert runs.docs["run_stale"]["heartbeat_at"] > "2026"


def test_heartbeat_context_touches_periodically_and_on_exit(runs):
    with md.heartbeat("run_fresh", every_s=0.05):
        time.sleep(0.2)
    assert runs.touches >= 4  # entry + >=2 beats + exit


def test_touch_run_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("db down")
    monkeypatch.setattr(md, "_db", boom)
    md.touch_run("run_x")  # best-effort


def test_abandon_then_reopen_records_continuation(runs):
    r = md.abandon_run("run_stale")
    assert r["status"] == "failed" and r["error"]["code"] == "ABANDONED"
    assert r["error"]["message"] == "abandoned: heartbeat stale"
    r = md.reopen_run("run_stale", "retry", execution="sess-1")
    assert r["status"] == "running" and "error" not in r and r["executions"] == 1
    assert r["continuations"][-1]["reason"] == "retry" and r["continuations"][-1]["execution"] == "sess-1"


def test_abandon_does_not_touch_a_finished_run(runs):
    assert md.abandon_run("run_done")["status"] == "succeeded"


def test_reopen_refuses_a_succeeded_run(runs):
    from poc_shared_tools.errors import ToolError
    with pytest.raises(ToolError) as e:
        md.reopen_run("run_done", "retry")
    assert e.value.code == "RUN_NOT_RESUMABLE"


def test_task_component_is_stable_for_new_and_legacy_tasks():
    assert md.task_component({"agent": "api_agent", "mode": "contract"}) == "contract"
    assert md.task_component({"agent": "api_agent", "mode": "code"}) == "backend"
    assert md.task_component({"agent": "data_seeding_agent", "mode": "code"}) == "seed"
    assert md.task_component({"agent": "frontend_agent", "mode": "repair"}) == "frontend"
    assert md.task_component({"agent": "coding_orchestrator", "component": "assemble"}) == "assemble"
