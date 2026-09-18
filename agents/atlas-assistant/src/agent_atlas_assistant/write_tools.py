"""Write tools for atlas-assistant.

Human review follows the Agentic Platform insurance-agent template pattern:
- human_review (agent sandbox) suspends with interrupt() and returns the reviewer's decision.
- pause_resume_cluster and scale_cluster (Tool Pod) perform the change. The agent calls them
  after human_review returns, passing the reviewer's decision, whether approved or denied.

PLATFORM CALLOUT: as in the platform template, the rule "call human_review before a change
tool" is enforced by the system prompt, not by a code-level gate. The change tools honour a
denied decision, but they cannot verify that a review took place.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import logging
import os
import re
import socket
from datetime import datetime, timedelta, timezone
from typing import Any

from langgraph.types import interrupt
from magenta_sdklanggraph import App

from agent_atlas_assistant import atlas_api
from agent_atlas_assistant.atlas_api import AtlasAPIError

logger = logging.getLogger(__name__)

ACCESS_LIST_VERSION = "application/vnd.atlas.2023-01-01+json"
DEFAULT_EXPIRY_HOURS = 24
MAX_EXPIRY_HOURS = 168
MIN_IPV4_PREFIX = 24
MIN_IPV6_PREFIX = 64
TIER_PATTERN = re.compile(r"[MR]\d+[A-Z0-9_]*")
VALID_DECISIONS = {"approved", "denied"}

HUMAN_REVIEW_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["decision"],
    "properties": {
        "decision": {
            "type": "string",
            "title": "Decision",
            "description": "Approve or deny this cluster change.",
            "enum": ["approved", "denied"],
        },
        "reviewer_notes": {
            "type": "string",
            "title": "Reviewer notes",
        },
    },
    "additionalProperties": False,
}


def build_access_entry(
    ip_or_cidr: str, reason: str, expiry_hours: int = DEFAULT_EXPIRY_HOURS
) -> tuple[dict[str, Any] | None, str | None]:
    """Apply IP access guardrails. Returns (entry, None) if allowed, or (None, refusal_reason)."""
    try:
        network = ipaddress.ip_network(str(ip_or_cidr).strip(), strict=False)
    except ValueError:
        return None, f"Not a valid IP address or CIDR: {ip_or_cidr}"

    min_prefix = MIN_IPV4_PREFIX if network.version == 4 else MIN_IPV6_PREFIX
    if network.prefixlen < min_prefix:
        return None, f"Range {network} is wider than the allowed /{min_prefix}. Use a narrower range."

    try:
        hours = int(expiry_hours)
    except (TypeError, ValueError):
        hours = DEFAULT_EXPIRY_HOURS
    hours = max(1, min(hours, MAX_EXPIRY_HOURS))
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0)
    entry: dict[str, Any] = {
        "comment": f"Atlas Assistant: {reason}"[:80],
        "deleteAfterDate": expires_at.isoformat().replace("+00:00", "Z"),
    }
    if network.prefixlen == network.max_prefixlen:
        entry["ipAddress"] = str(network.network_address)
    else:
        entry["cidrBlock"] = str(network)
    return entry, None


def _decision(value: str) -> str | None:
    d = str(value).strip().lower()
    return d if d in VALID_DECISIONS else None


def register_write_tools(app: App) -> None:
    @app.tool(is_local=True)
    def human_review(
        action: str,
        cluster_name: str,
        requested_change: str,
        reason: str,
        current_state: str,
        conversation_summary: str,
    ) -> str:
        """Request human review before changing an Atlas cluster.

        This tool SUSPENDS execution until a human provides input.
        Use this before every pause, resume or tier change of a cluster.

        Args:
            action: The change type: pause_cluster, resume_cluster or scale_cluster
            cluster_name: Exact cluster name as returned by list_clusters
            requested_change: The exact change, for example "pause cluster" or "change tier from M10 to M20"
            reason: Reason for the change, taken from the user's request
            current_state: Current state and tier from list_clusters, for example "IDLE, M10, not paused"
            conversation_summary: Summary of the request that led to this review
        """
        task_id = f"REVIEW-{action}-{cluster_name}"
        logger.info(
            "PLACEMENT approval-request %s %s host=%s mode=%s",
            action, cluster_name, socket.gethostname(), os.environ.get("RUNNER_MODE", "?"),
        )
        answer = interrupt(
            {
                "action": "review_cluster_change",
                "task_id": task_id,
                "change_type": action,
                "cluster": cluster_name,
                "requested_change": requested_change,
                "current_state": current_state,
                "reason": reason,
                "conversation_summary": conversation_summary,
                "message": "Please review the cluster change and approve or deny it.",
                "response_schema": HUMAN_REVIEW_RESPONSE_SCHEMA,
            }
        )
        if not isinstance(answer, dict):
            raise TypeError("human review resume value must be an object")
        review_decision = answer.get("decision")
        if review_decision not in VALID_DECISIONS:
            raise ValueError("human review decision must be 'approved' or 'denied'")
        reviewer_notes = answer.get("reviewer_notes", "")
        if not isinstance(reviewer_notes, str):
            raise TypeError("human review reviewer_notes must be a string")

        return json.dumps(
            {
                "task_id": task_id,
                "cluster": cluster_name,
                "change_type": action,
                "requested_change": requested_change,
                "decision": review_decision,
                "reviewer_notes": reviewer_notes,
            },
            indent=2,
        )

    @app.tool(is_local=False)
    def pause_resume_cluster(
        cluster_name: str, action: str, decision: str, reviewer_notes: str = ""
    ) -> str:
        """Pause or resume one Atlas cluster after human review.

        Use this after receiving the human_review decision. Pass the reviewer's decision
        exactly. If the decision is denied, no change is made in Atlas.
        Only dedicated clusters (M10 and larger) can be paused.

        Args:
            cluster_name: Exact cluster name as returned by list_clusters
            action: "pause" or "resume"
            decision: The reviewer's decision from human_review (approved or denied)
            reviewer_notes: Notes from the reviewer (optional)
        """
        action = action.strip().lower()
        if action not in ("pause", "resume"):
            return json.dumps({"status": "failed", "error": 'action must be "pause" or "resume"'})
        d = _decision(decision)
        if d is None:
            return json.dumps({"status": "failed", "error": "decision must be 'approved' or 'denied'"})
        if d == "denied":
            return json.dumps(
                {
                    "status": "not_changed",
                    "cluster": cluster_name,
                    "decision": d,
                    "reviewer_notes": reviewer_notes,
                    "message": f"The {action} of {cluster_name} was denied. No change was made in Atlas.",
                },
                indent=2,
            )
        try:
            pid = atlas_api.project_id()
            result = atlas_api.request(
                "PATCH",
                f"/groups/{pid}/clusters/{cluster_name}",
                json_body={"paused": action == "pause"},
            )
        except AtlasAPIError as exc:
            return json.dumps({"status": "failed", "cluster": cluster_name, "error": str(exc)})
        return json.dumps(
            {
                "status": "submitted",
                "cluster": cluster_name,
                "action": action,
                "state": result.get("stateName"),
                "paused": result.get("paused"),
                "decision": d,
                "reviewer_notes": reviewer_notes,
                "message": f"The {action} of {cluster_name} has been submitted to Atlas.",
            },
            indent=2,
        )

    @app.tool(is_local=False)
    def scale_cluster(
        cluster_name: str, target_tier: str, decision: str, reviewer_notes: str = ""
    ) -> str:
        """Change the instance size (tier) of one Atlas cluster after human review.

        Use this after receiving the human_review decision. Pass the reviewer's decision
        exactly. If the decision is denied, no change is made in Atlas. If compute
        auto-scaling limits block the change, the Atlas error is returned as is.

        Args:
            cluster_name: Exact cluster name as returned by list_clusters
            target_tier: New instance size, for example M10, M20, M30 or M30_GEN_2
            decision: The reviewer's decision from human_review (approved or denied)
            reviewer_notes: Notes from the reviewer (optional)
        """
        tier = target_tier.strip().upper().replace(" ", "_")
        if not TIER_PATTERN.fullmatch(tier):
            return json.dumps({"status": "failed", "error": f"Invalid tier format: {target_tier}"})
        d = _decision(decision)
        if d is None:
            return json.dumps({"status": "failed", "error": "decision must be 'approved' or 'denied'"})
        if d == "denied":
            return json.dumps(
                {
                    "status": "not_changed",
                    "cluster": cluster_name,
                    "decision": d,
                    "reviewer_notes": reviewer_notes,
                    "message": f"The tier change of {cluster_name} to {tier} was denied. No change was made in Atlas.",
                },
                indent=2,
            )
        try:
            pid = atlas_api.project_id()
            cluster = atlas_api.request("GET", f"/groups/{pid}/clusters/{cluster_name}")
            specs = copy.deepcopy(cluster.get("replicationSpecs") or [])
            old_tier = None
            for spec in specs:
                for region in spec.get("regionConfigs", []):
                    for key in ("electableSpecs", "readOnlySpecs", "analyticsSpecs"):
                        hardware = region.get(key)
                        if isinstance(hardware, dict) and hardware.get("instanceSize"):
                            if key == "electableSpecs" and old_tier is None:
                                old_tier = hardware["instanceSize"]
                            hardware["instanceSize"] = tier
            if old_tier is None:
                return json.dumps({"status": "failed", "cluster": cluster_name, "error": "No sizable node groups found."})
            result = atlas_api.request(
                "PATCH",
                f"/groups/{pid}/clusters/{cluster_name}",
                json_body={"replicationSpecs": specs},
            )
        except AtlasAPIError as exc:
            return json.dumps({"status": "failed", "cluster": cluster_name, "error": str(exc)})
        return json.dumps(
            {
                "status": "submitted",
                "cluster": cluster_name,
                "old_tier": old_tier,
                "new_tier": tier,
                "state": result.get("stateName"),
                "decision": d,
                "reviewer_notes": reviewer_notes,
                "message": f"The tier change of {cluster_name} to {tier} has been submitted to Atlas.",
            },
            indent=2,
        )

    @app.tool(is_local=False)
    def add_ip_access(ip_or_cidr: str, reason: str, expiry_hours: int = DEFAULT_EXPIRY_HOURS) -> str:
        """Add a temporary entry to the Atlas project IP access list. Runs without human review.

        Guardrails enforced in code: single IPs or ranges no wider than /24 (IPv4)
        or /64 (IPv6); every entry expires automatically (default 24 hours,
        maximum 168 hours); every entry is tagged with the reason.

        Args:
            ip_or_cidr: A single IP address such as 203.0.113.10, or a CIDR range such as 203.0.113.0/24
            reason: Short reason for access, taken from the user's request
            expiry_hours: Hours until Atlas removes the entry (1 to 168, default 24)
        """
        entry, refusal = build_access_entry(ip_or_cidr, reason, expiry_hours)
        if refusal or entry is None:
            return json.dumps({"status": "refused", "error": refusal})
        try:
            pid = atlas_api.project_id()
            atlas_api.request(
                "POST",
                f"/groups/{pid}/accessList",
                version=ACCESS_LIST_VERSION,
                json_body=[entry],  # type: ignore[arg-type]
            )
        except AtlasAPIError as exc:
            return json.dumps({"status": "failed", "error": str(exc)})
        return json.dumps(
            {
                "status": "added",
                "entry": entry.get("ipAddress") or entry.get("cidrBlock"),
                "expires_at_utc": entry["deleteAfterDate"],
                "comment": entry["comment"],
            }
        )
