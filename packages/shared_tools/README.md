# poc_shared_tools

The one shared library every POC Builder agent imports (Spec §7.1–7.3, §7.9). Owned by the platform lead.

| Module | Spec | What it gives an agent |
|---|---|---|
| `s3` | §7.1 | `put_object / get_object / get_text / head_object / list_prefix / delete_object / copy_object / presign_get / next_version / upload_dir / download_dir / make_bundle`. Every key must start with `pocs/{poc_id}/`; deletes only under `deploy/` and `test/`. |
| `metadata` | §7.2, §5.5 | `create_poc / get_poc / update_poc_status / set_current_version / record_approval / check_gate / require_gate / create_run / get_run_status / update_run_step / bump_repair_attempt / finish_run / create_task / finish_task / append_message / register_cloud_resource / release_cloud_resource / list_expired_resources / ensure_indexes`. One active run per (poc, stage) is enforced by a partial unique index. |
| `secrets` | §7.3 | `put_poc_secret / get_poc_secret / get_secret_arn / delete_poc_secret` under `msinha/poc-builder/{poc_id}/db`. |
| `guardrails` | §5.7, §7.9 | `scan_script / scan_bundle / clamp_seed_max`. Denylist + bundle size cap. |
| `config` | §5.6 | Everything from env: `POC_S3_BUCKET`, `AWS_REGION`, `POC_PLATFORM_MONGODB_URI`, `POC_PLATFORM_DB`, `POC_SECRET_PREFIX`, `SEED_MAX_DOCS`. |

Errors are `ToolError(code, message, retryable)`. `retryable=True` means back off and retry (max 5, §5.3).

## Install and test

```
cd packages/shared_tools
uv venv .venv && uv pip install -e ".[test]"
.venv/bin/python -m pytest -q                       # unit tests, no cloud
AWS_PROFILE=manish_mongo_AWS POC_PLATFORM_MONGODB_URI='mongodb+srv://…' \
  .venv/bin/python -m pytest -q -m integration      # live S3 + platform DB
```

## Environment defaults

`POC_S3_BUCKET=msinha-hackathon`, `AWS_REGION=ap-south-1`, `POC_PLATFORM_DB=poc_builder`,
`POC_SECRET_PREFIX=msinha/poc-builder`, `POC_RESOURCE_PREFIX=msinha-`.
