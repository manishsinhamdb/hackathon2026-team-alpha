# Contributing

Each agent has one owner. You work inside your own workspace under `agents/`
and, apart from two shared files, you should not need to touch anyone else's.

## Before you start

1. Claim your row in `agents_ownership.md`.
2. Install and authenticate the `agentic` CLI, and start Docker.
3. Copy `.env.example` to `.env` at the repository root and fill it in. Shared
   local services read this file. Your own agent's secrets go in its own `.env`.

## Adding your agent

1. Scaffold into a temporary directory, then move the workspace in. Running the
   create command in the repository root risks overwriting the root files.

       agentic create --help          # option names change between releases
       cd /tmp && agentic create ...  # answer the prompts
       mv /tmp/<project>/agents/<slug> <repo>/agents/<slug>

2. Add your entry to the root `agent.yaml`. CI fails if a workspace exists but
   is not listed, or if a listed path is missing.
3. Add your `a2a` block with skills, and set `allowed_callers` to match the call
   graph in `docs/agent-communication.md`.
4. Register the monorepo from the repository root:

       agentic init

   Agents that already carry a registration keep it.
5. Start the stack and confirm your agent appears:

       agentic dev up --all

## Branches and reviews

- One branch per agent, named `agent/<slug>`. Open a pull request into `main`.
- Changes to the root `agent.yaml`, `packages/poc_contracts`, the S3 layout, the
  platform collections or the shared scripts need review from every affected
  owner, per the working agreements in the specification.
- Never commit a real `.env`, a connection string or a credential.

## Definition of done

The specification's per-agent definition of done applies: entry points with the
envelope, contract validation, acceptance criteria in CI against fixtures, no
live cloud calls in unit tests, observability events emitted, a README covering
the prompt and graph, environment variables, how to run against fixtures and how
failures are repaired.
