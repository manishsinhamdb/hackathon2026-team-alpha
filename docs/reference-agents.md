# The reference pair

Two working agents live in this repository as a worked example. They are not
part of the POC Builder product: they exist so that anyone adding an agent can
read a complete, running one instead of starting from a blank template.

| Folder | What it is |
| --- | --- |
| `agents/atlas-assistant` | A specialist. Reads cluster state and metrics, adds temporary IP access behind code guardrails, and requires human review before pausing, resuming or scaling. |
| `agents/reference-chat-agent` | A front desk. Holds memory, has no external credentials, and delegates anything operational to the specialist over A2A. |

Together they demonstrate the three mechanisms every agent here needs:
delegation by skill, authorisation by allowlist, and human review before a
change.

## Where to look for each thing

| You want to see | Read |
| --- | --- |
| How an agent advertises itself | the `a2a` block in either `agent.yaml` |
| How a caller delegates | `agents/reference-chat-agent/src/.../main.py`, where the SDK's A2A tools are bound into the model binding and the tool node |
| When to delegate | `agents/reference-chat-agent/src/.../system_message.py` |
| A tool that calls an external API | `agents/atlas-assistant/src/.../tools.py` and `atlas_api.py` |
| Safety rules that are testable | `agents/atlas-assistant/src/.../write_tools.py` and `tests/test_guardrails.py` |
| Human review | the review tool in `write_tools.py`, and the rules in `system_message.py` |
| Where a tool runs | the placement flag in each tool decorator, and the marker log lines |

## Using it as a starting point

Copy the folder, rename it to your slug, then work through
`onboarding-an-existing-agent.md` from step 1. The parts you will certainly
change are the agent name, the skills in the `a2a` block, the system message,
the tools, and the secrets in `.env.example`.

## What is deliberately unfinished

- **Allowlists are empty.** The workspace IDs from the original environment were
  removed, because IDs are specific to a platform project. Fill them in after
  registration, following the call graph in `agent-communication.md`.
- **No `.agentic/` registration is committed.** Each agent registers into the
  team platform project on the first `agentic init` from the repository root.
- **Review before change is enforced by the prompt, not by code.** The action
  tools honour a denial but cannot verify that a review took place. This is the
  platform's own template pattern; it is recorded here rather than worked
  around.
