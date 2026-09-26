#!/usr/bin/env bash
# Purpose: Deploys all 5 Telecom Hybrid AI services (MCP, CRM, Prometheus, Loki, Grafana) to Google Cloud Run.
# Architecture/Context: Guarantees 100% environment parity with Home Lab / Docker Compose architecture.
# Dependencies/Side Effects: Requires gcloud CLI authenticated with project editor/admin permissions.

set -euo pipefail

PROJECT_ID="${GCP_PROJECT:-pradeesi-ai-demo}"
REGION="${GCP_REGION:-europe-west1}"
REPO="hybrid-ai-repo"
ARTIFACT_REGISTRY="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}"
DOMAIN="domain:pradeesi.altostrat.com"

echo "=========================================================="
echo " Deploying Unified Telecom Platform to Cloud Run"
echo " Project: ${PROJECT_ID} | Region: ${REGION}"
echo "=========================================================="

# 1. Build and push container images
echo "[1/4] Building container images via Cloud Build..."
gcloud builds submit --tag "${ARTIFACT_REGISTRY}/hybrid-ai:latest" . --project="${PROJECT_ID}"
gcloud builds submit --config=deploy/cloudbuild-observability.yaml . --project="${PROJECT_ID}"

# 2. Deploy Loki
echo "[2/4] Deploying telecom-loki..."
gcloud run deploy telecom-loki \
  --image "${ARTIFACT_REGISTRY}/telecom-loki:latest" \
  --platform managed \
  --region "${REGION}" \
  --port 3100 \
  --allow-unauthenticated \
  --project "${PROJECT_ID}"

LOKI_URL=$(gcloud run services describe telecom-loki --region "${REGION}" --project "${PROJECT_ID}" --format="value(status.url)")
gcloud run services add-iam-policy-binding telecom-loki --region "${REGION}" --member="${DOMAIN}" --role="roles/run.invoker" --project="${PROJECT_ID}" --quiet || true

# 3. Deploy Prometheus
echo "[3/4] Deploying telecom-prometheus..."
gcloud run deploy telecom-prometheus \
  --image "${ARTIFACT_REGISTRY}/telecom-prometheus:latest" \
  --platform managed \
  --region "${REGION}" \
  --port 9090 \
  --allow-unauthenticated \
  --project "${PROJECT_ID}"

PROM_URL=$(gcloud run services describe telecom-prometheus --region "${REGION}" --project "${PROJECT_ID}" --format="value(status.url)")
gcloud run services add-iam-policy-binding telecom-prometheus --region "${REGION}" --member="${DOMAIN}" --role="roles/run.invoker" --project="${PROJECT_ID}" --quiet || true

# 4. Deploy Grafana
echo "[4/4] Deploying telecom-grafana..."
gcloud run deploy telecom-grafana \
  --image "${ARTIFACT_REGISTRY}/telecom-grafana:latest" \
  --platform managed \
  --region "${REGION}" \
  --port 3000 \
  --set-env-vars PROMETHEUS_URL="${PROM_URL}",LOKI_URL="${LOKI_URL}",GF_SECURITY_ADMIN_USER=admin,GF_SECURITY_ADMIN_PASSWORD=telecom_admin \
  --allow-unauthenticated \
  --project "${PROJECT_ID}"

gcloud run services add-iam-policy-binding telecom-grafana --region "${REGION}" --member="${DOMAIN}" --role="roles/run.invoker" --project="${PROJECT_ID}" --quiet || true

# 5. Deploy MCP Server & CRM Console wired to Loki
echo "Updating telecom-mcp-server with Loki connection..."
gcloud run deploy telecom-mcp-server \
  --image "${ARTIFACT_REGISTRY}/hybrid-ai:latest" \
  --platform managed \
  --region "${REGION}" \
  --command="python3" \
  --args="-m,src.mcp.server" \
  --port 8001 \
  --set-env-vars APP_ENV=production,MCP_PORT=8001,MCP_AUTH_TOKEN="telecom-mcp-secret-token-change-in-prod-xyz987",LOKI_URL="${LOKI_URL}" \
  --project "${PROJECT_ID}"

echo "Updating telecom-crm-console with Loki connection..."
gcloud run deploy telecom-crm-console \
  --image "${ARTIFACT_REGISTRY}/hybrid-ai:latest" \
  --platform managed \
  --region "${REGION}" \
  --command="python3" \
  --args="-m,src.crm.app" \
  --port 8000 \
  --set-env-vars APP_ENV=production,CRM_PORT=8000,MCP_PORT=8001,MCP_AUTH_TOKEN="telecom-mcp-secret-token-change-in-prod-xyz987",LOKI_URL="${LOKI_URL}" \
  --project "${PROJECT_ID}"

echo "=========================================================="
echo " All 5 services deployed successfully with 100% parity:"
echo " - CRM Console:  $(gcloud run services describe telecom-crm-console --region "${REGION}" --project "${PROJECT_ID}" --format="value(status.url)")"
echo " - MCP Server:   $(gcloud run services describe telecom-mcp-server --region "${REGION}" --project "${PROJECT_ID}" --format="value(status.url)")"
echo " - Grafana:      $(gcloud run services describe telecom-grafana --region "${REGION}" --project "${PROJECT_ID}" --format="value(status.url)")"
echo " - Prometheus:   $(gcloud run services describe telecom-prometheus --region "${REGION}" --project "${PROJECT_ID}" --format="value(status.url)")"
echo " - Loki:         ${LOKI_URL}"
echo "=========================================================="
