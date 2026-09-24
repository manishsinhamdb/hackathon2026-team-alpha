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

## Local
```
../../scripts/vendor_packages.sh .      # copy shared packages into vendor/
cp .env.example .env                    # fill it (or symlink the repo-root .env)
uv sync --group dev && uv run pytest -q # graph tests with a fake SDK and faked cloud steps
agentic dev up
```

## Deploy
`agentic init` (from the repo root, once) → set `a2a.allowed_callers` to the Chat Agent workspace → `agentic build && agentic deploy` from this folder. Platform secrets needed: everything in `.env.example` plus `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` for the `msinha-poc-builder` user.
