import os
import pytest
from poc_infra_tools.bootstrap import render_user_data
from poc_infra_tools.atlas import provision_poc_database, _srv_uri
from poc_infra_tools.errors import InfraError
from poc_infra_tools.config import get_infra_config


def test_bootstrap_renders_platform_only_by_default():
    ud = render_user_data("arn:aws:secretsmanager:ap-south-1:1:secret:x", "ap-south-1", "shared_db")
    assert ud.startswith("#!/bin/bash") and "nodejs20" in ud and "nginx" in ud and "pm2" in ud
    assert "/etc/poc/env" in ud and "amazon-ssm-agent" in ud
    assert "mongodb-org" not in ud


def test_bootstrap_local_ec2_installs_mongodb():
    ud = render_user_data("arn:x", "ap-south-1", "local_ec2")
    assert "mongodb-org-8.0" in ud and "bindIp: 127.0.0.1" in ud and "mongod" in ud


def test_provision_modes_without_atlas(monkeypatch):
    monkeypatch.setenv("ATLAS_PROJECT_ID", "")
    poc = "poc_01K5ZGF1XTVREG0000000000A1"
    r = provision_poc_database(poc, "local_ec2")
    assert r["connection_string"] == f"mongodb://127.0.0.1:27017/{poc}" and r["resources"] == []
    r = provision_poc_database(poc, "external_uri", external_uri="mongodb+srv://u:p@h/x")
    assert r["cluster_name"] == "external" and r["connection_string"].startswith("mongodb+srv://")
    with pytest.raises(InfraError): provision_poc_database(poc, "external_uri")
    with pytest.raises(InfraError): provision_poc_database(poc, "shared_db")  # not configured


def test_srv_uri_escapes():
    u = _srv_uri("pov.abc.mongodb.net", "poc_x", "p@ss/word", "poc_1")
    assert "p%40ss%2Fword" in u and u.endswith("/poc_1?authSource=admin&retryWrites=true&w=majority")


def test_config_defaults(monkeypatch):
    for k in ("POC_SG_NAME", "POC_INSTANCE_PROFILE", "POC_RESOURCE_PREFIX"):
        monkeypatch.delenv(k, raising=False)
    c = get_infra_config()
    assert c.sg_name == "msinha-poc-instance-sg" and c.instance_profile == "msinha-poc-instance-role"
    assert c.default_instance_type == "t3.medium" and c.ttl_default_hours == 24 and c.ttl_max_hours == 168
