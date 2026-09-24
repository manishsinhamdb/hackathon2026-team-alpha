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


class A2AClient:
    def __init__(self, app: Any):
        self.app = app
        self._tools = None

    def _load(self):
        if self._tools is None:
            self._tools = {t.name: t for t in self.app.a2a_tools()}
        return self._tools

    def _find(self, *names: str):
        tools = self._load()
        for n in names:
            if n in tools:
                return tools[n]
        raise RuntimeError(f"A2A tool not found; have {list(tools)}")

    def discover(self) -> list[dict[str, Any]]:
        t = self._find("discover_available_agents", "discover_agents")
        out = t.invoke({})
        try:
            data = json.loads(out) if isinstance(out, str) else out
        except json.JSONDecodeError:
            return [{"raw": out}]
        return data if isinstance(data, list) else data.get("agents", [data])

    def find_agent(self, skill_or_name: str) -> dict[str, Any]:
        for a in self.discover():
            blob = json.dumps(a).lower()
            if skill_or_name.lower() in blob:
                return a
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
        out = t.invoke(args)
        text = out if isinstance(out, str) else json.dumps(out)
        try:
            return json.loads(_extract_json(text))
        except (json.JSONDecodeError, ValueError):
            return {"response": {"task_id": envelope["request"]["task_id"], "status": "failed",
                                 "error": {"code": "A2A_UNPARSEABLE", "message": text[:500]}}}


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("{"):
        return text
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        return text[i:j + 1]
    raise ValueError("no json")
