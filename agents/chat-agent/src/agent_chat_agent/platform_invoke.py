"""Top-level platform invocations for STARTING long-running stages (code / deploy / teardown).

Why this exists (platform execution model, confirmed against the reference guide):
An A2A invocation is a CHILD EXECUTION of the calling turn/session. When the chat turn completes (the
chat agent fast-acks a stage start and returns), the platform CANCELS any live child A2A execution. The
A2A call timeout is also capped at 300 s. Locally the OE let children outlive the parent turn, so the
fast-ack pattern worked; in the cloud the orchestrator/deploy child was killed mid-flight.

The fix: start a long-running stage with a TOP-LEVEL invocation of the specialist's workspace through the
platform invoke API — the same call an external client makes. That run is its OWN root session, independent
of the chat turn, so it survives the chat turn ending (and our short client-side disconnect). Draft stays
A2A (it returns within the turn); orchestrator→coders and deploy→test stay A2A (each < 300 s inside a parent
that is alive).

This module is dependency-free (stdlib urllib) so it adds nothing to the build. It:
  - exchanges the service-account client id/secret (project secrets POC_PLATFORM_SA_CLIENT_ID /
    POC_PLATFORM_SA_CLIENT_SECRET, injected into the pod env) for a short-lived access token, cached for
    its lifetime and refreshed on 401;
  - resolves a target workspace id by skill via the platform workspaces API (A2A discovery only exposes the
    A2A *app id*, not the ws- id), with an optional POC_WORKSPACE_IDS env override — never a hard-coded id;
  - fires POST .../workspaces/<id>/invoke with the AgentEnvelope as the `message`, plus session_id/user_id.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://agentic-platform.mongodb.com"
# The platform project this deployment lives in. The pod normally injects PROJECT_ID; this default (the
# hackathon2026 project, from `agentic context list`) is the fallback so the invoke URL still resolves if it
# is absent. It is a single environment constant, not a per-target id — target *workspace* ids are always
# resolved dynamically (resolve_workspace_id), never hard-coded. Override with any of the env names below.
DEFAULT_PROJECT_ID = "6aacc8c7a77ed8f0d8a5f8cf"

# skill (as used by A2A discovery / find_agent) -> deployed workspace name (as the platform lists it).
# Only stages started top-level need an entry; names, not ids, so nothing environment-specific is baked in.
_SKILL_WS_NAME = {
    "code-orchestration": "coding-orchestrator",
    "deploy-operations": "deploy-agent",
    "e2e-tests": "test-agent",
}


def _base_url() -> str:
    return (os.environ.get("AGENTIC_PLATFORM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _project_id() -> str:
    return (os.environ.get("PROJECT_ID") or os.environ.get("POC_PLATFORM_PROJECT_ID")
            or os.environ.get("AGENTIC_PLATFORM_PROJECT_ID") or os.environ.get("GROUP_ID")
            or DEFAULT_PROJECT_ID)


# --- low-level HTTP (monkeypatched in tests) ---------------------------------

def _http(method: str, url: str, *, headers: dict[str, str] | None = None,
          data: bytes | None = None, timeout: float = 30) -> tuple[int, bytes]:
    """Return (status_code, body_bytes). HTTP errors are returned, not raised, so callers can branch on 401."""
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read()
    except urllib.error.HTTPError as e:  # 4xx/5xx carry a body we want to see
        return e.code, e.read()


# --- token exchange + cache --------------------------------------------------

class _TokenCache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._token: str | None = None
        self._exp: float = 0.0

    def get(self, force: bool = False) -> str:
        with self._lock:
            if not force and self._token and time.time() < self._exp:
                return self._token
            token, ttl = _exchange_token()
            self._token = token
            # refresh a minute early; never trust a ttl shorter than 30 s
            self._exp = time.time() + max(int(ttl) - 60, 30)
            return token

    def clear(self) -> None:
        with self._lock:
            self._token, self._exp = None, 0.0


_tokens = _TokenCache()


def _exchange_token() -> tuple[str, int]:
    cid = os.environ.get("POC_PLATFORM_SA_CLIENT_ID")
    secret = os.environ.get("POC_PLATFORM_SA_CLIENT_SECRET")
    if not cid or not secret:
        raise RuntimeError("POC_PLATFORM_SA_CLIENT_ID / POC_PLATFORM_SA_CLIENT_SECRET are not set")
    body = urllib.parse.urlencode(
        {"grant_type": "client_credentials", "client_id": cid, "client_secret": secret}
    ).encode()
    status, raw = _http("POST", f"{_base_url()}/api/v1/oauth/token",
                        headers={"Content-Type": "application/x-www-form-urlencoded"}, data=body, timeout=30)
    if status != 200:
        # never log the credentials; the body is the platform's error, e.g. {"error":"invalid_client"}
        raise RuntimeError(f"platform token exchange failed: HTTP {status} {raw[:200]!r}")
    d = json.loads(raw)
    return d["access_token"], int(d.get("expires_in", 3600))


def get_token(force_refresh: bool = False) -> str:
    return _tokens.get(force=force_refresh)


# --- workspace id resolution -------------------------------------------------

_ws_map_cache: dict[str, str] = {}
_ws_lock = threading.Lock()


def _list_workspaces() -> dict[str, str]:
    """Return {workspace_name: workspace_id} from the platform workspaces API (refresh token once on 401)."""
    url = f"{_base_url()}/api/v1/projects/{_project_id()}/workspaces?limit=200"
    status, raw = _http("GET", url, headers={"Authorization": f"Bearer {get_token()}"}, timeout=30)
    if status == 401:
        status, raw = _http("GET", url, headers={"Authorization": f"Bearer {get_token(force_refresh=True)}"}, timeout=30)
    if status != 200:
        raise RuntimeError(f"platform workspace list failed: HTTP {status} {raw[:200]!r}")
    out: dict[str, str] = {}
    for w in json.loads(raw).get("workspaces", []):
        name = w.get("name") or w.get("workspace_name")
        wid = w.get("workspace_id") or w.get("id")
        if name and wid:
            out[name] = wid
    return out


def resolve_workspace_id(skill: str) -> str:
    """Resolve a target workspace id by skill. Env override POC_WORKSPACE_IDS (JSON {skill: ws-id}) wins;
    otherwise map skill -> workspace name and look the id up via the platform workspaces API (cached)."""
    override = os.environ.get("POC_WORKSPACE_IDS")
    if override:
        try:
            m = json.loads(override)
            if isinstance(m, dict) and m.get(skill):
                return m[skill]
        except json.JSONDecodeError:
            log.warning("POC_WORKSPACE_IDS is not valid JSON; ignoring")
    name = _SKILL_WS_NAME.get(skill, skill)
    with _ws_lock:
        if name not in _ws_map_cache:
            _ws_map_cache.update(_list_workspaces())
        wid = _ws_map_cache.get(name)
    if not wid:
        raise RuntimeError(f"could not resolve a workspace id for skill {skill!r} (workspace name {name!r})")
    return wid


# --- invoke ------------------------------------------------------------------

def invoke_workspace(workspace_id: str, message: str, *, session_id: str, user_id: str,
                     timeout_s: float = 120) -> dict[str, Any]:
    """Fire a TOP-LEVEL invocation of a workspace (its own root session) and return the parsed platform
    response dict {success, response, execution_id, status}. `message` is the AgentEnvelope JSON, exactly as
    the A2A path passes it. Refreshes the token once on 401. A short timeout_s is expected: the run may take
    minutes, but it keeps executing server-side after we disconnect (it is a root session, not our child)."""
    url = f"{_base_url()}/api/v1/projects/{_project_id()}/workspaces/{workspace_id}/invoke"
    payload = json.dumps({"message": message, "session_id": session_id, "user_id": user_id}).encode()

    def _post(token: str) -> tuple[int, bytes]:
        return _http("POST", url, data=payload, timeout=timeout_s,
                     headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})

    status, raw = _post(get_token())
    if status == 401:
        status, raw = _post(get_token(force_refresh=True))
    if status >= 400:
        raise RuntimeError(f"platform invoke failed: HTTP {status} {raw[:300]!r}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"success": True, "response": raw.decode("utf-8", "replace")}


def invoke_by_skill(skill: str, message: str, *, session_id: str, user_id: str,
                    timeout_s: float = 120) -> dict[str, Any]:
    """resolve_workspace_id(skill) then invoke_workspace(...)."""
    return invoke_workspace(resolve_workspace_id(skill), message,
                            session_id=session_id, user_id=user_id, timeout_s=timeout_s)
