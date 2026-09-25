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

## The UI (structure & design)

The visual layer is a **MongoDB-house / Leafygreen** design: the green accent `#00ED64` on near-black
`#001E2B` for the top bar and dark surfaces, white / `#F9FBFA` content surfaces, a grey text scale,
generous spacing and strong hierarchy — no gradients or decoration. It is built with **Tailwind**
(semantic colour tokens driven by CSS variables, so the same classes render in light and dark),
**lucide-react** icons and **react-markdown + remark-gfm** for agent replies. **Light is the default;
the app honours `prefers-color-scheme: dark`.** Layout is laptop-first — two columns ≥1024px, stacked
below. Data flows and routes are unchanged from the functional version; this is a presentation layer only.

Components (`src/components/`):

- **`TopBar`** — product name *POC Builder — Control Tower*, the POC selector (title + a status pill),
  **New POC** and **New session** actions, and a connection/health dot (green when the last poll
  succeeded, red on error).
- **`Chat`** — messages as bubbles with **markdown rendering** (tables and code blocks), per-message
  timestamps, a three-dot **streaming indicator** while a turn is in flight, and a sticky composer: a
  transcript textarea + `.txt` picker and a compact row of **action chips** (*Show me the spec* · *Build
  it* · *Deploy (4h TTL)* · *How's it going?* · *Tear it down* · *Retry*). **Enter sends; Shift+Enter is a
  newline.** The chips send the same canned messages as before (behaviour unchanged; only the labels are
  compact). `MessageBubble` renders one bubble; recovered history is shown dimmed behind a toggle.
- **`Stepper`** — the vertical **Draft → Spec approved → Code → Code approved → Deploy → Tests → Torn
  down** stage stepper. Each cell shows its state — *not started* / *running* with a live elapsed timer /
  *succeeded* with a final duration / *failed* with the error code+reason — gates show *approved
  \<version\> by \<who\>*, and run ids are click-to-copy. `StageStep` is pure (given a `StageCell` + `now`)
  and is covered by render tests (`src/test/stepper.test.tsx`).
- **`PipelineBoard`** — the stepper plus cards: **Clarification needed** (questions rendered, "answer in
  chat" hint), **Deployment** (App / API / Health as buttons, copyable EC2 id, live TTL countdown),
  **Cloud resources** (green `0` / red `>0` badge + table), **Test report** summary, and Spec/Code version
  chips in the header. The 10 s polling, 1 s ticker, tab-hidden pause and post-chat refresh are unchanged.
- **`Toasts`** — error toasts for BFF failures (chat and poll), with dedup so a repeatedly failing poll
  doesn't spam. **Loading skeletons** and **empty states** cover first paint and the "no POC selected" case.

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
