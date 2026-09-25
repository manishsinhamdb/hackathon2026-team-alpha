"""SDK-tool invocation helper for graph code (agent sandbox).

`invoke_tool` calls an SDK-wrapped tool the way the platform expects (a full ToolCall dict) and unwraps the
ToolMessage. It is used to call this agent's OWN Tool-Pod tools from the graph nodes.

NOTE: the deploy agent no longer makes A2A calls. Its two cross-agent calls — run_tests (-> test agent) and
repair_component (-> coding orchestrator) — happen late in a long deploy run, past the ~5-min OE A2A token
lifetime, so they are now SYNCHRONOUS TOP-LEVEL platform invocations via `poc_shared_tools.platform_invoke`
(see main.py `tests_node` / `repair_node`). The old A2AClient (with its now-unreachable token-refresh retry)
has been removed."""
from __future__ import annotations

from typing import Any


def invoke_tool(tool: Any, args: dict[str, Any]) -> Any:
    """Invoke an SDK-wrapped tool the way the platform expects: a full ToolCall, then unwrap the ToolMessage."""
    import uuid
    call_id = f"call_{uuid.uuid4().hex[:24]}"
    out = tool.invoke({"name": tool.name, "args": args, "id": call_id, "tool_call_id": call_id, "type": "tool_call"})
    content = getattr(out, "content", out)
    if isinstance(content, list):  # content blocks
        content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content
