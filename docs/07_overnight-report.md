# Overnight autonomous run — report

Started: 2026-09-24. Model: Opus 4.8 (1M). Working unattended per the addendum.

This report is appended after every phase so partial progress is visible.

## Decisions log (conventions chosen when the addendum left a choice)
- Draft Agent clarification cap: the GOAL text says `round < 3`; the spec guardrail
  is 5. The overriding task text wins, so the agent asks at most rounds 1–2 and
  force-drafts from round 3. Constant `MAX_CLARIFICATION_ROUNDS = 3` in pipeline.py.
- Draft Agent does NOT emit `api_contract.yaml` (in this codebase the API Agent's
  `contract` mode produces it, and `gen_golden_check.py` already generates it from the
  spec). Matches the existing Stage-2 chain.
- RAG index uses the manual-embedding fallback (spec §5.5): chunk poc_spec.md by
  section, embed with a pluggable `Embedder` (deterministic hash fallback when no
  provider), upsert into `spec_embeddings` (the collection `metadata.ensure_indexes`
  already creates). Atlas Vector Search index definition written to
  `docs/atlas-vector-index.json` as a platform-lead setup step.
- Draft acceptance lives in a new `scripts/draft_check.py` (kept separate from
  `gen_golden_check.py`, which it chains into) rather than a `--draft` flag.

---

## Phase (Draft Agent) — DONE

- Implemented `agents/draft-agent/src/agent_draft_agent/{pipeline.py, rag.py, main.py}`, updated
  `agent.yaml` (tool sandbox lists the five tools), replaced the skeleton test with
  `tests/test_agent.py` (17 tests), added `scripts/draft_check.py`, `docs/atlas-vector-index.json`,
  and `docs/06_status-and-handoff.md`.
- Unit tests: `uv run pytest -q tests` → **17 passed**.
- Live acceptance (`scripts/draft_check.py --skip-npm`, real LLM): **all PASS** —
  recsys drafts (missing=[]; 5 stories each w/ testids; 3 automatable SC; 2 collections w/ seed counts;
  5 query patterns all mapped); vague returns 5 questions covering success_criteria + data_entities;
  chained Stage-2 generation (contract/seed/backend/frontend) + guardrail scan + `node --check` all pass.
- Full npm-build chain (twice) running in background (see /tmp/draft_full.log); recorded below when done.

## Phase A — commit Draft Agent — DONE
- Commit `6d8a4e7` "[overnight] Draft Agent (Spec §6.2): ...". Working tree clean afterward.

## Phase B — Chat Agent — DONE
- Replaced `agents/chat-agent/src/agent_chat_agent/main.py` with the §6.1 ReAct agent: standard
  `agent` node + SDK-agnostic tool loop + `should_continue` (custom tool node so the same code runs
  under the fake SDK in tests and the runner image in prod), `build_llm().bind_tools(app.get_tools())`,
  best-effort `app.memory` recall guarded for absence.
- Tools: Tool Pod — chat_create_poc / chat_get_poc (secrets masked) / chat_record_approval /
  chat_get_run_status / chat_find_run / chat_list_artifacts / chat_read_artifact / chat_presign /
  chat_append_message. Agent Pod (A2A) — chat_call_draft / chat_start_code_run / chat_start_deploy_run /
  chat_run_tests / chat_teardown, each building an AgentEnvelope, `find_agent` by skill (draft-spec,
  code-orchestration, deploy-operations, e2e-tests), with a run-lookup fallback (newest run by
  poc_id+stage) on A2A timeout/exception. The LLM never composes envelope JSON.
- SYSTEM_PROMPT encodes the §6.1 MUST rules (show-before-approve, mandatory gates, one run per stage,
  implicit code_approved for "deploy without review", 3-round clarification then force_assumptions,
  progress from run status, conversation persistence) + a "what you can say" list matching the fixture.
- `agent.yaml`: agent-sandbox = A2A tools; tool-sandbox = DB/S3 tools; a2a skill "chat"; memory true.
- Decision: memory recall/save is wired defensively against `app.memory` (return-shape differs by SDK
  revision per the magenta-agent skill); durable conversation state uses chat_append_message. Did not
  hand-code a specific memory API that the local placeholder SDK cannot verify.
- Decision: used a custom tool-executing node instead of `langgraph.prebuilt.ToolNode` — ToolNode needs
  BaseTool-shaped tools; the fake SDK's tools aren't, and a custom node keeps the loop testable and
  identical across fake/real SDK. Same graph shape (agent → tools → agent).
- Tests: `agents/chat-agent/tests/test_agent.py` — **8 pass**. Scripted fake-LLM run asserts each gate is
  recorded before its run starts (spec_approved→code, code_approved→deploy), implicit approval carries
  through, a pre-gate run attempt starts nothing, and the clarification path asks + starts nothing.


</content>
</invoke>
