# Runbook — deploying and verifying agents on the MongoDB Agentic AI Platform

Written from the POC Builder deployment of 2026-09-25 (CLI `agentic 0.1.101-alpha`, cloud runner/OE **v0.11.1**).
Every step below was executed and either worked as written or failed in the way noted. Use it in order.
Placeholders: `<ctx>` = your CLI context name, `<agent>` = workspace name, `<ws-id>` = workspace id.

---

## 0. Platform facts you must know before you start

| Fact | Consequence |
|---|---|
| Cloud SDK module is `agent_engine_sdk_langgraph` (dist `agent-engine-sdk-langgraph`); core is `agent_engine_sdk`. The old `magenta_sdklanggraph` / `magenta-sdklanggraph` no longer exist in the registry. | `from agent_engine_sdk_langgraph import App`. Deps: `agent-engine-runner-shared[mongodb]`, `agent-engine-sdk-langgraph`. The CLI's **local** images still ship the old names, so local `dev up` and cloud differ. |
| The platform runner requires `protobuf >= 6.33.6, < 7`. | Pin it in `pyproject.toml`. |
| A `uv.lock` inside the build archive is honoured and can make the platform's own packages unresolvable. | Keep `uv.lock` out of the archive (`.agenticignore`). The agent template gitignores it anyway. |
| The CLI refuses a **symlinked** `.env` in an agent directory. | Each agent dir needs a real `.env` file (mode 600). |
| `A2A_JWT_SECRET` is a reserved platform secret name. | Never set it; remove it from any `.env` the CLI reads. |
| Every populated `.env` key must exist as a project or workspace secret, or `deploy` stops. | Load secrets first (§3). |
| New workspaces start with **deny_all** egress on both sandboxes (agent, tool). Base policy allows DNS, in-namespace services and the linked Atlas cluster only. | Any other outbound host (LLM API, third-party API, other Atlas projects) must be allow-listed in `agent.yaml` and redeployed (§7). |
| "Deployment succeeded / healthy" does **not** mean the agent's graph imported. | Only a successful `agentic invoke` proves it (§6). |
| An A2A call is a **child execution of the calling turn**; the platform cancels children when the parent turn/session ends or is reclaimed. A2A timeout max **300 s**. | Long background work must not hang off a chat turn as an A2A child. **This POC now uses NO A2A cross-agent call at all** — every stage (draft included, as of 2026-09-25) is a top-level invoke; see §8. |
| The A2A bearer token minted at session start is **not refreshed** (guide: "refresh is not yet implemented"). Observed TTL ≈ 5 min. | A session whose A2A calls span longer than that gets 401 on discovery/invoke. |
| Playground streaming drops on turns longer than ~60 s; the server-side turn continues. | For long turns use `agentic invoke --stream --timeout 8m`. |
| A **synchronous** top-level `POST …/invoke` (and CLI without `--stream`) is capped at **~60 s → 504** while the callee's root session runs on to completion. | A callee that reliably runs > ~60 s must be **fired + polled**, not awaited synchronously (§8.5 tier 3). |
| Log ingestion lags 30–90 s. | Wait before concluding "no logs". |

---

## 1. Code pre-flight (do this before touching the platform)

For every agent directory:

```bash
# import + deps
grep -n "import App" src/*/main.py            # must be: from agent_engine_sdk_langgraph import App
grep -nE 'agent-engine-runner-shared|agent-engine-sdk-langgraph|protobuf' pyproject.toml
# agent.yaml shape: sandboxes with their own network blocks, no is_local= on tools
agentic migrate sandboxes                     # run inside the agent dir; review the diff; commit
# real .env, no reserved keys
test -L .env && echo "SYMLINK — replace with a real file"
grep -c '^A2A_JWT_SECRET=' .env               # must be 0
```

Root of a monorepo: `.agenticignore` contains `uv.lock`; root `agent.yaml` lists every agent under `agents:`.
Run the unit tests with the fake SDK (`uv run pytest -q tests`) — they must be green before any build.

If the repo has `scripts/check_platform_contract.py` (POC Builder does), run it: it asserts all of the above.

---

## 2. Platform project init

```bash
agentic auth login                                              # once
agentic project list --org-id <org> --base-url https://agentic-platform.mongodb.com   # get the PROJECT id — verify the name!
agentic init --org-id <org> --project-id <project> --context <ctx> --yes --json        # from repo root
agentic context list
```

- Monorepo root with `agents:` map → init registers **one workspace per agent** in one go.
- Single agent → run init inside that agent dir.
- **Check the banner** of the next command (`◆ Project  <Org> / <project-name>`). A wrong project id silently registers workspaces and writes secrets into the wrong project (this happened; cleanup = `agentic workspace delete <ws-id> --context <ctx> -y`).
- Record every workspace id from the JSON.

---

## 3. Secrets (project-scoped)

From the repo root every project-level command needs both `--context <ctx>` and `--project-scope`.

```bash
# load every KEY=value from .env except reserved names, values never shown
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|'#'*|A2A_JWT_SECRET=*) continue;; esac
  k="${line%%=*}"; v="${line#*=}"
  printf '%s' "$v" | agentic secret set "$k" --stdin --context <ctx> --project-scope || echo "FAILED: $k"
done < .env
agentic secret list --context <ctx> --project-scope
```

- The bulk `--from-file -` import rejects the whole file if one name is reserved; the loop above isolates failures.
- `MONGODB_URI` and `VOYAGE_API_KEY` are created by `atlas setup` (§4); don't set them by hand.
- After a deployment exists, changed secrets need `agentic secret sync --context <ctx> --project-scope`.

---

## 4. Atlas link (per workspace)

```bash
agentic atlas setup --workspace <agent> --context <ctx>          # FIRST run per workspace: interactive
#   choose the existing Atlas project and cluster; decline cluster creation; accept the IP-access changes
agentic atlas setup --workspace <agent> --context <ctx> --yes    # only works after a project is saved for that workspace
agentic atlas status --workspace <agent> --context <ctx>         # expect: atlas link: linked / egress sync: ok
```

- Setup creates one DB user per workspace, uploads `MONGODB_URI` once (project-level), creates `VOYAGE_API_KEY` only for workspaces with `features.memory: true`, and adds the platform data-plane IPs (today: `44.214.209.237/32`, `52.44.27.64/32`) to the cluster's network access list.
- If an agent calls the **Atlas Admin API** with a service account, those same IPs must also be on that service account's **API access list** (Atlas UI, org admin). Not done by setup.
- The generated URI/key are shown once in interactive mode — don't paste them anywhere.

---

## 5. Build

```bash
./scripts/vendor_packages.sh                     # if the repo vendors shared packages
for a in <agent1> <agent2> <agent3>; do agentic build --workspace "$a" --context <ctx> --no-wait; done
for a in <agent1> <agent2> <agent3>; do agentic build list --workspace "$a" --context <ctx> | grep -E '^bld_' | head -1; done
agentic build logs <build-id> --workspace <agent> --context <ctx> | sed -n '/Entering phase BUILD/,/Phase complete: BUILD/p'
```

Builds are independent → run them in parallel. ~2–3 min each.
Read failures in the BUILD phase: a uv "No solution found" names the conflicting package (§0 rows 2–3).

---

## 6. Deploy + import proof

```bash
agentic deploy --workspace <agent> --context <ctx>             # ~6 min; --no-wait to parallelise
agentic status --workspace <agent> --context <ctx>
agentic invoke --workspace <agent> --context <ctx> --json "hello"
```

Pass criteria for the invoke:
- a real reply (for envelope-only agents an application-level error such as `INVALID_ENVELOPE` **is** a pass — the graph ran);
- **fail**: `The agent did not become ready during startup` → the module didn't import. Read
  `agentic logs --workspace <agent> --context <ctx> --since 15m --level error --tail 50` — the `ModuleNotFoundError`/traceback is there.

---

## 7. Egress verification (the main task for shared agents)

### 7.1 See what the deployed policy allows
```bash
agentic egress --workspace <agent> --context <ctx>
agentic egress export --workspace <agent> --context <ctx>     # prints the live policy as an agent.yaml network block
```
Both sandboxes are listed: `[agent]` (runs the graph, calls LLMs) and `[tool]` (runs tools). Each has its own mode.

### 7.2 List the agent's outbound dependencies
Grep the code for hosts: LLM provider / gateway, embeddings, Atlas Admin API (`cloud.mongodb.com`), AWS endpoints, any HTTP API, package/CDN hosts fetched at runtime. Decide per sandbox: what does the graph call, what do the tools call.

### 7.3 Allow-list (in `agent.yaml`, takes effect on redeploy)
```bash
agentic agent egress add <host>:443                 # inside the agent dir; repeat per host
agentic agent egress mode allow_list                # per sandbox if the CLI asks
# or, for a trusted internal agent only:
agentic agent egress mode allow_all --confirm-allow-all
agentic build … && agentic deploy …
```
`egress save/clear` are unsupported; live edits are not applied — only `agent.yaml` + redeploy.

### 7.4 Prove each destination from inside the pod
Invoke the agent with a message that forces each outbound call (an LLM answer proves the LLM host; a tool that reads Atlas proves the cluster; etc.). A blocked host fails **silently** as a connection error inside the pod — check:
```bash
agentic logs --workspace <agent> --context <ctx> --since 10m --tail 300 | grep -iE 'error|timeout|refused|denied|connection'
```
Record a table per agent: destination → sandbox → allowed (Y/N) → proven by (message/tool) → result.

---

## 8. Agent-to-agent (A2A) verification

1. Each callee's `agent.yaml` has `a2a.enabled: true` and at least one skill with a **unique** `name`; callers look agents up **by skill name**, not by workspace name.
2. `allowed_callers: []` = any agent in the project may call. Tighten with caller workspace ids only after the flow is proven.
3. Runtime log on the callee at startup must contain `A2A config registered for workspace <ws-id> (enabled=True)`.
4. Prove a hop: invoke the caller so it calls the callee; on the callee look for its tool steps; on the caller for `A2A invoke invoke_a2a_agent args={'agent_id': '<callee ws-id>' …}`.
5. Design limits (from §0), and the rule that follows:
   - a child A2A execution dies with the parent turn — never fire-and-forget a long callee from a chat turn;
   - each A2A call ≤ 300 s;
   - the caller's A2A token expires after ~5 min and is not refreshed;
   - a **synchronous** top-level invoke (the HTTP `POST …/invoke`, the CLI without `--stream`, the Playground)
     is capped at **~60 s** and returns **504** while the callee's root session keeps running to completion
     server-side (proven: a seed coder finished in ~82 s past its caller's 504).

   **The rule, in three tiers (apply the cheapest that fits):**
   - **short in-turn hop that returns within the turn and the ~5-min token → A2A.** In THIS system there is
     now **no such hop left** — the draft stage, once the last A2A caller, was moved to tier 3 below because
     a rich transcript's draft (~4 min) blew the 300 s A2A ceiling and lost the spec when the parent turn
     was cancelled (docs/06 "Draft stage is now START-AND-POLL"). Every cross-agent call is a top-level
     invoke. The tier is kept here only as guidance for a genuinely sub-token hop.
   - **work that can outlive the turn or the ~5-min token, but the callee replies in < ~60 s → a
     SYNCHRONOUS top-level invoke** of the callee's workspace via the platform invoke API with a project
     **service account** (`agentic service-account create <name> --role AGENT_DEVELOPER --context <ctx>`;
     token at `POST /api/v1/oauth/token`, client-credentials; invoke at
     `POST /api/v1/projects/<project>/workspaces/<ws-id>/invoke` with `{message, session_id, user_id}`). The
     callee runs in its own root session with a fresh token. Tolerate the ~60 s cap either by keeping the
     callee under it, or by having the callee create a run/task document its caller can poll on a 504
     (deploy → test: `deploy_find_test_run` + poll).
   - **work whose callee reliably runs > ~60 s → FIRE that top-level invoke and POLL for the result.** Start
     it with a short client timeout (`platform_invoke.start_invoke`: a client timeout or 504 == "started"),
     then poll a durable signal until done or a ceiling: a run/task document (orchestrator → coders: each
     coder marks its own task via `metadata.mark_coder_task`, the orchestrator polls `orch_task_status`,
     ~10 s interval / 15-min ceiling / clear `CODER_TIMEOUT` on ceiling) or an S3 artifact prefix
     `pocs/{poc}/code/{version}/{component}/`. Proven end to end in the cloud (docs/06 "Fire-and-poll code
     stage"). **Do not mix the two transports for one call** (either synchronous-and-tolerate, or
     fire-and-poll — not both).
     - **chat → draft is here too** (docs/06 "Draft stage is now START-AND-POLL"): a rich transcript's
       `draft_generate` runs ~4 min, so chat FIRES the draft workspace (`chat_call_draft` → `_invoke_run`,
       ~8 s ack) and the Draft Agent registers a `stage:draft` run document (`draft_start_run`) that the user
       polls with "how's it going?"; `draft_finish_run` closes it succeeded (spec or clarification questions
       in `outputs`) / failed. Proven in the cloud: DailyDabba draft finalized as a root session in ~5 m 47 s.

6. **Long stages must be resumable, hand themselves over, and heartbeat** (docs/06 "Resilient runs
   (2026-09-26)"). A platform execution is killed at **~10 min wall clock** (seen twice on the orchestrator,
   once on deploy) and a killed execution leaves its run document `running` forever. So:
   - **Heartbeat.** Every durable stage (draft, code, deploy, teardown) keeps `runs.heartbeat_at` fresh at
     least every 30 s while it works — wrap any tool that can take > 30 s in `metadata.heartbeat(run_id)`
     (a daemon thread; touches on entry, every 30 s, on exit). `update_run_step` also touches it.
   - **Stale = abandoned.** A `queued`/`running` run whose newest of `heartbeat_at`/`updated_at`/`started_at`
     is > 3 min old is abandoned (`metadata.run_is_stale` / `find_stale_runs`). Nothing may block on it:
     chat marks it `failed` (`abandon_run`: `error.code ABANDONED`, retryable) and continues or restarts.
   - **Resumable.** A long stage implements `continue_run(run_id)` (`mode: "continue"`): `reopen_run` the
     run (records `continuations[]`, `$inc executions`), then re-enter at the **first step not done**,
     re-attaching to an in-flight callee instead of re-firing it, reusing every artefact already in S3.
     It must be idempotent (continue on a succeeded run is a no-op). Each task carries a stable
     `tasks.component` key so the resume point is unambiguous.
   - **Self-handover.** At a step boundary, if the execution is > 6 min old or has made > 25 tool calls
     (`ORCH_/DEPLOY_HANDOVER_AFTER_S`, `ORCH_/DEPLOY_HANDOVER_TOOL_CALLS`; defaults 360 / 25), persist state
     and FIRE `continue_run` on your own skill as a new root session (`platform_invoke.start_invoke`), then
     reply `started`. Before a known-long step (deploy: `launch_instance`, `provision_db`, ~4 min headroom)
     hand over early rather than risk the kill mid-step.
   - **One bounded call per wait.** Never poll with one tool step per poll (it burns the recursion limit and
     the tool-call budget): a single tool polls internally for ≤ 4 min (heartbeating) and returns terminal
     or `running`; the graph loops or hands over.
   `scripts/check_platform_contract.py` asserts all of the above per agent.

---

## 9. Driving multi-turn tests

```bash
agentic invoke --workspace <agent> --context <ctx> --session <id> --user-id <user> --stream --timeout 8m --file <message.txt>
```
Reuse `--session` for one conversation; `--user-id` is mandatory for agents that stamp user identity. The Playground is good for the trace panel but drops its stream on long turns.

**Every stage is now START-AND-POLL, so no chat turn does long work — the Playground needs no reload.** Each
start tool (`chat_call_draft` included) fast-acks in well under a minute (fires a top-level invoke, waits a
short ack, returns a `run_id`); the specialist runs in its own root session and is polled with "how's it
going?". So a demo driven from the Playground never has a turn that outlives the stream / the ~60 s cap and
never needs a page reload to recover a lost turn. (A multi-step *progress* turn — "how's it going?" runs 2–3
ReAct tool steps — can still take a couple of minutes of LLM round-trips; drive those from the CLI with
`--stream` if you want to script them, but they never hit a platform ceiling.) Confirmed 2026-09-25 driving
the draft stage end to end (docs/06 "Draft stage is now START-AND-POLL"): the DailyDabba resume, its
clarification round, and the recsys regression all fast-acked; only the background poll turns took longer,
and `--stream` carried them with no 504 and no reload.

### 9.1 Client guidance — session threading, busy sessions, and cancel (proven live 2026-09-26)

For a **programmatic client** (e.g. the Control Tower BFF) driving chat over the platform invoke API with a
project service account, three platform behaviours matter — all reachable with the SA client-credentials
token:

- **Thread a conversation with the `X-Session-Id` request HEADER, not the body `session_id`.** The platform
  ignores the body field for threading (it mints a fresh per-execution session each turn) and threads on the
  header. Send the same header on `/invoke` and `/invokeStream`. (This is the fix for the "I don't have an
  active POC in context" bug — see docs/09 § Session handling.)

- **A second turn on a session that is still running the previous one is rejected with HTTP `409`
  `{"code":"SESSION_BUSY","blocking_execution_id":…,"blocking_status":"pending"}`.** This is a pre-flight
  guard: the send is **not** enqueued, so a client may safely re-attempt the same message on a poll (a 409
  never double-sends). The Control Tower queues the message, polls every 5 s, and auto-sends when the 409
  stops. A client must never surface this raw JSON to a user.

- **Runtime sessions — list and cancel** (the source for a "busy" indicator and the Stop button):
  - `GET  /api/v1/projects/{project}/workspaces/{ws}/runtime-sessions`
    → `{"sessions":[{"session_id","status":"active"|"stopping","last_activity_at","scheduled_release_at"}]}`.
    The `session_id` **is** the client's `X-Session-Id`. `status` is `active` while capacity is held
    (this **includes idle-but-reserved**, up to the workspace idle timeout — it is *not* a precise
    "a turn is running now" signal; for that, the 409 above is the oracle).
  - `POST /api/v1/projects/{project}/workspaces/{ws}/runtime-sessions/{session_id}/stop`
    → `200` accepted (the turn is cancelled and capacity releases; the session then shows `stopping` then
    disappears), or `404 {"code":"NOT_FOUND"}` if the session was already free. This is exactly what
    `agentic workspace sessions stop <session-id>` calls (capture with `agentic … -vv`). Proven: a live spec-
    summary turn stopped in ~1 s, after which the same session accepted "How's it going?" with no 409.
  - Note the `/api/v1/projects/{project}/executions/{id}` route exists but is unreliable for a busy probe
    (it 301-redirects / needs a workspace endpoint); use the runtime-sessions list + the 409 oracle instead.

- **No session↔POC or execution id is stored in the `poc_builder` DB.** `conversations` is keyed by `poc_id`
  (not `session_id`) and `runs` carry no `execution_id`/`session_id`/`trace_id`. A read-only client therefore
  cannot join a UI session to its POC from the DB — the chat agent keeps that link only in its (internal)
  LangGraph checkpointer. The Control Tower resolves the followed POC client-side ("a new POC appeared during
  this turn") with a newest-POC DB fallback; see docs/09 § Auto-follow.

---

## 10. Diagnosis commands

```bash
agentic logs --workspace <agent> --context <ctx> --since 15m --tail 300 [--level error] [--grep <text>] [--source agent|tool]
agentic deploy logs --workspace <agent> --context <ctx>                       # deployment event log
agentic build logs <bld-id> --workspace <agent> --context <ctx>
cd agents/<agent> && agentic workspace sessions list                           # sessions holding runtime capacity
```
Sandbox start-up lines (`Starting tool server…`, `Application startup complete`, CORS / RUNNER_AUTH warnings) are replicas booting — noise, not errors.

---

## 11. Cleanup

```bash
agentic workspace delete <ws-id> --context <ctx> -y
agentic secret delete <NAME> --context <ctx> --project-scope
agentic service-account delete <id> --context <ctx>
```
Never delete anything in a project you did not create for this exercise; check the banner first.

---

## 12. Checklist (copy per agent)

| # | Check | Result |
|---|---|---|
| 1 | Code pre-flight passes (import, deps, protobuf, no uv.lock in archive, real .env, sandboxes migrated, tests green) | |
| 2 | Workspace registered in the **right** project; id recorded | |
| 3 | All `.env` keys exist as project secrets (none reserved) | |
| 4 | Atlas: linked, egress sync ok; cluster IP access includes platform IPs; SA API access list if Admin API used | |
| 5 | Build succeeded (id) | |
| 6 | Deploy succeeded (id) **and** invoke returns a real reply | |
| 7 | Egress: policy read; every outbound destination allow-listed per sandbox and proven by an invoke | |
| 8 | A2A: skills unique; registration line in logs; one hop proven caller→callee; long-work rule respected | |
| 9 | Multi-turn test done via CLI `--session` | |
| 10 | Cleanup plan agreed (what stays, what is deleted) | |
