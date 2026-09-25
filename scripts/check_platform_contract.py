#!/usr/bin/env python3
"""Fails when any agent drifts from the platform 0.11.1 packaging contract.

The platform runner (runner/OE 0.11.1) ships the Agent Engine SDK as the
`agent_engine_sdk_langgraph` module (dist `agent-engine-sdk-langgraph`); the
older `magenta_sdklanggraph` / `runner-shared` names are gone from the package
registry. Every agent must therefore agree on one canonical definition:

  1. pyproject.toml declares the canonical SDK dependency lines (and the
     protobuf pin the platform runner-shared requires).
  2. main.py imports App from the canonical module.
  3. agent.yaml uses the migrated sandbox layout: a `sandboxes` block with the
     network policy inside each sandbox profile, and NO top-level `network:`.

Run from anywhere; paths resolve relative to the repo root. Requires PyYAML
(the CI image already installs it for scripts/check_agents_map.py).

See docs/06_status-and-handoff.md → "Platform 0.11.1 migration".
"""
from __future__ import annotations

import sys
from pathlib import Path

# --- Canonical contract -----------------------------------------------------
CANONICAL_DEPS = (
    "agent-engine-runner-shared[mongodb,tracing]",
    "agent-engine-sdk-langgraph",
)
CANONICAL_PROTOBUF = "protobuf>=6.33.6,<7"
CANONICAL_IMPORT = "from agent_engine_sdk_langgraph import App"
SANDBOX_PROFILES = ("agent", "tool")

ROOT = Path(__file__).resolve().parents[1]


def _module_dir(agent_name: str) -> str:
    """agents/data-seeding-agent -> agent_data_seeding_agent."""
    return "agent_" + agent_name.replace("-", "_")


def check_agent(agent_dir: Path, errors: list[str]) -> None:
    import yaml

    name = agent_dir.name
    rel = f"agents/{name}"

    # 1. pyproject dependency lines
    pyproject = agent_dir / "pyproject.toml"
    if not pyproject.is_file():
        errors.append(f"{rel}/pyproject.toml is missing")
    else:
        text = pyproject.read_text()
        for dep in CANONICAL_DEPS:
            if f'"{dep}"' not in text:
                errors.append(f"{rel}/pyproject.toml does not declare canonical dependency {dep!r}")
        if f'"{CANONICAL_PROTOBUF}"' not in text:
            errors.append(f"{rel}/pyproject.toml does not pin {CANONICAL_PROTOBUF!r}")

    # 2. main.py import line
    main_py = agent_dir / "src" / _module_dir(name) / "main.py"
    if not main_py.is_file():
        errors.append(f"{rel}/src/{_module_dir(name)}/main.py is missing")
    else:
        main_text = main_py.read_text()
        if CANONICAL_IMPORT not in main_text:
            errors.append(f"{rel}/main.py does not import App via {CANONICAL_IMPORT!r}")
        if "magenta_sdklanggraph" in main_text:
            errors.append(f"{rel}/main.py still references the retired module 'magenta_sdklanggraph'")

    # 3. agent.yaml sandbox layout
    agent_yaml = agent_dir / "agent.yaml"
    if not agent_yaml.is_file():
        errors.append(f"{rel}/agent.yaml is missing")
        return
    cfg = yaml.safe_load(agent_yaml.read_text()) or {}
    if "network" in cfg:
        errors.append(f"{rel}/agent.yaml still has a top-level 'network:' (must move into sandbox profiles)")
    sandboxes = cfg.get("sandboxes")
    if not isinstance(sandboxes, dict):
        errors.append(f"{rel}/agent.yaml has no 'sandboxes' block")
        return
    for profile in SANDBOX_PROFILES:
        if profile not in sandboxes:
            errors.append(f"{rel}/agent.yaml sandboxes is missing the {profile!r} profile")
            continue
        net = (sandboxes[profile] or {}).get("network")
        if not isinstance(net, dict) or "egress_mode" not in net:
            errors.append(
                f"{rel}/agent.yaml sandbox {profile!r} has no 'network.egress_mode' "
                "(run `agentic migrate sandboxes`)"
            )


# Chat-agent tools that start/drive a run in another agent and must NOT use an A2A child call. On the
# platform an A2A invocation is a CHILD EXECUTION of the calling turn and is cancelled when the turn ends
# (and it is capped at 300 s), so a minutes-long stage started that way is killed mid-flight. These must be
# started with a TOP-LEVEL platform invocation (its own root session) — see docs/06 "Platform execution
# model". chat_call_draft is now here too: a rich transcript's draft can take minutes and blew the 300 s A2A
# ceiling / lost the spec when the parent turn was cancelled, so draft is STARTED as a top-level invoke and
# the Draft Agent's run document is polled — there is NO remaining A2A caller anywhere in the system.
CHAT_TOPLEVEL_START_TOOLS = ("chat_call_draft", "chat_start_code_run", "chat_start_deploy_run",
                             "chat_teardown", "chat_run_tests")

# A2A caller markers that must NOT appear in the chat agent any more (it drives every stage via a top-level
# platform invoke). Checked across the chat agent's source (main.py + a2a.py).
CHAT_FORBIDDEN_A2A_MARKERS = ("A2AClient", ".find_agent(", "invoke_a2a", "_a2a_run(", "def _a2a(")

# Agents whose durable / long-running graph calls ANOTHER agent. That call must go through the shared
# platform_invoke helper (a top-level invoke — its own root session with a fresh token), never A2A: an A2A
# call is a child of the calling turn (cancelled when it ends, capped at 300 s) and its OE bearer token
# expires ~5 min into the run, so a late call in a long run 401s. Only chat -> draft stays A2A.
#
# The REQUIRED platform_invoke entry point differs by caller because of the ~60s synchronous-invoke gateway
# cap (a 504 while the callee runs on to completion):
#   - coding-orchestrator -> coders: the seed coder reliably runs >60 s, so a *synchronous* invoke_envelope
#     504s caller-side even though the coder succeeds. It must FIRE (platform_invoke.start_invoke) and POLL
#     the coder's task document instead. It must NOT use invoke_envelope for coders.
#   - deploy-agent -> test/repair: uses the synchronous platform_invoke.invoke_envelope, whose disconnect on
#     the cap is tolerated by the deploy_find_test_run + poll fallback.
# (Chat starts every stage — draft included — as a top-level invoke via _invoke_run; see
# check_chat_stage_starts. No A2A cross-agent call remains anywhere.)
GRAPH_PLATFORM_INVOKE_CALLERS = {
    "coding-orchestrator": {"require": ("platform_invoke.start_invoke(",),
                            "forbid": ("platform_invoke.invoke_envelope(",)},
    "deploy-agent": {"require": ("platform_invoke.invoke_envelope(",), "forbid": ()},
}


def check_graph_platform_invoke(errors: list[str]) -> None:
    for name, rules in GRAPH_PLATFORM_INVOKE_CALLERS.items():
        main_py = ROOT / "agents" / name / "src" / _module_dir(name) / "main.py"
        rel = f"agents/{name}/main.py"
        if not main_py.is_file():
            errors.append(f"{rel} is missing")
            continue
        text = main_py.read_text()
        if "platform_invoke" not in text:
            errors.append(f"{rel} does not import the shared platform_invoke helper "
                          "(cross-agent calls in a durable graph must use it, not A2A)")
        for needle in rules["require"]:
            if needle not in text:
                errors.append(f"{rel} must make its cross-agent calls via {needle} "
                              "(top-level invoke), not A2A — see docs/06 'Platform execution model'")
        for needle in rules["forbid"]:
            if needle in text:
                errors.append(f"{rel} must NOT use {needle} for its cross-agent call "
                              "(the ~60s synchronous-invoke cap forces fire-and-poll) — see docs/06")
        for marker in ("A2AClient", ".find_agent(", "invoke_a2a"):
            if marker in text:
                errors.append(f"{rel} still uses an A2A call path ({marker!r}); a cross-agent call in a "
                              "durable/long-running graph must use platform_invoke, not A2A")


def check_chat_stage_starts(errors: list[str]) -> None:
    chat_src = ROOT / "agents" / "chat-agent" / "src" / "agent_chat_agent"
    main_py = chat_src / "main.py"
    if not main_py.is_file():
        errors.append("agents/chat-agent/main.py is missing")
        return
    text = main_py.read_text()
    for tool in CHAT_TOPLEVEL_START_TOOLS:
        marker = f"def {tool}("
        i = text.find(marker)
        if i < 0:
            errors.append(f"chat-agent/main.py: start tool {tool!r} not found")
            continue
        # inspect the tool body up to the next top-level @app.tool / def
        j = text.find("\n@app.tool", i + 1)
        k = text.find("\ndef ", i + 1)
        end = min(x for x in (j, k, len(text)) if x > 0)
        body = text[i:end]
        if "_invoke_run(" not in body:
            errors.append(f"chat-agent/main.py: {tool!r} must start the stage via _invoke_run (top-level "
                          "invoke), not an A2A child call — see docs/06 'Platform execution model'")
        if "_a2a_run(" in body:
            errors.append(f"chat-agent/main.py: {tool!r} still uses the A2A child-call path _a2a_run "
                          "(cancelled when the chat turn ends)")

    # The chat agent must retain NO A2A caller path at all: every stage (draft included) is a top-level
    # invoke. Scan main.py + a2a.py for residual A2A markers.
    for fname in ("main.py", "a2a.py"):
        f = chat_src / fname
        if not f.is_file():
            continue
        ftext = f.read_text()
        for marker in CHAT_FORBIDDEN_A2A_MARKERS:
            if marker in ftext:
                errors.append(f"chat-agent/{fname} still contains the A2A caller marker {marker!r}; chat "
                              "must drive every stage via a top-level platform invoke (no A2A caller remains)")


def main() -> int:
    try:
        import yaml  # noqa: F401
    except ModuleNotFoundError:
        print("check_platform_contract: PyYAML is required (pip install pyyaml)", file=sys.stderr)
        return 2

    agent_dirs = sorted(p.parent for p in (ROOT / "agents").glob("*/agent.yaml"))
    errors: list[str] = []
    for agent_dir in agent_dirs:
        check_agent(agent_dir, errors)
    check_chat_stage_starts(errors)
    check_graph_platform_invoke(errors)

    if errors:
        print("Platform contract check failed:")
        for e in errors:
            print(f"  - {e}")
        return 1

    print(f"Platform contract check passed: {len(agent_dirs)} agent(s) conform to the 0.11.1 contract.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
