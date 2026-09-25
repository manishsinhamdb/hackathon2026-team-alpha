#!/usr/bin/env bash
# Create (or refresh) the `control-tower-secrets` Secret in namespace sa-demo on the Kanopy staging
# cluster, from apps/control-tower/.env.local. It carries the three server-only secrets the BFF needs:
#   PLATFORM_SA_CLIENT_ID, PLATFORM_SA_CLIENT_SECRET, POC_PLATFORM_MONGODB_URI
#
# Secret VALUES are never printed: they flow from .env.local over a pipe into kubectl. Requires a valid
# staging kubeconfig context (CorpSecure OIDC login) with write access to sa-demo.
#
# Usage:
#   export KUBECONFIG=~/.kube/config.staging      # a context pointing at api.staging.corp.mongodb.com
#   kubectl config use-context api.staging.corp.mongodb.com   # (re-run the OIDC login if the token expired)
#   apps/control-tower/deploy/kanopy-create-secret.sh
set -euo pipefail

NS=sa-demo
SECRET=control-tower-secrets
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENVFILE="$DIR/.env.local"
KEYS='PLATFORM_SA_CLIENT_ID|PLATFORM_SA_CLIENT_SECRET|POC_PLATFORM_MONGODB_URI'

[ -f "$ENVFILE" ] || { echo "error: $ENVFILE not found (fill it from .env.example first)" >&2; exit 1; }
for k in PLATFORM_SA_CLIENT_ID PLATFORM_SA_CLIENT_SECRET POC_PLATFORM_MONGODB_URI; do
  grep -qE "^${k}=." "$ENVFILE" || { echo "error: $k missing/empty in .env.local" >&2; exit 1; }
done

# Idempotent apply. The rendered YAML (with base64 values) is piped straight into apply — never shown.
kubectl create secret generic "$SECRET" -n "$NS" \
  --from-env-file=/dev/stdin --dry-run=client -o yaml \
  < <(grep -E "^(${KEYS})=" "$ENVFILE") \
  | kubectl apply -n "$NS" -f -

echo "OK: applied Secret '$SECRET' in namespace '$NS' (3 keys; values not printed)"
