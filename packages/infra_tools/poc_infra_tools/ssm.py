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
    import urllib.request, urllib.error
    deadline, last = time.time() + timeout_s, None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as r:
                if r.status == expect_status:
                    body = r.read(2000).decode(errors="ignore")
                    return {"ok": True, "status": r.status, "body": body}
                last = f"HTTP {r.status}"
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
        except Exception as e:
            last = str(e)
        time.sleep(interval_s)
    return {"ok": False, "status": None, "body": last or "no response"}


def publish_frontend_nginx(instance_id: str, poc_id: str, run_id: str, static_dir: str, api_path: str, upstream: str) -> dict[str, Any]:
    """Writes the nginx site (/ → static, /api → backend) and reloads. Same shape as fixtures/golden/nginx.conf."""
    conf = f"""server {{
  listen 80 default_server;
  root {static_dir};
  index index.html;
  location {api_path}/ {{ proxy_pass {upstream}{api_path}/; proxy_set_header Host $host; proxy_read_timeout 60s; }}
  location / {{ try_files $uri /index.html; }}
}}"""
    cmds = ["rm -f /etc/nginx/conf.d/default.conf", f"cat > /etc/nginx/conf.d/poc.conf <<'NGX'\n{conf}\nNGX",
            "sed -i 's/^\\(\\s*\\)server {/\\1server_disabled {/' /etc/nginx/nginx.conf || true",
            "nginx -t", "systemctl reload nginx", "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/"]
    return run_script(instance_id, poc_id, run_id, "publish_frontend", cmds, timeout_s=120)
