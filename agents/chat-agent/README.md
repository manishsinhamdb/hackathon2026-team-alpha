# Chat Agent

User-facing front door for POC Builder (Spec §6.1). Drives draft, code, deploy and test stages through the other agents. **Not yet implemented** — replies with `NOT_IMPLEMENTED` to all tools.

| Entry point | Description |
|---|---|
| (user-facing) | Envelope echo stub; full tool list in §6.1 will be implemented later |

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
