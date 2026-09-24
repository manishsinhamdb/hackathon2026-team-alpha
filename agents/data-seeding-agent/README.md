# Data Seeding Agent

Generates a deterministic, cap-respecting Node.js seeding script from schema_design.json (Spec §6.4). **Not yet implemented** — replies with `NOT_IMPLEMENTED` to all tools.

| Entry point | Description |
|---|---|
| `generate_seed` | `(poc_id, code_version, mode, inputs, failure?, previous_source_key?)` → `code/vNNN/seed/` |

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
