# Test Agent — POC Builder Stage 4 (Spec §6.8)

Validates a deployed POC end-to-end: API smoke tests, Playwright browser journeys, and success-criteria checks. Produces `test_report.json` (§8.8) and drives the Deploy Agent's repair loop via component attribution.

No LLM calls anywhere in this agent.

## Entry points

| Tool (A2A) | Description |
|---|---|
| `run_e2e` | Start a test run for a deployment; returns `{run_id}` immediately (`started`). Params: `{deployment_run_id, scope?}` where scope = `"all"` \| `"smoke"` \| `[us-id, ...]`. |

## Tool Pod tools (internal)

| Tool | Timeout | Purpose |
|---|---|---|
| `test_start_run` | 60 s | Validate envelope, create runs doc, set poc status to `testing` |
| `test_load_context` | 60 s | Load deployment.json, poc.manifest.json, spec front matter, api_contract.yaml |
| `test_generate_plan` | 60 s | Build test_plan.json and write to S3 |
| `test_run_api_smoke` | 60 s | Execute HTTP tests against `urls.api` from inside the Tool Pod |
| `test_run_browser` | 900 s | Run Playwright on EC2 via SSM; upload artifacts to S3 |
| `test_write_report` | 60 s | Assemble test_report.json, finish run, update poc status |

## Result shape (design decision 4)

On success, `response.result`:
```json
{
  "run_id": "run_...",
  "passed": 9,
  "failed": 0,
  "skipped": 0,
  "not_automatable": 2,
  "failed_ids": [],
  "suspected_component": null,
  "report_key": "pocs/.../test/.../test_report.json"
}
```
Plus `artifacts: [{"kind": "report", "key": "..."}]`.

On failure: `Envelope.failed` with code from `DEPLOYMENT_NOT_FOUND | SPEC_NOT_FOUND | CONTRACT_NOT_FOUND | RUNNER_FAILED | TIMEOUT`.

## Deliberate deviation from Spec §3 F10

The Spec (F10 ruling) says "Tests target `http://localhost:80`."
**This agent targets the PUBLIC url (`deployment.json urls.app`)** instead of localhost.

Rationale: The test run executes Playwright inside an SSM command on the POC's own EC2 instance. The spec's ruling assumes a single internal port, but the deployed stack uses nginx to serve both frontend and `/api` on port 80. Targeting the public URL exercises the full nginx → backend path and validates the deployment as a user would see it — the same URL the Deploy Agent healthchecks. Using localhost:80 is equivalent in practice (same nginx process) but the public URL also validates DNS/IP reachability and the security group rule on port 80.

## How results map to FailureReport components

| `suspected_component` | When | Deploy Agent action |
|---|---|---|
| `backend` | API smoke fails (non-2xx) or health endpoint is down or 5xx in browser failed_requests | `repair_component(component="backend")` |
| `frontend` | Playwright journey fails and no 5xx evidence | `repair_component(component="frontend")` |
| `seed` | Never set by the Test Agent directly; the Deploy Agent may infer from context | Not triggered |
| `unknown` | No attributable component | Run ends failed, no repair |

## Running locally

```bash
# Vendor packages and sync deps
cd agents/test-agent
../../scripts/vendor_packages.sh .
uv sync --group dev

# Run tests (no cloud required)
uv run pytest -q tests

# Start the agent (requires platform DB + AWS creds)
cp env.example .env  # fill in values
uv run test-agent
```

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `POC_S3_BUCKET` | `msinha-hackathon` | S3 bucket for artifacts |
| `AWS_REGION` | `ap-south-1` | AWS region |
| `POC_PLATFORM_MONGODB_URI` | — | Platform Atlas cluster URI |
| `POC_PLATFORM_DB` | `poc_builder` | Platform database name |
