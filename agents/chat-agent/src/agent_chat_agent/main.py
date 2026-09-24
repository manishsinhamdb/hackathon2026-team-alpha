"""
Chat Agent — POC Builder user-facing front door (Spec §6.1).

Message contract: every incoming message is an AgentEnvelope request (JSON, text/plain over A2A);
every reply is an AgentEnvelope response (JSON). This agent is user-facing and drives the full
pipeline (draft, code, deploy, test) through the other agents. Not yet implemented.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Annotated, Any, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph
from magenta_sdklanggraph import App

from poc_contracts import Envelope, new_id, validate

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S", stream=sys.stdout)
logger = logging.getLogger(__name__)
load_dotenv()

APP_NAME = "Chat Agent"
app = App(app_name=APP_NAME)
logger.info("✅ App created")


class _Opt(TypedDict, total=False):
    request: dict[str, Any]
    done: bool
    result: dict[str, Any]


class ChatState(_Opt):
    messages: Annotated[list[BaseMessage], add_messages]


def _last_human_text(messages: list[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""


@app.entrypoint
def build_agent() -> CompiledStateGraph:
    logger.info("Building Chat Agent graph...")

    def reply(state: ChatState, response: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [AIMessage(content=json.dumps(response))], "done": True, "result": response}

    def parse_node(state: ChatState) -> dict[str, Any]:
        text = _last_human_text(state["messages"])
        try:
            env = validate("agent_envelope", json.loads(text))["request"]
        except Exception as e:
            fake_task = new_id("task")
            return reply(state, Envelope.failed(fake_task, "INVALID_ENVELOPE", f"expected AgentEnvelope JSON: {str(e)[:300]}"))
        return {"request": env, "done": False}

    def dispatch_node(state: ChatState) -> dict[str, Any]:
        if state.get("done"):
            return {}
        req = state["request"]
        task_id = req["task_id"]
        return reply(state, Envelope.failed(task_id, "NOT_IMPLEMENTED", "chat_agent not implemented yet"))

    b = StateGraph(ChatState)
    b.add_node("parse", parse_node)
    b.add_node("dispatch", dispatch_node)
    b.add_edge(START, "parse")
    b.add_conditional_edges("parse", lambda s: "end" if s.get("done") else "dispatch", {"dispatch": "dispatch", "end": END})
    b.add_edge("dispatch", END)
    graph = b.compile(checkpointer=app.checkpointer())
    logger.info("✅ Chat Agent graph compiled")
    return graph


def main():
    logger.info("=" * 60)
    logger.info(f"🚀 Starting {APP_NAME} (Runner SDK)")
    logger.info("=" * 60)
    app.run()


if __name__ == "__main__":
    main()
