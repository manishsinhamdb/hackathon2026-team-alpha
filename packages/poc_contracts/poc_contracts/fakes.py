"""Fake agents that return contract-valid outputs without any LLM or cloud call.

Use them to develop any one agent before the others exist (Spec §10.5). Every
fake accepts an AgentEnvelope request and returns an AgentEnvelope response
whose artifact keys follow the §5.4 S3 layout. They write nothing to S3.
"""
from typing import Any

from .envelope import Envelope
from .ids import new_id
from .validate import validate


def _prefix(poc_id: str) -> str:
    return f"pocs/{poc_id}/"


class FakeDraftAgent:
    """draft_spec: returns needs_clarification once per poc unless answers are supplied."""
    def __init__(self):
        self._asked: set[str] = set()

    def draft_spec(self, env: dict[str, Any]) -> dict[str, Any]:
        req = validate("agent_envelope", env)["request"]
        p = req["params"]
        if not p.get("answers") and not p.get("force_assumptions") and req["poc_id"] not in self._asked:
            self._asked.add(req["poc_id"])
            return Envelope.needs_clarification(req["task_id"], [
                {"question_id": "q1", "field": "success_criteria", "question": "What measurable outcome defines success?",
                 "why_it_matters": "The Test Agent needs something it can assert.", "suggestions": ["p95 latency < 300 ms"]},
                {"question_id": "q2", "field": "data_entities", "question": "Which entities and rough volumes?",
                 "why_it_matters": "Drives schema design and seeding caps.", "suggestions": ["products 5k, orders 8k"]},
            ])
        v = "v001"
        pre = _prefix(req["poc_id"]) + f"spec/{v}/"
        return Envelope.succeeded(req["task_id"], {"spec_version": v, "assumptions": ["fake draft"]}, [
            Envelope.artifact("spec", pre + "poc_spec.md", v),
            Envelope.artifact("schema", pre + "schema_design.json", v),
            Envelope.artifact("query_patterns", pre + "query_patterns.json", v),
            Envelope.artifact("clarifications", pre + "clarifications.json", v),
        ])


class FakeCodingOrchestrator:
    def start_code_run(self, env: dict[str, Any]) -> dict[str, Any]:
        req = validate("agent_envelope", env)["request"]
        return Envelope.started(req["task_id"], new_id("run"))

    def repair_component(self, env: dict[str, Any]) -> dict[str, Any]:
        req = validate("agent_envelope", env)["request"]
        validate("failure_report", req["params"]["failure"])
        return Envelope.started(req["task_id"], new_id("run"))

    def get_code_bundle(self, env: dict[str, Any]) -> dict[str, Any]:
        req = validate("agent_envelope", env)["request"]
        v = req["params"]["code_version"]
        pre = _prefix(req["poc_id"]) + f"code/{v}/"
        return Envelope.succeeded(req["task_id"], {"bundle_key": pre + "bundle.tar.gz", "manifest_key": pre + "poc.manifest.json"},
                                  [Envelope.artifact("bundle", pre + "bundle.tar.gz", v)])


class _FakeCoder:
    component = ""
    def _gen(self, env: dict[str, Any], extra: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        req = validate("agent_envelope", env)["request"]
        v = req["params"]["code_version"]
        pre = _prefix(req["poc_id"]) + f"code/{v}/{self.component}/"
        return Envelope.succeeded(req["task_id"], {"component": self.component, "code_version": v},
                                  [Envelope.artifact("code", pre, v)] + (extra or []))


class FakeDataSeedingAgent(_FakeCoder):
    component = "seed"
    def generate_seed(self, env): return self._gen(env)


class FakeApiAgent(_FakeCoder):
    component = "backend"
    def generate_api(self, env):
        req = validate("agent_envelope", env)["request"]
        if req.get("mode") == "contract":
            v = req["params"]["code_version"]
            key = _prefix(req["poc_id"]) + f"code/{v}/api_contract.yaml"
            return Envelope.succeeded(req["task_id"], {"contract_key": key}, [Envelope.artifact("contract", key, v)])
        return self._gen(env)


class FakeFrontendAgent(_FakeCoder):
    component = "frontend"
    def generate_frontend(self, env): return self._gen(env)


class FakeDeployAgent:
    def start_deploy_run(self, env):
        req = validate("agent_envelope", env)["request"]
        return Envelope.started(req["task_id"], new_id("run"))
    def resume_run(self, env):
        req = validate("agent_envelope", env)["request"]
        return Envelope.started(req["task_id"], req["params"]["run_id"])
    def teardown_poc(self, env):
        req = validate("agent_envelope", env)["request"]
        return Envelope.started(req["task_id"], new_id("run"))
    def get_deployment(self, env):
        req = validate("agent_envelope", env)["request"]
        key = _prefix(req["poc_id"]) + f"deploy/{req['run_id']}/deployment.json"
        return Envelope.succeeded(req["task_id"], {"deployment_key": key}, [Envelope.artifact("deployment", key)])


class FakeTestAgent:
    def run_e2e(self, env):
        req = validate("agent_envelope", env)["request"]
        key = _prefix(req["poc_id"]) + f"test/{req['run_id']}/test_report.json"
        return Envelope.succeeded(req["task_id"], {"passed": 3, "failed": 0, "report_key": key},
                                  [Envelope.artifact("report", key)])


ALL_FAKES = {
    "draft_agent": FakeDraftAgent, "coding_orchestrator": FakeCodingOrchestrator,
    "data_seeding_agent": FakeDataSeedingAgent, "api_agent": FakeApiAgent,
    "frontend_agent": FakeFrontendAgent, "deploy_agent": FakeDeployAgent, "test_agent": FakeTestAgent,
}
