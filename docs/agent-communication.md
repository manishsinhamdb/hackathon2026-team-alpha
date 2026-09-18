# How agents find and call each other

This is the single mechanism for inter-agent communication in this repository.
No agent calls another over a URL, a queue or a shared database table.

## The mechanism

Agent-to-agent calling (A2A) is provided by the platform. Each agent declares
what it can do; the orchestration engine holds the register; a calling agent
searches that register and invokes what it finds.

1. **Declare.** Each agent's `agent.yaml` carries an `a2a` block with
   `enabled: true` and one or more skills. A skill has a name, a description
   and an example input. At startup the agent's name, skills, input and output
   modes and allowed callers are pushed to the orchestration engine
   automatically. Nothing is registered by hand.
2. **Discover.** A calling agent searches for a skill rather than an agent
   name. Treat skill names and descriptions as a public interface: other teams
   read them to decide whether to call you, so change them deliberately.
3. **Invoke.** An agent that calls others binds the SDK's A2A tools into both
   its model binding and its tool node. An agent that is only called needs
   nothing beyond its `a2a` block.
4. **Authorise.** `allowed_callers` in the callee's `agent.yaml` lists the
   workspace IDs permitted to call it. An empty list allows any A2A-enabled
   agent in the project; a non-empty list is an allowlist.

## Call graph and allowlists

The specification fixes who calls whom (section 4.1). Set `allowed_callers` to
match that table, not wider:

| Callee | Allowed callers |
| --- | --- |
| Draft Agent | Chat Agent |
| Coding Orchestrator | Chat Agent, Deploy Agent |
| Data Seeding Agent | Coding Orchestrator |
| API Agent | Coding Orchestrator |
| Frontend Agent | Coding Orchestrator |
| Deploy Agent | Chat Agent |
| Test Agent | Deploy Agent, Chat Agent |
| Chat Agent | user-facing; no agent callers |

Entries are workspace IDs, not agent names, and the IDs differ between
environments. Keep them in the agent's own `agent.yaml`, and expect to revisit
them when the project moves.

**Rejection is silent.** A caller that is not on the allowlist does not receive
an error; the callee simply does not appear in its discovery results, and the
calling model will usually report that no suitable agent is available. When a
delegation mysteriously finds nothing, check the allowlist first.

## Payload and correlation

The specification's AgentEnvelope (section 8.1) is the payload. Pass it as the
invocation message and return it unchanged in shape. `poc_id`, `run_id` and
`task_id` travel inside the envelope and must be propagated on every call, so
that a run can be reconstructed across agents.

## Limits worth knowing

- The invocation timeout is 300 seconds by default and by maximum; a caller may
  set a shorter one per call.
- The A2A token expires after five minutes and is not refreshed. A call that
  outlives it returns an authorisation error; treat it as transient.
- A called agent runs as its own execution. If it pauses for human review, the
  caller's turn completes with a note that approval is pending, and the result
  does not flow back into the caller's finished conversation.
- A child execution may arrive without a user identifier. Memory is scoped by
  user, so propagate identity deliberately if a called agent needs it.

## Running the whole set locally

From the repository root:

    agentic dev up --all      # never pipe this command; it runs in the foreground
    agentic dev status --all
    agentic dev stop --all

The user interface port is assigned dynamically; the startup summary prints it.
Address a specific agent with the `workspace` query parameter on the invoke
endpoint. Shared services read the root `.env`; each agent reads its own.
