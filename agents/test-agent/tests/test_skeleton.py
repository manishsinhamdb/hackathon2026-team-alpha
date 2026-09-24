"""Skeleton tests for Test Agent — verify envelope validation and bad-tool handling.
The NOT_IMPLEMENTED test has been removed since run_e2e is now fully implemented."""
import json
import pytest
from langchain_core.messages import HumanMessage
from poc_contracts import Envelope, new_id, validate

import agent_test_agent.main as m

POC, TASK = new_id("poc"), new_id("task")


def _invoke(tool: str, params: dict):
    graph = m.build_agent()
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="deploy_agent",
                           agent="test_agent", tool=tool, params=params, task_id=TASK)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                       config={"configurable": {"thread_id": "t"}})
    return validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]


def test_unknown_tool_bad_tool():
    resp = _invoke("nonexistent_tool", {"poc_id": POC})
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "BAD_TOOL"


def test_invalid_envelope():
    graph = m.build_agent()
    out = graph.invoke({"messages": [HumanMessage(content="not json at all")]},
                       config={"configurable": {"thread_id": "t2"}})
    resp = validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "INVALID_ENVELOPE"


def test_run_e2e_bad_tool_name():
    """run_e2e with wrong tool name in envelope → BAD_TOOL from test_start_run."""
    resp = _invoke("wrong_tool", {"poc_id": POC, "scope": "all"})
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "BAD_TOOL"
