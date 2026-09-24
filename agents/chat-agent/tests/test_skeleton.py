"""Skeleton tests for Chat Agent — verify NOT_IMPLEMENTED stubs and envelope validation."""
import json
import pytest
from langchain_core.messages import HumanMessage
from poc_contracts import Envelope, new_id, validate

import agent_chat_agent.main as m

POC, TASK = new_id("poc"), new_id("task")


def _invoke(tool: str, params: dict):
    graph = m.build_agent()
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="web_ui", agent="chat_agent",
                           tool=tool, params=params, task_id=TASK)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                       config={"configurable": {"thread_id": "t"}})
    return validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]


def test_chat_not_implemented():
    resp = _invoke("draft_spec", {"poc_id": POC})
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "NOT_IMPLEMENTED"


def test_invalid_envelope():
    graph = m.build_agent()
    out = graph.invoke({"messages": [HumanMessage(content="not json at all")]},
                       config={"configurable": {"thread_id": "t2"}})
    resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "INVALID_ENVELOPE"
