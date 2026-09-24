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

## Phase C — local end-to-end (all 8 agents) — PARTIAL (blocked at deploy by Atlas API IP allowlist)

Stack: `agentic dev up --all` (‑‑headless is unsupported with ‑‑all, as the addendum foresaw; ran with
the UI, backgrounded to /tmp/devup.log). All 11 containers healthy; all 8 graphs compiled. Invoke URL
`http://localhost:52983/invoke?workspace=<agent>`; every invoke MUST carry `user_id` (durable memory
identity) and a stable `session_id` for multi-turn.

Drove the happy path through the CHAT AGENT ONLY (session e2e2, user u_local). Timeline:
- **Turn 1** (paste recsys transcript): chat_create_poc → chat_call_draft (A2A) → Draft Agent drafted
  spec **v001** with no clarifications; chat summarised it. POC `poc_01M3ABSPT491B80BDYKMW3DH52`,
  status `spec_ready`. Proves chat→draft A2A + the whole Draft pipeline live. (~2.5 min: LLM gateway
  latency dominates every turn.)
- **Build turn** ("go ahead and build it"): gate `spec_approved v001` recorded, chat→orchestrator A2A
  started code runs. First runs FAILED — see Bug 3.
- After fixes + re-run: code run **succeeded**, `code_ready`, code **v004**, bundle built
  (contract→seed→backend→frontend all via A2A, orch_assemble + orch_finalize OK).
- **Deploy turn** ("deploy without reviewing the code, 4h TTL"): gate `code_approved v004` recorded
  with `implicit:true` (check_gate succeeded), chat→deploy A2A started a deploy run. Deploy **FAILED at
  `provision_db`** — Atlas Admin API returns 403 `IP_ADDRESS_NOT_ON_ACCESS_LIST` for `104.30.164.1`.

### Bugs found and fixed (each committed with a test)
1. **Chat durable-workflow nondeterminism** (`workflow is nondeterministic … changed=messages.tool_artifact`).
   A thin coordinator that mints ULIDs and calls specialists per turn can't satisfy the durable-replay
   guard. Fix: `features.durable_workflow: false` on chat-agent (durability lives in the specialists'
   `runs` docs). Also: every invoke must pass `user_id`.
2. **A2A reply not unwrapped** → `CODER_UNAVAILABLE`/KeyError `'response'`. The OE returns the callee's
   envelope wrapped as `{"status":"completed","result":"<envelope-json-string>","error":null}`; callers
   did `resp["response"]`. Fix: `_unwrap_envelope` in chat/orchestrator/deploy `a2a.py` normalises every
   shape to `{"response": …}`. Unit test in chat. (Confirmed from the captured raw reply in logs.)
3. **Frontend coder `LLM_OUTPUT_INVALID: missing FRONTEND_README.md`** — LLM omitted a build-irrelevant
   doc twice. Fix: `frontend-agent` backfills a default FRONTEND_README.md when absent. Regression test.
4. **Draft `aggregation_sketch` as strings** (from the full draft_check build, RUN 2) — dropped as an
   optional field. (Committed in the earlier draft commit.)

### The deploy blocker (environment, not code) — STUCK per the guard
- Atlas Admin API rejects the machine's egress IP `104.30.164.1` (a Cloudflare WARP address) with 403
  `IP_ADDRESS_NOT_ON_ACCESS_LIST`. Confirmed both inside the deploy container AND host-side — the whole
  machine egresses through that IP. The golden deployment succeeded earlier from a different, then-listed
  IP.
- Every Atlas Admin API call is affected: `provision_db` (shared_db still calls the Admin API to resolve
  the cluster / create the DB user), `allow_instance_ip`, and the Atlas half of teardown. `existing` DB
  mode does not avoid it (the EC2 IP still needs `allow_ip`).
- I cannot fix it within the rules: adding `104.30.164.1` to the Atlas project/API-key access list needs
  an Atlas org admin (UI/SSO — forbidden), and the self-service API call to add it is itself IP-blocked
  (chicken-and-egg). No admin, no fix.
- **Remediation for a human/next session:** in Atlas → Organization Access Manager → Applications /
  API Keys → the service-account key's *API Access List*, add the current egress IP (or 0.0.0.0/0 for a
  dev key), then re-run the deploy turn (`resume_run` on the failed deploy run, or a fresh deploy). Or
  run the whole stack from a host whose public IP is already on that list. Also ensure the POC cluster's
  **network** access list will accept the launched EC2 IP (the deploy's `allow_instance_ip` step adds it,
  once the Admin API is reachable).

### Cost guard status
- My e2e POC created **zero** cloud resources (deploy failed at the first cloud step). Verified
  `list_active_resources(poc_01M3ABSPT…) == []`.
- Golden POC `poc_01K5ZGF1XTVREG0000000000A1`: ran the Deploy Agent teardown pipeline. **EC2
  `i-01d8121191e7999a4` terminated and its Secrets Manager secret deleted** (AWS is not IP-restricted) —
  the only billable resource is gone. Two **non-billable Atlas** entries remain (`atlas_db_user
  poc_00000000a1`, `atlas_access_list_entry 13.206.95.60`) because their deletion needs the blocked Atlas
  API. I did **not** fake-release them in the ledger. They clear automatically once the IP is allowlisted
  and teardown is re-run (or via the reaper).
- No new EC2 launches happened tonight (0 of the ≤3 budget used).

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

## Phase D — reaper — DONE
- `scripts/reaper.py` (§5.7): `list_expired_resources` → group by poc_id → `deploy_pipeline.teardown` per
  POC; `--dry-run`; injectable `reap()` core; cron scheduling in the module docstring.
- `scripts/test_reaper.py`: 3 fake-ledger tests pass (dry-run tears nothing; one teardown per POC; a
  failing POC doesn't block the rest). Not run for real tonight beyond the injectable tests.

## Phase E — finish

### Phases done
A (commit Draft) ✓ · B (Chat Agent) ✓ · C (local e2e) PARTIAL — proven through `code_ready` via chat;
deploy blocked by the Atlas API IP allowlist (documented above) · D (reaper) ✓ · E (this) ✓.

### Commits made (all "[overnight]", on main, nothing pushed)
See `git log --oneline` below. Draft Agent → Chat Agent → reaper → draft sanitizer + dev.yaml pins →
Phase C integration fixes (A2A unwrap / chat durable off / frontend README) → docs.

### Tests (all green, per agent `uv run pytest -q tests`)
draft 18 · chat 9 · frontend 10 · reaper 3 (+ api/seed/orchestrator/deploy/test unchanged and green).

### Happy-path timings (LLM gateway latency dominates)
draft turn ≈ 2.5 min · each coder A2A call ≈ 35–70 s · full code run (4 coders + assemble) ≈ 5 min ·
deploy blocked immediately at provision_db.

### Open items
- **Atlas API IP allowlist** for `104.30.164.1` (blocks deploy + Atlas teardown). #1 to fix.
- Golden POC `poc_01K5ZGF1XTVREG0000000000A1` has 2 residual non-billable Atlas entries (db_user,
  access-list) pending the IP fix; EC2 already terminated.
- e2e POC `poc_01M3ABSPT491B80BDYKMW3DH52` left at status `deploying` (cosmetic; it has 0 cloud
  resources) — a re-run after the IP fix, or a manual `update_poc_status(..., "code_ready")`, tidies it.
- Chat memory recall/save is wired defensively against `app.memory` but not exercised (no VOYAGE calls in
  the local run); verify once a memory provider is configured.
- The api-agent backend occasionally emits a `$unwind` TS type error on non-golden specs (seen in the
  full draft_check RUN 1); the deploy repair loop is designed to catch it — validate once deploy is
  unblocked, or harden the backend prompt.

### Exact next commands for platform deployment (leaf-first)
1. Atlas: add the egress IP to the service-account key's **API Access List** (Atlas UI, org admin), and
   ensure the POC cluster **network** access list will admit the launched EC2 IP.
2. From the repo root: `agentic init`.
3. Set secrets (names only — never values): `agentic secret set LLM_API_KEY --sync`,
   `VOYAGE_API_KEY`, `A2A_JWT_SECRET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
   `ATLAS_CLIENT_ID`, `ATLAS_CLIENT_SECRET`, `POC_PLATFORM_MONGODB_URI`.
4. `scripts/vendor_packages.sh` (already run), then `agentic build` + `agentic deploy` **leaf-first**:
   data-seeding-agent, api-agent, frontend-agent → coding-orchestrator; test-agent → deploy-agent;
   draft-agent → chat-agent.
5. Fill each callee's `allowed_callers` with the caller workspace IDs (docs/02 §4.1 table):
   Draft←Chat; Orchestrator←Chat,Deploy; Seed/API/Frontend←Orchestrator; Deploy←Chat; Test←Deploy,Chat.
6. Create the Atlas Vector Search index `spec_vector_idx` on `poc_builder.spec_embeddings`
   (docs/atlas-vector-index.json).
7. Re-run the happy path through chat; re-run the golden teardown to clear its residual Atlas entries.

### Final resource state (cloud_resources active count per poc_id)
- `poc_01M3ABSPT491B80BDYKMW3DH52` (e2e): **0**
- `poc_01M3ABHFXNQ5QXRB5X8XABZY41` (stray from failed Turn 1): **0**
- `poc_01K5ZGF1XTVREG0000000000A1` (golden): **2** — atlas_db_user + atlas_access_list_entry
  (non-billable; blocked by the Atlas IP allowlist). EC2 terminated, secret deleted.
Not the all-zero the cost guard targets: the 2 golden Atlas entries cannot be deleted without Atlas API
access, and I did not fake-release them. They clear on re-run once the IP is allowlisted.
