#!/bin/sh
# Purpose: Grafana Cloud Run entrypoint with automatic IAM token injection for Prometheus and Loki datasources.
# Architecture/Context: Runs in Cloud Run under default compute service account which has roles/run.invoker.
# Dependencies/Side Effects: Fetches audience-specific ID tokens from metadata server and updates datasources.
set -e

mkdir -p /tmp/grafana /tmp/grafana/plugins /tmp/grafana/logs

# Start background sync to inject Cloud Run IAM ID tokens into Prometheus and Loki datasources
(
  # Wait until Grafana is ready
  while ! curl -s -f http://127.0.0.1:3000/api/health >/dev/null 2>&1; do
    sleep 2
  done

  echo "[Entrypoint] Grafana is healthy. Initializing datasource IAM token sync..."

  while true; do
    if [ -n "$PROMETHEUS_URL" ]; then
      PROM_TOKEN=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${PROMETHEUS_URL}" 2>/dev/null || true)
      if [ -n "$PROM_TOKEN" ]; then
        curl -s -X PUT \
          -u admin:telecom_admin \
          -H "Content-Type: application/json" \
          -d "{\"name\":\"Prometheus\",\"type\":\"prometheus\",\"uid\":\"PBFA97CFB590B2093\",\"access\":\"proxy\",\"url\":\"${PROMETHEUS_URL}\",\"jsonData\":{\"httpHeaderName1\":\"Authorization\"},\"secureJsonData\":{\"httpHeaderValue1\":\"Bearer ${PROM_TOKEN}\"}}" \
          "http://127.0.0.1:3000/api/datasources/uid/PBFA97CFB590B2093" >/dev/null 2>&1 || true
        echo "[Entrypoint] Injected fresh IAM token for Prometheus datasource."
      fi
    fi

    if [ -n "$LOKI_URL" ]; then
      LOKI_TOKEN=$(curl -s -H "Metadata-Flavor: Google" "http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience=${LOKI_URL}" 2>/dev/null || true)
      if [ -n "$LOKI_TOKEN" ]; then
        curl -s -X PUT \
          -u admin:telecom_admin \
          -H "Content-Type: application/json" \
          -d "{\"name\":\"Loki\",\"type\":\"loki\",\"uid\":\"P8E80F9AEF21F6940\",\"access\":\"proxy\",\"url\":\"${LOKI_URL}\",\"jsonData\":{\"httpHeaderName1\":\"Authorization\"},\"secureJsonData\":{\"httpHeaderValue1\":\"Bearer ${LOKI_TOKEN}\"}}" \
          "http://127.0.0.1:3000/api/datasources/uid/P8E80F9AEF21F6940" >/dev/null 2>&1 || true
        echo "[Entrypoint] Injected fresh IAM token for Loki datasource."
      fi
    fi

    # Refresh every 10 minutes (OIDC ID tokens expire in 60 minutes)
    sleep 600
  done
) &

exec /run.sh
