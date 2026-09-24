"""Platform metadata repository (§7.2) over the §5.5 collections in the poc_builder database.

Every write uses $set/$push with updated_at. create_run enforces one running
run per (poc_id, stage) via a partial unique index. Documents are validated
against poc_contracts on create.
"""
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

from pymongo import ASCENDING, DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from poc_contracts import new_id, validate

from .config import get_config
from .errors import ToolError

STAGES = ("draft", "code", "deploy", "test", "teardown")
GATE_FOR_STAGE = {"code": "spec_approved", "deploy": "code_approved"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@lru_cache(maxsize=1)
def _db():
    cfg = get_config()
    if not cfg.platform_uri_set:
        raise ToolError("NO_PLATFORM_DB", "POC_PLATFORM_MONGODB_URI (or MONGODB_URI) is not set")
    client = MongoClient(cfg.platform_mongodb_uri, serverSelectionTimeoutMS=10000)
    return client[cfg.platform_db]


def ensure_indexes() -> None:
    """Idempotent; the platform lead runs this once per environment (§5.5)."""
    db = _db()
    db.pocs.create_index("poc_id", unique=True)
    db.pocs.create_index([("owner_user_id", ASCENDING), ("created_at", DESCENDING)])
    db.runs.create_index("run_id", unique=True)
    db.runs.create_index([("poc_id", ASCENDING), ("started_at", DESCENDING)])
    db.runs.create_index([("poc_id", ASCENDING), ("stage", ASCENDING)], unique=True, name="one_running_per_stage",
                         partialFilterExpression={"status": {"$in": ["queued", "running", "waiting_user"]}})
    db.tasks.create_index("task_id", unique=True)
    db.tasks.create_index([("run_id", ASCENDING), ("seq", ASCENDING)])
    db.conversations.create_index([("poc_id", ASCENDING), ("seq", ASCENDING)], unique=True)
    db.cloud_resources.create_index([("status", ASCENDING), ("ttl_expires_at", ASCENDING)])
    db.cloud_resources.create_index("poc_id")
    db.spec_embeddings.create_index([("poc_id", ASCENDING), ("spec_version", ASCENDING), ("chunk_id", ASCENDING)], unique=True)


def _strip(doc: dict | None) -> dict | None:
    if doc is not None:
        doc.pop("_id", None)
    return doc


# ---- pocs -------------------------------------------------------------------

def create_poc(title: str, owner_user_id: str, tags: list[str] | None = None) -> dict[str, Any]:
    poc_id = new_id("poc")
    t = now()
    doc = {"poc_id": poc_id, "title": title, "owner_user_id": owner_user_id, "status": "drafting",
           "stack": {"backend": "node-express", "frontend": "react-vite", "database": "mongodb"},
           "s3_prefix": f"pocs/{poc_id}/", "current_versions": {}, "approvals": [], "tags": tags or [],
           "created_at": t, "updated_at": t}
    validate("poc_document", doc)
    try:
        _db().pocs.insert_one(dict(doc))
    except PyMongoError as e:
        raise ToolError("DB_ERROR", str(e), retryable=True)
    return doc


def get_poc(poc_id: str) -> dict[str, Any]:
    doc = _strip(_db().pocs.find_one({"poc_id": poc_id}))
    if not doc:
        raise ToolError("POC_NOT_FOUND", f"no poc {poc_id}")
    return doc


def update_poc_status(poc_id: str, status: str, **fields: Any) -> dict[str, Any]:
    r = _db().pocs.find_one_and_update({"poc_id": poc_id}, {"$set": {"status": status, "updated_at": now(), **fields}},
                                       return_document=ReturnDocument.AFTER)
    if not r:
        raise ToolError("POC_NOT_FOUND", f"no poc {poc_id}")
    return _strip(r)


def set_current_version(poc_id: str, kind: str, version: str) -> dict[str, Any]:
    if kind not in ("spec", "code"):
        raise ToolError("BAD_KIND", "kind must be spec or code")
    r = _db().pocs.find_one_and_update({"poc_id": poc_id}, {"$set": {f"current_versions.{kind}": version, "updated_at": now()}},
                                       return_document=ReturnDocument.AFTER)
    if not r:
        raise ToolError("POC_NOT_FOUND", f"no poc {poc_id}")
    return _strip(r)


def record_approval(poc_id: str, stage: str, version: str, approved_by: str, implicit: bool = False) -> dict[str, Any]:
    if stage not in ("spec_approved", "code_approved"):
        raise ToolError("BAD_STAGE", "stage must be spec_approved or code_approved")
    entry = {"stage": stage, "version": version, "approved_by": approved_by, "at": now(), "implicit": implicit}
    r = _db().pocs.find_one_and_update({"poc_id": poc_id}, {"$push": {"approvals": entry}, "$set": {"updated_at": now()}},
                                       return_document=ReturnDocument.AFTER)
    if not r:
        raise ToolError("POC_NOT_FOUND", f"no poc {poc_id}")
    return entry


def check_gate(poc_id: str, stage: str, version: str) -> bool:
    """True iff the approval required before `stage` (code|deploy) exists for `version` (§5.3)."""
    gate = GATE_FOR_STAGE.get(stage)
    if gate is None:
        return True
    poc = get_poc(poc_id)
    return any(a["stage"] == gate and a["version"] == version for a in poc.get("approvals", []))


def require_gate(poc_id: str, stage: str, version: str) -> None:
    if not check_gate(poc_id, stage, version):
        raise ToolError("GATE_NOT_APPROVED", f"{GATE_FOR_STAGE[stage]} missing for {version} on {poc_id}")


# ---- runs -------------------------------------------------------------------

def create_run(poc_id: str, stage: str, requested_by: str, started_by_agent: str,
               inputs: dict[str, Any] | None = None, trace_id: str | None = None) -> dict[str, Any]:
    if stage not in STAGES:
        raise ToolError("BAD_STAGE", f"stage must be one of {STAGES}")
    t = now()
    doc = {"run_id": new_id("run"), "poc_id": poc_id, "stage": stage, "status": "queued", "requested_by": requested_by,
           "started_by_agent": started_by_agent, "steps": [], "inputs": inputs or {}, "outputs": {},
           "repair_attempts": {}, "started_at": t, "created_at": t, "updated_at": t}
    if trace_id: doc["trace_id"] = trace_id
    validate("run_document", doc)
    try:
        _db().runs.insert_one(dict(doc))
    except DuplicateKeyError:
        raise ToolError("RUN_ALREADY_ACTIVE", f"a {stage} run is already active for {poc_id}")
    except PyMongoError as e:
        raise ToolError("DB_ERROR", str(e), retryable=True)
    return doc


def get_run(run_id: str) -> dict[str, Any]:
    doc = _strip(_db().runs.find_one({"run_id": run_id}))
    if not doc:
        raise ToolError("RUN_NOT_FOUND", f"no run {run_id}")
    return doc


def get_run_status(run_id: str) -> dict[str, Any]:
    r = get_run(run_id)
    return {k: r.get(k) for k in ("run_id", "poc_id", "stage", "status", "current_step", "error", "repair_attempts", "outputs")} | \
           {"steps": [{"name": s["name"], "status": s["status"]} for s in r.get("steps", [])]}


def set_run_status(run_id: str, status: str, **fields: Any) -> None:
    _db().runs.update_one({"run_id": run_id}, {"$set": {"status": status, "updated_at": now(), **fields}})


def update_run_step(run_id: str, name: str, status: str, output_ref: str | None = None, log_key: str | None = None,
                    duration_ms: int | None = None) -> None:
    """Upsert one step entry by name and set current_step; marks the run running on first step."""
    t = now()
    step: dict[str, Any] = {"name": name, "status": status}
    if status == "running": step["started_at"] = t
    if status in ("succeeded", "failed", "skipped"): step["ended_at"] = t
    if output_ref: step["output_ref"] = output_ref
    if log_key: step["log_key"] = log_key
    if duration_ms is not None: step["duration_ms"] = duration_ms
    col = _db().runs
    r = col.update_one({"run_id": run_id, "steps.name": name},
                       {"$set": {**{f"steps.$.{k}": v for k, v in step.items()}, "current_step": name, "status": "running", "updated_at": t}})
    if r.matched_count == 0:
        col.update_one({"run_id": run_id}, {"$push": {"steps": step}, "$set": {"current_step": name, "status": "running", "updated_at": t}})


def bump_repair_attempt(run_id: str, component: str) -> int:
    r = _db().runs.find_one_and_update({"run_id": run_id}, {"$inc": {f"repair_attempts.{component}": 1}, "$set": {"updated_at": now()}},
                                       return_document=ReturnDocument.AFTER)
    return int(r["repair_attempts"][component])


def finish_run(run_id: str, status: str, outputs: dict[str, Any] | None = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    if status not in ("succeeded", "failed", "cancelled"):
        raise ToolError("BAD_STATUS", "finish_run status must be succeeded|failed|cancelled")
    upd: dict[str, Any] = {"status": status, "ended_at": now(), "updated_at": now()}
    if outputs: upd["outputs"] = outputs
    if error: upd["error"] = error
    r = _db().runs.find_one_and_update({"run_id": run_id}, {"$set": upd}, return_document=ReturnDocument.AFTER)
    if not r:
        raise ToolError("RUN_NOT_FOUND", f"no run {run_id}")
    return _strip(r)


# ---- tasks ------------------------------------------------------------------

def create_task(run_id: str, poc_id: str, agent: str, tool: str, mode: str | None = None,
                input_ref: str | None = None, task_id: str | None = None) -> dict[str, Any]:
    t = now()
    seq = _db().tasks.count_documents({"run_id": run_id}) + 1
    doc = {"task_id": task_id or new_id("task"), "run_id": run_id, "poc_id": poc_id, "seq": seq, "agent": agent, "tool": tool,
           "status": "running", "started_at": t, "created_at": t, "updated_at": t}
    if mode: doc["mode"] = mode
    if input_ref: doc["input_ref"] = input_ref
    validate("task_document", doc)
    _db().tasks.insert_one(dict(doc))
    return doc


def finish_task(task_id: str, status: str, output_ref: str | None = None, duration_ms: int | None = None,
                token_usage: dict[str, int] | None = None) -> None:
    upd: dict[str, Any] = {"status": status, "ended_at": now(), "updated_at": now()}
    if output_ref: upd["output_ref"] = output_ref
    if duration_ms is not None: upd["duration_ms"] = duration_ms
    if token_usage: upd["token_usage"] = token_usage
    _db().tasks.update_one({"task_id": task_id}, {"$set": upd})


def list_tasks(run_id: str) -> list[dict[str, Any]]:
    return [_strip(d) for d in _db().tasks.find({"run_id": run_id}).sort("seq", ASCENDING)]


# ---- conversations ----------------------------------------------------------

def append_message(poc_id: str, role: str, content: str, tool_calls: list[dict] | None = None, run_id: str | None = None) -> int:
    seq = _db().conversations.count_documents({"poc_id": poc_id}) + 1
    doc = {"poc_id": poc_id, "seq": seq, "role": role, "content": content, "tool_calls": tool_calls or [],
           "created_at": now(), "updated_at": now()}
    if run_id: doc["run_id"] = run_id
    _db().conversations.insert_one(doc)
    return seq


# ---- cloud_resources --------------------------------------------------------

RESOURCE_TYPES = ("ec2_instance", "atlas_flex_cluster", "atlas_db_user", "atlas_access_list_entry", "secret")


def register_cloud_resource(resource_id: str, type_: str, poc_id: str, run_id: str, ttl_expires_at: str,
                            region: str | None = None) -> dict[str, Any]:
    if type_ not in RESOURCE_TYPES:
        raise ToolError("BAD_RESOURCE_TYPE", f"type must be one of {RESOURCE_TYPES}")
    doc = {"resource_id": resource_id, "type": type_, "poc_id": poc_id, "run_id": run_id,
           "region": region or get_config().aws_region, "status": "active", "ttl_expires_at": ttl_expires_at,
           "released_at": None, "created_at": now(), "updated_at": now()}
    _db().cloud_resources.update_one({"resource_id": resource_id, "type": type_}, {"$set": doc}, upsert=True)
    return doc


def release_cloud_resource(resource_id: str) -> None:
    _db().cloud_resources.update_one({"resource_id": resource_id}, {"$set": {"status": "released", "released_at": now(), "updated_at": now()}})


def list_active_resources(poc_id: str | None = None) -> list[dict[str, Any]]:
    q: dict[str, Any] = {"status": "active"}
    if poc_id: q["poc_id"] = poc_id
    return [_strip(d) for d in _db().cloud_resources.find(q)]


def list_expired_resources() -> list[dict[str, Any]]:
    return [_strip(d) for d in _db().cloud_resources.find({"status": "active", "ttl_expires_at": {"$lt": now()}})]
