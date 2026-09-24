# poc_infra_tools

EC2, SSM and Atlas provisioning tools used by the Deploy Agent (Spec §7.4–7.6). Owner: P5.

| Module | Tools |
|---|---|
| `ec2` | `resolve_ami / launch_poc_instance / wait_for_ssm_online / describe_instance / terminate_instance / tag_instance / list_managed_instances`. Instances are tagged `managed-by=poc-builder`, `poc_id`, `run_id`, `ttl_expires_at`; `terminate_instance` refuses anything without the tag. |
| `bootstrap` | `render_user_data(secret_arn, region, db_mode)` — Node 20, nginx, pm2, `/etc/poc/env` from Secrets Manager, `/opt/poc`; `local_ec2` mode also installs MongoDB Community 8.0 bound to localhost. |
| `ssm` | `run_script` (AWS-RunShellScript, output to S3 `deploy/{run_id}/logs/` + CloudWatch), `run_manifest_entry` (runs a `component.manifest.json` entrypoint; `start` becomes a pm2 process), `http_healthcheck`, `publish_frontend_nginx`. |
| `atlas` | `provision_poc_database(poc_id, mode, external_uri?)` for `shared_db | flex_cluster | local_ec2 | external_uri`; `drop_poc_database`; `allow_ip / revoke_ip`; `create_db_user / delete_db_user`; `create_flex_cluster / delete_flex_cluster`. Service-account OAuth. |

Configuration (env): `POC_SUBNET_ID`, `POC_VPC_ID`, `POC_SG_NAME`, `POC_INSTANCE_PROFILE`, `POC_INSTANCE_TYPE`, `POC_TTL_HOURS`, `ATLAS_PROJECT_ID`, `ATLAS_SANDBOX_CLUSTER`, `ATLAS_SANDBOX_SRV_HOST`, `ATLAS_SANDBOX_ADMIN_URI`, `ATLAS_CLIENT_ID`, `ATLAS_CLIENT_SECRET`.

## One-time AWS footprint
`scripts/aws_bootstrap.sh` creates exactly four `msinha-` resources in the shared account (SG, instance role + profile, platform IAM user + key) and verifies the bucket. Idempotent.

## Test
```
uv venv .venv && uv pip install -e ".[test]" && .venv/bin/python -m pytest -q
```
