"""EC2 tools (§7.4). Every instance is tagged managed-by=poc-builder, poc_id, run_id, ttl_expires_at."""
import base64
from datetime import datetime, timedelta, timezone
from typing import Any

import boto3
from botocore.exceptions import ClientError, WaiterError

from .bootstrap import render_user_data
from .config import get_infra_config
from .errors import InfraError

MANAGED_TAG = {"Key": "managed-by", "Value": "poc-builder"}


def _ec2():
    return boto3.client("ec2", region_name=get_infra_config().region)


def _ssm():
    return boto3.client("ssm", region_name=get_infra_config().region)


def _wrap(e: ClientError, op: str) -> InfraError:
    code = e.response.get("Error", {}).get("Code", "EC2_ERROR")
    return InfraError(f"EC2_{code.upper()}", f"{op}: {e}", retryable=code in ("RequestLimitExceeded", "InsufficientInstanceCapacity", "InternalError"))


def resolve_ami() -> str:
    try:
        return _ssm().get_parameter(Name=get_infra_config().ami_param)["Parameter"]["Value"]
    except ClientError as e:
        raise _wrap(e, "resolve_ami")


def security_group_id() -> str:
    cfg = get_infra_config()
    r = _ec2().describe_security_groups(Filters=[{"Name": "group-name", "Values": [cfg.sg_name]}, {"Name": "vpc-id", "Values": [cfg.vpc_id]}])
    if not r["SecurityGroups"]:
        raise InfraError("SG_MISSING", f"security group {cfg.sg_name} not found in {cfg.vpc_id}; run scripts/aws_bootstrap.sh")
    return r["SecurityGroups"][0]["GroupId"]


def ttl_from_hours(hours: int | None) -> str:
    cfg = get_infra_config()
    h = cfg.ttl_default_hours if hours is None else max(1, min(int(hours), cfg.ttl_max_hours))
    return (datetime.now(timezone.utc) + timedelta(hours=h)).isoformat(timespec="seconds").replace("+00:00", "Z")


def launch_poc_instance(poc_id: str, run_id: str, secret_arn: str, db_mode: str = "shared_db",
                        instance_type: str | None = None, ttl_expires_at: str | None = None,
                        wait_for_ssm: bool = True, ssm_timeout_s: int = 600) -> dict[str, Any]:
    """run_instances → wait running → public IP → wait status ok → wait SSM PingStatus Online."""
    cfg = get_infra_config()
    itype = instance_type or cfg.default_instance_type
    if itype not in cfg.allowed_instance_types:
        raise InfraError("BAD_INSTANCE_TYPE", f"{itype} not in {cfg.allowed_instance_types}")
    if not cfg.subnet_id:
        raise InfraError("NO_SUBNET", "POC_SUBNET_ID not set")
    ttl = ttl_expires_at or ttl_from_hours(None)
    user_data = render_user_data(secret_arn, cfg.region, db_mode)
    tags = [MANAGED_TAG, {"Key": "poc_id", "Value": poc_id}, {"Key": "run_id", "Value": run_id},
            {"Key": "ttl_expires_at", "Value": ttl}, {"Key": "Name", "Value": f"{cfg.resource_prefix}poc-{poc_id[-8:]}"}]
    try:
        r = _ec2().run_instances(
            ImageId=resolve_ami(), InstanceType=itype, MinCount=1, MaxCount=1,
            SubnetId=cfg.subnet_id, SecurityGroupIds=[security_group_id()],
            IamInstanceProfile={"Name": cfg.instance_profile},
            UserData=user_data, ClientToken=f"{poc_id}-{run_id}"[:64],
            MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled"},
            InstanceInitiatedShutdownBehavior="terminate",
            BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 20, "VolumeType": "gp3", "DeleteOnTermination": True}}],
            TagSpecifications=[{"ResourceType": "instance", "Tags": tags}, {"ResourceType": "volume", "Tags": [MANAGED_TAG, {"Key": "poc_id", "Value": poc_id}]}],
        )
    except ClientError as e:
        raise _wrap(e, "run_instances")
    iid = r["Instances"][0]["InstanceId"]
    try:
        _ec2().get_waiter("instance_running").wait(InstanceIds=[iid], WaiterConfig={"Delay": 5, "MaxAttempts": 60})
        d = describe_instance(iid)
        _ec2().get_waiter("instance_status_ok").wait(InstanceIds=[iid], WaiterConfig={"Delay": 10, "MaxAttempts": 60})
    except WaiterError as e:
        raise InfraError("EC2_WAIT_TIMEOUT", f"instance {iid} did not become ready: {e}", retryable=False)
    if wait_for_ssm:
        wait_for_ssm_online(iid, ssm_timeout_s)
    return {"instance_id": iid, "public_ip": d["public_ip"], "public_dns": d["public_dns"], "instance_type": itype,
            "ttl_expires_at": ttl, "region": cfg.region}


def wait_for_ssm_online(instance_id: str, timeout_s: int = 600) -> None:
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        r = _ssm().describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [instance_id]}])
        info = r.get("InstanceInformationList", [])
        if info and info[0].get("PingStatus") == "Online":
            return
        time.sleep(10)
    raise InfraError("SSM_NOT_ONLINE", f"{instance_id} SSM agent not Online within {timeout_s}s")


def describe_instance(instance_id: str) -> dict[str, Any]:
    try:
        r = _ec2().describe_instances(InstanceIds=[instance_id])
    except ClientError as e:
        raise _wrap(e, "describe_instances")
    inst = r["Reservations"][0]["Instances"][0]
    return {"instance_id": instance_id, "state": inst["State"]["Name"], "public_ip": inst.get("PublicIpAddress"),
            "public_dns": inst.get("PublicDnsName"), "instance_type": inst["InstanceType"],
            "tags": {t["Key"]: t["Value"] for t in inst.get("Tags", [])}}


def terminate_instance(instance_id: str, wait: bool = True) -> None:
    """Refuses to terminate anything not tagged managed-by=poc-builder (belt and braces on a shared account)."""
    d = describe_instance(instance_id)
    if d["tags"].get("managed-by") != "poc-builder":
        raise InfraError("NOT_MANAGED", f"{instance_id} is not a poc-builder instance; refusing to terminate")
    try:
        _ec2().terminate_instances(InstanceIds=[instance_id])
        if wait:
            _ec2().get_waiter("instance_terminated").wait(InstanceIds=[instance_id], WaiterConfig={"Delay": 10, "MaxAttempts": 30})
    except ClientError as e:
        raise _wrap(e, "terminate_instances")


def tag_instance(instance_id: str, tags: dict[str, str]) -> None:
    _ec2().create_tags(Resources=[instance_id], Tags=[{"Key": k, "Value": v} for k, v in tags.items()])


def list_managed_instances(poc_id: str | None = None) -> list[dict[str, Any]]:
    filters = [{"Name": "tag:managed-by", "Values": ["poc-builder"]}, {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}]
    if poc_id:
        filters.append({"Name": "tag:poc_id", "Values": [poc_id]})
    out = []
    for res in _ec2().describe_instances(Filters=filters)["Reservations"]:
        for i in res["Instances"]:
            out.append({"instance_id": i["InstanceId"], "state": i["State"]["Name"], "tags": {t["Key"]: t["Value"] for t in i.get("Tags", [])}})
    return out
