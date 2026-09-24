"""Skeleton tests for Frontend Agent — verify NOT_IMPLEMENTED stubs and envelope validation."""
import json
import pytest
from langchain_core.messages import HumanMessage
from poc_contracts import Envelope, new_id, validate

import agent_frontend_agent.main as m

POC, TASK = new_id("poc"), new_id("task")


def _invoke(tool: str, params: dict):
    graph = m.build_agent()
    env = Envelope.request(poc_id=POC, run_id=new_id("run"), caller="coding_orchestrator",
                           agent="frontend_agent", tool=tool, params=params, task_id=TASK)
    out = graph.invoke({"messages": [HumanMessage(content=json.dumps(env))]},
                       config={"configurable": {"thread_id": "t"}})
    return validate("agent_envelope", json.loads(out["messages"][-1].content))["response"]


def test_generate_frontend_not_implemented():
    resp = _invoke("generate_frontend", {"poc_id": POC, "code_version": "v001", "mode": "generate",
                                         "inputs": {"spec_key": "k1", "api_contract_key": "k2"}})
    assert resp["status"] == "failed"
    assert resp["error"]["code"] == "NOT_IMPLEMENTED"


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
