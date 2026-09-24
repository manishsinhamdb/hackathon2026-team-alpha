# Frontend Agent

Produces a React 18 + Vite + TypeScript frontend demonstrating the spec's user stories (Spec §6.6), guided by the
golden frontend as a one-shot exemplar. One page per user story carrying EXACTLY the declared `data-testid`s; an api
client with one function per `operationId` reading `VITE_API_BASE_URL`. Output is validated (required files +
`component_manifest` + every spec testid present) with one retry, guardrail-scanned, and uploaded.

| Entry point | Description |
|---|---|
| `generate_frontend` | `(poc_id, code_version, mode, inputs{spec_key, contract_key}, failure?, previous_source_key?)` → `code/vNNN/frontend/`; replies `succeeded` with a `code` artifact. |

Pure generation lives in `pipeline.generate(inputs, mode, ...)`; `main.py` loads the spec front matter + contract from
S3, calls it, and writes the component. `scripts/gen_golden_check.py` exercises it directly against the golden spec.

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
