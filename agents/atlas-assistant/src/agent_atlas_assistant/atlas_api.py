"""Thin Atlas Administration API client (API key pair, HTTP digest auth)."""

from __future__ import annotations

import logging
import os
import socket
from typing import Any

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://cloud.mongodb.com/api/atlas/v2"
CLUSTERS_VERSION = "application/vnd.atlas.2024-08-05+json"
MONITORING_VERSION = "application/vnd.atlas.2023-01-01+json"


class AtlasAPIError(Exception):
    """Raised when Atlas rejects a call or credentials are missing."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Atlas API {status}: {detail}")
        self.status = status
        self.detail = detail


def project_id() -> str:
    pid = os.environ.get("ATLAS_PROJECT_ID", "")
    if not pid:
        raise AtlasAPIError(0, "ATLAS_PROJECT_ID is missing from .env")
    return pid


def _auth() -> httpx.DigestAuth:
    public_key = os.environ.get("ATLAS_PUBLIC_KEY", "")
    private_key = os.environ.get("ATLAS_PRIVATE_KEY", "")
    if not public_key or not private_key:
        raise AtlasAPIError(0, "ATLAS_PUBLIC_KEY / ATLAS_PRIVATE_KEY are missing from .env")
    return httpx.DigestAuth(public_key, private_key)


def request(
    method: str,
    path: str,
    *,
    version: str = CLUSTERS_VERSION,
    params: Any = None,
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logger.info("PLACEMENT atlas-call %s %s host=%s mode=%s", method, path, socket.gethostname(), os.environ.get("RUNNER_MODE", "?"))
    headers = {"Accept": version}
    if json_body is not None:
        headers["Content-Type"] = version
    with httpx.Client(auth=_auth(), timeout=30) as client:
        resp = client.request(
            method, f"{BASE_URL}{path}", headers=headers, params=params, json=json_body
        )
    if resp.status_code >= 400:
        try:
            body = resp.json()
            detail = body.get("detail") or body.get("reason") or resp.text
        except ValueError:
            detail = resp.text
        raise AtlasAPIError(resp.status_code, str(detail))
    return resp.json() if resp.content else {}
