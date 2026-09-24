# poc_contracts

The frozen cross-agent contracts for POC Builder (High-Level Spec v1.0, §5 and §8).
Every agent depends on this package and nothing in it depends on any agent.

## What is here

| Path | Spec | Purpose |
|---|---|---|
| `schemas/agent_envelope.json` | §8.1 | Request/response shape of every agent-as-tool call |
| `schemas/poc_spec_frontmatter.json` | §8.2 | YAML front matter of `poc_spec.md` |
| `schemas/schema_design.json` | §8.3 | Draft Agent → Data Seeding / API agents |
| `schemas/query_patterns.json` | §8.4 | Draft Agent → API Agent |
| `schemas/component_manifest.json` | §8.6 | Per-component build/run entry points (Deploy Agent runs these) |
| `schemas/poc_manifest.json` | §8.6 | Whole code version, bundle, guardrail scan |
| `schemas/deployment.json` | §8.7 | Deploy Agent → Test Agent / Chat Agent |
| `schemas/test_report.json` | §8.8 | Test Agent → Deploy Agent / user |
| `schemas/failure_report.json` | §8.9 | Deploy Agent → `repair_component` |
| `schemas/poc_document.json`, `run_document.json`, `task_document.json` | §5.5 | Platform DB documents |
| `poc_contracts.validate` | — | `validate(kind, obj)` raises `ContractError` with field paths |
| `poc_contracts.envelope` | §8.1 | `Envelope.request / succeeded / started / needs_clarification / failed` |
| `poc_contracts.ids` | §5.2 | `new_id('poc'|'run'|'task')`, `next_version('v003') -> 'v004'` |
| `poc_contracts.fakes` | §10.5 | One fake per agent returning contract-valid outputs, no LLM, no cloud |

## Rules

1. Validate on the way in and on the way out. An agent that emits an object that fails
   `validate()` is broken, whatever the LLM said.
2. Changing a schema is a contract change: bump `CONTRACT_VERSION`, note it in the team channel.
3. Fakes are how you build your agent before the others exist. Swap them for real A2A calls last.

## Install

```
uv pip install -e packages/poc_contracts            # or: pip install -e packages/poc_contracts
python -m pytest packages/poc_contracts/tests -q
```

## Usage

```python
from poc_contracts import Envelope, validate, new_id, ContractError

req = Envelope.request(poc_id=poc_id, run_id=run_id, caller="chat_agent",
                       agent="draft_agent", tool="draft_spec", params={"transcript_key": key})
# ... call the agent, get `resp` back ...
resp = validate("agent_envelope", resp)

try:
    validate("schema_design", llm_output)
except ContractError as e:
    # e.errors is [(json_path, message)] — feed it back to the LLM and retry once
    ...
```

## Changelog
- **0.1.1** — `deployment.database.mode` gains `local_ec2` (MongoDB Community installed on the POC instance) and `external_uri` (user-supplied connection string; never provisioned or dropped by the platform).
- **0.1.0** — initial freeze.
