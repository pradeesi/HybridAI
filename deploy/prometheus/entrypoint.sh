#!/bin/sh
# Purpose: Prometheus startup entrypoint with Cloud Run token refresh loop.
# Architecture/Context: Automatically retrieves Google Cloud IAM ID tokens for scraping secure Cloud Run services.
# Dependencies/Side Effects: Periodically writes OIDC token to /tmp/mcp_token.
set -e

touch /tmp/mcp_token

(
  while true; do
    wget -q -O /tmp/mcp_token --header="Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=https://telecom-mcp-server-1111937452.europe-west1.run.app" 2>/dev/null || true
    sleep 1800
  done
) &

exec /bin/prometheus "$@"
