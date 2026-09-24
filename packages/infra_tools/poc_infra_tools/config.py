import os
from dataclasses import dataclass


@dataclass(frozen=True)
class InfraConfig:
    region: str
    subnet_id: str
    vpc_id: str
    sg_name: str
    instance_profile: str
    ami_param: str
    allowed_instance_types: tuple[str, ...]
    default_instance_type: str
    ttl_default_hours: int
    ttl_max_hours: int
    atlas_base: str
    atlas_project_id: str
    atlas_sandbox_cluster: str
    atlas_sandbox_srv_host: str
    atlas_sandbox_admin_uri: str
    atlas_client_id: str
    atlas_client_secret: str
    resource_prefix: str


def get_infra_config() -> InfraConfig:
    e = os.environ.get
    px = e("POC_RESOURCE_PREFIX", "msinha-")
    return InfraConfig(
        region=e("AWS_REGION", "ap-south-1"),
        subnet_id=e("POC_SUBNET_ID", ""),
        vpc_id=e("POC_VPC_ID", ""),
        sg_name=e("POC_SG_NAME", f"{px}poc-instance-sg"),
        instance_profile=e("POC_INSTANCE_PROFILE", f"{px}poc-instance-role"),
        ami_param=e("POC_AMI_PARAM", "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"),
        allowed_instance_types=("t3.small", "t3.medium", "t3.large"),
        default_instance_type=e("POC_INSTANCE_TYPE", "t3.medium"),
        ttl_default_hours=int(e("POC_TTL_HOURS", "24")),
        ttl_max_hours=int(e("POC_TTL_MAX_HOURS", "168")),
        atlas_base=e("ATLAS_API_BASE", "https://cloud.mongodb.com/api/atlas/v2"),
        atlas_project_id=e("ATLAS_PROJECT_ID", ""),
        atlas_sandbox_cluster=e("ATLAS_SANDBOX_CLUSTER", "POV"),
        atlas_sandbox_srv_host=e("ATLAS_SANDBOX_SRV_HOST", ""),
        atlas_sandbox_admin_uri=e("ATLAS_SANDBOX_ADMIN_URI", ""),
        atlas_client_id=e("ATLAS_CLIENT_ID", ""),
        atlas_client_secret=e("ATLAS_CLIENT_SECRET", ""),
        resource_prefix=px,
    )
