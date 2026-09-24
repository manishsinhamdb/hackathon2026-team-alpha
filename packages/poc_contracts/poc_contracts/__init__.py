"""poc_contracts — the frozen cross-agent contracts for POC Builder (Spec v1.0 §5, §8).

Every agent validates its inputs and outputs with `validate(kind, obj)`,
builds AgentEnvelope requests/responses with `Envelope`, and mints IDs with
`new_id`. Changing anything here is a contract change: bump CONTRACT_VERSION
and notify every agent owner.
"""
from .ids import new_id, next_version, is_id
from .validate import validate, ContractError, KINDS, schema_for
from .envelope import Envelope

CONTRACT_VERSION = "0.1.1"

__all__ = ["validate", "ContractError", "KINDS", "schema_for", "Envelope",
           "new_id", "next_version", "is_id", "CONTRACT_VERSION"]
