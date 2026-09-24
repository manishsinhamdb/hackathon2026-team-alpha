"""All configuration comes from the environment (§5.6). Nothing here is a secret value."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    s3_bucket: str
    aws_region: str
    platform_mongodb_uri: str
    platform_db: str
    secret_prefix: str
    resource_prefix: str
    seed_max_docs_default: int
    bundle_max_bytes: int

    @property
    def platform_uri_set(self) -> bool:
        return bool(self.platform_mongodb_uri)


def get_config() -> Config:
    return Config(
        s3_bucket=os.environ.get("POC_S3_BUCKET", "msinha-hackathon"),
        aws_region=os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")),
        platform_mongodb_uri=os.environ.get("POC_PLATFORM_MONGODB_URI", os.environ.get("MONGODB_URI", "")),
        platform_db=os.environ.get("POC_PLATFORM_DB", "poc_builder"),
        secret_prefix=os.environ.get("POC_SECRET_PREFIX", "msinha/poc-builder"),
        resource_prefix=os.environ.get("POC_RESOURCE_PREFIX", "msinha-"),
        seed_max_docs_default=int(os.environ.get("SEED_MAX_DOCS", "10000")),
        bundle_max_bytes=int(os.environ.get("POC_BUNDLE_MAX_BYTES", str(50 * 1024 * 1024))),
    )
