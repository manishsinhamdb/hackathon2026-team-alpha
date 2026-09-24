"""Secrets tools (§7.3). POC DB credentials live in AWS Secrets Manager under {prefix}/{poc_id}/db."""
import json
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .config import get_config
from .errors import ToolError


def _client():
    return boto3.client("secretsmanager", region_name=get_config().aws_region)


def secret_name(poc_id: str) -> str:
    return f"{get_config().secret_prefix}/{poc_id}/db"


def put_poc_secret(poc_id: str, payload: dict[str, str]) -> str:
    for k in ("MONGODB_URI", "DB_USER", "DB_PASSWORD"):
        if k not in payload:
            raise ToolError("BAD_SECRET_PAYLOAD", f"missing {k}")
    name, body = secret_name(poc_id), json.dumps(payload)
    tags = [{"Key": "poc_id", "Value": poc_id}, {"Key": "managed-by", "Value": "poc-builder"}]
    try:
        try:
            r = _client().create_secret(Name=name, SecretString=body, Tags=tags)
            return r["ARN"]
        except ClientError as e:
            if e.response["Error"]["Code"] != "ResourceExistsException":
                raise
            r = _client().put_secret_value(SecretId=name, SecretString=body)
            return r["ARN"]
    except ClientError as e:
        raise ToolError("SECRETS_ERROR", str(e), retryable=e.response["Error"]["Code"] in ("ThrottlingException", "InternalServiceError"))


def get_poc_secret(poc_id: str) -> dict[str, Any]:
    """Platform-side use only; never pass the result into an LLM prompt."""
    try:
        r = _client().get_secret_value(SecretId=secret_name(poc_id))
    except ClientError as e:
        if e.response["Error"]["Code"] == "ResourceNotFoundException":
            raise ToolError("SECRET_NOT_FOUND", f"no secret for {poc_id}")
        raise ToolError("SECRETS_ERROR", str(e))
    return json.loads(r["SecretString"])


def get_secret_arn(poc_id: str) -> str:
    try:
        return _client().describe_secret(SecretId=secret_name(poc_id))["ARN"]
    except ClientError as e:
        raise ToolError("SECRET_NOT_FOUND", str(e))


def delete_poc_secret(poc_id: str, recovery_days: int = 7) -> None:
    try:
        _client().delete_secret(SecretId=secret_name(poc_id), RecoveryWindowInDays=recovery_days)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise ToolError("SECRETS_ERROR", str(e))
