# Bringing an agent built elsewhere into this repository

Everyone is building in their own repository first. This page is the whole
procedure for moving a finished workspace in. It is a folder move plus a small
number of edits; nothing needs rewriting.

Read it in full before you start. Steps 3 and 4 are the ones that go wrong.

## What stays and what changes

| Stays as it is | Changes |
| --- | --- |
| Your `src/` tree, prompts, tools, tests | The workspace folder name, if it does not match the agreed slug |
| Your `pyproject.toml` dependencies | `name` in your `agent.yaml`, to match the root entry |
| Your graph, state and LLM configuration | Where your secrets live, and which are shared |
| Your `README.md` | Your platform registration, which moves to the team project |
| | Your `a2a` block: skills, and `allowed_callers` |

## Procedure

### 1. Agree the name

Take the slug from `agents_ownership.md`: lowercase, hyphens, unique in the
repository. Your folder, the `name` in your own `agent.yaml` and the entry in
the root `agent.yaml` must all use it.

### 2. Move the workspace in

From a clone of this repository, on a branch named `agent/<slug>`:

    mkdir -p agents/<slug>
    cp -R <your-repo>/agents/<your-old-slug>/ agents/<slug>/

If your repository was single-agent, your `agent.yaml`, `pyproject.toml`, `src/`
and `.env` sit at its root instead; copy those into `agents/<slug>/`.

Do not copy: `logs/`, `.venv/`, `__pycache__/`, `dist/`, or the generated
`dev.yaml` if your CLI version regenerates it.

### 3. Decide what happens to your registration

Your workspace is registered on the platform in whichever organisation and
project you used while working alone. That registration lives in the hidden
`.agentic/state.json` file inside your workspace.

This repository deploys as **one platform project for the whole team**, because
A2A discovery and `allowed_callers` only work between agents in the same
project. So:

- **If you built in your own personal project** (the usual case), do **not**
  copy `.agentic/` across. Leave it behind, and `agentic init` will register
  your agent freshly in the team project.
- **If you were already working in the team project**, copy `.agentic/` with
  the rest of the workspace. This preserves your existing workspace ID. Losing
  it orphans the old registration and gives you a new one.

Either way, `.agentic/` is git-ignored and never committed.

### 4. Split your secrets

Two `.env` files exist in a monorepo, and neither is committed.

- **Root `.env`** holds what the shared services need: the LLM key, the A2A
  signing secret, and the embedding key if memory is used. Copy `.env.example`
  and fill it in.
- **`agents/<slug>/.env`** holds only your agent's own secrets.

Also declare those secrets under `required_secrets` in your `agent.yaml`. An
empty list means deny by default, so an undeclared secret is unavailable at
runtime. Commit an `.env.example` in your workspace listing the names with
empty values, so the next person knows what to supply.

### 5. Declare your interface

In `agents/<slug>/agent.yaml`, make sure the `a2a` block is present:

    a2a:
      enabled: true
      skills:
        - name: <skill-name>
          description: <what another agent gets by calling you>
          example_input: "<a realistic request>"
      allowed_callers: []      # fill in at step 7

The skill description is what other agents read when they search. Write it for
them, not for yourself.

### 6. List yourself in the root file

Uncomment or add your entry in the root `agent.yaml`:

    agents:
      - name: <slug>
        path: agents/<slug>

Then check it locally before pushing:

    python scripts/check_agents_map.py

CI runs the same check on every pull request.

### 7. Register and fill in the allowlist

From the repository root:

    agentic init

This registers any agent that has no `.agentic/state.json` and leaves the others
alone. Registration gives your agent a workspace ID.

`allowed_callers` takes workspace IDs, not names, so it can only be completed
once the agents that call you have been registered. Use the call graph in
`docs/agent-communication.md`: list exactly the agents shown as your callers,
and nothing wider. An empty list means any agent in the project can call you,
which is the wrong default for a shared project.

### 8. Run the whole set

    agentic dev up --all

Do not pipe this command anywhere; it runs in the foreground and hides prompts.
Check the startup summary for the user interface port, then confirm your agent
answers a direct request and that its intended caller can reach it.

### 9. Open the pull request

Include: your workspace, your root `agent.yaml` entry, your `.env.example`, and
your row updated in `agents_ownership.md`. Do not include secrets, `.agentic/`,
logs or build output.

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| CI fails on the agent map | The folder and the root `agent.yaml` disagree. |
| A caller reports that no suitable agent is available | You are missing from its target's allowlist, or your `a2a` block is absent. Rejection is silent by design. |
| Your agent starts but has no credentials | The secret is in the wrong `.env`, or not declared under `required_secrets`. |
| Configuration change appears to do nothing | On macOS, YAML changes may not trigger the file watcher. Touch a Python file. |
| You end up with a second workspace on the platform | `.agentic/` was dropped when it should have been kept, or kept when it should have been dropped. See step 3. |

---

## Continuous integration

The agent map check is provided at `docs/ci/agents-map.yml`. GitHub blocks tokens
without the `workflow` scope from creating workflow files, so copy it to
`.github/workflows/agents-map.yml` from the GitHub web interface, or commit it
with a token that carries that scope. Until then, run
`python3 scripts/check_agents_map.py` locally before opening a pull request.
