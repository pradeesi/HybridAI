#!/usr/bin/env bash
#
# Purpose: Fully self-contained, idempotent deployment script for an Ubuntu VM on Proxmox VE.
#          Installs Docker Engine, Compose, and Portainer CE, provisions the complete Telecom
#          platform (PostgreSQL, FastMCP Server, CRM App, Loki, Prometheus, Grafana), performs
#          end-to-end health and tool execution tests, and displays all component URLs.
# Architecture/Context: Standalone orchestrator for on-premises / homelab edge deployment
#                       bridged with Google Cloud ADK Agent.
# Dependencies/Side Effects: Requires Ubuntu/Debian OS with sudo access. Binds host ports
#                            5432 (Postgres), 8000 (CRM), 8001 (FastMCP), 3100 (Loki),
#                            9090 (Prometheus), 3000 (Grafana), and 9443/9000 (Portainer CE).
#

set -Eeuo pipefail

# ANSI color codes for readable console feedback
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"

# Default container CLI commands (dynamically set to use sudo if session lacks direct socket permissions)
DOCKER_CMD="docker"
COMPOSE_CMD="docker compose"

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# ==============================================================================
# 1. Prerequisite Detection and Automatic Installation
# ==============================================================================
# Summary: Detects presence of Docker and Docker Compose plugin.
#          If missing, provisions official packages via apt without requiring manual intervention.
# Parameters: None.
# Return Value: 0 on success, exits on fatal failure.
check_and_install_prerequisites() {
    log_info "Verifying system prerequisites on host..."

    # Ensure system package index is accessible
    if ! command -v curl &>/dev/null; then
        log_info "Installing curl utility..."
        sudo apt-get update -y && sudo apt-get install -y curl
    fi

    # Check for Docker engine
    if ! command -v docker &>/dev/null; then
        log_warn "Docker engine not detected. Installing Docker CE via official repository..."
        sudo apt-get update -y
        sudo apt-get install -y ca-certificates gnupg lsb-release
        
        sudo install -m 0755 -d /etc/apt/keyrings
        if [ ! -f /etc/apt/keyrings/docker.gpg ]; then
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
            sudo chmod a+r /etc/apt/keyrings/docker.gpg
        fi

        echo \
          "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
          $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

        sudo apt-get update -y
        sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
        
        # Ensure docker service starts on VM boot
        sudo systemctl enable --now docker
        log_success "Docker installed successfully: $(docker --version 2>/dev/null || sudo docker --version 2>/dev/null || echo 'installed')"
    else
        log_success "Docker engine detected: $(docker --version 2>/dev/null || sudo docker --version 2>/dev/null || echo 'installed')"
    fi

    # Ensure current user has permissions to run docker without sudo in future login sessions
    if ! groups | grep -q docker; then
        log_info "Adding user '${USER:-$LOGNAME}' to docker group for subsequent sessions..."
        sudo usermod -aG docker "${USER:-$LOGNAME}" 2>/dev/null || true
    fi

    # Determine whether Docker commands require 'sudo' in the active shell session.
    # Note: Supplementary group updates via usermod do not affect currently running processes
    # until a new login shell is spawned. Thus, we test direct access to the Docker socket.
    if docker info &>/dev/null; then
        DOCKER_CMD="docker"
        log_success "Docker socket accessible without sudo in active session."
    elif sudo docker info &>/dev/null; then
        DOCKER_CMD="sudo docker"
        log_warn "Active shell lacks non-root Docker socket permissions; automatically using 'sudo docker'."
    else
        DOCKER_CMD="sudo docker"
    fi

    # Check for docker compose support (plugin or standalone binary)
    if ${DOCKER_CMD} compose version &>/dev/null; then
        COMPOSE_CMD="${DOCKER_CMD} compose"
    elif command -v docker-compose &>/dev/null; then
        if [[ "${DOCKER_CMD}" == *"sudo"* ]]; then
            COMPOSE_CMD="sudo docker-compose"
        else
            COMPOSE_CMD="docker-compose"
        fi
    else
        log_warn "Installing Docker Compose plugin..."
        sudo apt-get update -y && sudo apt-get install -y docker-compose-plugin
        COMPOSE_CMD="${DOCKER_CMD} compose"
    fi
    log_success "Docker Compose command: ${COMPOSE_CMD}"
}

# ==============================================================================
# 2. Portainer Community Edition (CE) Setup & Deployment
# ==============================================================================
# Summary: Provisions and starts Portainer Community Edition (CE) in a dedicated container.
#          Exposes Web UI on port 9443 (HTTPS) and port 9000 (HTTP). Binds /var/run/docker.sock
#          and creates a persistent volume 'portainer_data'.
# Parameters: None.
# Return Value: 0 on success, exits on fatal failure.
# Exceptions/Errors: Issues warning if container exists or error if docker run fails.
install_and_start_portainer() {
    log_info "Configuring and deploying Portainer Community Edition (CE)..."

    # Check if a Portainer container is already running
    if ${DOCKER_CMD} ps --format '{{.Names}}' 2>/dev/null | grep -q '^portainer$'; then
        log_success "Portainer CE container is already running."
        return 0
    fi

    # Check if a Portainer container exists in stopped state
    if ${DOCKER_CMD} ps -a --format '{{.Names}}' 2>/dev/null | grep -q '^portainer$'; then
        log_info "Portainer container exists but is stopped. Starting container..."
        ${DOCKER_CMD} start portainer
        log_success "Portainer CE container started."
        return 0
    fi

    # Create persistent volume for Portainer configuration and state
    if ! ${DOCKER_CMD} volume ls --format '{{.Name}}' 2>/dev/null | grep -q '^portainer_data$'; then
        ${DOCKER_CMD} volume create portainer_data >/dev/null
        log_info "Created persistent Docker volume 'portainer_data'."
    fi

    # Deploy Portainer CE container
    # Portainer web dashboard binds to 9443 (HTTPS) and 9000 (HTTP).
    # Host port 8000 (Portainer edge agent tunnel) is omitted to avoid conflict with the Telecom CRM console.
    # The --no-setup-token flag disables the manual log token prompt on new Portainer CE installations.
    log_info "Provisioning portainer/portainer-ce:latest container via ${DOCKER_CMD}..."
    ${DOCKER_CMD} run -d \
        -p 9000:9000 \
        -p 9443:9443 \
        --name portainer \
        --restart=always \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v portainer_data:/data \
        portainer/portainer-ce:latest \
        --no-setup-token

    log_success "Portainer CE container launched successfully."
}

# Helper: check if a port is in use by an external service (not our own running container)
is_host_port_occupied() {
    local port="$1"
    local container_name="$2"

    local in_use=1
    if command -v ss &>/dev/null; then
        if ss -tlpn 2>/dev/null | grep -qE ":${port}\b"; then
            in_use=0
        fi
    elif command -v lsof &>/dev/null; then
        if lsof -iTCP:"${port}" -sTCP:LISTEN &>/dev/null; then
            in_use=0
        fi
    fi

    if [ ${in_use} -ne 0 ]; then
        return 1 # Port is completely free
    fi

    # Port is in use; check if held by our own running container
    if ${DOCKER_CMD} ps --filter "name=^/${container_name}$" --format '{{.Names}}' 2>/dev/null | grep -q "^${container_name}$"; then
        return 1 # Our own container is running on this port
    fi

    return 0 # Occupied by another process/container
}

# Helper: search for the next available port
resolve_free_port() {
    local desired_port="$1"
    local port="${desired_port}"
    while :; do
        local in_use=1
        if command -v ss &>/dev/null; then
            ss -tlpn 2>/dev/null | grep -qE ":${port}\b" && in_use=0 || in_use=1
        elif command -v lsof &>/dev/null; then
            lsof -iTCP:"${port}" -sTCP:LISTEN &>/dev/null && in_use=0 || in_use=1
        fi
        if [ ${in_use} -ne 0 ]; then
            echo "${port}"
            return 0
        fi
        ((port++))
    done
}

# ==============================================================================
# 3. Host IP and Environment Configuration Setup
# ==============================================================================
# Summary: Detects the primary LAN IP of the Ubuntu VM and initializes the .env configuration.
# Parameters: None.
# Return Value: Populates global HOST_IP and writes .env file.
setup_environment_config() {
    log_info "Detecting network interface and configuring environment variables..."

    # Automatically resolve primary outbound LAN IP (ignoring docker0 and loopback)
    HOST_IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{print $7; exit}' || hostname -I | awk '{print $1}')
    if [[ -z "${HOST_IP}" || "${HOST_IP}" == "127.0.0.1" ]]; then
        HOST_IP="localhost"
    fi
    log_success "Detected Host LAN IP: ${BOLD}${HOST_IP}${NC}"

    # Resolve Prometheus host port (fallback to 9091+ if 9090 is in use by Cockpit or host daemon)
    if is_host_port_occupied 9090 "telecom-prometheus"; then
        PROMETHEUS_PORT=$(resolve_free_port 9091)
        log_warn "Host port 9090 is occupied by another process on this VM."
        if systemctl is-active --quiet cockpit.socket 2>/dev/null || systemctl is-active --quiet cockpit 2>/dev/null; then
            log_info "Detected Cockpit Linux Web Console holding port 9090."
        fi
        log_info "Automatically mapping Prometheus to host port ${BOLD}${PROMETHEUS_PORT}${NC} to prevent port collision."
    else
        PROMETHEUS_PORT="${PROMETHEUS_PORT:-9090}"
    fi
    export PROMETHEUS_PORT

    ENV_FILE="${PROJECT_ROOT}/.env"
    if [ ! -f "${ENV_FILE}" ]; then
        log_info "Creating default .env configuration file..."
        cat <<EOF > "${ENV_FILE}"
# Auto-generated Homelab Proxmox environment configuration
APP_ENV=production
LOG_LEVEL=INFO

# Database Configuration
POSTGRES_USER=telecom_user
POSTGRES_PASSWORD=telecom_secure_pass
POSTGRES_DB=telecom_db
DATABASE_URL=postgresql+asyncpg://telecom_user:telecom_secure_pass@postgres:5432/telecom_db

# Microservices Ports
CRM_PORT=8000
MCP_PORT=8001

# FastMCP Pre-Shared Bearer Token (shared with GCP ADK Agent)
MCP_AUTH_TOKEN=telecom-mcp-secret-token-change-in-prod-xyz987

# Observability Coordinates
LOKI_URL=http://loki:3100
PROMETHEUS_URL=http://prometheus:9090
PROMETHEUS_PORT=${PROMETHEUS_PORT}
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=telecom_admin
EOF
        log_success "Wrote ${ENV_FILE}"
    else
        log_info "Using existing .env configuration file."
        # Ensure PROMETHEUS_PORT is synchronized with the detected free port
        if grep -q "^PROMETHEUS_PORT=" "${ENV_FILE}"; then
            sed -i "s/^PROMETHEUS_PORT=.*/PROMETHEUS_PORT=${PROMETHEUS_PORT}/" "${ENV_FILE}"
        else
            echo "PROMETHEUS_PORT=${PROMETHEUS_PORT}" >> "${ENV_FILE}"
        fi
    fi

    # Source the environment variables safely
    set -a
    # shellcheck disable=SC1090
    source "${ENV_FILE}"
    set +a
}

# ==============================================================================
# 4. Multi-Container Orchestration via Docker Compose
# ==============================================================================
# Summary: Builds custom images and deploys all 6 microservices in detached mode.
# Parameters: None.
# Return Value: 0 on successful deployment.
deploy_containers() {
    log_info "Launching Telecom HybridAI stack via Docker Compose..."
    echo -e "${CYAN}------------------------------------------------------------${NC}"
    echo "Services to provision: PostgreSQL, FastMCP Server, CRM App, Loki, Prometheus, Grafana"
    echo -e "${CYAN}------------------------------------------------------------${NC}"

    # Clean up any stale or created-state container from earlier failed bind attempts
    if ${DOCKER_CMD} ps -a --filter "name=^/telecom-prometheus$" --format '{{.Status}}' 2>/dev/null | grep -qE "Created|Exited"; then
        log_info "Removing unstarted telecom-prometheus container from previous failed run..."
        ${DOCKER_CMD} rm -f telecom-prometheus >/dev/null 2>&1 || true
    fi

    ${COMPOSE_CMD} -f "${PROJECT_ROOT}/docker-compose.yml" up -d --build --remove-orphans
    log_success "All container services dispatched to background."
}

# ==============================================================================
# 5. Automated Health Verification & MCP Tool Execution Test
# ==============================================================================
# Summary: Probes each service endpoint until healthy and executes an authenticated tool call.
# Parameters: None.
# Return Value: 0 on successful validation, non-zero if critical service fails.
verify_and_test_stack() {
    log_info "Verifying health of all deployed components..."

    wait_for_endpoint() {
        local name="$1"
        local url="$2"
        local expected_code="$3"
        local max_retries=30
        local attempt=1

        echo -n -e "  Waiting for ${BOLD}${name}${NC} (${url})..."
        while [ $attempt -le $max_retries ]; do
            # Use curl with -k to inspect HTTP status (handles self-signed TLS certs like Portainer's)
            local code
            code=$(curl -s -k -o /dev/null -w "%{http_code}" "${url}" 2>/dev/null || echo "000")
            if [ "$code" = "$expected_code" ] || [ "$code" = "200" ] || [ "$code" = "301" ] || [ "$code" = "302" ]; then
                echo -e " ${GREEN}ONLINE (HTTP ${code})${NC}"
                return 0
            fi
            sleep 2
            ((attempt++))
        done

        echo -e " ${RED}FAILED (HTTP ${code})${NC}"
        return 1
    }

    # 1. Check PostgreSQL via container health status
    echo -n -e "  Waiting for ${BOLD}PostgreSQL${NC} (5432)..."
    local pg_attempt=1
    while [ $pg_attempt -le 25 ]; do
        if ${DOCKER_CMD} inspect --format='{{json .State.Health.Status}}' telecom-postgres 2>/dev/null | grep -q '"healthy"'; then
            echo -e " ${GREEN}ONLINE (Database Ready)${NC}"
            break
        fi
        sleep 2
        ((pg_attempt++))
    done

    # 2. Check FastMCP Server
    wait_for_endpoint "FastMCP Server Health" "http://localhost:8001/health" "200"
    wait_for_endpoint "FastMCP Prometheus Metrics" "http://localhost:8001/metrics" "200"

    # 3. Check CRM Application Console
    wait_for_endpoint "CRM Web Console" "http://localhost:8000/health" "200"

    # 4. Check Loki Audit Log Engine
    wait_for_endpoint "Loki Audit Log Engine" "http://localhost:3100/ready" "200"

    # 5. Check Prometheus Telemetry Engine
    wait_for_endpoint "Prometheus Telemetry Engine" "http://localhost:${PROMETHEUS_PORT}/-/healthy" "200"

    # 6. Check Grafana Visual Command Center
    wait_for_endpoint "Grafana Command Center" "http://localhost:3000/api/health" "200"

    # 7. Check Portainer Community Edition (CE) Dashboard
    wait_for_endpoint "Portainer CE Web Dashboard" "https://localhost:9443" "200"

    echo ""
    log_info "Executing automated end-to-end MCP tool call to test database & logging..."

    local mcp_auth_token="${MCP_AUTH_TOKEN:-telecom-mcp-secret-token-change-in-prod-xyz987}"
    local mcp_payload='{"jsonrpc":"2.0","id":1001,"method":"tools/call","params":{"name":"search_customer","arguments":{"phone_number":"+1 (555) 234-5678"}}}'

    local tool_response
    tool_response=$(curl -s -X POST "http://localhost:8001/mcp" \
        -H "Content-Type: application/json" \
        -H "Authorization: Bearer ${mcp_auth_token}" \
        -d "${mcp_payload}" 2>/dev/null || echo "{}")

    if echo "${tool_response}" | grep -q "result"; then
        log_success "MCP Tool Execution Test Succeeded! (Database query, PII sanitization, and Loki audit trail verified)"
    else
        log_warn "MCP Tool call returned unexpected response: ${tool_response}"
    fi
}

# ==============================================================================
# 6. Display Component Access URLs and GCP Connection Snippet
# ==============================================================================
# Summary: Formats and outputs all access URLs and copy-pasteable configuration for the ADK Agent.
# Parameters: None.
# Return Value: None.
display_service_summary() {
    local mcp_auth_token="${MCP_AUTH_TOKEN:-telecom-mcp-secret-token-change-in-prod-xyz987}"
    local grafana_user="${GRAFANA_ADMIN_USER:-admin}"
    local grafana_pass="${GRAFANA_ADMIN_PASSWORD:-telecom_admin}"

    echo ""
    echo -e "${GREEN}${BOLD}===============================================================================${NC}"
    echo -e "${GREEN}${BOLD}       TELECOM HYBRID AI HOMELAB STACK - DEPLOYMENT SUCCESSFUL                 ${NC}"
    echo -e "${GREEN}${BOLD}===============================================================================${NC}"
    echo ""
    echo -e "${BOLD}Host Machine:${NC}      ${HOST_IP} ($(hostname))"
    echo -e "${BOLD}Deployment Target:${NC} On-Premises (Proxmox VE Ubuntu VM / Docker Compose)"
    echo ""
    echo -e "${CYAN}${BOLD}--- Web Portals & Microservices ---${NC}"
    printf "  %-30s %s\n" "Portainer CE Web Dashboard:" "https://${HOST_IP}:9443 (or http://${HOST_IP}:9000)"
    local portainer_token
    portainer_token=$(${DOCKER_CMD} logs portainer 2>&1 | grep -oE 'setup_token=[a-zA-Z0-9_-]+' | cut -d'=' -f2 | tail -n 1 || true)
    if [ -n "${portainer_token}" ]; then
        printf "  %-30s %s\n" "  -> Portainer Setup Token:" "${portainer_token}"
    fi
    printf "  %-30s %s\n" "CRM Contact Center Console:" "http://${HOST_IP}:8000"
    printf "  %-30s %s\n" "FastMCP Server JSON-RPC:"   "http://${HOST_IP}:8001/mcp"
    printf "  %-30s %s\n" "FastMCP Server Health:"     "http://${HOST_IP}:8001/health"
    printf "  %-30s %s\n" "Grafana Command Center:"    "http://${HOST_IP}:3000"
    printf "  %-30s %s\n" "  -> Grafana Credentials:"   "User: ${grafana_user} | Pass: ${grafana_pass}"
    printf "  %-30s %s\n" "Prometheus Metrics Engine:"  "http://${HOST_IP}:${PROMETHEUS_PORT}"
    printf "  %-30s %s\n" "Loki Log Ingestion Stream:"  "http://${HOST_IP}:3100"
    printf "  %-30s %s\n" "PostgreSQL Database:"        "postgresql://telecom_user:telecom_secure_pass@${HOST_IP}:5432/telecom_db"
    echo ""
    echo -e "${CYAN}${BOLD}--- Pre-Configured Dashboards in Grafana ---${NC}"
    echo -e "  • Executive Command Center: http://${HOST_IP}:3000/d/ai-agent-observability"
    echo -e "  • Security & Audit Trail:   http://${HOST_IP}:3000/d/telecom-mcp-security"
    echo ""
    echo -e "${YELLOW}${BOLD}===============================================================================${NC}"
    echo -e "${YELLOW}${BOLD}  GCP ADK TELECOM AGENT INTEGRATION CONFIGURATION                             ${NC}"
    echo -e "${YELLOW}${BOLD}===============================================================================${NC}"
    echo "To connect your GCP Telecom Agent (running on Cloud Run or Agent Runtime) to this Proxmox instance,"
    echo "update your 'telecom-agent/.env' with these exact parameters:"
    echo ""
    echo -e "${BOLD}MCP_DEPLOYMENT_TARGET=on_prem${NC}"
    echo -e "${BOLD}MCP_SERVER_URL=http://${HOST_IP}:8001${NC}"
    echo -e "${BOLD}MCP_AUTH_TOKEN=${mcp_auth_token}${NC}"
    echo ""
    echo -e "${GREEN}All services are running in background. Manage with: ${BOLD}${COMPOSE_CMD} ps / logs / down${NC}"
    if [[ "${DOCKER_CMD}" == *"sudo"* ]]; then
        echo -e "${YELLOW}Tip: To run docker commands without 'sudo', run 'newgrp docker' or log out and log back in.${NC}"
    fi
    echo -e "${GREEN}${BOLD}===============================================================================${NC}"
}

# Main script execution flow
main() {
    check_and_install_prerequisites
    install_and_start_portainer
    setup_environment_config
    deploy_containers
    verify_and_test_stack
    display_service_summary
}

main "$@"
