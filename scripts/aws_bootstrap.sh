#!/usr/bin/env bash
# Creates the POC Builder AWS footprint in a SHARED account: exactly four msinha- resources, nothing else.
# Idempotent: re-running skips what exists. Never modifies anything without the prefix.
#   1. security group  ${PREFIX}poc-instance-sg   (inbound 80/443 from ALLOW_CIDR, in the default VPC)
#   2. instance role   ${PREFIX}poc-instance-role  + instance profile of the same name
#   3. IAM user        ${PREFIX}poc-builder        + inline policy + ONE access key (printed once; put it in platform secrets)
#   4. (bucket ${BUCKET} must already exist — verified, not created)
# Usage: AWS_PROFILE=manish_mongo_AWS ./scripts/aws_bootstrap.sh            # create / verify
#        AWS_PROFILE=manish_mongo_AWS ./scripts/aws_bootstrap.sh --new-key  # also rotate the access key
set -euo pipefail
PREFIX="${POC_RESOURCE_PREFIX:-msinha-}"
REGION="${AWS_REGION:-ap-south-1}"
BUCKET="${POC_S3_BUCKET:-msinha-hackathon}"
SECRET_PREFIX="${POC_SECRET_PREFIX:-msinha/poc-builder}"
VPC_ID="${POC_VPC_ID:-}"
ALLOW_CIDR="${POC_ALLOW_CIDR:-0.0.0.0/0}"
SG_NAME="${PREFIX}poc-instance-sg"
ROLE_NAME="${PREFIX}poc-instance-role"
USER_NAME="${PREFIX}poc-builder"
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export AWS_DEFAULT_REGION="$REGION"
say(){ printf '\n== %s\n' "$*"; }

say "0. bucket $BUCKET must exist"
aws s3api head-bucket --bucket "$BUCKET" >/dev/null && echo "ok: $BUCKET"
aws s3api put-bucket-versioning --bucket "$BUCKET" --versioning-configuration Status=Enabled && echo "ok: versioning enabled"

say "1. security group $SG_NAME"
[ -z "$VPC_ID" ] && VPC_ID=$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true --query 'Vpcs[0].VpcId' --output text)
SG_ID=$(aws ec2 describe-security-groups --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$VPC_ID" --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo None)
if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID=$(aws ec2 create-security-group --group-name "$SG_NAME" --description "POC Builder instances (managed-by=poc-builder)" --vpc-id "$VPC_ID" \
          --tag-specifications "ResourceType=security-group,Tags=[{Key=managed-by,Value=poc-builder},{Key=Name,Value=$SG_NAME}]" --query GroupId --output text)
  aws ec2 authorize-security-group-ingress --group-id "$SG_ID" --ip-permissions \
    "IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=$ALLOW_CIDR,Description=poc-http}]" \
    "IpProtocol=tcp,FromPort=443,ToPort=443,IpRanges=[{CidrIp=$ALLOW_CIDR,Description=poc-https}]" >/dev/null
  echo "created: $SG_ID"
else echo "exists: $SG_ID"; fi

say "2. instance role $ROLE_NAME"
TRUST='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
if ! aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-role --role-name "$ROLE_NAME" --assume-role-policy-document "$TRUST" --tags Key=managed-by,Value=poc-builder >/dev/null
  echo "created role"
else echo "exists role"; fi
aws iam attach-role-policy --role-name "$ROLE_NAME" --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
aws iam put-role-policy --role-name "$ROLE_NAME" --policy-name poc-instance-scope --policy-document "$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Effect":"Allow","Action":["s3:GetObject","s3:GetObjectVersion"],"Resource":"arn:aws:s3:::$BUCKET/pocs/*"},
 {"Effect":"Allow","Action":["s3:ListBucket"],"Resource":"arn:aws:s3:::$BUCKET","Condition":{"StringLike":{"s3:prefix":["pocs/*"]}}},
 {"Effect":"Allow","Action":["s3:PutObject"],"Resource":"arn:aws:s3:::$BUCKET/pocs/*/deploy/*"},
 {"Effect":"Allow","Action":["secretsmanager:GetSecretValue"],"Resource":"arn:aws:secretsmanager:$REGION:$ACCOUNT_ID:secret:$SECRET_PREFIX/*"},
 {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"arn:aws:logs:$REGION:$ACCOUNT_ID:log-group:/poc-builder/*"}
]}
JSON
)"
if ! aws iam get-instance-profile --instance-profile-name "$ROLE_NAME" >/dev/null 2>&1; then
  aws iam create-instance-profile --instance-profile-name "$ROLE_NAME" >/dev/null
  aws iam add-role-to-instance-profile --instance-profile-name "$ROLE_NAME" --role-name "$ROLE_NAME"
  echo "created instance profile"; sleep 10
else echo "exists instance profile"; fi

say "3. platform IAM user $USER_NAME"
if ! aws iam get-user --user-name "$USER_NAME" >/dev/null 2>&1; then
  aws iam create-user --user-name "$USER_NAME" --tags Key=managed-by,Value=poc-builder >/dev/null; echo "created user"
else echo "exists user"; fi
aws iam put-user-policy --user-name "$USER_NAME" --policy-name poc-builder-platform --policy-document "$(cat <<JSON
{"Version":"2012-10-17","Statement":[
 {"Sid":"S3","Effect":"Allow","Action":["s3:PutObject","s3:GetObject","s3:GetObjectVersion","s3:DeleteObject","s3:ListBucket","s3:GetBucketVersioning"],
  "Resource":["arn:aws:s3:::$BUCKET","arn:aws:s3:::$BUCKET/*"]},
 {"Sid":"Secrets","Effect":"Allow","Action":["secretsmanager:CreateSecret","secretsmanager:PutSecretValue","secretsmanager:GetSecretValue","secretsmanager:DescribeSecret","secretsmanager:DeleteSecret","secretsmanager:RestoreSecret","secretsmanager:TagResource"],
  "Resource":"arn:aws:secretsmanager:$REGION:$ACCOUNT_ID:secret:$SECRET_PREFIX/*"},
 {"Sid":"Ec2Read","Effect":"Allow","Action":["ec2:DescribeInstances","ec2:DescribeInstanceStatus","ec2:DescribeSecurityGroups","ec2:DescribeSubnets","ec2:DescribeVpcs","ec2:DescribeImages"],"Resource":"*"},
 {"Sid":"Ec2Run","Effect":"Allow","Action":["ec2:RunInstances"],"Resource":"*"},
 {"Sid":"Ec2Tag","Effect":"Allow","Action":["ec2:CreateTags"],"Resource":"*","Condition":{"StringEquals":{"ec2:CreateAction":["RunInstances","CreateVolume"]}}},
 {"Sid":"Ec2TagManaged","Effect":"Allow","Action":["ec2:CreateTags"],"Resource":"arn:aws:ec2:$REGION:$ACCOUNT_ID:instance/*","Condition":{"StringEquals":{"aws:ResourceTag/managed-by":"poc-builder"}}},
 {"Sid":"Ec2Terminate","Effect":"Allow","Action":["ec2:TerminateInstances","ec2:StopInstances"],"Resource":"arn:aws:ec2:$REGION:$ACCOUNT_ID:instance/*","Condition":{"StringEquals":{"aws:ResourceTag/managed-by":"poc-builder"}}},
 {"Sid":"PassRole","Effect":"Allow","Action":["iam:PassRole"],"Resource":"arn:aws:iam::$ACCOUNT_ID:role/$ROLE_NAME"},
 {"Sid":"Ssm","Effect":"Allow","Action":["ssm:SendCommand","ssm:GetCommandInvocation","ssm:CancelCommand","ssm:ListCommandInvocations","ssm:DescribeInstanceInformation","ssm:GetParameter"],"Resource":"*"},
 {"Sid":"Logs","Effect":"Allow","Action":["logs:DescribeLogGroups","logs:GetLogEvents","logs:FilterLogEvents"],"Resource":"arn:aws:logs:$REGION:$ACCOUNT_ID:log-group:/poc-builder/*"}
]}
JSON
)"
if [ "${1:-}" = "--new-key" ] || [ "$(aws iam list-access-keys --user-name "$USER_NAME" --query 'length(AccessKeyMetadata)' --output text)" = "0" ]; then
  KEY=$(aws iam create-access-key --user-name "$USER_NAME" --query 'AccessKey.[AccessKeyId,SecretAccessKey]' --output text)
  echo; echo "NEW ACCESS KEY (shown once — store as platform secrets AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, never in git):"
  echo "$KEY" | awk '{print "AWS_ACCESS_KEY_ID="$1"\nAWS_SECRET_ACCESS_KEY="$2}'
else echo "access key exists (use --new-key to rotate)"; fi

say "summary"
printf 'SG_ID=%s\nROLE=%s\nUSER=%s\nBUCKET=%s\nREGION=%s\n' "$SG_ID" "$ROLE_NAME" "$USER_NAME" "$BUCKET" "$REGION"
