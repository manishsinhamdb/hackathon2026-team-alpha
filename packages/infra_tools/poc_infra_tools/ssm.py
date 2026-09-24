"""Script execution via SSM Run Command (§7.5). Output lands in S3 under deploy/{run_id}/logs/{step}/ and CloudWatch."""
import json
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from poc_shared_tools import s3 as s3t
from poc_shared_tools.config import get_config

from .config import get_infra_config
from .errors import InfraError

TERMINAL = {"Success", "Failed", "TimedOut", "Cancelled"}


def _ssm():
    return boto3.client("ssm", region_name=get_infra_config().region)


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\"'\"'") + "'"


def run_script(instance_id: str, poc_id: str, run_id: str, step: str, commands: list[str],
               env: dict[str, str] | None = None, timeout_s: int = 1800, workdir: str = "/opt/poc",
               poll_s: int = 5) -> dict[str, Any]:
    """Runs `commands` as one shell script on the instance; returns exit code, first 24k of stdout/stderr and the S3 log keys."""
    bucket = get_config().s3_bucket
    log_prefix = f"pocs/{poc_id}/deploy/{run_id}/logs/{step}"
    exports = [f"export {k}={_sh_quote(v)}" for k, v in (env or {}).items()]
    script = [f"mkdir -p {workdir}", f"cd {workdir}", "set -o pipefail", "source /etc/poc/env 2>/dev/null || true", *exports, *commands]
    try:
        r = _ssm().send_command(
            InstanceIds=[instance_id], DocumentName="AWS-RunShellScript",
            Parameters={"commands": script, "executionTimeout": [str(timeout_s)]},
            TimeoutSeconds=600,
            OutputS3BucketName=bucket, OutputS3KeyPrefix=log_prefix,
            CloudWatchOutputConfig={"CloudWatchLogGroupName": f"/poc-builder/{poc_id}", "CloudWatchOutputEnabled": True},
            Comment=f"{poc_id} {run_id} {step}"[:100],
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        raise InfraError(f"SSM_{code.upper()}", f"send_command: {e}", retryable=code in ("ThrottlingException", "InternalServerError"))
    cid = r["Command"]["CommandId"]
    t0 = time.time()
    time.sleep(2)
    while True:
        try:
            inv = _ssm().get_command_invocation(CommandId=cid, InstanceId=instance_id)
        except _ssm().exceptions.InvocationDoesNotExist:
            time.sleep(poll_s); continue
        status = inv["Status"]
        if status in TERMINAL:
            break
        if time.time() - t0 > timeout_s + 120:
            _ssm().cancel_command(CommandId=cid)
            raise InfraError("SSM_TIMEOUT", f"{step} exceeded {timeout_s}s")
        time.sleep(poll_s)
    duration_ms = int((time.time() - t0) * 1000)
    out = {
        "status": status, "exit_code": inv.get("ResponseCode", -1), "command_id": cid,
        "stdout": inv.get("StandardOutputContent", ""), "stderr": inv.get("StandardErrorContent", ""),
        "stdout_key": f"{log_prefix}/{cid}/{instance_id}/awsrunShellScript/0.awsrunShellScript/stdout",
        "stderr_key": f"{log_prefix}/{cid}/{instance_id}/awsrunShellScript/0.awsrunShellScript/stderr",
        "duration_ms": duration_ms,
    }
    # Also write a flat, predictable copy for the UI and FailureReport (§5.4 logs/{step}.stdout.txt)
    try:
        s3t.put_object(poc_id, run_id, f"pocs/{poc_id}/deploy/{run_id}/logs/{step}.stdout.txt", out["stdout"] + ("\n--- stderr ---\n" + out["stderr"] if out["stderr"] else ""), "text/plain", "deploy_agent")
        out["log_key"] = f"pocs/{poc_id}/deploy/{run_id}/logs/{step}.stdout.txt"
    except Exception:
        out["log_key"] = out["stdout_key"]
    return out


def run_manifest_entry(instance_id: str, poc_id: str, run_id: str, component: str, entry: str,
                       manifest: dict[str, Any], env: dict[str, str] | None = None, timeout_s: int = 1800) -> dict[str, Any]:
    """Runs one entrypoint from a component.manifest.json inside /opt/poc/{workdir}.
    `start` installs a pm2 process instead of blocking; `healthcheck` is done platform-side with http_healthcheck."""
    entries = manifest.get("entrypoints", {})
    if entry not in entries:
        raise InfraError("NO_ENTRYPOINT", f"{component} manifest has no '{entry}' entrypoint")
    if entry == "healthcheck":
        raise InfraError("USE_HTTP_HEALTHCHECK", "healthcheck is run platform-side")
    workdir = f"/opt/poc/{manifest.get('workdir', component)}"
    cmds = list(entries[entry])
    if entry == "start":
        start_cmd = " && ".join(cmds)
        cmds = [f"pm2 delete poc-{component} >/dev/null 2>&1 || true",
                f"pm2 start bash --name poc-{component} -- -lc {_sh_quote(f'source /etc/poc/env; cd {workdir}; {start_cmd}')}",
                "pm2 save >/dev/null 2>&1 || true", "sleep 3", f"pm2 describe poc-{component} | head -20"]
    step = f"{entry}_{component}"
    return run_script(instance_id, poc_id, run_id, step, cmds, env=env, timeout_s=timeout_s, workdir=workdir)


def http_healthcheck(url: str, expect_status: int = 200, timeout_s: int = 60, interval_s: int = 5) -> dict[str, Any]:
    """Best-effort HTTP GET from wherever this code runs. In the platform Tool Pod this goes through
    the egress proxy (HTTP(S)_PROXY), which refuses raw-IP hosts with a proxy-generated 403 — so callers
    that need the site's *real* public status must use public_healthcheck_via_ssm and treat this as advisory.
    Returns ok/status/body plus the response headers and a `proxy_denied` hint for that case."""
    import urllib.request, urllib.error
    deadline, last = time.time() + timeout_s, None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                body = r.read(2000).decode(errors="ignore")
                if r.status == expect_status:
                    return {"ok": True, "status": r.status, "body": body, "headers": dict(r.headers), "proxy_denied": False}
                last = {"status": r.status, "body": body, "headers": dict(r.headers)}
        except urllib.error.HTTPError as e:
            last = {"status": e.code, "body": e.read(2000).decode(errors="ignore"), "headers": dict(e.headers)}
        except Exception as e:
            last = {"status": None, "body": str(e), "headers": {}}
        time.sleep(interval_s)
    res = last if isinstance(last, dict) else {"status": None, "body": "no response", "headers": {}}
    return {"ok": False, **res, "proxy_denied": _looks_like_proxy_denial(res)}


def _looks_like_proxy_denial(resp: dict[str, Any]) -> bool:
    """True when a non-2xx response looks like it was generated by an egress proxy rather than the app.
    The dev-stack proxy answers a raw-IP host with a plain-text 'egress denied' 403 and carries proxy
    headers (Via/Proxy-Agent/X-Forwarded-*); the real app health endpoint returns a JSON body."""
    headers = {k.lower(): v for k, v in (resp.get("headers") or {}).items()}
    if any(h in headers for h in ("via", "proxy-agent", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto")):
        return True
    body = (resp.get("body") or "").strip()
    if "egress denied" in body.lower() or "proxy" in body.lower():
        return True
    ct = headers.get("content-type", "")
    # real health payloads are JSON; a proxy denial is text/plain
    return bool(body) and not body.startswith("{") and "json" not in ct.lower()


def public_healthcheck_via_ssm(instance_id: str, poc_id: str, run_id: str, public_ip: str,
                               api_path: str = "/api", expect_status: int = 200,
                               retries: int = 12, interval_s: int = 5, timeout_s: int = 120) -> dict[str, Any]:
    """Checks public reachability FROM the instance itself: it curls its own public IP, which an EC2
    box reaches back through the Internet Gateway, so the result is nginx exactly as the internet sees
    it. This deliberately avoids the platform Tool Pod's egress path (its proxy refuses raw-IP hosts
    with a 403), which is not on the request path a real user takes. Returns per-URL status + body head."""
    health_url = f"http://{public_ip}{api_path}/health"
    root_url = f"http://{public_ip}/"
    cmds = [
        f"health_url={_sh_quote(health_url)}; root_url={_sh_quote(root_url)}; hc=000",
        f'for i in $(seq 1 {retries}); do hc=$(curl -s -o /tmp/hbody -w "%{{http_code}}" --max-time 10 "$health_url"); '
        f'[ "$hc" = "{expect_status}" ] && break; sleep {interval_s}; done',
        'rc=$(curl -s -o /tmp/rbody -w "%{http_code}" --max-time 10 "$root_url")',
        'echo "HEALTH_STATUS=$hc"',
        'echo "ROOT_STATUS=$rc"',
        'echo "HEALTH_BODY=$(head -c 300 /tmp/hbody | tr "\\n\\r" "  ")"',
        'echo "ROOT_BODY=$(head -c 120 /tmp/rbody | tr "\\n\\r" "  ")"',
    ]
    r = run_script(instance_id, poc_id, run_id, "publish_healthcheck", cmds, timeout_s=timeout_s)

    def _grab(key: str) -> str:
        for ln in r["stdout"].splitlines():
            if ln.startswith(key + "="):
                return ln[len(key) + 1:].strip()
        return ""

    health = {"url": health_url, "status": _grab("HEALTH_STATUS"), "body": _grab("HEALTH_BODY")}
    root = {"url": root_url, "status": _grab("ROOT_STATUS"), "body": _grab("ROOT_BODY")}
    ok = r["exit_code"] == 0 and health["status"] == str(expect_status) and root["status"] == str(expect_status)
    return {"ok": ok, "health": health, "root": root, "log_key": r.get("log_key"), "command_id": r.get("command_id")}


def publish_frontend_nginx(instance_id: str, poc_id: str, run_id: str, static_dir: str, api_path: str, upstream: str) -> dict[str, Any]:
    """Writes a complete minimal nginx.conf (no stock default server) plus the POC site: / -> static, /api -> backend."""
    main_conf = """user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log notice;
pid /run/nginx.pid;
events { worker_connections 1024; }
http {
  include /etc/nginx/mime.types;
  default_type application/octet-stream;
  log_format main '$remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"';
  access_log /var/log/nginx/access.log main;
  sendfile on;
  keepalive_timeout 65;
  include /etc/nginx/conf.d/*.conf;
}"""
    site = f"""server {{
  listen 80 default_server;
  server_name _;
  root {static_dir};
  index index.html;
  location {api_path}/ {{ proxy_pass {upstream}{api_path}/; proxy_set_header Host $host; proxy_set_header X-Forwarded-For $remote_addr; proxy_read_timeout 60s; }}
  location / {{ try_files $uri /index.html; }}
}}"""
    cmds = [f"cat > /etc/nginx/nginx.conf <<'NGXMAIN'\n{main_conf}\nNGXMAIN",
            "rm -f /etc/nginx/conf.d/default.conf",
            f"cat > /etc/nginx/conf.d/poc.conf <<'NGX'\n{site}\nNGX",
            "nginx -t", "systemctl restart nginx", "sleep 1",
            "curl -s -o /dev/null -w 'local / -> %{http_code}\\n' http://127.0.0.1/",
            f"curl -s -o /dev/null -w 'local {api_path}/health -> %{{http_code}}\\n' http://127.0.0.1{api_path}/health"]
    return run_script(instance_id, poc_id, run_id, "publish_frontend", cmds, timeout_s=120)
