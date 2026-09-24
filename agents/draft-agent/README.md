# Draft Agent

Turns a meeting transcript into an approval-ready POC specification (Spec §6.2). **Not yet implemented** — replies with `NOT_IMPLEMENTED` to all tools.

| Entry point | Description |
|---|---|
| `draft_spec` | `(poc_id, answers?, template_poc_id?, force_assumptions?)` → spec artifacts or `needs_clarification` |

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
