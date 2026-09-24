"""EC2 user_data (§7.4). Does exactly the platform work and nothing app-specific:
Node 20, nginx, git, tar, pm2, /etc/poc/env from Secrets Manager, /opt/poc, SSM agent up.
In local_ec2 mode it also installs MongoDB Community 8.0 and binds it to localhost."""

_MONGO_LOCAL = r"""
cat > /etc/yum.repos.d/mongodb-org-8.0.repo <<'REPO'
[mongodb-org-8.0]
name=MongoDB Repository
baseurl=https://repo.mongodb.org/yum/amazon/2023/mongodb-org/8.0/x86_64/
gpgcheck=1
enabled=1
gpgkey=https://pgp.mongodb.com/server-8.0.asc
REPO
dnf install -y mongodb-org
sed -i 's/^  bindIp: .*/  bindIp: 127.0.0.1/' /etc/mongod.conf
systemctl enable --now mongod
for i in $(seq 1 30); do mongosh --quiet --eval 'db.runCommand({ping:1}).ok' >/dev/null 2>&1 && break; sleep 2; done
"""


def render_user_data(secret_arn: str, region: str, db_mode: str, port: int = 8080) -> str:
    mongo = _MONGO_LOCAL if db_mode == "local_ec2" else ""
    return f"""#!/bin/bash
set -euxo pipefail
exec > >(tee /var/log/poc-bootstrap.log) 2>&1
dnf install -y nodejs20 nginx git tar python3
alternatives --set node /usr/bin/node-20 2>/dev/null || true
ln -sf /usr/bin/node-20 /usr/bin/node || true
ln -sf /usr/bin/npm-20 /usr/bin/npm || true
npm i -g pm2
mkdir -p /etc/poc /opt/poc
{mongo}
aws secretsmanager get-secret-value --region {region} --secret-id '{secret_arn}' --query SecretString --output text \\
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("export MONGODB_URI=\\"%s\\"" % d["MONGODB_URI"])' > /etc/poc/env
echo 'export PORT={port}' >> /etc/poc/env
chmod 600 /etc/poc/env
systemctl enable --now nginx
systemctl enable --now amazon-ssm-agent || systemctl restart amazon-ssm-agent
touch /etc/poc/bootstrap.done
"""
