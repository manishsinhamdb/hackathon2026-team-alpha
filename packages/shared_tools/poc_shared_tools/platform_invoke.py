"""Top-level platform invocations for cross-agent calls that can outlive an A2A token or the calling turn.

Why this exists (platform execution model, confirmed live 2026-09-25):
  - An A2A invocation is a CHILD EXECUTION of the calling turn/session. When the turn completes (or the
    session is reclaimed) the platform CANCELS any live child A2A execution, and a single A2A call is capped
    at 300 s. So a stage started as an A2A child of a fast-acking chat turn is killed mid-flight in the cloud.
  - The OE A2A bearer token has a ~5-minute lifetime and is NOT refreshable from app code. Any A2A call
    chain that runs longer than that 401s on later calls (e.g. the last coder of a ~6-min code run).

The fix, for anything that can outlive the turn or the ~5-min A2A token: start/call the target with a
TOP-LEVEL invocation of its workspace through the platform invoke API — the same call an external client
makes. That run is its OWN root session, with its own freshly-minted token, independent of the caller.
Short in-turn hops (chat -> draft) stay on A2A.

This module is shared (poc_shared_tools) so any agent can use it, and dependency-free (stdlib urllib) so it
adds nothing to the build. It:
  - exchanges the service-account client id/secret (project secrets POC_PLATFORM_SA_CLIENT_ID /
    POC_PLATFORM_SA_CLIENT_SECRET, injected into the pod env) for a short-lived access token, cached for its
    lifetime and refreshed on 401;
  - resolves a target workspace id by skill via the platform workspaces API (A2A discovery only exposes the
    A2A *app id*, not the ws- id), with an optional POC_WORKSPACE_IDS env override — never a hard-coded id;
  - fires POST .../workspaces/<id>/invoke with the AgentEnvelope as the `message`, plus session_id/user_id;
  - offers `invoke_envelope(skill, envelope, ...)`, a SYNCHRONOUS helper for a long-lived caller that blocks
    for the callee's reply and returns the callee's AgentEnvelope normalised the same way A2A's
    `_unwrap_envelope` does (the platform reply's `response` field carries the callee's envelope).
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
# Names, not ids, so nothing environment-specific is baked in — the id is looked up by name at runtime.
_SKILL_WS_NAME = {
    "code-orchestration": "coding-orchestrator",
    "deploy-operations": "deploy-agent",
    "e2e-tests": "test-agent",
    "generate-api": "api-agent",
    "generate-seed": "data-seeding-agent",
    "generate-frontend": "frontend-agent",
    "draft-spec": "draft-agent",
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
    the A2A path passes it. Refreshes the token once on 401.

    For a fire-and-forget start (chat) a SHORT timeout_s is used: the run keeps executing server-side after
    we disconnect and the caller polls the runs document. For a synchronous caller (invoke_envelope) a large
    timeout_s blocks for the whole callee run."""
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


def invoke_envelope(skill: str, envelope: dict[str, Any], *, user_id: str, session_id: str,
                    timeout_s: float = 900) -> dict[str, Any]:
    """SYNCHRONOUS top-level invoke of a callee workspace by skill: block for the callee's reply and return it
    normalised to {"response": <AgentEnvelope>} — the same shape the A2A path's `_unwrap_envelope` yields, so
    a caller migrating off A2A keeps `...["response"]` unchanged.

    Use this for a long-lived caller (orchestrator -> coders, deploy -> test/repair) whose own A2A token
    would expire before the callee replied: the callee runs in its OWN root session with a fresh token. The
    default timeout_s is generous (15 min) because the callee (a coder ~35-70 s; a test run a few minutes)
    runs entirely within this call."""
    raw = invoke_by_skill(skill, json.dumps(envelope), session_id=session_id, user_id=user_id,
                          timeout_s=timeout_s)
    return _unwrap_platform_response(raw)


# --- envelope unwrapping (self-contained copy of the a2a.py helper) ----------
# The platform invoke reply carries the callee's final message under `response`, which — like an A2A reply —
# the OE may itself have wrapped (e.g. {"result": "<envelope json>", "status": ...}). Normalise any of these
# to a dict carrying the AgentEnvelope under "response". Kept here (rather than imported from an agent's
# a2a.py) so the shared package has no dependency on agent code.

def _unwrap_platform_response(raw: Any) -> dict[str, Any]:
    inner = raw.get("response") if isinstance(raw, dict) else raw
    if isinstance(inner, str):
        try:
            inner = json.loads(_extract_json(inner))
        except (json.JSONDecodeError, ValueError):
            return {"response": {"status": "failed",
                                 "error": {"code": "INVOKE_UNPARSEABLE", "message": str(inner)[:500]}}}
    return _unwrap_envelope(inner)


def _unwrap_envelope(obj: Any) -> dict[str, Any]:
    if not isinstance(obj, dict):
        return {"response": {"status": "failed", "error": {"code": "A2A_BAD_REPLY", "message": str(obj)[:300]}}}
    if "response" in obj:
        return obj
    res = obj.get("result")
    if isinstance(res, str):
        try:
            inner = json.loads(_extract_json(res))
            if isinstance(inner, dict) and "response" in inner:
                return inner
            if isinstance(inner, dict) and ("status" in inner or "task_id" in inner):
                return {"response": inner}
        except (json.JSONDecodeError, ValueError):
            pass
    if isinstance(res, dict) and "response" in res:
        return res
    if isinstance(res, dict) and ("status" in res or "task_id" in res):
        return {"response": res}
    if "status" in obj or "task_id" in obj:  # a bare response envelope
        return {"response": obj}
    return {"response": {"status": "failed", "error": {"code": "A2A_NO_RESPONSE", "message": json.dumps(obj)[:300]}}}


def _extract_json(text: str) -> str:
    text = text.strip()
    if text.startswith("{"):
        return text
    i, j = text.find("{"), text.rfind("}")
    if i >= 0 and j > i:
        return text[i:j + 1]
    raise ValueError("no json")
