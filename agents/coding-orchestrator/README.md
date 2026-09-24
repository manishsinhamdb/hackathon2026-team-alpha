# Coding Orchestrator

Runs the three coding sub-agents from the approved spec artifacts and assembles the versioned code bundle the
Deploy Agent consumes (Spec §6.3). No LLM here — all generation is delegated over A2A to the coder agents
(skills `generate-api`, `generate-seed`, `generate-frontend`).

| Tool | Description |
|---|---|
| `start_code_run` | `(poc_id, spec_version)` → builds a fresh code version (`contract → seed → backend → frontend`), assembles `poc.manifest.json` + `bundle.tar.gz`, replies `{run_id, code_version, changed_components}` with `bundle` + `contract` artifacts. Checks the `spec_approved` gate first (`GATE_NOT_APPROVED` otherwise). |
| `repair_component` | `(poc_id, code_version, component, failure)` → allocates the next code version, copies forward the unchanged components + contract, regenerates only the failing component in `repair` mode (or, when `failure_class == CONTRACT_MISMATCH`, rebuilds the contract + backend + frontend fresh), reassembles, replies as above. |
| `get_code_bundle` | `(poc_id, code_version)` → `{bundle_key, manifest}`. |

## Flow (`start_code_run` / `repair_component`)

`parse → start_run → load_inputs → coders (A2A, one task + run step each) → assemble → finalize → reply succeeded`.

Order is `contract → seed → backend → frontend`: the contract is written to `code/vNNN/api_contract.yaml` first so the
backend and frontend both implement it. Each coder loads its own inputs from S3 (params carry S3 keys, never content),
writes its component to `code/vNNN/<component>/`, and replies `succeeded` with a `code`/`contract` artifact. `assemble`
downloads the whole version, runs `guardrails.scan_bundle`, `make_bundle`, and writes a validated `poc.manifest.json`
(`produced_by` records `run_id`, `changed_components`, and `repairs_of` on repair). `finalize` sets
`pocs.current_versions.code`, flips the POC to `code_ready`, and finishes the run.

### Long runs and A2A timeouts

The orchestrator replies `succeeded` **at the end** of the whole build, not up front. A full `start_code_run` runs four
LLM generations back-to-back and can exceed the ~300 s A2A ceiling, so a caller whose A2A call times out must **find the
run and poll it** rather than treat the timeout as a failure: query the `runs` collection for
`{"stage": "code", "poc_id": <poc_id>}`, newest by `started_at` first, and read its terminal `status` and
`outputs.code_version`. Single-component repairs are one generation and normally finish well under the ceiling.

## Local

```bash
../../scripts/vendor_packages.sh .
uv sync --group dev
uv run pytest -q tests
```

## Deploy

```bash
agentic deploy
```
