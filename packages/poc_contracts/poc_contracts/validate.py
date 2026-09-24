"""JSON-schema validation for every §8 artifact and §5.5 document."""
import json
from functools import lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

KINDS = (
    "agent_envelope",
    "poc_spec_frontmatter",
    "schema_design",
    "query_patterns",
    "component_manifest",
    "poc_manifest",
    "deployment",
    "test_report",
    "failure_report",
    "poc_document",
    "run_document",
    "task_document",
)


class ContractError(ValueError):
    """Raised when an object does not satisfy its contract.

    `errors` is a list of (json_path, message) so an agent can retry an LLM
    call with the exact fields that were wrong.
    """

    def __init__(self, kind: str, errors: list[tuple[str, str]]):
        self.kind = kind
        self.errors = errors
        lines = "\n".join(f"  {p or '$'}: {m}" for p, m in errors)
        super().__init__(f"{kind} failed contract validation:\n{lines}")


@lru_cache(maxsize=None)
def schema_for(kind: str) -> dict[str, Any]:
    if kind not in KINDS:
        raise KeyError(f"unknown contract kind {kind!r}; known: {KINDS}")
    text = resources.files("poc_contracts.schemas").joinpath(f"{kind}.json").read_text()
    return json.loads(text)


@lru_cache(maxsize=None)
def _validator(kind: str) -> Draft202012Validator:
    return Draft202012Validator(schema_for(kind), format_checker=FormatChecker())


def validate(kind: str, obj: Any) -> Any:
    """Validate `obj` against contract `kind`; returns `obj` unchanged or raises ContractError."""
    errs = sorted(_validator(kind).iter_errors(obj), key=lambda e: list(e.absolute_path))
    if errs:
        raise ContractError(kind, [("/".join(str(p) for p in e.absolute_path), e.message) for e in errs])
    return obj


def is_valid(kind: str, obj: Any) -> bool:
    try:
        validate(kind, obj)
        return True
    except ContractError:
        return False
