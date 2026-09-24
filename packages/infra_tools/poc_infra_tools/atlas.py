"""Atlas provisioning (§7.6) with four database modes:
  shared_db    — database + scoped user on the pre-created sandbox cluster (default)
  flex_cluster — one Atlas Flex cluster per POC
  local_ec2    — MongoDB Community on the POC instance (nothing to provision here)
  external_uri — user-supplied connection string (nothing to provision, nothing to drop)
Auth: Atlas service account (OAuth2 client credentials)."""
import secrets as pysecrets
import string
import time
from typing import Any
from urllib.parse import quote_plus

import requests
from pymongo import MongoClient

from .config import get_infra_config
from .errors import InfraError

ACCEPT = "application/vnd.atlas.2024-11-13+json"
_token: dict[str, Any] = {"value": None, "exp": 0}


def _cfg():
    c = get_infra_config()
    if not c.atlas_project_id:
        raise InfraError("ATLAS_NOT_CONFIGURED", "ATLAS_PROJECT_ID not set")
    return c


def _token_value() -> str:
    c = _cfg()
    if _token["value"] and time.time() < _token["exp"] - 60:
        return _token["value"]
    if not (c.atlas_client_id and c.atlas_client_secret):
        raise InfraError("ATLAS_NOT_CONFIGURED", "ATLAS_CLIENT_ID / ATLAS_CLIENT_SECRET not set")
    r = requests.post("https://cloud.mongodb.com/api/oauth/token", auth=(c.atlas_client_id, c.atlas_client_secret),
                      data={"grant_type": "client_credentials"}, headers={"Accept": "application/json"}, timeout=30)
    if r.status_code != 200:
        raise InfraError("ATLAS_AUTH_FAILED", f"token request failed: {r.status_code} {r.text[:200]}")
    j = r.json()
    _token.update(value=j["access_token"], exp=time.time() + int(j.get("expires_in", 3600)))
    return _token["value"]


def _req(method: str, path: str, **kw) -> requests.Response:
    c = _cfg()
    h = {"Accept": ACCEPT, "Content-Type": "application/json", "Authorization": f"Bearer {_token_value()}"}
    r = requests.request(method, f"{c.atlas_base}{path}", headers=h, timeout=60, **kw)
    if r.status_code == 429 or r.status_code >= 500:
        raise InfraError("ATLAS_RETRYABLE", f"{method} {path}: {r.status_code} {r.text[:200]}", retryable=True)
    if r.status_code >= 400 and r.status_code != 404:
        raise InfraError("ATLAS_API_ERROR", f"{method} {path}: {r.status_code} {r.text[:300]}")
    return r


def _password(n: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(pysecrets.choice(alphabet) for _ in range(n))


def _srv_uri(host: str, user: str, pwd: str, db: str) -> str:
    return f"mongodb+srv://{quote_plus(user)}:{quote_plus(pwd)}@{host}/{db}?retryWrites=true&w=majority"


def _short(poc_id: str) -> str:
    return poc_id[-10:].lower()


def create_db_user(username: str, password: str, database_name: str, cluster_name: str) -> None:
    c = _cfg()
    body = {"databaseName": "admin", "username": username, "password": password,
            "roles": [{"roleName": "readWrite", "databaseName": database_name}],
            "scopes": [{"name": cluster_name, "type": "CLUSTER"}]}
    r = _req("POST", f"/groups/{c.atlas_project_id}/databaseUsers", json=body)
    if r.status_code == 404:
        raise InfraError("ATLAS_API_ERROR", f"create user 404: {r.text[:200]}")


def delete_db_user(username: str) -> None:
    c = _cfg()
    _req("DELETE", f"/groups/{c.atlas_project_id}/databaseUsers/admin/{username}")


def allow_ip(ip: str, comment: str, delete_after: str | None = None) -> dict[str, Any]:
    c = _cfg()
    entry: dict[str, Any] = {"ipAddress": ip, "comment": comment[:80]}
    if delete_after: entry["deleteAfterDate"] = delete_after
    _req("POST", f"/groups/{c.atlas_project_id}/accessList", json=[entry])
    return entry


def revoke_ip(ip: str) -> None:
    c = _cfg()
    _req("DELETE", f"/groups/{c.atlas_project_id}/accessList/{quote_plus(ip)}")


def create_flex_cluster(name: str, poc_id: str, region: str = "AP_SOUTH_1", timeout_s: int = 900) -> str:
    c = _cfg()
    body = {"name": name, "providerSettings": {"backingProviderName": "AWS", "regionName": region},
            "terminationProtectionEnabled": False, "tags": [{"key": "poc_id", "value": poc_id}, {"key": "managed-by", "value": "poc-builder"}]}
    _req("POST", f"/groups/{c.atlas_project_id}/flexClusters", json=body)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = _req("GET", f"/groups/{c.atlas_project_id}/flexClusters/{name}")
        j = r.json()
        if j.get("stateName") == "IDLE":
            return j["connectionStrings"]["standardSrv"].replace("mongodb+srv://", "")
        time.sleep(15)
    raise InfraError("ATLAS_FLEX_TIMEOUT", f"flex cluster {name} not IDLE within {timeout_s}s")


def delete_flex_cluster(name: str, timeout_s: int = 600) -> None:
    c = _cfg()
    _req("DELETE", f"/groups/{c.atlas_project_id}/flexClusters/{name}")
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if _req("GET", f"/groups/{c.atlas_project_id}/flexClusters/{name}").status_code == 404:
            return
        time.sleep(15)


def provision_poc_database(poc_id: str, mode: str = "shared_db", external_uri: str | None = None,
                           region: str = "AP_SOUTH_1") -> dict[str, Any]:
    """Returns {mode, cluster_name, database_name, username, password, connection_string, resources[]}.
    The connection string is a secret: caller writes it to Secrets Manager and never logs it."""
    database_name = poc_id
    if mode == "external_uri":
        if not external_uri:
            raise InfraError("NO_EXTERNAL_URI", "external_uri mode needs a connection string")
        return {"mode": mode, "cluster_name": "external", "database_name": database_name, "username": "", "password": "",
                "connection_string": external_uri, "resources": []}
    if mode == "local_ec2":
        return {"mode": mode, "cluster_name": "local", "database_name": database_name, "username": "", "password": "",
                "connection_string": f"mongodb://127.0.0.1:27017/{database_name}", "resources": []}
    c = _cfg()
    username, password = f"poc_{_short(poc_id)}", _password()
    if mode == "shared_db":
        if not c.atlas_sandbox_srv_host:
            raise InfraError("ATLAS_NOT_CONFIGURED", "ATLAS_SANDBOX_SRV_HOST not set")
        cluster, host = c.atlas_sandbox_cluster, c.atlas_sandbox_srv_host
        resources = [{"resource_id": username, "type": "atlas_db_user"}]
    elif mode == "flex_cluster":
        cluster = f"{c.resource_prefix}poc-{_short(poc_id)}"[:64]
        host = create_flex_cluster(cluster, poc_id, region)
        resources = [{"resource_id": cluster, "type": "atlas_flex_cluster"}, {"resource_id": username, "type": "atlas_db_user"}]
    else:
        raise InfraError("BAD_DB_MODE", f"unknown mode {mode}")
    create_db_user(username, password, database_name, cluster)
    time.sleep(15)  # user propagation (§7.6)
    return {"mode": mode, "cluster_name": cluster, "database_name": database_name, "username": username, "password": password,
            "connection_string": _srv_uri(host, username, password, database_name), "resources": resources}


def drop_poc_database(poc_id: str, mode: str, cluster_name: str, username: str | None) -> None:
    """Teardown (§6.7): shared_db → drop database + delete user; flex → delete cluster; local/external → nothing."""
    if mode in ("local_ec2", "external_uri"):
        return
    c = _cfg()
    if mode == "shared_db":
        if not c.atlas_sandbox_admin_uri:
            raise InfraError("ATLAS_NOT_CONFIGURED", "ATLAS_SANDBOX_ADMIN_URI not set")
        MongoClient(c.atlas_sandbox_admin_uri, serverSelectionTimeoutMS=15000).drop_database(poc_id)
        if username: delete_db_user(username)
    elif mode == "flex_cluster":
        delete_flex_cluster(cluster_name)
