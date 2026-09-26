"""A minimal stand-in for agent_engine_sdk_langgraph so the graph can be exercised without the platform.
Tools decorated with @app.tool are collected; app.get_tools() returns LangChain-compatible tools whose
.invoke(kwargs) calls the function; a2a_tools() returns two fake tools driven by the test."""
import os, sys, types, json
from typing import Any, Callable


class _Tool:
    def __init__(self, fn: Callable, name: str):
        self.fn, self.name = fn, name
        self.args = {}
    def invoke(self, call: dict[str, Any]):
        args = call.get("args", call) if isinstance(call, dict) and call.get("type") == "tool_call" else call
        return self.fn(**args)


class FakeApp:
    def __init__(self, app_name: str):
        self.app_name = app_name
        self._tools: dict[str, _Tool] = {}
        self._entry = None
        self.a2a_handlers: dict[str, Callable[[dict], dict]] = {}
    def tool(self, **_kw):
        def deco(fn):
            self._tools[fn.__name__] = _Tool(fn, fn.__name__)
            return fn
        return deco
    def entrypoint(self, fn):
        self._entry = fn
        return fn
    def get_tools(self):
        return list(self._tools.values())
    def get_tool_schemas(self):
        return []
    def checkpointer(self):
        return None
    def llm(self, llm):
        return llm
    def get_current_user_id(self):
        return "u_test"
    def a2a_tools(self):
        app = self
        def discover_available_agents():
            return json.dumps([{"name": n, "id": f"ws-{n}", "skills": [n]} for n in app.a2a_handlers])
        def invoke_a2a_agent(agent_id: str, message: str, timeout: int = 300):
            name = agent_id.replace("ws-", "")
            return json.dumps(app.a2a_handlers[name](json.loads(message)))
        t1, t2 = _Tool(discover_available_agents, "discover_available_agents"), _Tool(invoke_a2a_agent, "invoke_a2a_agent")
        t2.args = {"agent_id": {}, "message": {}, "timeout": {}}
        return [t1, t2]
    def run(self):
        pass


mod = types.ModuleType("agent_engine_sdk_langgraph")
mod.App = FakeApp
sys.modules["agent_engine_sdk_langgraph"] = mod

# The backend compile gate (npm install + tsc) is exercised with injected fakes; never hit npm from unit tests.
os.environ.setdefault("API_TYPECHECK", "0")
