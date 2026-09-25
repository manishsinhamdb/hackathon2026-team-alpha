"""Programmatic A2A calls from graph code (agent sandbox). The SDK exposes A2A as tools
(`app.a2a_tools()`), the same ones the LLM would call; we invoke them directly with an
AgentEnvelope JSON body and parse the JSON reply. Every callee returns quickly (started/succeeded),
so the 300 s A2A ceiling is never approached; long work is polled through the runs collection."""
from __future__ import annotations

import inspect
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


class A2AClient:
    def __init__(self, app: Any):
        self.app = app
        self._tools = None

    def _load(self, force: bool = False):
        if force or self._tools is None:
            self._tools = {t.name: t for t in self.app.a2a_tools()}
        return self._tools

    def refresh(self) -> None:
        """Drop the cached A2A tools and re-fetch them from app.a2a_tools().

        Guards against a genuinely transient discovery blip. NOTE (confirmed live 2026-09-25): this does
        NOT fix the ~5-minute OE token expiry — the SDK mints the OE bearer token once when the A2A client
        is first created and caches that client at the App level, so re-calling app.a2a_tools() reuses the
        same expired token and /a2a/discover 401s again immediately. Refreshing the OE token mid-run needs
        an SDK-level mechanism we cannot reach from app code. See docs/06 'Platform execution model' → the
        A2A token-lifetime BLOCKER."""
        self._load(force=True)

    def _find(self, *names: str):
        tools = self._load()
        for n in names:
            if n in tools:
                return tools[n]
        raise RuntimeError(f"A2A tool not found; have {list(tools)}")

    def discover(self) -> list[dict[str, Any]]:
        t = self._find("discover_available_agents", "discover_agents")
        out = invoke_tool(t, {})
        try:
            data = json.loads(out) if isinstance(out, str) else out
        except json.JSONDecodeError:
            return [{"raw": out}]
        return data if isinstance(data, list) else data.get("agents", [data])

    def find_agent(self, skill_or_name: str, _allow_refresh: bool = True) -> dict[str, Any]:
        for a in self.discover():
            blob = json.dumps(a).lower()
            if skill_or_name.lower() in blob:
                return a
        # No match. On a long run this is almost always the expired-token case: discovery 401'd and returned
        # nothing. Re-mint the token and try once more before giving up (a genuinely-absent agent still fails).
        if _allow_refresh:
            log.warning("no A2A agent matching %r; refreshing the A2A token (it expires ~5 min into a run) "
                        "and retrying discovery", skill_or_name)
            self.refresh()
            return self.find_agent(skill_or_name, _allow_refresh=False)
        raise RuntimeError(f"no A2A agent matching {skill_or_name!r}")

    def invoke(self, agent: dict[str, Any], envelope: dict[str, Any], timeout_s: int = 120) -> dict[str, Any]:
        """Send an AgentEnvelope request; return the parsed AgentEnvelope response."""
        t = self._find("invoke_a2a_agent", "invoke_agent")
        schema = getattr(t, "args", None) or {}
        args: dict[str, Any] = {}
        body = json.dumps(envelope)
        # map onto whatever the SDK's parameter names are
        for k in schema:
            kl = k.lower()
            if "message" in kl or "input" in kl or "prompt" in kl or "task" in kl or "text" in kl:
                args[k] = body
            elif "timeout" in kl:
                args[k] = timeout_s
            elif "id" in kl or "name" in kl or "agent" in kl or "workspace" in kl or "url" in kl:
                args[k] = agent.get("workspace_id") or agent.get("id") or agent.get("agent_id") or agent.get("name") or agent.get("url")
        log.info("A2A invoke %s args=%s", t.name, {k: (v if k in ("timeout",) else str(v)[:60]) for k, v in args.items()})
        out = invoke_tool(t, args)
        log.info("A2A raw reply type=%s repr=%s", type(out).__name__, repr(out)[:600])
        text = out if isinstance(out, str) else json.dumps(out)
        try:
            return _unwrap_envelope(json.loads(_extract_json(text)))
        except (json.JSONDecodeError, ValueError):
            return {"response": {"task_id": envelope["request"]["task_id"], "status": "failed",
                                 "error": {"code": "A2A_UNPARSEABLE", "message": text[:500]}}}


def _unwrap_envelope(obj: Any) -> dict[str, Any]:
    """The OE returns the callee's final message wrapped (e.g. {"result": "<envelope json>", "status": ...}).
    Normalise any of these shapes to a dict carrying the AgentEnvelope under "response"."""
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
