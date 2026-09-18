# MongoDB Agentic AI Platform — Starter Guide

A practical, ordered account of building and running agents on the MongoDB Agentic AI Platform, written for someone approaching the platform for the first time. It covers a single agent first, then a multi-agent setup in which agents discover and call one another.

The guide is deliberately free of project specifics. Where an example is needed it refers to "the external system" rather than any particular API.

---

## 1. Scope and assumptions

This guide covers local development with the `agentic` CLI, from installation through to a working multi-agent setup with human review, access control and tracing. It does not cover production deployment, which follows a separate sequence (`agentic init`, project setup, secrets, build, deploy).

Assumptions:

- A workstation with Docker and sufficient resources allocated to it (allow roughly 8 CPUs and 12 GB of memory for a two-agent stack).
- Access to the platform organisation and at least one project.
- An LLM provider reachable from the workstation, either a public provider or an internal gateway.
- Familiarity with Python and, ideally, LangGraph. Deep LangGraph knowledge is not required; the scaffolded templates provide a working graph.

---

## 2. Working principles

These principles save more time than any individual command.

1. **Check `agentic <command> --help` before running a command.** The CLI moves faster than the written documentation, and option names change between alpha releases.
2. **Treat the installed skill as the primary reference.** The skill shipped with the CLI (`.claude/skills/magenta-agent/SKILL.md` inside each agent workspace) is more precise and more current than general documentation. Search it before writing platform-specific code.
3. **Follow the platform's own starter templates.** Scaffold a reference template into a temporary directory and read it before inventing a pattern. Where a template and the documentation disagree, the template is the working truth.
4. **Run the untouched scaffold once before changing anything.** A baseline proves the environment, the credentials and the model configuration in isolation.
5. **Change one thing at a time, and verify after each change.** Hot reload makes this cheap.
6. **Prove external credentials outside the agent first.** A plain API call from the workstation removes an entire class of ambiguity later.
7. **Document trade-offs rather than working around them.** Where the platform pattern has a limitation, record it in the agent's documentation; a private workaround becomes an obstacle when the platform changes.

---

## 3. Environment preparation

1. Confirm the CLI version, and self-update if an update is available. The update requires elevated privileges.
2. Refresh the platform login if the session has expired.
3. Start Docker and confirm the resources allocated to it.
4. Log in to the container registry and pull the runner base image. Pulling it explicitly avoids a slow first start.
5. Confirm the Python toolchain. The host Python version does not need to match the container's; the containers run their own interpreter.

If an image pull stalls during a later start, stop the stack, re-run the registry login, pull the image directly with Docker, and start again.

---

## 4. Design before code

Write down two things before scaffolding.

**The operating procedure.** For each capability the agent will have, record the action, its risk level, and whether it runs autonomously or requires human review. A simple rule works well: read operations run autonomously; changes with limited blast radius run autonomously behind code guardrails; changes that cost money, reduce availability or widen access require human review.

**The tool contracts.** For each tool, record its name, arguments, return shape and failure behaviour. This prevents the prompt and the tools drifting apart later.

Also record what is explicitly out of scope. An agent with an unbounded remit is difficult to reason about and difficult to review.

---

## 5. Scaffolding the first agent

Create the agent with `agentic create`. The command asks a series of questions; read `--help` first so the answers are deliberate.

Expect to supply:

- the agent name, which becomes a lowercase, hyphenated workspace slug;
- the LLM provider and model, plus a base URL if an internal gateway is used;
- whether to run a smoke test after creation;
- the egress policy for outbound network access;
- optional features such as memory.

The command creates a project directory containing `project-config.yaml` and an `agents/<slug>` workspace holding `agent.yaml`, `.env`, `pyproject.toml` and the agent source. The project directory is initialised as a Git repository.

Read the generated source before modifying it. The scaffold demonstrates the graph structure, tool registration, state definition and system message that the platform expects.

**Configuration versus secrets.** Non-secret provider settings, such as the base URL, belong in `agent.yaml` under the `config` block. The `.env` file holds API keys only. The `.env` file is the sole source of secrets at runtime: host environment variables are deliberately ignored by the container, so a key missing from the file is missing inside the container.

---

## 6. First run

Start the agent in hot-reload development mode and invoke it with a simple message to establish a baseline.

Several log lines are normal in local development and can be ignored:

- telemetry endpoint errors;
- A2A-related service errors when agent-to-agent calling is not yet configured;
- unset CORS or runner authentication tokens;
- checkpointer messages when a process is running in tool mode.

---

## 7. External credentials

1. Create the credential in the external system with the narrowest scope that satisfies the operating procedure.
2. Prove it with a direct API call from the workstation before wiring it into a tool.
3. Store it in the agent's `.env` file, entered through a hidden prompt rather than shell history.
4. Re-test the direct call to confirm the stored value is correct.

When checking external state later, prefer calls made from the workstation. A shell opened inside a container does not receive the platform-injected environment, and container working directories differ between stack modes.

---

## 8. Building tools

Replace the template tools with the tools defined in the tool contracts, plus a small helper module for the external client.

Points that matter in practice:

- **Registration.** After every tool change, confirm the reloaded runtime lists the expected tools. Hot reload restarts both the agent runtime and the tool server.
- **Placement.** Each tool runs either in the agent runtime or in the tool pod. Set this explicitly in the tool decorator rather than relying on the default. Tools that reach external systems belong in the tool pod; tools that participate in human review belong in the agent runtime.
- **Proving placement.** Logs alone are misleading, because the agent runtime records a dispatch line for every tool regardless of where it executes. Add a marker log that prints the hostname and the runner mode, and read that instead.
- **Parallel calls.** The model may request several tools in one turn. The standard tool node handles this; tools must therefore be independent of one another.
- **System message.** Keep the operating procedure in the system message, including the order in which tools must be called and the conditions under which the agent must refuse.

---

## 9. Guardrails and tests

Safety rules belong in pure functions that take plain arguments and return a decision. This keeps them testable without the runtime.

Two observations:

- A model will often refuse an unsafe request before the tool is ever called. This is convenient but is not evidence that the guardrail works. Test the guardrail function directly.
- Run the test suite inside the container so that the dependency versions match the runtime.

A short suite covering boundary values, invalid input and the default and maximum limits is enough to make later refactoring safe.

---

## 10. Human review

Human review pauses an execution until a person approves or denies a proposed action.

**Follow the template pattern.** The platform's starter templates implement review as two separate tools:

1. A review tool that runs in the agent runtime, calls the framework's interrupt function with a structured response schema, and returns the decision.
2. An action tool that runs in the tool pod, accepts the decision as an argument, and performs the change only when the decision is an approval.

The system message instructs the model to call the review tool first and then the action tool with the returned decision.

**Mechanics to know:**

- A suspended execution reports a suspend context containing an interrupt identifier. Resume by posting to the same endpoint with the session identifier and a resume map keyed by that identifier.
- Interrupt payloads must be replay-stable. Do not include random identifiers, timestamps or values read live from an external system, because the tool body re-runs during replay.
- A pause appears in the logs as a graph interrupt error. This is the normal signal for a pause, not a failure.
- On resume, completed steps replay from the checkpoint, so a marker log inside the review tool appears more than once. This is expected.
- Two suspend mechanisms exist: the interrupt function, whose return value flows back into the calling function, and a suspend payload returned by a tool and recorded out of band. Follow whichever the current template uses.
- Raw interrupt suspends are not listed in the local pending-review page, which shows suspend-payload style suspends. Resume them through the API.
- Always confirm the outcome in the external system. An approved change may pass through transitional states before it settles.

**Platform callout.** In the template pattern, the requirement to review before changing is enforced by the prompt, not by code. The action tool honours a denial, but it cannot verify that a review actually took place. Record this in the agent's documentation so that a reader does not mistake it for a hard gate.

---

## 11. Execution modes

Two local modes exist.

**Hot-reload mode** mounts the source into the containers. Code changes take effect within seconds. This is the mode for day-to-day work.

**Isolated mode** builds the code into the image, which is closer to a deployed environment. Any change to code or configuration requires a stop and restart.

After switching modes, the status command may report the other stack as unhealthy. Trust the startup summary or the container list instead.

**A local development note.** On macOS, file-watch events for bind-mounted directories are not raised reliably for YAML files. After editing `agent.yaml`, touching a Python file triggers the reload.

---

## 12. Multiple agents

Local multi-agent development uses a monorepo: a root `agent.yaml` that lists each agent and its directory, alongside the individual agent workspaces. The whole set starts with a single command that brings up a shared orchestration engine, user interface, memory server and database.

Practical points:

- **Scaffolding additional agents.** Generate a new agent into a temporary directory and move the workspace into `agents/`. Running the create command in an existing project root risks overwriting the root files.
- **Shared configuration.** Under the combined stack, shared services read the root `.env` file, which needs its own LLM key, the A2A signing secret and any memory provider key. Agent-level `.env` files remain in use by their own agents.
- **Addressing agents.** The user interface port is assigned dynamically. Address a specific agent by passing its workspace name as a query parameter on the invoke endpoint.
- **Do not pipe the startup command** into another process. It runs in the foreground and any prompts are hidden. Check status from a second terminal.

---

## 13. Agent-to-agent calling

Agent-to-agent calling (A2A) is how one agent delegates a task to another. It is distinct from tool protocols such as MCP, which expose tools to a single agent. Discovery and routing are handled by the orchestration engine, so no separate server is required.

**Configuration.** Each agent that can be called declares an `a2a` block in its `agent.yaml` with `enabled: true` and one or more skills. A skill carries a name, a description and an example input; these are what a calling agent sees during discovery, so write them as an interface, not as marketing.

**Calling agent.** An agent that delegates must bind the SDK's A2A tools into both its model binding and its tool node. A specialist that is only called needs nothing beyond its own `a2a` block. A2A tools come from the SDK and do not appear in the registered tool list; prove delegation from the orchestration engine's discovery and invocation logs together with the specialist's own tool logs.

**Access control.** The `allowed_callers` field restricts who may discover and invoke an agent. An empty list allows any A2A-enabled agent in the same project; a non-empty list is an allowlist. The entries are workspace identifiers, not agent names, and those identifiers are environment-specific, so the list must be revisited when moving between environments. Setting it is the right default: least privilege is the platform's stated posture.

Rejection is silent. A caller that is not on the allowlist does not receive an error; the target is simply absent from its discovery results, and the calling model will usually report that no suitable agent is available. This is graceful but it complicates diagnosis, so test the allowlist deliberately in both directions.

**Limits.** The invocation timeout defaults to 300 seconds, which is also the maximum; a caller may set a shorter timeout per call. The A2A token expires after five minutes by default and is not refreshed automatically, so a call that outlives it can return an authorisation error, which should be treated as a transient failure. Network and HTTP errors surface as HTTP client exceptions, which the calling model sees as a tool error.

**Human review across agents.** When a delegated task requires review, the specialist suspends as a child execution with its own session. The caller's run completes with a message that the request is awaiting approval. The child's eventual result does not flow back into the caller's finished conversation, so a user working through the calling agent must be told where the outcome will appear.

**Linking parent and child.** The documentation describes a parent execution identifier and a shared root session identifier that group a cross-agent call tree. In local development the executions API returns neither on the detail or list endpoint; the link is visible in the orchestration engine's container logs. Verify the API behaviour in a deployed environment before building on it.

**User identity.** A child execution can arrive without a user identifier. Memory is scoped by user, so a delegated task will not see the caller's memory unless the identity is propagated deliberately. Establish this early if agents are expected to share context.

---

## 14. Memory

Memory has two parts: storage and search, which requires an embedding provider key, and optional background extraction, which requires its own LLM key. Extraction can be disabled by setting its enabled list to empty, which is sensible until a compatible extraction model is available.

Memory is scoped by user identifier, so pass it on every invocation.

---

## 15. Observability

The executions API returns an execution record wrapped in a container object; the record includes identifiers, status, suspension context and results. The list endpoint returns the same fields but omits suspension timing.

Traces are exported over OTLP. In local development the collector is usually not running, so the trace view appears empty; running a collector container alongside the stack restores it. Marker logs remain valuable regardless, because they record which process actually executed a call.

---

## 16. Repository hygiene

Before the first commit, confirm that every `.env` file is ignored, along with the CLI's working directory, Python bytecode and runtime logs. Runtime logs in particular are written by the containers and will otherwise be committed on every run. Never commit real secrets; commit the example environment file instead, with placeholder values.

---

## 17. Sequence summary

1. Verify and update the CLI; refresh the login.
2. Start Docker; confirm resources; pull the runner image.
3. Write the operating procedure and tool contracts.
4. Scaffold the agent; read the generated source.
5. Start in hot-reload mode; run a baseline invocation.
6. Create and prove external credentials; store them in `.env`.
7. Implement read tools, the system message and the sandbox declarations; test end to end.
8. Implement guarded autonomous tools; unit-test the guardrails.
9. Implement human review following the template pattern; test denial, then approval; verify both externally.
10. Run once in isolated mode to confirm the code is self-contained.
11. Prove tool placement with marker logs; set placement explicitly.
12. Move to a monorepo; scaffold the second agent into a temporary directory and move it in.
13. Configure the root files and shared secrets; start the combined stack.
14. Enable A2A on both sides; confirm registration; run a delegated call.
15. Test human review across agents, including resume and external verification.
16. Apply `allowed_callers`; test allowed, rejected and restored.
17. Confirm timeouts, token lifetime and trace linkage.
18. Commit a clean baseline.

---

## Appendix A — Reference behaviours

| Item | Value or behaviour |
| --- | --- |
| A2A invocation timeout | 300 seconds, default and maximum; shorter values may be set per call |
| A2A token lifetime | 5 minutes, no automatic refresh; expiry returns an authorisation error |
| `allowed_callers` | List of workspace identifiers; empty means any A2A agent in the project |
| Discovery rejection | Silent; the target is omitted from discovery results |
| Suspend and resume | Session identifier plus a resume map keyed by the interrupt identifier |
| Secrets | `.env` only; host environment variables are ignored by the container |
| Log levels | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## Appendix B — Troubleshooting

| Symptom | Cause and remedy |
| --- | --- |
| Graph interrupt error in the logs | A pause, not a failure. Read the suspend context and resume. |
| Image pull stalls on start | Stop the stack, re-run the registry login, pull the image directly, start again. |
| Configuration change has no effect | On macOS, YAML file-watch events are unreliable; touch a Python file to trigger reload. |
| Caller reports no available agent | The caller is not on the target's allowlist, or A2A is not enabled on the target. |
| Authorisation error on a long call | The A2A token expired. Treat as transient and shorten the call. |
| Trace view is empty locally | No OTLP collector is running. Add one to the local stack. |
| Marker log appears twice | Checkpoint replay during resume. Expected. |
| Tool appears to run in the wrong place | The agent runtime logs every dispatch. Read the marker log, not the dispatch line. |

---

## Appendix C — Platform callouts

1. Review before change is enforced by the prompt, not by code. Action tools honour a denial but cannot verify that a review occurred.
2. Caller rejection is silent, which trades diagnosability for graceful degradation.
3. A child execution's result does not return to the caller's finished conversation.
4. Parent and child executions are linked in documentation, but the local executions API does not expose the link.
5. A child execution may carry no user identifier, which affects memory scoping.
6. Allowlist entries are environment-specific workspace identifiers and must be revisited per environment.
