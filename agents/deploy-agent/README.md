# deploy-agent

POC Builder **Deploy Agent** (Spec §6.7, Stages 3–5) on the MongoDB Agentic AI Platform.

## Message contract
Every message in is an `AgentEnvelope` request as JSON; every reply is an `AgentEnvelope` response as JSON (see `packages/poc_contracts`). Tools it answers:

| tool | params | reply |
|---|---|---|
| `start_deploy_run` | `code_version`, `options {db_mode?: shared_db\|flex_cluster\|local_ec2\|external_uri, external_uri?, instance_type?, ttl_hours?, seed_max_docs?, run_tests?}` | `succeeded {run_id, urls, code_version, test_passed}` + artifacts, or `failed` |
| `resume_run` | `run_id` | continues from the first step not yet succeeded |
| `teardown_poc` | — | `succeeded {run_id, released[], errors[]}` |
| `get_deployment` | — | `succeeded {deployment}` |
| `get_run_status` | `run_id` | `succeeded {status, current_step, steps[], urls}` |

## How it runs
The LangGraph graph is the pipeline: `check_gate → provision_db → store_secret → launch_instance → fetch_bundle → seed_data → build_backend → start_backend → build_frontend → publish_frontend → write_deployment → run_tests → finalize`. Each step is one Tool Pod call (`deploy_execute_step`) that writes to `runs.steps` and `runs.outputs`, so `resume_run` picks up wherever a run stopped.

- Failures in `seed_data`, `build_backend`, `start_backend`, `build_frontend` (and test failures attributed to a component) become a `FailureReport`, go to the Coding Orchestrator's `repair_component` over A2A, and the run rewinds to `fetch_bundle` with the new code version. Max 3 repairs per component.
- Tests go to the Test Agent's `run_e2e` over A2A. Both callees reply `started`; the deploy run polls their `runs` document, so no A2A call approaches the 300 s ceiling.
- Secrets: the POC `MONGODB_URI` goes to Secrets Manager (`msinha/poc-builder/{poc_id}/db`); EC2 reads it at boot through its instance role. It never appears in `runs.outputs`, logs or replies.

### publish_frontend — why the public check runs from the instance
`publish_frontend` rewrites the whole `nginx.conf` (no stock default server) and must confirm the site is publicly reachable before it records `outputs.urls`. It does that reachability check **from the EC2 instance itself** (`ssm.public_healthcheck_via_ssm` → `curl http://<public_ip>/` and `.../api/health`); an EC2 box reaches its own public IP back through the Internet Gateway, so the result is exactly what the internet sees. It deliberately does **not** rely on a check from the Tool Pod: the platform routes Tool Pod HTTP through an egress proxy that refuses raw-IP hosts (`egress denied: IP literals are not allowed … declare a hostname`) and answers with a proxy-generated `403`, which is not on the request path a real user takes. `ssm.http_healthcheck` is kept only as an advisory probe here — it logs its result (including a `proxy_denied` hint) but never fails the step. On failure the step's error carries the real evidence: which URL, its status, and the first 300 chars of the body.

## Local
```
../../scripts/vendor_packages.sh .      # copy shared packages into vendor/
cp .env.example .env                    # fill it (or symlink the repo-root .env)
uv sync --group dev && uv run pytest -q # graph tests with a fake SDK and faked cloud steps
agentic dev up
```

## Deploy
`agentic init` (from the repo root, once) → set `a2a.allowed_callers` to the Chat Agent workspace → `agentic build && agentic deploy` from this folder. Platform secrets needed: everything in `.env.example` plus `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for the `msinha-poc-builder` user.
