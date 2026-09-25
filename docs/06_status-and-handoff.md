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

## Overnight run outcome (see docs/07 for the full narrative)

- **chat-agent** implemented (§6.1 ReAct front door) and **proven live**: through the chat agent only,
  transcript → spec v001 (chat→draft A2A) → `spec_approved` gate → code run v004 (`code_ready`,
  chat→orchestrator→3 coders A2A) → `code_approved` (implicit) gate. All gates recorded before their runs.
- **reaper** added (`scripts/reaper.py`, §5.7) with a fake-ledger unit test.
- Four real integration bugs found by running the full `agentic dev up --all` stack and fixed with tests:
  chat `durable_workflow: false` (nondeterminism); A2A `_unwrap_envelope` (OE wraps the reply as
  `{"result":"<envelope json>",...}`); frontend backfills a missing FRONTEND_README.md; draft drops a
  malformed optional `aggregation_sketch`. Every invoke must pass `user_id`.
- **BLOCKER (deploy):** Atlas Admin API rejects the machine egress IP `104.30.164.1` (403
  `IP_ADDRESS_NOT_ON_ACCESS_LIST`). Deploy fails at `provision_db`; the Atlas half of teardown fails too.
  Fix = add that IP to the Atlas service-account key's API Access List (needs an Atlas admin), then re-run.
- **Golden teardown:** EC2 `i-01d8121191e7999a4` terminated + secret deleted (AWS ok). Two non-billable
  Atlas entries remain (blocked); clear them by re-running teardown once the IP is allowlisted.

## Root-stack invoke mechanics (`agentic dev up --all`)

The local UI proxies `/invoke` to the OE and injects the per-workspace scope from `AGENTIC_WORKSPACES_JSON`
(orgId / **projectId `550725d3a4fb9b891ad1bdcd`** / workspaceId). A plain `POST /invoke` with no workspace
returns `{"error":"project_id does not match orchestration engine scope"}` — you MUST scope it.

- **Port:** the UI is published on a random localhost port; read it from `docker ps` — the row
  `15_hackathon2026-all-ui-1  127.0.0.1:<PORT>->3000/tcp`. It changes across `dev up` runs (was `52983`
  this session). App containers are `15_hackathon2026-all-app-<agent>-1`; the platform DB + S3 live on the
  Atlas POV cluster (each app process sources `/app/.env` at runtime — `POC_PLATFORM_MONGODB_URI` there wins
  over the compose `MONGODB_URI`, which points at the throwaway local `mongodb` container).
- **Scope:** add `?workspace=<agent>` (e.g. `chat-agent`); the proxy fills in the matching project/workspace.
- **Body:** `{"message": "<plain English>", "session_id": "<stable id>", "user_id": "<id>"}`. `session_id`
  reuse is what makes a multi-turn conversation cohere — the SDK derives the LangGraph `thread_id` as
  `session_id:workspace_id`, so reuse the same `session_id` across turns of one conversation and pick a fresh
  one to start over. `user_id` is mandatory (durable-memory identity; the chat agent also stamps approvals
  with it). Response: `{"result":"<agent reply text>","session_id":..,"user_id":..,"execution_id":..,"status":"completed"}`.
- **Curl:**
  ```bash
  PORT=$(docker ps --format '{{.Names}} {{.Ports}}' | sed -n 's/.*ui-1 127.0.0.1:\([0-9]*\)->3000.*/\1/p')
  curl -s -X POST "http://127.0.0.1:$PORT/invoke?workspace=chat-agent" -H 'content-type: application/json' \
    -d '{"message":"How is it going?","session_id":"e2e3","user_id":"u_local"}'
  ```
- **Turn latency:** the chat agent is a ReAct loop, so a turn is several LLM round-trips (gateway latency
  dominates: ~30–70 s each). Stage-start tools no longer block for the whole stage (see below), but a turn
  can still run a couple of minutes if the LLM takes several tool steps — poll from the CLI with a long
  `curl -m`, or fire the turn and watch the `runs` document directly.

### Fixes made during the live end-to-end run (2026-09-25)
The Atlas API allowlist unblocked deploy; driving the happy path through chat then surfaced five real bugs,
each fixed with a test:
- **Chat replies fast after starting a stage** (ergonomics). `chat_start_*`/`chat_teardown` used to wait on
  the A2A call up to the 300 s ceiling (the SDK's `invoke_a2a_agent` has no timeout and blocks until the
  callee graph returns), so a long turn came back as Bad Gateway / empty to the UI while the run carried on
  unseen. The specialists are durable (`durable_workflow: true`) and persist their `runs` document at graph
  start, so `_a2a_run` now runs the A2A call on a **context-copied daemon thread**
  (`contextvars.copy_context()` — without it the SDK call silently fails to leave the process), waits only a
  short ack (`A2A_ACK_TIMEOUT_S`), then watches for the freshly registered run doc (`_await_new_run`),
  returns `{"status":"started","run_id":…}`, and tells the user to ask "how's it going?". Verified live:
  a teardown turn returned in ~22 s with a new run_id while the teardown ran in the background.
- **A failed deploy releases the POC status.** `deploy_start_run` flips `pocs.status` to `deploying`;
  previously a failed run left it stuck there (blocking a fresh deploy — exactly the overnight state).
  `deploy_finish_run` now resets `pocs.status` back to `code_ready` when a **deploy-stage** run finishes
  `failed` (teardown finishes via `metadata.finish_run` directly, so it is unaffected). Test:
  `test_failed_deploy_resets_poc_status`.
- **Deploy found the Test Agent / Orchestrator by internal name, not skill.** `tests_node`/`repair_node`
  called `find_agent("test_agent")` / `find_agent("coding_orchestrator")`, but A2A discovery exposes the
  **skill** name; the lookup failed with `no A2A agent matching 'test_agent'`. Now `find_agent("e2e-tests")`
  / `find_agent("code-orchestration")` (matching how the Chat Agent resolves them). The fake-SDK tests now
  key handlers by skill name so they mirror real discovery.
- **Frontend repair choked on trailing prose.** `parse_output` did a plain `json.loads`, which failed with
  `Extra data: line 1 column N` when the model appended notes/REPAIR_NOTES after the JSON object; the repair
  code run failed and the deploy exhausted its repair budget. It now decodes the first complete JSON value
  with `json.JSONDecoder().raw_decode` (tolerating leading and trailing prose). Test:
  `test_parse_output_tolerates_trailing_and_leading_prose`.
- **Deploy URLs used the raw IP, not the public DNS.** `publish_frontend` built the app/api/health URLs from
  `public_ip`; the golden deployment uses the EC2 **public DNS** name (and the egress proxy refuses raw-IP
  hosts). Now it uses `public_dns` (falling back to the IP only if none is assigned). Test:
  `test_publish_frontend_urls_use_public_dns`. NB: the delivered e2e run was deployed just before this fix so
  its recorded URLs are IP-based; the fix applies to subsequent deploys.

## Platform 0.11.1 migration (2026-09-25)

The platform runner/OE was upgraded to **0.11.1**, which **renamed the SDK distributions** and their
top-level modules. Every deployed pod failed at import with
`No module named 'magenta_sdklanggraph'`. The CLI (agentic **0.1.101-alpha**, runner-base 0.1.101-alpha)
still ships the OLD SDK (`magenta_sdklanggraph` 0.0.89) locally, so the cloud was ahead of the CLI.

**The module name (established empirically, not guessed).** Local containers only had the old SDK and
the platform ECR account (`867958226915`) is not readable with our POC-builder keys, so the truth was
recovered by deploying a probe that **raises a `RuntimeError` at import time** whose message enumerates
the installed distributions, their site-packages module dirs, and each module's public `dir()`. Import
exceptions are logged verbatim by the runner (`Failed to import module 'agent_<x>.main': <message>`), so
the answer came back in `agentic logs`. Result:

| old (0.0.89) dist / module          | new (0.11.1) dist / module                    |
|-------------------------------------|-----------------------------------------------|
| `magenta-sdklanggraph` / `magenta_sdklanggraph` | `agent-engine-sdk-langgraph` / **`agent_engine_sdk_langgraph`** |
| `magenta-sdk-core` / `magenta_sdk_core`         | `agent-engine-sdk` / `agent_engine_sdk` (has `BaseApp`) |
| `runner-shared` / `runner_shared`               | `agent-engine-runner-shared` / `agent_engine_runner_shared` |
| — (new)                                          | `agent-engine-sdk-memory` / `agent_engine_sdk_memory` |

**API differences: none.** `agent_engine_sdk_langgraph`'s public surface is **byte-identical** to the old
`magenta_sdklanggraph` — same `App` (with `tool`, `get_tools`, `a2a_tools`, `entrypoint`, `checkpointer`,
`run`, `memory`, `llm`, `get_current_user_id`), same `Memory`, `PlatformCheckpointer`, `secure_llm`,
`durable_*`, etc. The A2A helpers `find_agent` / `invoke_a2a_agent` are **our own** wrappers in each
agent's `a2a.py` (over `app.a2a_tools()`), not SDK imports, so they were untouched. The migration is
therefore purely a **module-import rename** plus the sandbox-layout change.

**What changed (all 8 agents):**
- `main.py`: `from magenta_sdklanggraph import App` → `from agent_engine_sdk_langgraph import App`.
- `tests/conftest.py`: the fake SDK is registered under `sys.modules["agent_engine_sdk_langgraph"]`.
- `agent.yaml`: ran `agentic migrate sandboxes` in each agent dir — it moved the top-level
  `network: {egress_mode: allow_all}` **into each sandbox profile** (`agent` + `tool`), reindented to
  2 spaces, and **dropped the `is_local=` placement args** from every `@app.tool(...)` (the sandbox
  `tools:` lists in agent.yaml are now authoritative for pod placement). It did **not** rename the import.
- pyproject deps were already correct (`agent-engine-runner-shared[mongodb,tracing]` +
  `agent-engine-sdk-langgraph`) — the packages resolved fine all along; only the Python import was stale.
- No try/except shims, no vendored SDK. Per-agent `uv run pytest -q tests` stays green
  (seed 9 · api 11 · chat 11 · orchestrator 9 · deploy 8 · draft 18 · frontend 11 · test 11).

**Packaging rules kept (do not regress):** protobuf pinned `>=6.33.6,<7` in every pyproject; `uv.lock`
excluded from the build archive via `.agenticignore` (a shipped lock freezes versions the platform
packages can't satisfy); `agents/*/.env` are real files omitting the reserved `A2A_JWT_SECRET`; run
`./scripts/vendor_packages.sh` before every build. `scripts/check_platform_contract.py` asserts all of
the above (SDK dep lines, import line, sandbox layout) against one canonical definition and is wired into
CI in `docs/ci/agents-map.yml`.

**Exact commands that worked (context `hackathon2026`, runner/OE 0.11.1):**
```bash
# per agent directory, once:
cd agents/<agent> && agentic migrate sandboxes && cd -
# before every build:
./scripts/vendor_packages.sh
# build (parallel with --no-wait), then deploy (parallel with --no-wait):
agentic build  --workspace <agent> --context hackathon2026 [--no-wait]
agentic deploy --workspace <agent> --context hackathon2026 [--no-wait]
# a healthy status is NOT proof; only a real invoke reply is:
agentic invoke --workspace <agent> --context hackathon2026 --json "hello"
# import failure surfaces as: Error: invoke: The agent did not become ready during startup
# a successful graph replies with an AgentEnvelope (e.g. INVALID_ENVELOPE for a non-envelope message)
```

**Result — all 8 deployed and invoke-proven (2026-09-25).** data-seeding, api, frontend,
coding-orchestrator, test, deploy, draft each return a real `INVALID_ENVELOPE` AgentEnvelope reply
(graph imported); chat-agent answers "How is it going?" conversationally. Build/deploy ids:

| agent | build id | deployment id |
|-------|----------|---------------|
| data-seeding-agent | `bld_01M3BSP4Q77C26H33CJTGJK52M` | `deploy-db3bf360` (r4) |
| api-agent | `bld_01M3BT0JYCTG1MEFD2KZDTHESV` | `deploy-bb10e645` |
| frontend-agent | `bld_01M3BT0Q6D3ZPXAX0WFYDY1Z7G` | `deploy-a6d7ce78` |
| coding-orchestrator | `bld_01M3BT0V7R3M47GK260RZTWZ7D` | `deploy-e206cee1` |
| test-agent | `bld_01M3BT0ZCW1ZZGV0KXPYBY47ED` | `deploy-1c148488` |
| deploy-agent | `bld_01M3BT13AW8HPY14BAGM9V5BFV` | `deploy-dc56bad7` |
| draft-agent | `bld_01M3BT17JTNSXM1JEFB4396AWC` | `deploy-ba524644` |
| chat-agent | `bld_01M3BT1C1KP0SX71BQCVDAYTB4` | `deploy-029e9402` |

## Platform execution model (2026-09-25)

Driving the happy path through the **deployed** chat agent surfaced a cloud-only failure the local stack
never showed: after the orchestrator started and ran api-agent (`api_execute`, contract, 38 s) it went
completely silent — no timeout, no fallback, no further log lines — leaving run
`run_01M3BWT1AT8VQDXDAW2YX0TWQH` (POC `poc_01M3BW7CNPFCPNSQDN7JFMFVVR`) stuck with task 1 "running" and
zero cloud resources. Root cause is the platform execution model:

- **A2A is a CHILD EXECUTION of the calling turn/session.** When the calling turn completes (the chat
  agent fast-acks a stage start and returns), or the session is finished/reclaimed, the platform
  **cancels any live child A2A execution**. Locally the OE let children outlive the parent turn, which is
  exactly what the chat agent's fast-ack pattern relied on — so it worked in `agentic dev up` and died in
  the cloud. The orchestrator (a chat child) was killed the moment the chat turn returned.
- **The A2A call timeout is capped at 300 s.** Even without the parent-turn cancellation, a single A2A
  call cannot cover a minutes-long stage.
- **The Playground stream drops on long turns.** For the end-to-end proof, use the CLI
  (`agentic invoke --workspace chat-agent --context hackathon2026 --session <id> --user-id <id> --json`),
  not the Playground.

### New stage-start design (chat-agent) — top-level invoke, not A2A child

Only **how long-running stages are STARTED** changed. For code, deploy and teardown starts
(`chat_start_code_run`, `chat_start_deploy_run`, `chat_teardown`), the A2A child call is replaced with a
**TOP-LEVEL invocation** of the specialist's workspace through the platform invoke API — the same call an
external client makes — so each run is its **own root session, independent of the chat turn** and survives
the turn ending (and our short client-side disconnect). At the time this was written **draft stayed A2A**
(it returns within the turn) and **orchestrator→coders**, **deploy→test** and `chat_run_tests` were left on
A2A — that last part was **superseded** once the token-TTL blocker below bit; see "Shared transport +
token-TTL resolution" at the end of this section. The specialists' graphs, gates, and `chat_get_run_status`
polling are unchanged. The fast-ack ergonomics are identical (`_invoke_run` mirrors `_a2a_run`: fire on a
context-copied daemon thread, wait a short ack, then watch the `runs` document, return
`{"status":"started","run_id":…}`).

The invoke transport lives in **`poc_shared_tools.platform_invoke`** (stdlib `urllib`, no new deps; moved
here from the chat agent so every agent can use it — `agent_chat_agent/platform_invoke.py` is now a thin
alias). Exact API calls:

- **Token** (cached for its lifetime, refreshed on 401): `POST {base}/api/v1/oauth/token`,
  `Content-Type: application/x-www-form-urlencoded`, body
  `grant_type=client_credentials&client_id=$POC_PLATFORM_SA_CLIENT_ID&client_secret=$POC_PLATFORM_SA_CLIENT_SECRET`.
  Returns `{access_token, token_type, expires_in}`; bad creds → `401 {"error":"invalid_client"}`. Creds are
  project secrets (service account `poc-builder-chat`, role AGENT_DEVELOPER, client id
  `ae_sa_id_6ab63f46bcd65e0d08e0a405`); chat-agent's `agent.yaml` grants them via `secrets: ["*"]` and the
  agent reads them from the pod env like every other secret. `base` = `AGENTIC_PLATFORM_BASE_URL` or
  `https://agentic-platform.mongodb.com`. Egress is allow_all so the pod reaches the public API.
- **Workspace id resolution** (by skill, no hard-coded ids): A2A discovery only exposes the A2A *app id*
  (e.g. `902433…`), **not** the `ws-…` workspace id the invoke API needs, so we resolve via
  `GET {base}/api/v1/projects/{project}/workspaces?limit=200` (Bearer token) →
  `{workspaces:[{workspace_id, name}]}` and map skill→workspace-name→id (cached). Env override
  `POC_WORKSPACE_IDS` (JSON `{skill: ws-id}`) wins if set. `project` = `PROJECT_ID`/`GROUP_ID` from the pod
  env, else the fixed project constant.
- **Invoke** (fired on a daemon thread, short client timeout — the run may take minutes but keeps executing
  server-side after we disconnect):
  `POST {base}/api/v1/projects/{project}/workspaces/{ws}/invoke`, `Authorization: Bearer <token>`,
  `Content-Type: application/json`, body `{"message": <AgentEnvelope JSON, exactly as A2A passes it>,
  "session_id": "<stage>-<run_id>", "user_id": <caller user>}`. Response
  `{"success":true,"response":"<envelope>","execution_id":…,"status":"completed"}`. We do not wait for it;
  `_await_new_run` returns the freshly-registered `runs` document.

`scripts/check_platform_contract.py` mechanically guards the rule: the three start tools must call
`_invoke_run` and must not call `_a2a_run`.

### Two operational findings from the live cloud run (2026-09-25)

1. **The synchronous invoke gateway caps a turn at ~60 s (HTTP 504).** A non-streaming
   `POST /workspaces/<id>/invoke` (and the CLI without `--stream`, and the Playground) returns **504 at
   ~60 s** if the agent turn has not finished. The chat **draft turn** (chat→draft A2A + summarise, ~2.5 min)
   exceeds that. The chat *root* session keeps running server-side past the 504 (the POC advanced to
   `spec_ready`), but the HTTP response is lost and — because chat is `durable_workflow:false` — the pod is
   recycled at the cap, cancelling the in-flight draft A2A child. **Fix for driving the demo: use
   `agentic invoke --stream`** (or `/invokeStream`); streaming keeps the connection alive through a long
   turn. Verified: the draft turn completed cleanly over `--stream` (spec v001, no 504). The stage-start
   turns (code/deploy/teardown) now fast-ack in <60 s so they are fine either way.

2. **BLOCKER — the A2A/OE bearer token has a ~5-minute lifetime and cannot be refreshed from app code.**
   With the chat→orchestrator fix in place the orchestrator finally runs the **whole** coder chain in the
   cloud (contract→seed→backend→frontend) instead of dying after contract — and that exposed a latent cap:
   a full code run is ~5.5–6 min (4 coder calls × ~1.5 min), which **outlives the orchestrator's A2A token**.
   The SDK mints the token once when the A2A client is first created (`A2A client created for OE …`, logged
   exactly once per run) and caches the client at the `App` level. Around the 5-minute mark
   `GET …/a2a/discover?limit=50` returns **401 Unauthorized**, discovery comes back empty, and
   `find_agent("generate-frontend")` raises `no A2A agent matching 'generate-frontend'` on the **last** coder
   (contract/seed/backend all land inside the window). Evidence (three runs): token minted `10:55:59`, first
   401 at `11:01:03` (5m04s later). **An app-level workaround does not work:** `find_agent` now drops the
   cached tools and re-calls `app.a2a_tools()` on a no-match and retries once (committed, with unit tests, in
   coding-orchestrator and deploy-agent), but the retry **401s again immediately** and no new client is
   created — re-calling `a2a_tools()` reuses the cached OE client + expired token. The real SDK that owns the
   token is cloud-only (ECR not readable with POC-builder keys), so the refresh path is not reachable from
   here. **This is a platform cap** (analogous to the 300 s A2A ceiling): any A2A call chain that runs longer
   than ~5 min will 401 on later calls. It blocks the cloud code stage at the frontend coder, and would block
   the deploy stage's deploy→test lookup at the end of a long deploy run. Per the task guardrail we stop and
   report rather than re-architect orchestrator→coders (explicitly "stays A2A") onto top-level invoke.
   **Open question for the platform/SDK owners:** how does an agent refresh or extend the OE A2A token
   mid-run — is there an `App` method to force OE-client/token recreation, a longer-TTL/refresh config, or
   must a >5-min multi-call A2A chain be re-architected (e.g. coders started as top-level invokes + run-doc
   polling, like chat now starts the orchestrator)?

### Shared transport + token-TTL resolution (2026-09-25)

The token-TTL blocker (finding 2) is resolved by re-architecting the two long-lived A2A call chains onto the
same top-level-invoke transport chat already uses, rather than waiting on an SDK token-refresh. The decisive
rule, now applied everywhere:

> **short in-turn hop → A2A; anything that can outlive the turn or the ~5-min A2A token → a top-level
> platform invoke with a service account** (own root session, freshly-minted token).

Concretely:

- **`platform_invoke` moved into the shared package** `poc_shared_tools.platform_invoke` (was
  `agent_chat_agent/platform_invoke.py`, now a thin alias) so any agent can use it. It gains
  `invoke_envelope(skill, envelope, *, user_id, session_id, timeout_s=900)` — a **synchronous** helper that
  blocks for the callee's whole reply and returns it normalised to `{"response": <AgentEnvelope>}` (the exact
  shape the A2A path yielded via `_unwrap_envelope`, so callers keep `...["response"]` unchanged). The
  skill→workspace-name map covers the coder + test skills (`generate-api`→api-agent, `generate-seed`→
  data-seeding-agent, `generate-frontend`→frontend-agent, `e2e-tests`→test-agent, plus the existing
  code-orchestration/deploy-operations/draft-spec).
- **coding-orchestrator → each coder** is now a **synchronous top-level invoke** (`invoke_envelope`, 15-min
  client timeout), not an A2A child. Each coder runs in its own root session with a fresh token, so a full
  4-coder run (~6 min) no longer 401s on the last coder. `A2AClient` (and its now-unreachable refresh retry)
  is removed from the orchestrator; `a2a.py` keeps only `invoke_tool` for its own Tool-Pod calls.
- **deploy-agent → test agent** and **deploy-agent → orchestrator (repair)** likewise become **synchronous
  top-level invokes**: both fire late in a long deploy run, past the ~5-min token. The test agent already
  replies synchronously with the report; the orchestrator replies with the repaired `code_version`. The
  A2A-timeout run-doc recovery in `tests_node` is kept as a client-disconnect fallback. `A2AClient` is
  removed from the deploy agent too.
- **chat_run_tests** moves from `_a2a_run` to `_invoke_run` (top-level), so the **only** remaining A2A caller
  in the system is **chat → draft** (a short in-turn hop that returns within the turn and the token).
- **Secrets:** the coders/orchestrator/deploy pods read the same SA creds (`POC_PLATFORM_SA_CLIENT_ID` /
  `POC_PLATFORM_SA_CLIENT_SECRET`, service account `poc-builder-chat`) from the pod env; their `agent.yaml`
  already grants all project secrets via `secrets: ["*"]`.
- **Guard:** `scripts/check_platform_contract.py` now asserts coding-orchestrator and deploy-agent make their
  cross-agent calls via `platform_invoke.invoke_envelope` and reference **no** A2A call path
  (`A2AClient`/`find_agent`/`invoke_a2a`) in their graph, and that all four chat start/drive tools (code,
  deploy, teardown, tests) use `_invoke_run`, not `_a2a_run`.
- **Caveat still open (finding 1):** a synchronous top-level `POST …/invoke` is capped at ~60 s by the
  gateway (504). A coder call is ~35–70 s and a test run is minutes, so both `invoke_envelope` callers can
  exceed that cap on the public gateway. deploy→test tolerates it (the test agent's root session keeps
  running and creates its run doc; the `tests_node` fallback polls `deploy_find_test_run` for the outcome),
  but a coder that runs >60 s has **no** run-doc fallback and would surface as `CODER_UNAVAILABLE`. If the
  cloud proof hits this cap, per the task guardrail we stop and report rather than re-architect coders onto
  started+poll. (See the cloud-proof note below / docs/07 for the observed outcome.)

## Remaining plan

1. **Allowlist the egress IP on the Atlas API access list** (unblocks all Atlas ops), then re-run the
   deploy turn through chat (or `resume_run` the failed deploy run) → deployed → tested → torn_down; and
   re-run the golden teardown to clear its last 2 Atlas entries.
2. Platform deployment (leaf-first): `agentic init` from the root; set secrets (names only:
   `LLM_API_KEY`, `VOYAGE_API_KEY`, `A2A_JWT_SECRET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`,
   `ATLAS_CLIENT_ID`, `ATLAS_CLIENT_SECRET`, `POC_PLATFORM_MONGODB_URI`); atlas setup + data-plane IPs;
   `agentic build`/`deploy` seed, api, frontend → orchestrator; test → deploy; draft → chat; fill each
   callee's `allowed_callers` with the caller workspace IDs (§4.1 table in docs/02).
3. Create the Atlas Vector Search index `spec_vector_idx` on `poc_builder.spec_embeddings`
   (docs/atlas-vector-index.json) for Draft-Agent RAG.
