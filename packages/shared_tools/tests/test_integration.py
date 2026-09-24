"""Live tests: run with `pytest -m integration` after `aws sso login` and with POC_PLATFORM_MONGODB_URI set.
They create and remove one small object under pocs/<fresh poc_id>/test/ and one poc/run/task document set."""
import os
import pytest
from poc_contracts import new_id, validate

pytestmark = pytest.mark.integration
needs_mongo = pytest.mark.skipif(not os.environ.get("POC_PLATFORM_MONGODB_URI"), reason="POC_PLATFORM_MONGODB_URI not set")
needs_aws = pytest.mark.skipif(not (os.environ.get("AWS_PROFILE") or os.environ.get("AWS_ACCESS_KEY_ID")), reason="no AWS creds")


@needs_aws
def test_s3_roundtrip():
    from poc_shared_tools import s3
    poc, run = new_id("poc"), new_id("run")
    key = f"pocs/{poc}/test/{run}/probe.txt"
    r = s3.put_object(poc, run, key, "hello", "text/plain", "integration_test")
    assert r["sha256"]
    assert s3.get_text(key) == "hello"
    h = s3.head_object(key); assert h["metadata"]["producer"] == "integration_test"
    assert s3.next_version(poc, "spec") == "v001"
    assert any(o["key"] == key for o in s3.list_prefix(f"pocs/{poc}/"))
    s3.delete_object(key)


@needs_mongo
def test_metadata_lifecycle():
    from poc_shared_tools import metadata as m
    m.ensure_indexes()
    poc = m.create_poc("integration test", "u_test")
    pid = poc["poc_id"]
    assert not m.check_gate(pid, "code", "v001")
    m.record_approval(pid, "spec_approved", "v001", "u_test")
    assert m.check_gate(pid, "code", "v001")
    run = m.create_run(pid, "code", "u_test", "chat_agent", {"spec_version": "v001"})
    with pytest.raises(Exception):  # second active code run is rejected
        m.create_run(pid, "code", "u_test", "chat_agent")
    m.update_run_step(run["run_id"], "load_inputs", "running")
    m.update_run_step(run["run_id"], "load_inputs", "succeeded")
    t = m.create_task(run["run_id"], pid, "api_agent", "generate_api", mode="contract")
    m.finish_task(t["task_id"], "succeeded", duration_ms=5)
    assert m.bump_repair_attempt(run["run_id"], "frontend") == 1
    st = m.get_run_status(run["run_id"]); assert st["steps"][0]["status"] == "succeeded"
    done = m.finish_run(run["run_id"], "succeeded", {"code_version": "v001"})
    validate("run_document", done)
    m.set_current_version(pid, "code", "v001")
    m.update_poc_status(pid, "code_ready")
    validate("poc_document", m.get_poc(pid))
    m.register_cloud_resource("i-test", "ec2_instance", pid, run["run_id"], "2020-01-01T00:00:00Z")
    assert any(r["resource_id"] == "i-test" for r in m.list_expired_resources())
    m.release_cloud_resource("i-test")
    # cleanup
    db = m._db()
    for c in ("pocs", "runs", "tasks", "cloud_resources", "conversations"):
        db[c].delete_many({"poc_id": pid})
