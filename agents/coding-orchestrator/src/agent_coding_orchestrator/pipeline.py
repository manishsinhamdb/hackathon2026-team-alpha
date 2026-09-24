"""Pure planning + assembly helpers for the Coding Orchestrator (Spec §6.3). No LLM, no cloud, no DB —
so the graph's decision logic can be unit-tested directly. Everything that touches S3 / the platform DB
lives in main.py tools; this module only computes plans and the poc.manifest.json document.
"""
from __future__ import annotations

from typing import Any

from poc_contracts import validate

# The full build order for a fresh code version (Spec §6.3): contract → seed → backend → frontend.
FRESH_ORDER = ["contract", "seed", "backend", "frontend"]

# Static description of each coder call: which agent/skill/tool serves it and what it produces.
CODERS: dict[str, dict[str, str]] = {
    "contract": {"skill": "generate-api", "agent": "api_agent", "tool": "generate_api", "produces": "contract"},
    "seed":     {"skill": "generate-seed", "agent": "data_seeding_agent", "tool": "generate_seed", "produces": "code"},
    "backend":  {"skill": "generate-api", "agent": "api_agent", "tool": "generate_api", "produces": "code"},
    "frontend": {"skill": "generate-frontend", "agent": "frontend_agent", "tool": "generate_frontend", "produces": "code"},
}

# The three real code components (contract is a versioned root file, not a component).
COMPONENTS = ["seed", "backend", "frontend"]


def _fresh_mode(step: str) -> str:
    """The A2A `mode` for a fresh (non-repair) build of a step: 'contract' for the contract, 'code' otherwise."""
    return "contract" if step == "contract" else "code"


def keys_for(poc_id: str, spec_version: str, code_version: str) -> dict[str, str]:
    """The canonical S3 keys a run reads and writes."""
    spec = f"pocs/{poc_id}/spec/{spec_version}/"
    return {
        "spec_key": spec + "poc_spec.md",
        "schema_key": spec + "schema_design.json",
        "query_patterns_key": spec + "query_patterns.json",
        "contract_key": f"pocs/{poc_id}/code/{code_version}/api_contract.yaml",
    }


def _inputs_for(step: str, keys: dict[str, str]) -> dict[str, str]:
    if step == "contract":
        return {"spec_key": keys["spec_key"], "schema_key": keys["schema_key"], "query_patterns_key": keys["query_patterns_key"]}
    if step == "seed":
        return {"schema_key": keys["schema_key"], "query_patterns_key": keys["query_patterns_key"]}
    if step == "backend":
        return {"contract_key": keys["contract_key"], "schema_key": keys["schema_key"], "query_patterns_key": keys["query_patterns_key"]}
    if step == "frontend":
        return {"spec_key": keys["spec_key"], "contract_key": keys["contract_key"]}
    raise ValueError(f"unknown step {step}")


def build_plan(ctx: dict[str, Any]) -> dict[str, Any]:
    """Compute the ordered coder calls for a run.

    ctx: {tool, poc_id, spec_version, code_version, prev_version?, failure?}
    Returns {steps: [...], changed_components: [...], repairs_of: vNNN|None, copy_components: [...], copy_contract: bool}.
    Each step: {step, component?, skill, agent, tool, mode, produces, inputs, previous_source_key?, failure?}.
    """
    poc_id = ctx["poc_id"]
    keys = keys_for(poc_id, ctx["spec_version"], ctx["code_version"])

    if ctx["tool"] == "start_code_run":
        steps = [_step(s, _fresh_mode(s), keys, ctx) for s in FRESH_ORDER]
        return {"steps": steps, "changed_components": list(COMPONENTS), "repairs_of": None,
                "copy_components": [], "copy_contract": False}

    # repair_component
    failure = ctx["failure"]
    prev = ctx["prev_version"]
    if failure.get("failure_class") == "CONTRACT_MISMATCH":
        regen = ["contract", "backend", "frontend"]           # rebuild the contract and both implementers, fresh
        changed = ["backend", "frontend"]
        modes = {"contract": "contract", "backend": "code", "frontend": "code"}
    else:
        comp = failure["component"]
        regen = [comp]                                        # repair only the failing component
        changed = [comp]
        modes = {comp: "repair"}
    steps = [_step(s, modes[s], keys, ctx, failure=failure) for s in regen]
    copy_components = [c for c in COMPONENTS if c not in changed]
    copy_contract = "contract" not in regen
    return {"steps": steps, "changed_components": changed, "repairs_of": prev,
            "copy_components": copy_components, "copy_contract": copy_contract}


def _step(step: str, mode: str, keys: dict[str, str], ctx: dict[str, Any],
          failure: dict[str, Any] | None = None) -> dict[str, Any]:
    meta = CODERS[step]
    out: dict[str, Any] = {
        "step": step,
        "skill": meta["skill"], "agent": meta["agent"], "tool": meta["tool"],
        "produces": meta["produces"], "mode": mode,
        "inputs": _inputs_for(step, keys),
    }
    if step != "contract":
        out["component"] = step
    if mode == "repair" and failure is not None:
        out["failure"] = failure
        out["previous_source_key"] = f"pocs/{ctx['poc_id']}/code/{ctx['prev_version']}/{step}/"
    return out


def build_poc_manifest(*, poc_id: str, code_version: str, spec_version: str, stack: dict[str, str],
                       contract_key: str, bundle_key: str, bundle_sha256: str, scanned_at: str,
                       violations: list[dict[str, Any]], run_id: str, changed_components: list[str],
                       repairs_of: str | None) -> dict[str, Any]:
    """Assemble and validate the poc.manifest.json for a whole code version (Spec §8.6)."""
    produced_by: dict[str, Any] = {"run_id": run_id, "changed_components": changed_components}
    if repairs_of:
        produced_by["repairs_of"] = repairs_of
    manifest = {
        "poc_id": poc_id,
        "code_version": code_version,
        "spec_version": spec_version,
        "stack": stack,
        "components": list(COMPONENTS),
        "contract_key": contract_key,
        "bundle_key": bundle_key,
        "bundle_sha256": bundle_sha256,
        "guardrail_scan": {"ok": not violations, "scanned_at": scanned_at, "violations": violations},
        "produced_by": produced_by,
    }
    return validate("poc_manifest", manifest)
