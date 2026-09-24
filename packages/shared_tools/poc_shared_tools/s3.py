"""S3 tools (§7.1). Every key MUST start with pocs/{poc_id}/ — enforced here, not by IAM alone."""
import hashlib
import io
import os
import re
import tarfile
from typing import Any

import boto3
from botocore.exceptions import ClientError

from .config import get_config
from .errors import ToolError

_KEY_RE = re.compile(r"^pocs/(poc_[0-9A-HJKMNP-TV-Z]{26})/")
_DELETABLE = re.compile(r"^pocs/poc_[0-9A-HJKMNP-TV-Z]{26}/(deploy|test)/")


def _client():
    return boto3.client("s3", region_name=get_config().aws_region)


def _bucket() -> str:
    return get_config().s3_bucket


def assert_key(key: str, poc_id: str | None = None) -> str:
    m = _KEY_RE.match(key)
    if not m:
        raise ToolError("BAD_S3_KEY", f"key must start with pocs/{{poc_id}}/: {key}")
    if poc_id and m.group(1) != poc_id:
        raise ToolError("BAD_S3_KEY", f"key {key} does not belong to {poc_id}")
    return key


def _wrap(e: ClientError, op: str) -> ToolError:
    code = e.response.get("Error", {}).get("Code", "S3_ERROR")
    retryable = code in {"SlowDown", "Throttling", "RequestTimeout", "InternalError", "ServiceUnavailable"}
    return ToolError(f"S3_{code.upper()}", f"{op} failed: {e}", retryable)


def put_object(poc_id: str, run_id: str, key: str, body: bytes | str, content_type: str, producer: str) -> dict[str, Any]:
    assert_key(key, poc_id)
    data = body.encode() if isinstance(body, str) else body
    sha = hashlib.sha256(data).hexdigest()
    try:
        r = _client().put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=content_type,
                                 ServerSideEncryption="AES256",
                                 Metadata={"poc_id": poc_id, "run_id": run_id, "producer": producer, "content_sha256": sha})
    except ClientError as e:
        raise _wrap(e, "put_object")
    return {"key": key, "version_id": r.get("VersionId"), "sha256": sha, "size": len(data)}


def get_object(key: str, version_id: str | None = None) -> dict[str, Any]:
    assert_key(key)
    kw = {"Bucket": _bucket(), "Key": key}
    if version_id: kw["VersionId"] = version_id
    try:
        r = _client().get_object(**kw)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
            raise ToolError("S3_NOT_FOUND", f"no object at {key}")
        raise _wrap(e, "get_object")
    return {"body": r["Body"].read(), "metadata": r.get("Metadata", {}), "version_id": r.get("VersionId"),
            "content_type": r.get("ContentType")}


def get_text(key: str) -> str:
    return get_object(key)["body"].decode()


def head_object(key: str) -> dict[str, Any] | None:
    assert_key(key)
    try:
        r = _client().head_object(Bucket=_bucket(), Key=key)
    except ClientError as e:
        if e.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return None
        raise _wrap(e, "head_object")
    return {"metadata": r.get("Metadata", {}), "size": r["ContentLength"], "version_id": r.get("VersionId")}


def list_prefix(prefix: str, max_keys: int = 1000) -> list[dict[str, Any]]:
    assert_key(prefix)
    out, token = [], None
    try:
        while len(out) < max_keys:
            kw = {"Bucket": _bucket(), "Prefix": prefix, "MaxKeys": min(1000, max_keys - len(out))}
            if token: kw["ContinuationToken"] = token
            r = _client().list_objects_v2(**kw)
            out += [{"key": o["Key"], "size": o["Size"], "last_modified": o["LastModified"].isoformat()} for o in r.get("Contents", [])]
            if not r.get("IsTruncated"): break
            token = r["NextContinuationToken"]
    except ClientError as e:
        raise _wrap(e, "list_prefix")
    return out


def delete_object(key: str) -> None:
    assert_key(key)
    if not _DELETABLE.match(key):
        raise ToolError("S3_DELETE_FORBIDDEN", "delete is only allowed under deploy/ and test/ prefixes")
    try:
        _client().delete_object(Bucket=_bucket(), Key=key)
    except ClientError as e:
        raise _wrap(e, "delete_object")


def copy_object(src_key: str, dst_key: str) -> None:
    assert_key(src_key); assert_key(dst_key)
    try:
        _client().copy_object(Bucket=_bucket(), CopySource={"Bucket": _bucket(), "Key": src_key}, Key=dst_key,
                              ServerSideEncryption="AES256", MetadataDirective="COPY")
    except ClientError as e:
        raise _wrap(e, "copy_object")


def presign_get(key: str, expires: int = 900) -> str:
    assert_key(key)
    return _client().generate_presigned_url("get_object", Params={"Bucket": _bucket(), "Key": key}, ExpiresIn=expires)


def next_version(poc_id: str, kind: str) -> str:
    """Next vNNN under pocs/{poc_id}/{spec|code}/ — single-writer per POC by design (§7.1)."""
    if kind not in ("spec", "code"):
        raise ToolError("BAD_KIND", "kind must be spec or code")
    prefix = f"pocs/{poc_id}/{kind}/"
    try:
        r = _client().list_objects_v2(Bucket=_bucket(), Prefix=prefix, Delimiter="/")
    except ClientError as e:
        raise _wrap(e, "next_version")
    versions = sorted(p["Prefix"][len(prefix):-1] for p in r.get("CommonPrefixes", []) if re.fullmatch(r"v\d{3}", p["Prefix"][len(prefix):-1]))
    return "v001" if not versions else f"v{int(versions[-1][1:]) + 1:03d}"


def upload_dir(poc_id: str, run_id: str, local_dir: str, prefix: str, producer: str) -> list[str]:
    assert_key(prefix, poc_id)
    keys = []
    for root, _, files in os.walk(local_dir):
        for f in files:
            if f in (".DS_Store",) or "node_modules" in root: continue
            path = os.path.join(root, f)
            rel = os.path.relpath(path, local_dir).replace(os.sep, "/")
            with open(path, "rb") as fh:
                put_object(poc_id, run_id, prefix.rstrip("/") + "/" + rel, fh.read(), "application/octet-stream", producer)
            keys.append(prefix.rstrip("/") + "/" + rel)
    return keys


def download_dir(prefix: str, local_dir: str) -> list[str]:
    assert_key(prefix)
    paths = []
    for obj in list_prefix(prefix):
        rel = obj["key"][len(prefix.rstrip("/")) + 1:]
        if not rel: continue
        dest = os.path.join(local_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as fh:
            fh.write(get_object(obj["key"])["body"])
        paths.append(dest)
    return paths


def make_bundle(poc_id: str, run_id: str, code_version: str, local_code_dir: str, producer: str) -> dict[str, Any]:
    """tar.gz the whole code/vNNN folder and upload it as bundle.tar.gz; returns key + sha256 (§5.4)."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in sorted(os.listdir(local_code_dir)):
            if name in ("bundle.tar.gz", "node_modules"): continue
            tar.add(os.path.join(local_code_dir, name), arcname=name,
                    filter=lambda ti: None if "node_modules" in ti.name or ti.name.endswith(".DS_Store") else ti)
    data = buf.getvalue()
    if len(data) > get_config().bundle_max_bytes:
        raise ToolError("BUNDLE_TOO_LARGE", f"bundle is {len(data)} bytes, cap is {get_config().bundle_max_bytes}")
    key = f"pocs/{poc_id}/code/{code_version}/bundle.tar.gz"
    r = put_object(poc_id, run_id, key, data, "application/gzip", producer)
    return {"bundle_key": key, "bundle_sha256": r["sha256"], "size": len(data)}
