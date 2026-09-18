# Deploying to the Agentic Platform

Everything on this page comes from the platform reference guide, chapters 15
and 17 to 20. Where the guide is silent, that is said plainly rather than
filled in.

## Pushing to GitHub deploys nothing

The platform does not watch this repository. A merge to `main` shares code with
the team; it does not change what is running. Deployment is two explicit
commands, `agentic build` then `agentic deploy`, preceded by one-time setup per
workspace.

Git appears in exactly one place: the build label defaults to your repository's
`branch@sha12`, so a build is traceable to a commit.

## One-time setup per workspace

### 1. Registration

From the repository root, `agentic init` detects the monorepo and registers each
agent listed in the root `agent.yaml` as its own workspace. Agents that already
have an `.agentic/state.json` keep their registration.

### 2. Atlas resources

The platform needs an Atlas cluster of its own to store execution state,
checkpoints and history. `agentic atlas setup` provisions it and sets the
`MONGODB_URI` and `VOYAGE_API_KEY` secrets for you, and adds the platform's data
plane IP addresses to the cluster's access list.

The CLI accepts Atlas **service account** credentials only. Atlas user logins
and the legacy public/private API key pairs are not supported for this command,
and the service account needs the Project Owner permission. If you point
`MONGODB_URI` at a cluster you provisioned yourself, you must add the platform's
data plane addresses to that cluster's IP access list manually, or the deployed
agent cannot connect. The address list is in chapter 17 of the reference guide.

### 3. Secrets

Deployed agents do not read `.env`. That file is local development only.
Secrets live in the platform's secret store:

    agentic secret set NAME VALUE [--description "<text>"] [--sync]

Required values:

| Secret | When |
| --- | --- |
| `MONGODB_URI` | every deployment |
| An LLM key | at least one is required. The guide names `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` and `CEREBRAS_API_KEY` |
| `VOYAGE_API_KEY` | when `features.memory` is true, and for all TypeScript agents |
| `MONGOMEM_DB_NAME` | optional; the memory database name, default `agentic_memory` |

Naming rules: uppercase letters, digits and underscores only; no more than 128
characters; do not start a name with `AGENTIC_PLATFORM_`; and `RUNNER_MODE`,
`APP_ID`, `ORG_ID`, `GROUP_ID`, `AER_ENDPOINT` and `TOOL_ENDPOINT` are reserved.
The limit is 100 secrets per project and 100 per workspace.

`agentic secret delete NAME` is permanent, and deployments that read the secret
fail until it is set again. In a script it needs `--yes`, otherwise it refuses
to run outside an interactive terminal.

**Open point for this team.** The reference guide lists the four provider key
names above. Agents configured against an internal gateway use a different
variable name in local development. Confirm what a gateway-backed agent needs as
a deployed secret before relying on it; this is not covered by the guide.

## Build

    agentic build [--label <str>] [--no-wait] [--workspace <name>] [--all] [--json]

The command packages the source as a `tar.gz`, uploads it through a presigned
URL, and starts a remote build that produces an image in the container registry.
`--workspace` builds one agent; `--all` creates the archive once and then builds
every workspace in sequence. The two flags are mutually exclusive.

**What gets packaged matters in a monorepo.** By default the command packages
only the agent directory. It packages from the monorepo root instead when the
agent directory appears *exactly as written* in the `agents[].path` list in the
root `agent.yaml`. A directory nested under a listed path does not count. This
is why the root file and the folders must agree, and why `scripts/check_agents_map.py`
exists.

`.agenticignore` at the archive root controls exclusions, using `.gitignore`
syntax. Some things are always excluded and cannot be negated with `!`: `.env`
and `.env.*`, `*.pem` and `*.key`, common SSH private keys, the `.git` directory,
and cloud and tooling credential stores. Note that `.npmrc` **is** included, so
never put a registry token in it.

Management commands: `agentic build list`, `agentic build logs <build_id>`,
`agentic build cancel <build_id>`.

Limits per project: 10 concurrent builds, 100 builds in a rolling 24 hours.

## Deploy

    agentic deploy [--build-id <id>] [--workspace <name>] [--all] [--no-wait] [--timeout <duration>]

Deploys the workspace's last successful build unless you name one. The command
polls every five seconds until the deployment reports `succeeded`, `failed` or
`rolled_back`, with a default timeout of 15 minutes.

Runtime note from the guide: a deployed agent must write local files only under
`/tmp` or `/scratch`. Other paths may not be writable.

Management commands: `agentic deploy list`, `agentic deploy get [<id>] [-f]`
(the follow flag streams events), `agentic deploy logs [<id>]` for the state
transitions, and `agentic deploy cancel <id>`, which errors once a deployment
has reached a terminal state.

## After a teammate merges

Their merge changes nothing that is running. To put it live:

    git pull
    agentic build --workspace <name>
    agentic deploy --workspace <name>

Because both commands are per workspace, **each owner can deploy their own agent
from their own machine**, provided their CLI is authenticated and their agent is
registered in the team project. Nobody has to route deployments through one
person.

A full release of everything is one person's job:

    agentic build --all
    agentic deploy --all

## Promoting instead of rebuilding

`agentic build promote <source_build_id> --workspace <name>` copies a tested
image into another workspace without rebuilding, so the image is byte-identical
to the one tested. The source build must have at least one successful deployment
unless `--force` is used, which requires the Project Owner role in the target.

Promotion copies the image only. Secrets, Atlas connection configuration and
egress policies do not transfer and must be configured on the target workspace
first.

## What about deploying from CI?

Not yet, and not on the strength of the guide. The guide documents API keys and
service account access tokens for the platform's **API routes**, including agent
invocation, and it documents non-interactive flags such as `--yes` and `--json`
for scripting. It does not document authenticating the CLI itself in a pipeline
for build and deploy.

Before anyone builds a release pipeline, check `agentic login --help` and the
current CLI documentation, which moves faster than the written guide. Until that
is confirmed, releases run from an authenticated member's machine.
