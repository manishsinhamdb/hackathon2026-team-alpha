# Control Tower

A web UI for the POC Builder platform. It does three things:

1. **Chat** with the deployed `chat-agent` (the only agent users talk to).
2. Shows the **live pipeline state** of a POC by polling the platform DB every 10 s — no chat turns required.
3. Offers **one-click gate/stage actions** (canned chat messages) for the selected POC.

It is a Next.js (App Router, TypeScript) app where the **server routes act as a BFF**: the browser never sees
the service-account secret, the DB URI, or any platform token. It runs as a single container, is configured
entirely by env vars, and exposes `/healthz` — so the same image runs locally on Docker and on an internal
Kubernetes platform (Kanopy) with no vendor-specific assumptions.

## Architecture

```
browser  ──HTTP──►  Next.js server (BFF)  ──►  platform invoke API (chat)   [OAuth SA token, cached]
                          │                └──►  platform DB (READ-ONLY)      [pocs/runs/tasks/…]
                          └── secrets stay here; never sent to the browser
```

- `POST /api/chat` — exchanges the SA client-credentials for a token (cached to expiry, refreshed on 401),
  then invokes the chat workspace. Uses the **streaming** endpoint (`/invokeStream`) so a long turn (e.g. a
  rich spec summary that exceeds the ~60 s synchronous gateway cap) still returns the real reply; falls back
  to the synchronous `/invoke` if streaming errors, and surfaces a 504 / timeout gracefully as "still
  replying, poll".
- `GET /api/pocs` — recent POCs (id, title, status, versions, updated_at).
- `GET /api/pocs/:id` — the aggregated read model the board polls: poc doc + runs (stage/run_id/status/
  timings) + tasks per run + active cloud_resources + latest deployment (app/api/health URLs, EC2 id, TTL) +
  latest test summary + pending clarification questions. **Never writes.**
- `GET /api/pocs/:id/conversation` — the POC's stored conversation, recovered on load.
- `GET /healthz` — liveness (no DB / platform dependency).

The client is two panes: **left** a chat session (id generated per browser tab, "New session" button,
history recovered from the `conversations` collection); **right** a pipeline board (a
Draft → Spec approved → Code → Code approved → Deploy → Tests → Torn down stepper, clarification card,
deployment links, cloud-resource count with a red badge when > 0, TTL countdown). The board polls
`GET /api/pocs/:id` every 10 s (pausing when the tab is hidden) and refreshes immediately after a chat turn.

## Environment variables

All server-side. Copy `.env.example` to `.env.local` (gitignored) and fill in the values.

| Var | Meaning |
|---|---|
| `PLATFORM_BASE_URL` | Platform base URL (`https://agentic-platform.mongodb.com`). |
| `PLATFORM_PROJECT_ID` | Platform project id (hackathon2026 = `6aacc8c7a77ed8f0d8a5f8cf`). |
| `CHAT_WORKSPACE_ID` | The chat agent's workspace id (`ws-…`). |
| `PLATFORM_SA_CLIENT_ID` / `PLATFORM_SA_CLIENT_SECRET` | A **new** project service account for this UI (see below). |
| `POC_PLATFORM_MONGODB_URI` | Platform DB connection string (READ-ONLY use). |
| `POC_PLATFORM_DB` | Platform DB name (default `poc_builder`). |
| `UI_USER_ID` | `user_id` stamped on chat turns (default `u_ui`). |
| `PORT` | Listen port (default `3100`). |

### Create the UI service account (once)

Do **not** reuse an agent's SA. Create a dedicated one and store the pair only in `.env.local`:

```bash
agentic service-account create control-tower --role AGENT_DEVELOPER --context hackathon2026 --json
```

Copy `service_account.client_id` → `PLATFORM_SA_CLIENT_ID` and `client_secret` → `PLATFORM_SA_CLIENT_SECRET`.

## Run locally (dev)

```bash
npm install
cp .env.example .env.local   # then fill it in
npm run dev                  # http://localhost:3100
```

## Run in Docker

```bash
docker build -t control-tower:local .
docker run --rm --env-file .env.local -p 3100:3100 control-tower:local
# open http://localhost:3100 ; liveness at http://localhost:3100/healthz
```

The image is multi-stage (`node:20-alpine`), runs as a non-root user, honours `PORT`, and has a
`HEALTHCHECK` on `/healthz`.

## Tests

```bash
npm test
```

Covers the **token cache** (caching within TTL, refresh on expiry, force refresh, TTL floor, credential-safe
error, 401 retry on invoke, 504→pending, SSE parsing) and the **POC status aggregation** (stepper mapping,
gates, clarification, deployment URLs, test summary, task ordering, live elapsed, active-resource count)
with a fake fetch/clock and fixture DB documents — no network or DB required.

## Point it at another project / workspace

Nothing is hard-coded. Change `PLATFORM_PROJECT_ID`, `CHAT_WORKSPACE_ID`, `POC_PLATFORM_MONGODB_URI`, and
`POC_PLATFORM_DB` (and create an SA in that project). The skill→workspace mapping and ids all come from env,
so the same image serves any project.

## Deploy elsewhere (Kanopy / Kubernetes)

Single container, config via env, `/healthz` for probes, non-root, `PORT`-driven. Provide the env vars as
Kubernetes secrets/config, point a liveness+readiness probe at `/healthz`, and expose the `PORT`. No
persistent volume, no vendor SDK, no build-time secrets.

**Staging (namespace `sa-demo`) is wired**, mirroring the MXH pattern:

- **`/.drone.yml`** (repo root) — `kaniko-ecr` build with `context: apps/control-tower` →
  `drone-helm` deploy of `mongodb/web-app` 4.30.0, release `control-tower`.
- **`environments/staging.yaml`** — Helm values: host
  `control-tower.sa-demo.staging.corp.mongodb.com`, `targetPort 3100`, `/healthz` probes, non-secret
  `env`, and `envSecrets` → the `control-tower-secrets` Secret.
- **`deploy/kanopy-create-secret.sh`** — creates `control-tower-secrets` in `sa-demo` from `.env.local`
  (`PLATFORM_SA_CLIENT_ID`, `PLATFORM_SA_CLIENT_SECRET`, `POC_PLATFORM_MONGODB_URI`) without printing
  values.

The pod's DB reads egress via Kanopy's staging NAT IPs `35.174.112.8 / 35.170.235.251 / 35.174.21.138`,
which must be on the Atlas `pov` network-access list. Full runbook: `docs/09_control-tower.md`
§ Deploy on Kanopy.
