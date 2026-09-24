# Frontend Agent

Produces a React 18 + Vite + TypeScript frontend demonstrating spec user stories (Spec §6.6). **Not yet implemented** — replies with `NOT_IMPLEMENTED` to all tools.

| Entry point | Description |
|---|---|
| `generate_frontend` | `(poc_id, code_version, mode, inputs, failure?, previous_source_key?)` → `code/vNNN/frontend/` |

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
