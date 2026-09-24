# API Agent

Authors the OpenAPI contract and implements it as an Express + Mongoose + TypeScript backend (Spec §6.5), guided by
the golden contract/backend as one-shot exemplars. Output is validated (contract: parses, has health, path params
declared; backend: required files + `component_manifest`) with one retry, guardrail-scanned, and uploaded.

| Entry point | Description |
|---|---|
| `generate_api` | `(poc_id, code_version, mode, inputs, failure?, previous_source_key?)`. `mode` `contract` → `code/vNNN/api_contract.yaml` (`contract` artifact); `mode` `code`/`repair` → `code/vNNN/backend/` (`code` artifact). |

Pure generation lives in `pipeline.generate(inputs, mode, ...)`; `main.py` loads inputs from S3 (spec front matter,
contract, schema, query patterns), calls it, and writes the output. `scripts/gen_golden_check.py` exercises it directly.

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
