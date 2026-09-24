# API Agent

Implements the Draft Agent's API contract as an Express + Mongoose Node.js backend (Spec §6.5). **Not yet implemented** — replies with `NOT_IMPLEMENTED` to all tools.

| Entry point | Description |
|---|---|
| `generate_api` | `(poc_id, code_version, mode, inputs, failure?, previous_source_key?)` → `code/vNNN/backend/` |

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
