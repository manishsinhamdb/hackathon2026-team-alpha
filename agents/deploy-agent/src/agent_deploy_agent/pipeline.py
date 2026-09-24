"""Deploy pipeline steps (Spec §6.7). Each step is a pure function of (run document, step name)
that does its cloud work, persists what the next step needs into runs.outputs, and returns
{"ok": bool, "outputs": {...}, "error": {...}}. Steps are idempotent enough to be re-run after a
repair. Nothing in here touches the LLM. Secrets never enter `outputs`."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from poc_contracts import validate
from poc_shared_tools import metadata as md, s3 as s3t, secrets as sec
from poc_shared_tools.config import get_config
from poc_shared_tools.guardrails import scan_bundle
from poc_infra_tools import atlas, ec2, ssm
from poc_infra_tools.config import get_infra_config

log = logging.getLogger(__name__)

STEPS = ["check_gate", "provision_db", "store_secret", "launch_instance", "fetch_bundle", "seed_data",
         "build_backend", "start_backend", "build_frontend", "publish_frontend", "write_deployment",
         "run_tests", "finalize"]
REPAIRABLE = {"seed_data": "seed", "build_backend": "backend", "start_backend": "backend", "build_frontend": "frontend"}
MAX_REPAIRS = 3


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "outputs": {}, "error": {"code": code, "message": message[:2000], **extra}}


def _ok(**outputs: Any) -> dict[str, Any]:
    return {"ok": True, "outputs": outputs, "error": None}


def _manifest(poc_id: str, code_version: str, component: str) -> dict[str, Any]:
    key = f"pocs/{poc_id}/code/{code_version}/{component}/component.manifest.json"
    return validate("component_manifest", json.loads(s3t.get_text(key)))


def _poc_manifest(poc_id: str, code_version: str) -> dict[str, Any]:
    return validate("poc_manifest", json.loads(s3t.get_text(f"pocs/{poc_id}/code/{code_version}/poc.manifest.json")))


def run_step(run: dict[str, Any], step: str) -> dict[str, Any]:
    poc_id, run_id = run["poc_id"], run["run_id"]
    o = run.get("outputs", {})
    inputs = run.get("inputs", {})
    code_version = o.get("code_version") or inputs["code_version"]
    opts = inputs.get("options", {})
    db_mode = opts.get("db_mode", "shared_db")
    cfg = get_config()

    if step == "check_gate":
        if not md.check_gate(poc_id, "deploy", code_version):
            return _err("GATE_NOT_APPROVED", f"code_approved missing for {code_version}")
        pm = _poc_manifest(poc_id, code_version)
        if not pm["guardrail_scan"]["ok"]:
            return _err("GUARDRAIL_VIOLATION", "poc.manifest.json guardrail scan is not ok")
        return _ok(code_version=code_version, bundle_key=pm["bundle_key"], contract_key=pm["contract_key"],
                   spec_version=pm["spec_version"], db_mode=db_mode)

    if step == "provision_db":
        ext = opts.get("external_uri")
        r = atlas.provision_poc_database(poc_id, db_mode, external_uri=ext)
        ttl = ec2.ttl_from_hours(opts.get("ttl_hours"))
        for res in r["resources"]:
            md.register_cloud_resource(res["resource_id"], res["type"], poc_id, run_id, ttl)
        # stash the URI transiently in the secret step; never in outputs
        sec.put_poc_secret(poc_id, {"MONGODB_URI": r["connection_string"], "DB_USER": r["username"], "DB_PASSWORD": r["password"]})
        return _ok(db={"mode": r["mode"], "cluster_name": r["cluster_name"], "database_name": r["database_name"], "username": r["username"]}, ttl_expires_at=ttl)

    if step == "store_secret":
        arn = sec.get_secret_arn(poc_id)
        md.register_cloud_resource(arn, "secret", poc_id, run_id, o["ttl_expires_at"])
        return _ok(secret_arn=arn)

    if step == "launch_instance":
        if o.get("instance_id"):
            d = ec2.describe_instance(o["instance_id"])
            if d["state"] == "running":
                return _ok()
        inst = ec2.launch_poc_instance(poc_id, run_id, o["secret_arn"], db_mode=db_mode,
                                       instance_type=opts.get("instance_type"), ttl_expires_at=o["ttl_expires_at"])
        md.register_cloud_resource(inst["instance_id"], "ec2_instance", poc_id, run_id, o["ttl_expires_at"])
        if db_mode in ("shared_db", "flex_cluster"):
            atlas.allow_ip(inst["public_ip"], f"{poc_id} {run_id}", delete_after=o["ttl_expires_at"])
            md.register_cloud_resource(inst["public_ip"], "atlas_access_list_entry", poc_id, run_id, o["ttl_expires_at"])
        # bootstrap must have finished before any SSM step
        r = ssm.run_script(inst["instance_id"], poc_id, run_id, "wait_bootstrap",
                           ["for i in $(seq 1 60); do [ -f /etc/poc/bootstrap.done ] && break; sleep 5; done",
                            "test -f /etc/poc/bootstrap.done", "node --version", "nginx -v"], timeout_s=400)
        if r["exit_code"] != 0:
            return _err("BOOTSTRAP_FAILED", r["stderr"] or r["stdout"], log_key=r.get("log_key"))
        return _ok(instance_id=inst["instance_id"], public_ip=inst["public_ip"], instance_type=inst["instance_type"])

    if step == "fetch_bundle":
        bucket, key = cfg.s3_bucket, o["bundle_key"]
        r = ssm.run_script(o["instance_id"], poc_id, run_id, "fetch_bundle",
                           ["rm -rf /opt/poc/* && mkdir -p /opt/poc && cd /opt/poc",
                            f"aws s3 cp s3://{bucket}/{key} /opt/poc/bundle.tar.gz --region {cfg.aws_region}",
                            "tar xzf /opt/poc/bundle.tar.gz -C /opt/poc && ls -la /opt/poc"], timeout_s=300)
        if r["exit_code"] != 0:
            return _err("FETCH_FAILED", r["stderr"] or r["stdout"], log_key=r.get("log_key"))
        return _ok()

    if step == "seed_data":
        m = _manifest(poc_id, code_version, "seed")
        env = {"SEED_MAX_DOCS": str(opts.get("seed_max_docs", cfg.seed_max_docs_default))}
        r = ssm.run_manifest_entry(o["instance_id"], poc_id, run_id, "seed", "install", m, env=env, timeout_s=600)
        if r["exit_code"] != 0:
            return _err("SEED_ERROR", r["stderr"] or r["stdout"], log_key=r.get("log_key"), component="seed")
        r = ssm.run_manifest_entry(o["instance_id"], poc_id, run_id, "seed", "seed", m, env=env, timeout_s=900)
        if r["exit_code"] != 0:
            return _err("SEED_ERROR", r["stderr"] or r["stdout"], log_key=r.get("log_key"), component="seed")
        last = [ln for ln in r["stdout"].strip().splitlines() if ln.strip()][-1:]
        try:
            summary = json.loads(last[0]) if last else {}
        except json.JSONDecodeError:
            summary = {}
        return _ok(seed_summary=summary)

    if step == "build_backend":
        m = _manifest(poc_id, code_version, "backend")
        for entry, t in (("install", 900), ("build", 900)):
            r = ssm.run_manifest_entry(o["instance_id"], poc_id, run_id, "backend", entry, m, timeout_s=t)
            if r["exit_code"] != 0:
                return _err("BUILD_ERROR", r["stderr"] or r["stdout"], log_key=r.get("log_key"), component="backend")
        return _ok(backend_manifest=m)

    if step == "start_backend":
        m = o.get("backend_manifest") or _manifest(poc_id, code_version, "backend")
        r = ssm.run_manifest_entry(o["instance_id"], poc_id, run_id, "backend", "start", m, env={"PORT": "8080"}, timeout_s=120)
        if r["exit_code"] != 0:
            return _err("RUNTIME_ERROR", r["stderr"] or r["stdout"], log_key=r.get("log_key"), component="backend")
        hc = m["entrypoints"]["healthcheck"]
        url = hc["url"]  # checked from inside the instance; port 8080 is not exposed, nginx fronts it later
        r = ssm.run_script(o["instance_id"], poc_id, run_id, "healthcheck_backend",
                           [f"for i in $(seq 1 12); do c=$(curl -s -o /tmp/h -w '%{{http_code}}' {url}); [ \"$c\" = \"{hc['expect']}\" ] && cat /tmp/h && exit 0; sleep 5; done",
                            "pm2 logs poc-backend --nostream --lines 60 || true", "exit 1"], timeout_s=120)
        if r["exit_code"] != 0:
            return _err("HEALTHCHECK_FAILED", r["stdout"][-3000:], log_key=r.get("log_key"), component="backend")
        return _ok()

    if step == "build_frontend":
        m = _manifest(poc_id, code_version, "frontend")
        env = {"VITE_API_BASE_URL": "/api"}
        for entry, t in (("install", 900), ("build", 900)):
            r = ssm.run_manifest_entry(o["instance_id"], poc_id, run_id, "frontend", entry, m, env=env, timeout_s=t)
            if r["exit_code"] != 0:
                return _err("BUILD_ERROR", r["stderr"] or r["stdout"], log_key=r.get("log_key"), component="frontend")
        return _ok(frontend_manifest=m)

    if step == "publish_frontend":
        m = o.get("frontend_manifest") or _manifest(poc_id, code_version, "frontend")
        static_dir = f"/opt/poc/{m['workdir']}/{m['static_dir']}"
        proxy = m["publish"]["api_proxy"]
        r = ssm.publish_frontend_nginx(o["instance_id"], poc_id, run_id, static_dir, proxy["path"], proxy["upstream"])
        if r["exit_code"] != 0:
            return _err("PUBLISH_FAILED", r["stderr"] or r["stdout"], log_key=r.get("log_key"))
        hc = ssm.http_healthcheck(f"http://{o['public_ip']}/api/health", 200, timeout_s=60)
        if not hc["ok"]:
            return _err("HEALTHCHECK_FAILED", f"public /api/health: {hc['body']}", component="backend")
        return _ok(urls={"app": f"http://{o['public_ip']}/", "api": f"http://{o['public_ip']}/api", "health": f"http://{o['public_ip']}/api/health"})

    if step == "write_deployment":
        steps_doc = [{"name": s["name"], "status": s["status"], **({"log_key": s["log_key"]} if s.get("log_key") else {}),
                      **({"duration_ms": s["duration_ms"]} if s.get("duration_ms") is not None else {})} for s in md.get_run(run_id)["steps"]]
        dep = {"poc_id": poc_id, "run_id": run_id, "code_version": code_version, "status": "deployed",
               "instance": {"instance_id": o["instance_id"], "instance_type": o["instance_type"], "public_ip": o["public_ip"],
                            "region": cfg.aws_region, "ttl_expires_at": o["ttl_expires_at"]},
               "database": {"mode": o["db"]["mode"], "cluster_name": o["db"]["cluster_name"], "database_name": o["db"]["database_name"], "secret_arn": o["secret_arn"]},
               "urls": o["urls"], "env": {"PORT": "8080", "VITE_API_BASE_URL": "/api", "SEED_MAX_DOCS": str(opts.get("seed_max_docs", cfg.seed_max_docs_default))},
               "seed_summary": o.get("seed_summary", {}), "steps": steps_doc}
        validate("deployment", dep)
        key = f"pocs/{poc_id}/deploy/{run_id}/deployment.json"
        s3t.put_object(poc_id, run_id, key, json.dumps(dep, indent=2), "application/json", "deploy_agent")
        md.update_poc_status(poc_id, "deployed", deployment={"run_id": run_id, "ec2_instance_id": o["instance_id"], "public_url": o["urls"]["app"],
                                                              "db": dep["database"], "ttl_expires_at": o["ttl_expires_at"], "deployment_key": key})
        return _ok(deployment_key=key)

    if step == "run_tests":
        # The A2A call to the Test Agent happens in the graph (agent sandbox); this step only records intent.
        if not opts.get("run_tests", True):
            return _ok(tests_skipped=True)
        return _ok(tests_requested=True)

    if step == "finalize":
        status = "tested" if o.get("test_run_id") and o.get("test_passed") else ("deployed" if o.get("tests_skipped") or not o.get("test_run_id") else "failed")
        md.update_poc_status(poc_id, status)
        return _ok(final_status=status)

    return _err("UNKNOWN_STEP", step)


def build_failure_report(run: dict[str, Any], step: str, error: dict[str, Any], attempt: int) -> dict[str, Any]:
    o = run.get("outputs", {})
    component = error.get("component") or REPAIRABLE.get(step, "backend")
    fr = {"poc_id": run["poc_id"], "deploy_run_id": run["run_id"], "code_version": o.get("code_version") or run["inputs"]["code_version"],
          "component": component, "stage_step": step, "failure_class": _classify(step, error),
          "exit_code": 1, "stderr_excerpt": (error.get("message") or "")[:8000],
          "attempt": attempt, "max_attempts": MAX_REPAIRS, "hints": ["contract unchanged", "node 20", f"step {step}"]}
    if error.get("log_key"):
        fr["stdout_key"] = error["log_key"]
    if error.get("test_result_ids"):
        fr["test_result_ids"] = error["test_result_ids"]
    return validate("failure_report", fr)


def _classify(step: str, error: dict[str, Any]) -> str:
    code = error.get("code", "")
    if code in ("BUILD_ERROR", "RUNTIME_ERROR", "SEED_ERROR", "HEALTHCHECK_FAILED", "TEST_FAILURE", "CONTRACT_MISMATCH", "GUARDRAIL_VIOLATION"):
        return code
    if step.startswith("build_"): return "BUILD_ERROR"
    if step == "seed_data": return "SEED_ERROR"
    if step == "start_backend": return "HEALTHCHECK_FAILED"
    return "RUNTIME_ERROR"


def teardown(poc_id: str, run_id: str) -> dict[str, Any]:
    """Stage 5 (§6.7): terminate instance, revoke IP, drop DB/user or flex cluster, delete secret, release resources."""
    released, errors = [], []
    poc = md.get_poc(poc_id)
    dep = poc.get("deployment", {}) or {}
    db = dep.get("db", {}) or {}
    for res in md.list_active_resources(poc_id):
        rid, typ = res["resource_id"], res["type"]
        try:
            if typ == "ec2_instance":
                ec2.terminate_instance(rid, wait=False)
            elif typ == "atlas_access_list_entry":
                atlas.revoke_ip(rid)
            elif typ == "atlas_db_user":
                pass  # dropped together with the database below
            elif typ == "atlas_flex_cluster":
                atlas.delete_flex_cluster(rid, timeout_s=60)
            elif typ == "secret":
                sec.delete_poc_secret(poc_id)
            md.release_cloud_resource(rid)
            released.append({"resource_id": rid, "type": typ})
        except Exception as e:  # keep going; report everything at the end
            errors.append({"resource_id": rid, "type": typ, "error": str(e)[:300]})
    try:
        if db.get("mode") == "shared_db":
            user = next((r["resource_id"] for r in md.list_active_resources(poc_id) if r["type"] == "atlas_db_user"), None) or \
                   next((r["resource_id"] for r in md._db().cloud_resources.find({"poc_id": poc_id, "type": "atlas_db_user"})), None)
            atlas.drop_poc_database(poc_id, "shared_db", db.get("cluster_name", ""), user)
            for r in md._db().cloud_resources.find({"poc_id": poc_id, "type": "atlas_db_user", "status": "active"}):
                md.release_cloud_resource(r["resource_id"])
    except Exception as e:
        errors.append({"resource_id": poc_id, "type": "database", "error": str(e)[:300]})
    md.update_poc_status(poc_id, "torn_down")
    return {"released": released, "errors": errors, "remaining_active": len(md.list_active_resources(poc_id))}
