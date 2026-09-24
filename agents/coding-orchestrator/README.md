# Coding Orchestrator

Runs the three coding sub-agents in parallel from spec artifacts and assembles the versioned bundle (Spec §6.3). **Not yet implemented** — replies with `NOT_IMPLEMENTED` to all tools.

| Entry point | Description |
|---|---|
| `start_code_run` | `(poc_id, spec_version)` → `{run_id}` (background) |
| `repair_component` | `(poc_id, code_version, component, failure)` → `{run_id}` (background repair run) |
| `get_code_bundle` | `(poc_id, code_version)` → bundle key, sha256, manifest |

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
