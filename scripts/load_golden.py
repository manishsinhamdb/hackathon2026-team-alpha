#!/usr/bin/env python3
"""Load the golden POC (fixtures/golden) into S3 and the platform DB so Deploy and Test can be exercised
without any coding agents. Idempotent: re-running re-uploads and re-approves the same versions.

Usage (from repo root, with .env sourced and AWS_PROFILE set):
    python3 scripts/load_golden.py            # load and print the start_deploy_run envelope
    python3 scripts/load_golden.py --envelope-only
Requires the shared packages to be importable: run it with an agent's venv, e.g.
    agents/deploy-agent/.venv/bin/python scripts/load_golden.py
"""
import argparse
import json
import os
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "fixtures" / "golden"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--envelope-only", action="store_true")
    ap.add_argument("--db-mode", default="shared_db", choices=["shared_db", "flex_cluster", "local_ec2", "external_uri"])
    ap.add_argument("--no-tests", action="store_true")
    args = ap.parse_args()

    from poc_contracts import Envelope, new_id, validate
    from poc_shared_tools import metadata as md, s3 as s3t
    from poc_shared_tools.guardrails import scan_bundle

    poc_id = (GOLDEN / "POC_ID").read_text().strip()
    run_id = new_id("run")

    if not args.envelope_only:
        # ---- platform DB: pocs document with both gates approved
        md.ensure_indexes()
        db = md._db()
        now = md.now()
        doc = {"poc_id": poc_id, "title": "Kirana Basket — live \"also bought\" recommendations (GOLDEN)", "owner_user_id": "u_golden",
               "status": "code_ready", "stack": {"backend": "node-express", "frontend": "react-vite", "database": "mongodb"},
               "s3_prefix": f"pocs/{poc_id}/", "current_versions": {"spec": "v001", "code": "v001"},
               "approvals": [{"stage": "spec_approved", "version": "v001", "approved_by": "u_golden", "at": now, "implicit": False},
                             {"stage": "code_approved", "version": "v001", "approved_by": "u_golden", "at": now, "implicit": False}],
               "tags": ["golden", "recommendation", "grocery"], "created_at": now, "updated_at": now}
        validate("poc_document", doc)
        existing = db.pocs.find_one({"poc_id": poc_id})
        if existing:
            db.pocs.update_one({"poc_id": poc_id}, {"$set": {k: v for k, v in doc.items() if k != "created_at"}})
            print(f"pocs: updated {poc_id}")
        else:
            db.pocs.insert_one(doc)
            print(f"pocs: inserted {poc_id}")
        # clear any stale active runs for this poc so a fresh deploy can start
        db.runs.update_many({"poc_id": poc_id, "status": {"$in": ["queued", "running", "waiting_user"]}}, {"$set": {"status": "cancelled"}})

        # ---- S3: input, spec, code
        s3t.put_object(poc_id, run_id, f"pocs/{poc_id}/input/transcript.txt", (ROOT / "fixtures/transcripts/recsys_meeting.txt").read_text(), "text/plain", "load_golden")
        for f in (GOLDEN / "spec/v001").iterdir():
            s3t.put_object(poc_id, run_id, f"pocs/{poc_id}/spec/v001/{f.name}", f.read_bytes(), "application/octet-stream", "load_golden")
        print("spec/v001 uploaded")

        # copy code to a temp dir without build artefacts, scan, upload, bundle
        tmp = Path(tempfile.mkdtemp())
        src = GOLDEN / "code/v001"
        shutil.copytree(src, tmp / "v001", ignore=shutil.ignore_patterns("node_modules", "dist", "*.tsbuildinfo", ".DS_Store"))
        code = tmp / "v001"
        scan = scan_bundle(str(code))
        if not scan["ok"]:
            print("guardrail violations:", scan["violations"]); return 2
        keys = s3t.upload_dir(poc_id, run_id, str(code), f"pocs/{poc_id}/code/v001", "load_golden")
        b = s3t.make_bundle(poc_id, run_id, "v001", str(code), "load_golden")
        m = json.loads((code / "poc.manifest.json").read_text())
        m["bundle_sha256"] = b["bundle_sha256"]; m["bundle_key"] = b["bundle_key"]
        m["guardrail_scan"] = {"ok": True, "scanned_at": now, "violations": []}
        m["produced_by"] = {"run_id": run_id, "changed_components": ["seed", "backend", "frontend"]}
        validate("poc_manifest", m)
        s3t.put_object(poc_id, run_id, f"pocs/{poc_id}/code/v001/poc.manifest.json", json.dumps(m, indent=2), "application/json", "load_golden")
        shutil.rmtree(tmp)
        print(f"code/v001 uploaded: {len(keys)} files, bundle {b['size']} bytes sha {b['bundle_sha256'][:12]}…")

    env = Envelope.request(poc_id=poc_id, run_id=run_id, caller="chat_agent", agent="deploy_agent", tool="start_deploy_run",
                           params={"code_version": "v001", "options": {"db_mode": args.db_mode, "run_tests": not args.no_tests, "ttl_hours": 6}})
    print("\nPaste this as the message to the Deploy Agent:\n")
    print(json.dumps(env))
    return 0


if __name__ == "__main__":
    sys.exit(main())
