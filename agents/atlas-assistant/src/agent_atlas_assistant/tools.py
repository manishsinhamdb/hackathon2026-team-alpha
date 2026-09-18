"""Tool definitions for atlas-assistant (Phase 3: read-only tools)."""

from __future__ import annotations

import json
from statistics import mean
from typing import Any

from magenta_sdklanggraph import App

from agent_atlas_assistant import atlas_api
from agent_atlas_assistant.atlas_api import AtlasAPIError

MAX_METRIC_HOURS = 48
PROCESS_METRICS = [
    "PROCESS_NORMALIZED_CPU_USER",
    "SYSTEM_MEMORY_USED",
    "SYSTEM_MEMORY_AVAILABLE",
    "CONNECTIONS",
]


def _first_region(cluster: dict[str, Any]) -> dict[str, Any]:
    try:
        return cluster["replicationSpecs"][0]["regionConfigs"][0]
    except (KeyError, IndexError, TypeError):
        return {}


def _series(payload: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {m.get("name"): m.get("dataPoints", []) for m in payload.get("measurements", [])}


def _summary(points: list[dict[str, Any]]) -> dict[str, float] | None:
    values = [p["value"] for p in points if p.get("value") is not None]
    if not values:
        return None
    return {
        "avg": round(mean(values), 2),
        "peak": round(max(values), 2),
        "latest": round(values[-1], 2),
    }


def register(app: App) -> None:
    @app.tool(is_local=False)
    def list_clusters() -> str:
        """List every cluster in the configured Atlas project.

        Returns each cluster's name, tier (instance size such as M10), state
        (such as IDLE, CREATING, UPDATING), whether it is paused, MongoDB version,
        cloud provider and region. Call this first whenever the user has not
        named a cluster exactly.
        """
        try:
            pid = atlas_api.project_id()
            data = atlas_api.request("GET", f"/groups/{pid}/clusters")
        except AtlasAPIError as exc:
            return json.dumps({"error": str(exc)})
        clusters = []
        for c in data.get("results", []):
            region = _first_region(c)
            clusters.append(
                {
                    "name": c.get("name"),
                    "tier": region.get("electableSpecs", {}).get("instanceSize"),
                    "state": c.get("stateName"),
                    "paused": c.get("paused"),
                    "mongodb_version": c.get("mongoDBVersion"),
                    "provider": region.get("backingProviderName") or region.get("providerName"),
                    "region": region.get("regionName"),
                }
            )
        return json.dumps({"count": len(clusters), "clusters": clusters})

    @app.tool(is_local=False)
    def get_cluster_metrics(cluster_name: str, hours: int = 48) -> str:
        """Summarise recent health metrics for one Atlas cluster.

        Reads hourly metrics from the cluster's primary node for the last
        `hours` hours (1 to 48, default 48) and returns average, peak and latest
        values for CPU percent, memory used percent, connections and disk space
        used percent. Paused clusters and free or shared tiers (M0, Flex) have no
        node metrics; the result says so.

        Args:
            cluster_name: Exact cluster name as returned by list_clusters.
            hours: Look-back window in hours, between 1 and 48.
        """
        hours = max(1, min(int(hours), MAX_METRIC_HOURS))
        try:
            pid = atlas_api.project_id()
            cluster = atlas_api.request("GET", f"/groups/{pid}/clusters/{cluster_name}")
            if cluster.get("paused"):
                return json.dumps(
                    {
                        "cluster": cluster_name,
                        "paused": True,
                        "message": "Cluster is paused; Atlas collects no live metrics while paused.",
                    }
                )
            processes = atlas_api.request(
                "GET",
                f"/groups/{pid}/processes",
                version=atlas_api.MONITORING_VERSION,
                params={"itemsPerPage": 500},
            ).get("results", [])
            prefix = cluster_name.lower() + "-"
            members = [
                p
                for p in processes
                if str(p.get("userAlias") or p.get("hostname") or "").lower().startswith(prefix)
            ]
            if not members:
                return json.dumps(
                    {
                        "cluster": cluster_name,
                        "error": "No monitored nodes found. Free and shared tiers (M0, Flex) do not expose node metrics.",
                    }
                )
            primary = next((p for p in members if p.get("typeName") == "REPLICA_PRIMARY"), members[0])
            process_id = f"{primary['hostname']}:{primary['port']}"
            window = [("granularity", "PT1H"), ("period", f"PT{hours}H")]
            node = _series(
                atlas_api.request(
                    "GET",
                    f"/groups/{pid}/processes/{process_id}/measurements",
                    version=atlas_api.MONITORING_VERSION,
                    params=window + [("m", m) for m in PROCESS_METRICS],
                )
            )
        except AtlasAPIError as exc:
            return json.dumps({"cluster": cluster_name, "error": str(exc)})

        used = _summary(node.get("SYSTEM_MEMORY_USED", []))
        available = _summary(node.get("SYSTEM_MEMORY_AVAILABLE", []))
        memory = None
        if used and available and (used["avg"] + available["avg"]) > 0:
            memory = {
                "avg": round(100 * used["avg"] / (used["avg"] + available["avg"]), 2),
                "latest": round(100 * used["latest"] / (used["latest"] + available["latest"]), 2)
                if (used["latest"] + available["latest"]) > 0
                else None,
            }

        disk = None
        try:
            partitions = atlas_api.request(
                "GET",
                f"/groups/{pid}/processes/{process_id}/disks",
                version=atlas_api.MONITORING_VERSION,
            ).get("results", [])
            if partitions:
                partition = partitions[0].get("partitionName")
                disk_series = _series(
                    atlas_api.request(
                        "GET",
                        f"/groups/{pid}/processes/{process_id}/disks/{partition}/measurements",
                        version=atlas_api.MONITORING_VERSION,
                        params=window + [("m", "DISK_PARTITION_SPACE_PERCENT_USED")],
                    )
                )
                disk = _summary(disk_series.get("DISK_PARTITION_SPACE_PERCENT_USED", []))
        except AtlasAPIError:
            disk = None

        return json.dumps(
            {
                "cluster": cluster_name,
                "node": primary.get("userAlias") or primary.get("hostname"),
                "node_role": primary.get("typeName"),
                "window_hours": hours,
                "granularity": "1 hour",
                "cpu_percent": _summary(node.get("PROCESS_NORMALIZED_CPU_USER", [])),
                "memory_used_percent": memory,
                "connections": _summary(node.get("CONNECTIONS", [])),
                "disk_used_percent": disk,
            }
        )
