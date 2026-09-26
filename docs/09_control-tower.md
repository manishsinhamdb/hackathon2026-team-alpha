# Control Tower — the POC Builder web UI

`apps/control-tower` is a Next.js (App Router, TypeScript) web UI that lets a user (1) chat with the deployed
`chat-agent`, (2) watch a POC's pipeline advance live without sending chat turns, and (3) drive gate/stage
actions with one click. It is deliberately **host-agnostic**: a single container, configured only by env
vars, with a `/healthz` route — it runs on Docker locally and is ready to deploy on Kanopy (internal K8s)
unchanged.

Full run/build/test instructions live in `apps/control-tower/README.md`; this doc explains **what it does**,
**the polling design**, **why the BFF holds the credentials**, and **how to deploy the container elsewhere**.

## What it does

Two screens, both laptop-first (1440-wide reference), built to the approved v2 design in
`apps/control-tower/design/`.

### 1. Workspace (`/`)

- **Top bar.** Product mark *POC Builder / CONTROL TOWER*; a **POC selector** button showing the title,
  `spec vNNN`, a status pill and `created <d Mon · HH:MM>` (click to pick another POC — archived POCs are
  hidden here); **New POC** (green), **POC library**, a health chip `<project> · healthy` (green when the
  last poll succeeded, red on error), a **light/dark theme toggle** (sun/moon), and the signed-in user avatar.
- **Left — Conversation.** The session id, `Sessions (n)` (opens the library) and `New session` links.
  Messages are bubbles — user green (`#00684A`, right-aligned), agent card (`#112733`) with markdown and
  per-message timestamps; **run ids in an agent reply are surfaced mono with a copy button** (a structured
  hint). An *agent is replying* pill shows while a turn streams. The six action chips sit above the composer
  (a transcript textarea + `.txt` picker + send); the canned messages are unchanged.
- **Right — Pipeline** for the selected POC: the poc id and `live · refreshed Ns ago`; a **horizontal
  7-stage stepper** *Draft → Spec approved → Code → Code approved → Deploy → Tests → Torn down* with states
  **done** (green check + duration/version), **running** (blue ring + elapsed), **gate** (dashed ring, and
  `gate · by <who> HH:MM` once recorded), **not started** (grey ring + a quiet hint) and **failed** (red ring
  + error code); a **Code run** card listing each coder (contract / seed / backend / frontend / assemble) as
  a progress row (done / running / queued, from the `tasks` collection); a right stack of **Cloud resources**
  (green `0 active` / red `<n> active`), **Deployment** (App / API / Health, disabled until published; EC2 id
  + TTL countdown when live) and **Versions** chips (spec / code / deploy); and a **Run history** table
  (stage, run id, status pill, started, duration). A *Clarification needed* card appears when a draft run
  finished with questions.

### 2. POC library (`/library`)

- A search box (title or poc id); filter chips **Active / Deployed / Torn down / Archived** with counts; a
  table sorted **by created, newest first** — title + poc id, status pill, created (`d Mon YYYY · HH:MM`),
  versions (`s3 · c1`), and **Open** (→ workspace with that POC selected) + **Archive / Restore**.
- A right column: a **Sessions** card (this browser's chat sessions, persisted client-side, with
  rename/close) and a **Today** card with four DB-derived counts (POCs drafted today, deployed & tested
  today, cloud resources live, and the most recent transcript→tested duration).

The stepper / gates / coder rows / run history / today counts are all derived from the read model
(`src/lib/aggregate.ts`) — a pure function of the DB documents (`packages/shared_tools/poc_shared_tools/
metadata.py` / `poc_contracts`), unit-tested with fixtures.

### The one write: archive

The UI is otherwise **strictly read-only on the DB** — every pipeline state change happens through the chat
agent. The **single exception** is **Archive / Restore**, which sets/unsets `pocs.ui_archived: true` (plus
`ui_archived_at`) via `POST /api/pocs/:id/archive`. **Agents ignore `ui_archived`**; it only controls UI
visibility (hidden from the workspace selector, shown under the library's *Archived* filter). Runs, spec and
code are untouched, so an archived POC can be restored at any time. The write is a pure `setArchived`
function (`src/lib/archive.ts`) that only ever touches those two fields — covered by `src/test/archive.test.ts`
with a fake collection.

### Sessions

Chat sessions are **client-side** (`src/lib/sessions.ts`, persisted in `localStorage`): a session is a
browser-local thread (id generated per tab) tracked with a name, turn count and last-active time, shared
between the workspace and the library. Conversation *history* still comes from the `conversations`
collection on load; the session store never touches the server.

## The UI (structure & design)

The visual layer is the approved **v2 design** (`apps/control-tower/design/`), available in **dark and
light**. Fonts are **Sora** (headings), **IBM Plex Sans** (body) and **IBM Plex Mono** (ids), loaded from
Google Fonts via a `<link>` (no build-time font fetch; falls back to system fonts if blocked). Built with
**Tailwind**, **lucide-react** icons and **react-markdown + remark-gfm**. Layout is laptop-first; the
workspace is chat (5/12) · pipeline (7/12), stacked below `lg`.

**Theming (light + dark).** Every colour is a **CSS variable** holding space-separated RGB channels (e.g.
`--green: 0 237 100`), and every Tailwind token is `rgb(var(--x) / <alpha-value>)` so opacity modifiers
(`bg-green/10`, `border-fail/40`) keep working. The active palette is chosen by a **`data-theme`** attribute
on `<html>`; **no component holds a literal hex** — switching themes only flips the variables
(`tailwind.config.ts` + the two palettes in `globals.css`).

- **Dark** = the v2 palette: Leafygreen accent `#00ED64` on near-black ink `#001E2B`, `#112733` cards,
  a `#06232F` pipeline column, running blue `#0498EC`/`#C3E7FE` on `#0C3B5B`, questions `#FFDD49` on
  `#3B2A0B`, failed `#FF9F97` on `#3D1512`, success mint `#71F6BA` on `#023430`.
- **Light** = the MongoDB house palette: surfaces `#FFFFFF`/`#F9FBFA`, ink text `#001E2B`, borders
  `#E8EDEB`/`#C1C7C6`, green `#00ED64` (on-green `#001E2B`), running `#016BF8` on `#E1F7FF`, questions
  `#944F01` on `#FEF7DB`, failed `#DB3030` on `#FFEAE5`, success `#00684A` on `#E3FCF7`.

The default follows `prefers-color-scheme`; a user's choice is persisted in `localStorage` (`ct.theme`).
An inline script in `<head>` applies the resolved theme **before first paint** so there is no flash (the
`<html>` element carries `suppressHydrationWarning` because that script mutates it before hydration). A
sun/moon **toggle** (aria-labelled) sits in both top bars. The palette/toggle logic lives in
`src/lib/theme.ts` and `src/components/ThemeToggle.tsx`; `src/test/theme.test.tsx` covers the resolver, the
toggle (attribute + persistence) and rendering under both themes.

Components (`src/components/`):

- **`TopBar`** — product mark, the POC selector dropdown (title, `spec vNNN`, status pill, created), **New
  POC**, a **POC library** link, the health chip and user avatar. Archived POCs are filtered out of the
  selector by the parent.
- **`Chat`** — the Conversation pane: session id, `Sessions (n)` / `New session`, bubbles via
  `MessageBubble` (markdown, timestamps, run-id copy hints, recovered history behind a toggle), the
  *agent is replying* pill (`ReplyingPill`), the six action chips and the composer. **Enter sends;
  Shift+Enter is a newline.** Canned messages are unchanged.
- **`Stepper`** — the **horizontal** 7-stage stepper. `StageStep` is pure (given a `StageCell` + `now`) and
  maps to *done / running / gate / not-started / failed*; covered by `src/test/stepper.test.tsx`.
- **`CodeRun`** — the Code run card: each coder (contract / seed / backend / frontend / assemble) as a
  progress row (done / running / queued), derived from the code run's tasks.
- **`RunHistory`** — the run-history table (stage, run id, status pill, started, duration).
- **`PipelineBoard`** — composes the header, stepper, clarification card, Code run, the right stack (Cloud
  resources / Deployment / Versions) and Run history. The 10 s poll, 1 s ticker, tab-hidden pause and
  post-chat refresh are unchanged.
- **`Library`** — the `/library` screen (search, filter chips, table with Archive/Restore, Sessions card,
  Today card). Filters/sort/counts come from pure helpers in `aggregate.ts`
  (`selectPocs` / `filterCounts` / `matchesFilter`), unit-tested in `src/test/aggregate.test.ts`.
- **`ui.tsx`** — shared primitives: `StatusPill` / `RunStatusPill` (the five design tones), `VersionChip`,
  `HealthChip`, `CopyId` / `CopyButton`, `Card` / `CardTitle`, `Skeleton`.
- **`Toasts`** — error toasts for BFF failures, deduped so a repeatedly failing poll doesn't spam. Loading
  skeletons and empty states cover first paint and the "no POC selected" case.

## The polling design

The board polls `GET /api/pocs/:id` on an **adaptive cadence** and shows a **visible countdown** to the next
poll. Key properties:

- **Visible, no full-page refresh.** The board is a client component; each poll fetches the aggregated JSON
  and re-renders in place. A **circular SVG ring** in the header fills as the next poll approaches, with the
  text `next check in M:SS`; on a successful poll it briefly flashes `updated`. A **Refresh now** button next
  to the ring polls immediately.
- **Adaptive interval that never stops while active.** Poll every **15 s** while *any* run for the selected
  POC is live (draft/code/deploy/tests/teardown), and every **2 min** when the POC is idle. The cadence is
  re-decided from each poll's data (`pollIntervalMs` in `src/lib/live.ts`), so it speeds up the moment a
  stage starts and slows down once everything is terminal — but it never stops while a run is active.
- **Pauses when the tab is hidden** (`visibilitychange`) and resumes with an **immediate** fetch when it
  becomes visible again — no wasted DB reads in a background tab.
- **Immediate refresh after a chat turn.** When a chat turn returns, the chat pane bumps a signal that makes
  the board fetch right away, so a stage that just started shows up without waiting for the next tick.
- **Terminal-transition cues.** Comparing each poll's stepper cells to the previous one, a stage that just
  reached a terminal state (**succeeded/failed**) **flashes** its stepper node, and a freshly **succeeded
  deploy** surfaces the live **App link in a toast**.
- **Auto-follow a new POC.** If an agent reply names a `poc_…` and nothing is selected, the workspace selects
  it (so the board starts following it) and shows a `Following poc_… — <title>` toast. Manual selection
  always wins — an already-selected POC is never overridden.
- **A 1 s local ticker** advances the elapsed time of a running run and the TTL countdown between polls (and
  drives the countdown ring + fires the due poll), so the board feels live without polling faster.
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

### Session handling (multi-turn continuity)

A UI conversation must reach the chat agent under **one stable session** so its checkpointer keeps the
thread across turns (otherwise it replies "I don't have an active POC in context"). The platform threads a
session by the **`X-Session-Id` request header**, **not** the `session_id` body field (which it ignores for
threading — it mints a fresh per-execution session each turn). So the BFF sends the UI's stable session id
as **`X-Session-Id`** on every `/invokeStream` request (and on the sync `/invoke` fallback); the body still
carries `session_id`/`user_id` for the documented shape, but the header is what threads. The UI session id
*is* the platform session id — the stream returns only an `execution_id`, never a session id, so there is
nothing to capture and remap. Proven live 2026-09-26: two consecutive turns on one header-session share the
thread (turn 2 recalls turn 1); "How's it going?" after a draft turn reports the draft run's state.
`src/test/tokenCache.test.ts` asserts the same `X-Session-Id` is sent on turn 1 and turn 2.

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

### Session/theme/polling verification (2026-09-26)

- **Session bug fixed.** The platform threads a chat session on the **`X-Session-Id` header**, not the body
  `session_id` (proven live: with the header, turn 2 recalls a token set in turn 1; without it, both the sync
  and streaming paths forget). The `agentic invoke --session` CLI works because it sends the same header
  (captured in its `-vv` debug). End-to-end through the BFF `/api/chat`, two turns on one UI session share the
  thread; **"How's it going?"** for `poc_01M3DPW17ND98R1K9RXV2TYWNR` reports its **draft run**
  (`run_01M3DPWZZPWCEVSZ593A04043F`, spec_ready v001).
- **Both screens checked in a real browser in both themes** driving the medicine-finder POC (workspace +
  library, dark + light). The adaptive poll indicator shows the countdown ring (2 min while idle) and
  Refresh-now button. No code/deploy/teardown run — no AWS/Atlas resources.
- **Unit tests:** 62 passing (token cache + SSE parsing + session-header threading; POC aggregation; stepper
  mapping; archive write; theme resolver/toggle/both-theme render; live auto-follow + adaptive-cadence
  helpers) with fake fetch/clock and fixture DB documents.

## Deploy on Kanopy (staging, namespace `sa-demo`)

This mirrors the MXH / mdb-playground pattern exactly (`mongodb/web-app` Helm chart via `drone-helm`,
image built by `kaniko-ecr`) — no new pattern was invented. Two files carry it:

- **`/.drone.yml`** (repo root — Drone only reads the pipeline from the root). Two steps, both gated on
  push to `main`:
  - **`publish`** (`plugins/kaniko-ecr`) → builds the image and pushes it to ECR
    `795250896452.dkr.ecr.us-east-1.amazonaws.com/sa-demo/${DRONE_REPO_NAME}`, tags `git-<sha7>` + `latest`.
    Because this is a **monorepo**, the build context and Dockerfile are pinned to the app:
    `context: apps/control-tower`, `dockerfile: apps/control-tower/Dockerfile`.
  - **`deploy-staging`** (`public.ecr.aws/kanopy/drone-helm:v3`) → `helm upgrade` of chart
    `mongodb/web-app` `4.30.0`, `namespace: sa-demo`, `release: control-tower`, `values_files:
    apps/control-tower/environments/staging.yaml`, `api_server: https://api.staging.corp.mongodb.com`,
    token from the `staging_kubernetes_token` Drone secret.
- **`apps/control-tower/environments/staging.yaml`** — the Helm values: `ingress.hosts:
  [control-tower.sa-demo.staging.corp.mongodb.com]`; one `http` service, `port 80` → `targetPort 3100`;
  liveness+readiness `httpGet /healthz`; non-secret `env` (`PLATFORM_BASE_URL`, `PLATFORM_PROJECT_ID`,
  `CHAT_WORKSPACE_ID`, `POC_PLATFORM_DB`, `UI_USER_ID`, `PORT=3100`); and `envSecrets` mapping
  `PLATFORM_SA_CLIENT_ID` / `PLATFORM_SA_CLIENT_SECRET` / `POC_PLATFORM_MONGODB_URI` to the k8s Secret
  `control-tower-secrets`. `security`/`ownership` blocks match the house convention.

**Hostname:** `https://control-tower.sa-demo.staging.corp.mongodb.com`.

**The secret** (never in the repo). Convention `<release>-secrets` → `control-tower-secrets` in `sa-demo`,
created from `apps/control-tower/.env.local` **without printing values**:

```bash
export KUBECONFIG=~/.kube/config.staging          # context: api.staging.corp.mongodb.com, ns sa-demo
apps/control-tower/deploy/kanopy-create-secret.sh # reads the 3 keys from .env.local, pipes into kubectl
```

**Identity / CorpSecure.** CorpSecure at the ingress is the login. Kanopy injects **no** trustworthy
identity header and forwards client headers unstripped — so the app **trusts no inbound identity header**:
chat turns are stamped with the fixed server-side `UI_USER_ID`, never a header value. No annotations are
needed in the values file (SSO is enforced upstream of the pod), matching MXH.

**Atlas egress (operator step — not automatable here).** The pod's DB reads leave through Kanopy's staging
NAT egress IPs **`35.174.112.8`, `35.170.235.251`, `35.174.21.138`**. These must be on the Atlas cluster
`pov` **Network Access** list or the pod cannot reach `POC_PLATFORM_MONGODB_URI` (chat still works; the
board's polling fails). This is an Atlas UI action for the operator.

**Verification.** After the build+deploy: `curl https://control-tower.sa-demo.staging.corp.mongodb.com/healthz`
→ `{"status":"ok",...}`; open the host (CorpSecure login), select a POC, confirm the board polls (if the
board errors on DB reads but chat works, the egress IPs are not yet allow-listed).
