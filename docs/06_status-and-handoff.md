# POC Builder — status and handoff

A new session should be able to continue from this file. Last updated during the Draft Agent session
(and the overnight run that follows it — see `docs/07_overnight-report.md`).

## What exists and is proven

- **`poc_contracts` 0.1.1** — frozen JSON schemas (envelope, poc_spec_frontmatter, schema_design,
  query_patterns, component/poc manifest, deployment, test_report, failure_report, and the §5.5
  documents), `Envelope` builders, `new_id`/`next_version`. `validate(kind, obj)` / `is_valid`.
- **`shared_tools` / `infra_tools`** — `s3` (keys forced under `pocs/{poc_id}/`; deletes only under
  `deploy/`,`test/`), `metadata` (pocs/runs/tasks/conversations/cloud_resources; one-running-run-per-stage
  partial unique index), `config`, `guardrails`, `secrets`; EC2/SSM/Atlas tools.
- **Golden fixtures** — `fixtures/golden/spec/v001/*` (worked example the Draft Agent reproduces),
  `fixtures/transcripts/{recsys_meeting,vague_meeting}.txt`, `fixtures/conversation_happy_path.json`.
- **deploy-agent** — golden deploy succeeded end to end on EC2, run `run_01M39W0SAPZC91CTA1RW0E8R7W`
  (poc `poc_01K5ZGF1XTVREG0000000000A1`); teardown / resume / repair paths exercised.
- **test-agent** — 9/9 on the golden deployment, p95 119 ms.
- **Stage-2 agents** (coding-orchestrator, data-seeding, api contract+code+repair, frontend) +
  `scripts/gen_golden_check.py` (contract→seed→backend→frontend builds with the live LLM, passes 2×).
- **draft-agent** (this session) — see below. **chat-agent** — implemented in the overnight run
  (Phase B; see `docs/07`). Was a skeleton before that.

## Draft Agent (Spec §6.2) — this session

- Entry point `draft_spec(poc_id, transcript_key?, answers?[{question_id, answer}], template_poc_id?,
  force_assumptions?)`. Graph: `parse → load → analyze → (questions | generate → finalize) → reply`.
  Tools all run in the Tool Pod: `draft_load`, `draft_analyze`, `draft_questions`, `draft_generate`,
  `draft_finalize` (`agents/draft-agent/src/agent_draft_agent/main.py`).
- Pure, LLM-injectable pipeline in `pipeline.py`: `analyze` (extract + `missing`, retry once),
  `generate_questions` (≤5, ids `q<round>-<n>`, deterministic fallback guarantees field coverage),
  `generate` (assembles + validates poc_spec_frontmatter / schema_design / query_patterns, retries once
  on ContractError; forces `database_name = poc_id`, `deterministic_seed = 42`, seed counts ≤ 10000,
  ≥2 automatable success criteria, 3–7 stories with testids, patterns mapped to real story ids;
  guardrail-scans for connection strings). Golden fixture embedded as the one-shot exemplar.
- Clarification cap: `MAX_CLARIFICATION_ROUNDS = 3` (task text: ask while `round < 3`; then force-draft).
- RAG (`rag.py`): chunk poc_spec.md by section → `Embedder` (deterministic 1024-dim hash fallback;
  swap in Voyage/memory-server via `POC_EMBED_PROVIDER`) → upsert `spec_embeddings`
  `{poc_id, spec_version, chunk_id, section, text, embedding, owner_user_id}`. Best-effort in finalize.
  Atlas Vector Search index `spec_vector_idx` definition in `docs/atlas-vector-index.json` (platform-lead
  setup step). Note: `s3.delete_object` forbids deletes outside deploy/test, so the pending
  clarifications file is **tombstoned** (`{"resolved_into": vNNN}`), not deleted.
- Does **not** emit `api_contract.yaml` (the API Agent's `contract` mode produces it; the Stage-2 chain
  and `gen_golden_check.py` generate it from the spec).
- Tests: `agents/draft-agent/tests/test_agent.py` — 17 pass (`uv run pytest -q tests`).
- Live acceptance: `scripts/draft_check.py` — recsys drafts + validates, vague returns ≥2 questions
  covering success_criteria & data_entities, chained into the Stage-2 build (contract→seed→backend→
  frontend). Passes with the live LLM.

## Environment facts

- AWS shared account **979559056307**; SSO profile **manish_mongo_AWS** for admin work — prefix admin
  CLI with `env -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY` because `.env` exports the platform user's
  keys. Only `msinha-` resources: bucket **msinha-hackathon**, SG **msinha-poc-instance-sg**
  (sg-0bee8a4cde8c2416f), role/profile **msinha-poc-instance-role**, IAM user **msinha-poc-builder**.
  Default VPC **vpc-0d2951724bd3d2170**, subnet **subnet-0ff518b49a66697fb**.
- Atlas project **69841ac356a4daced92e3d2a**, cluster name lowercase **"pov"** (platform DB `poc_builder`
  and POC databases), service-account auth.

## Platform facts

- CLI agentic **0.1.101-alpha** (0.1.108 has no darwin/arm64 build). Runner image ships the SDK as
  **`magenta_sdklanggraph` 0.0.89** (`agent_engine_*` names are placeholders); every agent imports
  `from magenta_sdklanggraph import App`. Locally the SDK is a placeholder, so tests use the fake SDK in
  each agent's `tests/conftest.py`.
- `uv.toml` cooldown removed in every agent. Shared packages are vendored into `agents/<x>/vendor/` by
  `scripts/vendor_packages.sh` before `dev up`/`build`.
- SDK-wrapped tools are invoked with a full ToolCall dict (`a2a.invoke_tool`). The local Tool Pod egress
  proxy rejects IP-literal hosts (use hostnames; public reachability checked from the instance via SSM).
  `agentic dev logs` never returns through a pipe (use `docker logs <container> --since`).
- `dev.yaml` pins the playground to 3000 so only one agent stack runs at a time unless run from the root
  with `--all`. `A2A_JWT_SECRET` must be shared in the root `.env` for local A2A. A2A ceiling 300 s, so
  long-running callees create their runs document first and callers fall back to polling `runs`
  (test: `inputs.deployment_run_id`; code: `stage + poc_id`).

## How to run things

- `scripts/load_golden.py` seeds the golden POC into S3 + platform DB.
- Draft acceptance: `cd agents/draft-agent && set -a && . ../../.env && set +a && uv run python
  ../../scripts/draft_check.py [--skip-npm|--skip-chain|--keep]`.
- Stage-2 acceptance: `cd agents/api-agent && set -a && . ./.env && set +a && uv run python
  ../../scripts/gen_golden_check.py`.
- `/invoke` curl pattern with an AgentEnvelope as the message + polling snippets: see below / `docs/07`.

## Remaining plan

chat-agent (done overnight — verify); root `agentic dev up --all` happy path
(`fixtures/conversation_happy_path.json`); reaper; `agentic init` / secrets (incl. AWS keys) / atlas
setup / data-plane IPs; build+deploy leaf-first (seed, api, frontend → orchestrator; test → deploy;
draft → chat) and fill `allowed_callers`; end-to-end on the platform; tear down the golden instance.
</content>
