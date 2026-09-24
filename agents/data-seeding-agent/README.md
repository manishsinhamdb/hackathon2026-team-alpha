# Data Seeding Agent

Generates a deterministic, cap-respecting Node.js seed script from `schema_design.json` (Spec §6.4). One LLM
generation, guided by the golden `seed` component as a one-shot exemplar; output is validated (required files +
`component_manifest` contract) with one retry, guardrail-scanned, and uploaded to `code/vNNN/seed/`.

| Entry point | Description |
|---|---|
| `generate_seed` | `(poc_id, code_version, mode, inputs{schema_key, query_patterns_key}, failure?, previous_source_key?)` → `code/vNNN/seed/`; replies `succeeded` with a `code` artifact. `mode` `code` (fresh) or `repair`. |

Pure generation lives in `pipeline.generate(inputs, mode, ...)` (dicts in, dicts out — no SDK/cloud needed);
`main.py` loads inputs from S3, calls it, and writes the component. `scripts/gen_golden_check.py` exercises the
pipeline directly against the golden spec.

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
