"""AgentEnvelope builders (Spec §8.1). Every agent-as-tool call uses these."""
from datetime import datetime, timezone
from typing import Any

from .ids import new_id
from .validate import validate


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class Envelope:
    @staticmethod
    def request(*, poc_id: str, run_id: str, caller: str, agent: str, tool: str,
                params: dict[str, Any], mode: str | None = None, task_id: str | None = None,
                trace_id: str | None = None, deadline_at: str | None = None,
                max_tokens: int | None = None) -> dict[str, Any]:
        req: dict[str, Any] = {
            "poc_id": poc_id, "run_id": run_id, "task_id": task_id or new_id("task"),
            "caller": caller, "agent": agent, "tool": tool, "params": params,
        }
        if mode: req["mode"] = mode
        if trace_id: req["trace_id"] = trace_id
        if deadline_at: req["deadline_at"] = deadline_at
        if max_tokens: req["budget"] = {"max_tokens": max_tokens}
        return validate("agent_envelope", {"request": req})

    @staticmethod
    def succeeded(task_id: str, result: dict[str, Any] | None = None,
                  artifacts: list[dict[str, Any]] | None = None,
                  usage: dict[str, int] | None = None) -> dict[str, Any]:
        resp: dict[str, Any] = {"task_id": task_id, "status": "succeeded"}
        if result is not None: resp["result"] = result
        if artifacts: resp["artifacts"] = artifacts
        if usage: resp["usage"] = usage
        return validate("agent_envelope", {"response": resp})

    @staticmethod
    def started(task_id: str, run_id: str) -> dict[str, Any]:
        """For async agents: acknowledge and return the run_id within 2 s (§5.3)."""
        return validate("agent_envelope", {"response": {"task_id": task_id, "status": "started", "result": {"run_id": run_id}}})

    @staticmethod
    def needs_clarification(task_id: str, questions: list[dict[str, Any]]) -> dict[str, Any]:
        return validate("agent_envelope", {"response": {"task_id": task_id, "status": "needs_clarification",
                                                        "result": {"questions": questions}}})

    @staticmethod
    def failed(task_id: str, code: str, message: str, retryable: bool = False,
               detail: dict[str, Any] | None = None) -> dict[str, Any]:
        err: dict[str, Any] = {"code": code, "message": message, "retryable": retryable}
        if detail: err["detail"] = detail
        return validate("agent_envelope", {"response": {"task_id": task_id, "status": "failed", "error": err}})

    @staticmethod
    def artifact(kind: str, key: str, version: str | None = None) -> dict[str, Any]:
        a: dict[str, Any] = {"kind": kind, "key": key}
        if version: a["version"] = version
        return a

    @staticmethod
    def now() -> str:
        return _now()
