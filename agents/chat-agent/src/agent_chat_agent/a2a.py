"""Chat-agent local helpers used by the graph (agent sandbox).

There is NO agent-to-agent (A2A) call path here any more: every stage is started as a top-level platform
invoke (see main._invoke_run + poc_shared_tools.platform_invoke). What remains is the SDK tool-invocation
shim (`invoke_tool`, used by the ReAct tool node) and the envelope-unwrapping helper used to pull a run_id
out of a platform invoke reply (`_unwrap_envelope` / `_extract_json`)."""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


def invoke_tool(tool: Any, args: dict[str, Any]) -> Any:
    """Invoke an SDK-wrapped tool the way the platform expects: a full ToolCall, then unwrap the ToolMessage."""
    import uuid
    call_id = f"call_{uuid.uuid4().hex[:24]}"
    out = tool.invoke({"name": tool.name, "args": args, "id": call_id, "tool_call_id": call_id, "type": "tool_call"})
    content = getattr(out, "content", out)
    if isinstance(content, list):  # content blocks
        content = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return content


def _unwrap_envelope(obj: Any) -> dict[str, Any]:
    """A platform invoke reply carries the callee's final message possibly wrapped (e.g.
    {"result": "<envelope json>", "status": ...}). Normalise any of these shapes to a dict carrying the
    AgentEnvelope under "response"."""
    if not isinstance(obj, dict):
        return {"response": {"status": "failed", "error": {"code": "A2A_BAD_REPLY", "message": str(obj)[:300]}}}
    if "response" in obj:
        return obj
    res = obj.get("result")
    if isinstance(res, str):
        try:
            inner = json.loads(_extract_json(res))
            if isinstance(inner, dict) and "response" in inner:
                return inner
            if isinstance(inner, dict) and ("status" in inner or "task_id" in inner):
                return {"response": inner}
        except (json.JSONDecodeError, ValueError):
            pass
    if isinstance(res, dict) and "response" in res:
        return res
    if isinstance(res, dict) and ("status" in res or "task_id" in res):
        return {"response": res}
    if "status" in obj or "task_id" in obj:  # a bare response envelope
        return {"response": obj}
    return {"response": {"status": "failed", "error": {"code": "A2A_NO_RESPONSE", "message": json.dumps(obj)[:300]}}}


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("{"):
        return text
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        return text[i:j + 1]
    raise ValueError("no json")
