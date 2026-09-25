# Control Tower — the POC Builder web UI

`apps/control-tower` is a Next.js (App Router, TypeScript) web UI that lets a user (1) chat with the deployed
`chat-agent`, (2) watch a POC's pipeline advance live without sending chat turns, and (3) drive gate/stage
actions with one click. It is deliberately **host-agnostic**: a single container, configured only by env
vars, with a `/healthz` route — it runs on Docker locally and is ready to deploy on Kanopy (internal K8s)
unchanged.

Full run/build/test instructions live in `apps/control-tower/README.md`; this doc explains **what it does**,
**the polling design**, **why the BFF holds the credentials**, and **how to deploy the container elsewhere**.

## What it does

Two panes on one laptop-width screen:

- **Left — chat.** A session id is generated per browser tab and shown; "New session" starts a fresh one.
  Messages are kept client-side and the selected POC's stored conversation is recovered from the
  `conversations` collection on load. Six quick-action buttons send canned messages through the same chat
  path (scoped to the selected POC): *Show me the spec* · *Looks good, go ahead and build it* · *Deploy it, I
  don't need to review the code (4-hour TTL)* · *How's it going?* · *Tear it down* · *Retry*. A textarea plus
  a `.txt` file picker (pastes the file content into the message — no S3 upload in this version) handle
  free-form input, e.g. pasting a meeting transcript to start a new POC.
- **Right — pipeline board** for the selected POC: a stepper
  **Draft → Spec approved → Code → Code approved → Deploy → Tests → Torn down**, each cell showing the run id,
  status, and elapsed/final time (gates show who approved which version); a "Clarification needed" card when a
  draft run finished with questions; a links card (App / API / Health) when deployed; a cloud-resources count
  with a **red badge when > 0**; and a live TTL countdown. It reads the platform DB documents exactly as
  `packages/shared_tools/poc_shared_tools/metadata.py` / `poc_contracts` define them.

The UI is **strictly read-only on the DB**. It never writes — every state change happens through the
chat agent (and the agent's own tools), which is what the action buttons drive.

## The polling design

The board polls `GET /api/pocs/:id` every **10 s**. Key properties:

- **Visible, no full-page refresh.** The board is a client component; each tick fetches the aggregated JSON
  and re-renders in place. A small indicator shows the cadence and the last poll time.
- **Pauses when the tab is hidden** (`visibilitychange`) and resumes (with an immediate fetch) when it
  becomes visible again — no wasted DB reads in a background tab.
- **Immediate refresh after a chat turn.** When a chat turn returns, the chat pane bumps a signal that makes
  the board fetch right away, so a stage that just started shows up without waiting for the next tick.
- **A 1 s local ticker** advances the elapsed time of a running run and the TTL countdown between polls, so
  the board feels live without polling faster.
- **Separation of concerns:** progress is read from the **DB**, never by asking the agent. This is the whole
  point — the platform's stage runs (draft/code/deploy/test/teardown) each register a `runs` document and
  update it as they go, so the UI reflects real state even while a stage runs in its own root session. The
  aggregation (`src/lib/aggregate.ts`) is a **pure function** of (poc, runs, tasks, resources) — unit-tested
  with fixtures — that maps those documents onto the fixed stepper: draft/code/deploy/test/teardown cells come
  from the latest run of each stage; the "Spec approved" / "Code approved" cells come from `pocs.approvals`;
  the clarification card from a draft run that finished `succeeded` with `outputs.needs_clarification`.

## Why the BFF holds the credentials

The Next.js **server routes are a Backend-For-Frontend**. The browser talks only to `/api/*` on the same
origin; the server holds every secret and makes the privileged calls:

- **The service-account secret, the platform token, and the DB URI never reach the browser.** They are read
  from server-only env (`src/lib/env.ts` imports `server-only`, so importing it into client code is a build
  error). A leaked SA secret would let anyone invoke agents in the project; a leaked DB URI would expose the
  whole platform DB. Keeping them server-side is the security boundary.
- **The UI uses its own project service account** (`control-tower`, role `AGENT_DEVELOPER`), created
  separately from the agents' `poc-builder-chat` SA, so its access can be rotated/revoked independently. The
  pair lives only in `apps/control-tower/.env.local` (gitignored); `.env.example` carries names only.
- **Token lifecycle is handled server-side:** client-credentials exchange at `POST /api/v1/oauth/token`,
  cached until ~60 s before expiry, force-refreshed once on a 401. The browser is oblivious to all of it.
- **The DB connection is read-only and pooled** in the server (one cached `MongoClient`), not opened per
  request and never from the browser.

### Chat transport note (adapted from the documented platform behaviour)

The chat agent's turns are usually short, but summarising a **rich** spec (e.g. DailyDabba v003) is a
multi-step ReAct turn that exceeds the platform's **~60 s synchronous-invoke cap** (a plain
`POST …/invoke` returns **504** at ~60 s, and because chat is `durable_workflow:false` its pod is recycled at
the cap so the reply is *lost* — confirmed live). So the BFF invokes the chat workspace over the
**streaming** endpoint `POST …/invokeStream` (SSE: `data:` lines with `chunk_type` of
`metadata|progress|text|done`; the `done` chunk carries the full reply). Streaming keeps the connection alive
through the long turn, so the browser gets the real reply. The BFF aggregates the stream server-side and
returns plain JSON, so the client stays a simple request/response caller. If streaming itself errors it falls
back to the synchronous `/invoke`, and any residual 504 / client timeout is surfaced gracefully as
"the agent is still replying — poll" (the board keeps advancing regardless). This matches the runbook rule
(docs/08 §0, §9): long turns go over the stream, never the synchronous gateway.

## How to deploy the container elsewhere (Kanopy / Kubernetes)

The image makes **no vendor-specific hosting assumptions**:

- **Single container**, multi-stage `node:20-alpine`, **non-root** user, Next.js `standalone` output (small
  image, no `node_modules` shipped at runtime).
- **Config entirely via env vars** (see the README table). Provide them as Kubernetes `Secret` /
  `ConfigMap` — the SA pair and DB URI as secrets. No build-time secrets, no baked-in ids.
- **`PORT`-driven** (default 3100; the standalone server honours `PORT` + `HOSTNAME=0.0.0.0`).
- **`/healthz`** returns `{status:"ok"}` without touching the DB or platform, suitable for **both liveness
  and readiness probes** (a DB/platform outage should not evict the pod).
- **Stateless** — no volumes, no sticky sessions (chat session ids are client-generated; conversation history
  is recovered from the DB). Horizontal scaling is safe.

A Kanopy deployment is therefore: build/push the image, set the env vars as secrets, point liveness+readiness
at `/healthz`, expose `PORT`. Nothing else changes between local Docker and the cluster.

## Proven locally (2026-09-25/26)

- Service account **`control-tower`** created (role `AGENT_DEVELOPER`, project hackathon2026); the pair is
  stored only in `apps/control-tower/.env.local`.
- Built and run in Docker on **http://localhost:3100**; `/healthz` → 200.
- Drove POC **`poc_01M3CHJMABWPXT6XP182RHSCEK`** (DailyDabba, `spec_ready` v003) through the UI in a real
  browser:
  - the board shows the draft run **`run_01M3CN9ANTM0D1CCBYZR1E0BDX` = succeeded** ("took 5m 47s") and the
    **Code stage = not started** (and Spec approved / Code approved / Deploy / Tests / Torn down not started;
    cloud resources 0);
  - **"Show me the spec"** rendered the full **v003** summary (city & kitchen leaderboard, delivery-time
    distribution buckets, coupon effectiveness, pipeline inspector, single public URL) over the streaming
    path — past the ~60 s cap, no 504;
  - **"How's it going?"** left the board consistent and updated via the 10 s poll with no page refresh.
- **No code/deploy/teardown was run — no AWS/Atlas resources were created.**
- **Unit tests:** 31 passing (token cache + SSE parsing; POC aggregation) with fake fetch/clock and fixture DB
  documents.

## Left open for the Kanopy deployment

- Build & push the image to the internal registry; wire the env vars as K8s secrets/config; set
  liveness+readiness probes on `/healthz`; expose `PORT`.
- Decide network egress: the pod needs outbound to `PLATFORM_BASE_URL` and to the Atlas cluster
  (`POC_PLATFORM_MONGODB_URI`) — the Atlas network access list must include the cluster's egress IP for
  Control Tower's pod (same class of allowlist step the agents need).
- Optional hardening: put the UI behind the internal SSO/ingress; the SA secret rotation cadence; a readiness
  probe that also checks DB reachability if you want the pod to drop out of rotation on a DB outage (the
  current `/healthz` intentionally does not).
