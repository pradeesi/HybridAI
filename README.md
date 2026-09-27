<!--
  Purpose: Primary documentation, architectural specification, local execution instructions, and cloud/on-prem deployment playbooks for the Telecom Customer Care Hybrid AI Platform.
  Architecture/Context: Central guide for developers, AI engineers, and operators deploying the FastMCP server, CRM agent console, and observability stack across any environment.
  Dependencies/Side Effects: Governs environment configuration and standardized deployment targets: Cloud (Google Cloud Run) and On-Premises (Proxmox VE / Ubuntu VM & Docker Compose).
-->

# Telecom Customer Care Hybrid AI Platform & Secure FastMCP Server

An enterprise-grade, containerized Telecom Customer Care platform replicating real-world telecommunications frontline operations (Home Broadband, Mobile 5G, Billing Disputes, Line Diagnostics, and Proactive Upselling). 

Exposes a hardened **FastMCP (Server-Sent Events)** server consumed by the **Google Gemini Enterprise App (GE App)** to assist human contact center agents in real time while minimizing Average Handle Time (AHT).

Built with **Python 3.11+ (FastAPI)**, **Jinja2**, **HTML5**, **locally stored Bootstrap 5** (zero external CDN dependencies), **PostgreSQL 16** (with automatic local SQLite fallback), and a full observability suite (**Prometheus**, **Grafana Loki**, and **Grafana**).

---

## 1. Application Architecture

The platform supports an enterprise **Dual-Tier Hybrid AI Architecture**:

1. **Autonomous Telecom ADK Agent (GCP Agent Runtime)**:
   - Built with the **Google Agent Development Kit (ADK)** and registered directly into the **Google Gemini Enterprise App (GE App)**.
   - Deployed on **Google Cloud Agent Runtime (Vertex AI Reasoning Engines)** with native Google Identity / IAM authentication.
   - Strictly bounded to the 8 telecom MCP tools, rejecting all out-of-scope requests.
   - Observability: Monitored via Google Cloud-native logging, Cloud Trace, and Vertex AI telemetry. **The ADK Agent does not write directly to Prometheus or Loki.** Instead, it injects authenticated Google Identity claims (`X-Goog-Authenticated-User-Email`), trace IDs, and agent identifiers into its HTTP tool calls to the MCP server.

2. **Hardened Telecom FastMCP & Support Infrastructure**:
   - Deployed flexibly either to **Google Cloud Run** or **On-Premises on a Proxmox VE Server** (via Docker Compose).
   - Houses the FastMCP server, the Customer Care CRM Console, PostgreSQL database, and on-prem compliance stack (Prometheus, Loki, Grafana).
   - Observability: Captures the security context passed by the ADK agent, streams immutable audit trails to **Grafana Loki**, and exposes live operational metrics to **Prometheus**.

```mermaid
graph TD
    subgraph GeminiEnterprise["Google Workspace / Gemini Enterprise Tier"]
        AgentUser["Human Frontline Agent (Google Identity Login)"]
        GE_App["Gemini Enterprise App Interface"]
    end

    subgraph GCPAgentRuntime["GCP Agent Runtime (Vertex AI Reasoning Engines)"]
        ADK_Agent["Telecom ADK Agent (telecom_agent)"]
        ADK_Scope["Strict Scope Guard (8 MCP Tools Only)"]
        Header_Enricher["Security & Compliance Header Enricher"]
        GCP_Logging["GCP Cloud Logging & Cloud Trace"]
    end

    subgraph HybridMCPBoundary["FastMCP & Support Tier (Cloud Run OR On-Prem Proxmox VE)"]
        AuthGuard["Bearer Token Security & PII Redactor"]
        MCP_Server["FastMCP Server (Port 8001 / SSE & JSON-RPC)"]
        CRM_Backend["Telecom CRM Console (Port 8000)"]
        PG[(PostgreSQL 16 Telecom DB)]
        
        subgraph ComplianceObservability["On-Prem / Cloud Compliance Stack"]
            Prometheus["Prometheus (Port 9090)"]
            Loki["Grafana Loki (Port 3100)"]
            Grafana["Grafana Dashboards (Port 3000)"]
        end
    end

    AgentUser -->|Prompts in Natural Language| GE_App
    GE_App -->|Google Identity OIDC Session| ADK_Agent
    ADK_Agent --> ADK_Scope
    ADK_Agent -.->|Internal Agent Telemetry| GCP_Logging
    ADK_Agent -->|Enriches with User Email, Trace ID, Agent ID| Header_Enricher
    Header_Enricher -->|HTTP JSON-RPC /mcp| AuthGuard
    AuthGuard --> MCP_Server

    MCP_Server -->|Diagnostics, Queries, Actions| PG
    CRM_Backend -->|CRM Queries & Notes| PG
    
    MCP_Server -->|Immutable Audit Logs with User Identity| Loki
    MCP_Server -->|Invocation Metrics & Latency /metrics| Prometheus
    Prometheus --> Grafana
    Loki --> Grafana
```

### Architectural & Security Pillars
- **Strict Domain Boundary**: The ADK Agent operates exclusively through the 8 defined telecom MCP tools. Unrelated requests (general coding, weather, trivia) are deterministically rejected with a standard compliance refusal message.
- **Enterprise Identity Propagation**: End-user Google Identity emails (`admin@pradeesi.altostrat.com`) and distributed trace contexts are forwarded from Gemini Enterprise through the ADK agent to the MCP backend on every request.
- **Zero-Trust Token Authentication**: MCP invocations require valid Google Cloud Identity OIDC tokens (when target is Cloud Run) or static bearer tokens (when target is on-prem Proxmox).
- **Dynamic PII Masking & PCI Compliance**: Subscriber names, phone numbers, SSNs, street addresses, and payment card numbers are automatically sanitized before leaving the database boundary.
- **Immutable Audit Logging**: Every tool execution is recorded in Grafana Loki with caller identity (`AI_AGENT`), initiating user email, tool arguments, and results.
- **Universal Portability**: The MCP backend and CRM stack can run in Cloud Run, local Docker, or on-prem Proxmox without code modifications.

---

## 2. Project Directory Structure

```text
HybridAI/
├── .env.example                               # Environment variable template with safe default keys
├── .gitignore                                 # Gitignore shielding secrets, credentials, build outputs, and venvs
├── Dockerfile                                 # Multi-stage hardened OCI container running as non-root user
├── README.md                                  # Production documentation, architecture, and playbooks
├── docker-compose.yml                         # Full 6-service orchestration (Postgres, MCP, CRM, Prometheus, Loki, Grafana)
├── pyproject.toml                             # Standard Python package metadata and dependencies
├── requirements.txt                           # Pinned Python requirements
├── deploy/                                    # Infrastructure & Observability provisioning manifests
│   ├── cloudbuild-observability.yaml          # Multi-image Cloud Build manifest for Prometheus, Loki, and Grafana
│   ├── deploy-cloudrun.sh                     # Automated 1-click deployment script for all 5 Cloud Run services
│   ├── grafana/
│   │   ├── Dockerfile                         # Hardened Grafana container with auto-provisioning
│   │   ├── dashboards/
│   │   │   ├── ai_agent_observability.json    # Executive command center: AI tool calls, success rates, latency, guardrails, & Loki audit stream
│   │   │   └── telecom_mcp_security.json      # Live audit stream, PII counters, and auth failure dashboard
│   │   └── provisioning/
│   │       ├── dashboards/dashboards.yml      # Automated Grafana dashboard provider definition
│   │       └── datasources/datasources.yml    # Auto-wired Prometheus and Loki datasources with env interpolation
│   ├── loki/
│   │   ├── Dockerfile                         # Container definition for Loki log TSDB
│   │   └── loki-config.yml                    # Grafana Loki 3.0 TSDB storage & HTTP push configuration
│   └── prometheus/
│       ├── Dockerfile                         # Container definition for Prometheus scraper
│       └── prometheus.yml                     # Scrape configs for MCP server and CRM app (local & cloud)
├── skills/                                    # Gemini Enterprise App Skills & Playbooks
│   └── telecom_customer_care_agent/
│       └── SKILL.md                           # Turn-by-turn interactive Contact Center assistant playbook (No Canvas)
├── src/                                       # Core application source code
│   ├── __init__.py
│   ├── core/
│   │   ├── audit.py                           # Structured Loki HTTP audit client & fallback logger
│   │   ├── config.py                          # Pydantic BaseSettings environment loader
│   │   └── security.py                        # Bearer auth validator & PCI/PII masking engine
│   ├── db/
│   │   ├── database.py                        # SQLAlchemy async engine, sessionmaker, and SQLite fallback
│   │   ├── models.py                          # Telecom relational schema (Customers, Devices, Outages, etc.)
│   │   └── seed_data.py                       # Synthetic scenario generator (4 personas, catalog, outages)
│   ├── mcp/
│   │   ├── server.py                          # FastMCP Server (SSE transport, JSON-RPC, /metrics, Bearer auth)
│   │   └── tools.py                           # 8 telecom tools with telemetry, actions, and Loki audit hooks
│   └── crm/
│       ├── app.py                             # FastAPI CRM web application
│       ├── static/                            # Locally stored frontend assets (zero CDN dependencies)
│       │   ├── css/bootstrap.min.css          # Bootstrap 5.3.3 CSS
│       │   ├── css/bootstrap-icons.min.css    # Bootstrap Icons 1.11.3 CSS
│       │   ├── fonts/bootstrap-icons.woff2    # Local icon webfont
│       │   └── js/bootstrap.bundle.min.js     # Bootstrap 5.3.3 JS Bundle
│       └── templates/                         # Jinja2 HTML templates
│           ├── base.html                      # Master layout with responsive navbar & status indicators
│           ├── customer_detail.html           # Customer 360 console with live telemetry & remote action buttons
│           └── dashboard.html                 # Queue view, active outages banner, and GE App integration helper
├── scripts/                                   # Developer utilities & authenticated proxies
│   └── cloudrun_gateway.py                    # Multi-service local proxy bridge for private Cloud Run services
├── telecom-agent/                             # Google ADK Agent (Agent Runtime & Gemini Enterprise)
│   ├── agents-cli-manifest.yaml               # Google Agents CLI manifest descriptor
│   ├── deployment_metadata.json               # Agent Runtime deployment metadata
│   ├── Dockerfile                             # Hardened container for standalone agent deployment
│   ├── pyproject.toml                         # ADK agent packaging with google-adk[gcp,mcp]
│   ├── app/
│   │   ├── __init__.py                        # ADK app exports
│   │   ├── agent.py                           # Root LlmAgent definition, model setup, & strict prompt
│   │   ├── config.py                          # Runtime settings (MCP URL, target mode, model)
│   │   ├── fast_api_app.py                    # FastAPI server for A2A and Vertex AI Reasoning Engine routes
│   │   ├── tools.py                           # The 8 strict telecom tools with dynamic audit header injection
│   │   └── app_utils/                         # Reasoning engine adapter, A2A routes, & telemetry
│   └── tests/
│       ├── eval/                              # Evaluation suite with LLM-as-judge scoring
│       │   ├── eval_config.yaml               # Evaluation criteria & metrics
│       │   └── datasets/basic-dataset.json    # In-scope tool queries & out-of-scope refusal test cases
│       └── unit/
│           └── test_agent.py                  # Pytest suite verifying agent structure, prompt, & headers
└── tests/
    └── test_telecom_mcp.py                    # Automated test suite (PII, Auth, DB seeding, MCP tools)
```

---

## 3. Frameworks & Libraries

- **Language & Runtime**: Python 3.11+
- **Agent Framework**: [Google Agent Development Kit (ADK)](https://adk.dev/) (`google-adk[gcp,mcp]>=2.0.0`)
- **Foundation Models**: [Google GenAI SDK](https://github.com/google-gemini/google-genai) (`google-genai>=2.0.0`) with Gemini 2.5 Flash
- **API & Web Framework**: [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) (High-performance ASGI)
- **Data Persistence**: [SQLAlchemy 2.0 Async](https://www.sqlalchemy.org/) + [asyncpg](https://github.com/MagicStack/asyncpg) + [aiosqlite](https://github.com/omnilib/aiosqlite)
- **Protocol**: FastMCP Server implementing [Model Context Protocol](https://modelcontextprotocol.io/) via Server-Sent Events (SSE) & JSON-RPC
- **Frontend & Styling**: Jinja2 HTML5 + Bootstrap 5.3.3 + Bootstrap Icons 1.11.3 (100% offline, locally stored)
- **Observability**: [Prometheus Client](https://github.com/prometheus/client_python), [Grafana Loki](https://grafana.com/oss/loki/), and [Grafana](https://grafana.com/)
- **Data Validation & Security**: Pydantic v2 Settings, Cryptography, Secrets

---

## 4. Environment Variables

All settings are externalized and dynamically loaded from the environment or `.env` file.

### Platform & FastMCP Server Environment Variables
| Variable Name | Data Type | Required | Default Value | Description | Example |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `APP_ENV` | `string` | No | `development` | Deployment environment mode (`development`, `staging`, `production`) | `production` |
| `LOG_LEVEL` | `string` | No | `INFO` | Logging threshold (`DEBUG`, `INFO`, `WARN`, `ERROR`) | `INFO` |
| `CRM_PORT` | `integer` | No | `8000` | HTTP port for the Customer Care CRM Console | `8000` |
| `MCP_PORT` | `integer` | No | `8001` | HTTP & SSE port for the FastMCP Server | `8001` |
| `MCP_AUTH_TOKEN` | `string` | **Yes** | `telecom-mcp-secret-token-change-in-prod-xyz987` | Secret Bearer token required for MCP authentication | `telecom-sec-xyz-987` |
| `DATABASE_URL` | `string` | No | Auto-detected | Database connection string (PostgreSQL or automatic SQLite fallback) | `postgresql+asyncpg://user:pass@host:5432/db` |
| `POSTGRES_USER` | `string` | No | `telecom_user` | PostgreSQL superuser username | `telecom_user` |
| `POSTGRES_PASSWORD` | `string` | **Yes** | `telecom_secure_pass` | PostgreSQL superuser password | `SuperSecretPass123!` |
| `POSTGRES_DB` | `string` | No | `telecom_db` | Primary database name | `telecom_db` |
| `LOKI_URL` | `string` | No | `http://localhost:3100` | HTTP push endpoint for Grafana Loki log ingestion | `http://localhost:3100` |
| `GRAFANA_ADMIN_USER` | `string` | No | `admin` | Initial administrator username for Grafana | `admin` |
| `GRAFANA_ADMIN_PASSWORD` | `string` | **Yes** | `telecom_admin` | Initial administrator password for Grafana | `GrafanaPass456!` |

### Telecom ADK Agent Environment Variables (`telecom-agent/.env`)
| Variable Name | Data Type | Required | Default Value | Description | Example |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `GOOGLE_GENAI_USE_VERTEXAI`| `boolean`| No | `true` | Enables Vertex AI backend authentication | `true` |
| `GOOGLE_CLOUD_PROJECT` | `string` | **Yes** | `pradeesi-ai-demo` | Target Google Cloud Project ID | `pradeesi-ai-demo` |
| `GOOGLE_CLOUD_LOCATION`| `string` | No | `us-central1` | Target GCP region for Vertex AI runtime | `us-central1` |
| `MODEL_NAME` | `string` | No | `gemini-2.5-flash` | Foundation model driving agent reasoning | `gemini-2.5-flash` |
| `MCP_SERVER_URL` | `string` | **Yes** | `https://telecom-mcp-server-...run.app` | Target FastMCP server URL (Cloud Run or Proxmox) | `http://localhost:8001` |
| `MCP_DEPLOYMENT_TARGET`| `string` | No | `cloud_run` | Target MCP hosting model (`cloud_run` or `on_prem`) | `cloud_run` |
| `MCP_AUTH_TOKEN` | `string` | Conditional | `telecom-mcp-secret-...` | Static Bearer token required when target is `on_prem` | `telecom-sec-token` |

---

## 5. No Hardcoding Rule

> [!IMPORTANT]
> **Strict Security Notice**: Absolutely zero sensitive data, database passwords, or API tokens are hardcoded within this repository. All credentials are injected via environment variables at container launch or read from a `.env` file (which is strictly excluded by `.gitignore`). Always use [`.env.example`](.env.example) as the template for environment configuration.

---

## 6. Execution Playbooks by Environment

Choose the playbook that fits your target environment:

### Playbook A: Local Workstation / Laptop (Standalone Python, No Docker Required)

*Ideal for development, demonstration, or testing when Docker is not installed on your machine. Automatically uses the high-performance asynchronous SQLite database engine and seeds synthetic telecom records instantly.*

1. **Clone the repository:**
   ```bash
   git clone https://github.com/pradeesi/HybridAI.git
   cd HybridAI
   ```

2. **Initialize Environment Variables:**
   ```bash
   cp .env.example .env
   ```

3. **Set Up Python Virtual Environment:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Verify System with Automated Tests:**
   ```bash
   python3 -m unittest discover tests/
   ```

5. **Start FastMCP Server (Terminal 1):**
   ```bash
   source .venv/bin/activate
   python3 -m src.mcp.server
   ```
   *FastMCP Server is live on `http://localhost:8001` (SSE: `http://localhost:8001/sse`).*

6. **Start CRM Agent Console (Terminal 2):**
   ```bash
   source .venv/bin/activate
   python3 -m src.crm.app
   ```
   *CRM Agent Console is live on `http://localhost:8000`.*

---

### Playbook B: On-Premises Proxmox VE (Ubuntu VM / LXC & Docker Compose)

*Standardized on-premises deployment on **Proxmox VE**, running the entire platform inside a dedicated **Ubuntu Server VM** (or lightweight LXC container). Docker Compose handles 100% of the orchestration, persistence, and pre-wiring for all 6 microservices—zero manual database or package compilation required.*

#### What Runs Inside the Ubuntu Environment:
- **FastMCP Server (`telecom-mcp-server`)**: Hardened SSE / JSON-RPC tool server (port 8001).
- **CRM Agent Console (`telecom-crm-console`)**: Frontline web UI with customer 360 and diagnostics (port 8000).
- **PostgreSQL 16 (`postgres:16-alpine`)**: Relational database with persistent volume (`pg_data`) and auto-seeded personas (port 5432).
- **Prometheus 2.50+ (`prom/prometheus:v2.50.1`)**: Automated metrics scraper pre-configured with [`deploy/prometheus/prometheus.yml`](deploy/prometheus/prometheus.yml) (port 9090).
- **Grafana Loki 3.0+ (`grafana/loki:3.0.0`)**: Centralized TSDB log and audit engine pre-configured with [`deploy/loki/loki-config.yml`](deploy/loki/loki-config.yml) (port 3100).
- **Grafana 10.4+ (`grafana/grafana:10.4.0`)**: Pre-provisioned with Prometheus & Loki datasources and auto-loaded AI Agent Observability dashboards (port 3000).

---

#### Step-by-Step Instructions for Proxmox VE:

##### Option 1: Standard Ubuntu Server VM on Proxmox (Recommended)
*Running inside a dedicated Ubuntu 24.04 or 22.04 LTS VM provides complete kernel isolation, native Docker storage drivers (overlay2), and maximum stability.*

1. **Create the Ubuntu VM in Proxmox VE**:
   - Open Proxmox VE Web GUI (`https://<proxmox-host-ip>:8006`).
   - Click **Create VM** (top right):
     - **General**: VM ID `100` (or next free), Name: `telecom-hybrid-ai`.
     - **OS**: Select **Ubuntu Server 24.04 / 22.04 LTS ISO** image.
     - **System**: Machine: Default (`q35` or `i440fx`), SCSI Controller: `VirtIO SCSI single`, Qemu Agent: Checked.
     - **Disks**: Device: `SCSI`, Disk size: `32 GB` (or larger), Storage: `local-lvm` (or ZFS pool), Discard: Checked.
     - **CPU**: Sockets: `1`, Cores: `4`, Type: `host` (for optimal AES/virtualization performance).
     - **Memory**: `8192 MB` (8 GB RAM).
     - **Network**: Bridge: `vmbr0`, Model: `VirtIO (paravirtualized)`, Firewall: Checked.
   - Click **Finish**, start the VM, and complete the standard Ubuntu Server OS installation.

   *(CLI Alternative directly from Proxmox Root Shell):*
   ```bash
   qm create 100 --name telecom-hybrid-ai \
     --memory 8192 --cores 4 --cpu host \
     --net0 virtio,bridge=vmbr0 \
     --scsihw virtio-scsi-single \
     --scsi0 local-lvm:32,discard=on \
     --boot order=scsi0;ide2 \
     --ide2 local:iso/ubuntu-24.04-live-server-amd64.iso,media=cdrom \
     --agent 1
   qm start 100
   ```

2. **Install Docker Engine & Git Inside the Ubuntu VM**:
   *SSH into the Ubuntu VM or open the Proxmox NoVNC Console:*
   ```bash
   # 1. Update package lists and install prerequisites
   sudo apt update && sudo apt upgrade -y
   sudo apt install -y ca-certificates curl gnupg git jq

   # 2. Add Docker official GPG key and APT repository
   sudo install -m 0755 -d /etc/apt/keyrings
   curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
   sudo chmod a+r /etc/apt/keyrings/docker.gpg

   echo \
     "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
     $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
     sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

   # 3. Install Docker Engine and Docker Compose plugin
   sudo apt update
   sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

   # 4. Enable Docker to start on boot and allow non-root user execution
   sudo systemctl enable --now docker
   sudo usermod -aG docker $USER
   newgrp docker
   ```

3. **(Optional) Configure Private Mesh VPN (Tailscale)**:
   *To securely access the Ubuntu VM and its services from your laptop or cloud without exposing ports on your router:*
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   tailscale ip -4
   ```

4. **Clone Repository & Launch All 6 Services**:

   **Option A: Automated One-Shot Deployment Script (Recommended)**
   *Runs Docker prerequisite checks, installs Docker/Compose if missing, bootstraps all containers, performs health verification and end-to-end tool execution tests, and displays all access URLs:*
   ```bash
   git clone https://github.com/pradeesi/HybridAI.git
   cd HybridAI
   chmod +x scripts/deploy_proxmox.sh
   ./scripts/deploy_proxmox.sh
   ```

   **Option B: Manual Docker Compose Launch**
   ```bash
   git clone https://github.com/pradeesi/HybridAI.git
   cd HybridAI
   cp .env.example .env

   # Launch all 6 services with Docker Compose:
   docker compose up -d --build
   ```

5. **Verify All Services are Healthy**:
   ```bash
   docker compose ps
   ```
   *Expected output: `telecom-postgres` (healthy), `telecom-mcp-server` (healthy), `telecom-crm-console` (healthy), `telecom-prometheus` (running), `telecom-loki` (running), `telecom-grafana` (running).*

6. **(Optional) Auto-Start Stack on VM Boot**:
   *Ensure the Docker Compose stack starts automatically whenever the Ubuntu VM reboots:*
   ```bash
   sudo tee /etc/systemd/system/telecom-ai.service > /dev/null <<EOF
   [Unit]
   Description=Telecom Hybrid AI Docker Compose Stack
   Requires=docker.service
   After=docker.service

   [Service]
   Type=oneshot
   RemainAfterExit=yes
   WorkingDirectory=$(pwd)
   ExecStart=/usr/bin/docker compose up -d
   ExecStop=/usr/bin/docker compose down
   TimeoutStartSec=0

   [Install]
   WantedBy=multi-user.target
   EOF

   sudo systemctl daemon-reload
   sudo systemctl enable telecom-ai.service
   ```

---

##### Option 2: Lightweight Proxmox LXC Container (Alternative)
*If you prefer near-zero CPU/RAM virtualization overhead and fast instantiation:*

1. Open Proxmox VE Web GUI (`https://<proxmox-host-ip>:8006`).
2. Click **Create CT**:
   - **General**: Hostname: `telecom-hybrid-ai`, uncheck *Unprivileged container* (or keep unprivileged with nesting enabled).
   - **Template**: Select `ubuntu-24.04-standard` or `ubuntu-22.04-standard`.
   - **Disks**: 30 GB or more on fast storage pool.
   - **CPU**: 4 Cores.
   - **Memory**: 8192 MB (8 GB RAM).
   - **Network**: Bridge `vmbr0`, IPv4: DHCP or Static IP.
3. **Mandatory Proxmox Setting for Docker in LXC**:
   - Before starting the container: CT -> **Options** -> double-click **Features** -> Check **Nesting** (`nesting=1`) and **keyctl** (`keyctl=1`).
4. Start container, open console, install Docker & clone repo:
   ```bash
   apt update && apt install -y docker.io docker-compose-v2 git curl
   systemctl enable --now docker
   git clone https://github.com/pradeesi/HybridAI.git
   cd HybridAI
   cp .env.example .env
   docker compose up -d --build
   ```

---

#### Accessing On-Premises Endpoints:
Replace `<NODE_IP>` with your Ubuntu VM's local LAN IP (e.g. `192.168.1.50`) or Tailscale IP:
- **CRM Frontline Console**: `http://<NODE_IP>:8000`
- **FastMCP Server (SSE & JSON-RPC)**: `http://<NODE_IP>:8001/mcp` (SSE stream: `http://<NODE_IP>:8001/sse`)
- **Grafana Observability Dashboards**: `http://<NODE_IP>:3000` (User: `admin` / Password: value from `.env`)
- **Prometheus Metrics Engine**: `http://<NODE_IP>:9090`
- **Grafana Loki Log API**: `http://<NODE_IP>:3100`

#### Managing the Stack:
```bash
# View unified live logs:
docker compose logs -f mcp-server crm-app

# Restart services:
docker compose restart

# Stop the stack:
docker compose down
```

---

### Playbook C: Google Cloud Run (Serverless Managed Containers)

*Ideal for cloud-hosted scalability without managing VMs. Fully serverless execution where Google Cloud handles TLS certificates, auto-scaling, and health monitoring.*

#### 1. Enable Required Google Cloud APIs & Configure Build Permissions:
```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"

gcloud config set project ${PROJECT_ID}

gcloud services enable run.googleapis.com \
                       artifactregistry.googleapis.com \
                       cloudbuild.googleapis.com \
                       sqladmin.googleapis.com \
                       secretmanager.googleapis.com

# Retrieve project number and grant Cloud Build runner permissions to read storage and push images:
PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/storage.objectViewer"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/artifactregistry.writer"

gcloud projects add-iam-policy-binding ${PROJECT_ID} \
    --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
    --role="roles/logging.logWriter"
```

#### 2. Choose Your Database Strategy (Option A: Standalone vs. Option B: Cloud SQL)

##### Option A: Fast Standalone Demo (Recommended — Zero DB Provisioning & Zero Extra Cloud Cost)
*The platform includes an embedded SQLite database engine with all 4 telecom personas pre-seeded. No Cloud SQL setup or additional infrastructure cost is needed.*
*(Skip directly to Step 3 below!)*

##### Option B: Enterprise Cloud SQL for PostgreSQL 16 (Production Grade)
*If you wish to use a fully managed PostgreSQL instance in GCP:*
```bash
# 1. Create a Cloud SQL PostgreSQL 16 instance (Enterprise Edition)
gcloud sql instances create telecom-pg-instance \
    --database-version=POSTGRES_16 \
    --edition=ENTERPRISE \
    --tier=db-custom-1-3840 \
    --region=${REGION} \
    --root-password="telecom_secure_pass"

# 2. Create the application database and user
gcloud sql databases create telecom_db --instance=telecom-pg-instance
gcloud sql users create telecom_user --instance=telecom-pg-instance --password="telecom_secure_pass"

# 3. Retrieve and export the Cloud SQL Instance Connection Name:
export INSTANCE_CONNECTION_NAME=$(gcloud sql instances describe telecom-pg-instance --format='value(connectionName)')
echo "Cloud SQL Connection Name: ${INSTANCE_CONNECTION_NAME}"
```

---

#### 3. Build & Push Image to Google Artifact Registry:
```bash
# 1. Create Docker repository in Artifact Registry (skip if already created)
gcloud artifacts repositories create hybrid-ai-repo \
    --repository-format=docker \
    --location=${REGION} \
    --description="Telecom HybridAI Repository" 2>/dev/null || true

# 2. Clone repository in Cloud Shell and navigate into the project folder:
git clone https://github.com/pradeesi/HybridAI.git
cd HybridAI
```

---

#### 4. Automated One-Click Deployment (Recommended)

To deploy the entire 5-service stack with 100% environment parity in a single step, execute the automated Cloud Run deployment script:

```bash
chmod +x deploy/deploy-cloudrun.sh
./deploy/deploy-cloudrun.sh
```

This script automatically:
1. Builds the core application and observability container images via Google Cloud Build.
2. Deploys **`telecom-loki`**, **`telecom-prometheus`**, and **`telecom-grafana`** to Cloud Run.
3. Automatically injects internal service URLs into Grafana's pre-provisioned Prometheus and Loki datasources.
4. Deploys **`telecom-mcp-server`** and **`telecom-crm-console`** wired directly to Loki log streaming.
5. Configures domain and invoker IAM permissions.

---

#### 5. Step-by-Step Manual Deployment:

If you prefer deploying and managing each component individually:

##### Step 5.1: Build Container Images via Cloud Build
```bash
# 1. Build core application container (MCP Server & CRM Console):
gcloud builds submit --tag ${REGION}-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/hybrid-ai:latest .

# 2. Build observability containers (Grafana, Prometheus, and Loki):
gcloud builds submit --config=deploy/cloudbuild-observability.yaml .
```

##### Step 5.2: Deploy Loki (Log Aggregation & Audit TSDB)
```bash
gcloud run deploy telecom-loki \
    --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/telecom-loki:latest \
    --platform managed \
    --region ${REGION} \
    --port 3100 \
    --allow-unauthenticated \
    --project ${PROJECT_ID}

LOKI_URL=$(gcloud run services describe telecom-loki --region ${REGION} --format="value(status.url)")
```

##### Step 5.3: Deploy Prometheus (Metrics Scraper)
```bash
gcloud run deploy telecom-prometheus \
    --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/telecom-prometheus:latest \
    --platform managed \
    --region ${REGION} \
    --port 9090 \
    --allow-unauthenticated \
    --project ${PROJECT_ID}

PROM_URL=$(gcloud run services describe telecom-prometheus --region ${REGION} --format="value(status.url)")
```

##### Step 5.4: Deploy Grafana (Contact Center & Security Dashboards)
```bash
gcloud run deploy telecom-grafana \
    --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/telecom-grafana:latest \
    --platform managed \
    --region ${REGION} \
    --port 3000 \
    --set-env-vars PROMETHEUS_URL="${PROM_URL}",LOKI_URL="${LOKI_URL}",GF_SECURITY_ADMIN_USER=admin,GF_SECURITY_ADMIN_PASSWORD=telecom_admin \
    --allow-unauthenticated \
    --project ${PROJECT_ID}

GRAFANA_URL=$(gcloud run services describe telecom-grafana --region ${REGION} --format="value(status.url)")
```

##### Step 5.5: Deploy FastMCP Server (Wired to Loki)
```bash
gcloud run deploy telecom-mcp-server \
    --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/hybrid-ai:latest \
    --platform managed \
    --region ${REGION} \
    --command="python3,-m,src.mcp.server" \
    --port 8001 \
    --set-env-vars APP_ENV=production,MCP_PORT=8001,MCP_AUTH_TOKEN="telecom-mcp-secret-token-change-in-prod-xyz987",LOKI_URL="${LOKI_URL}" \
    --allow-unauthenticated \
    --project ${PROJECT_ID}

MCP_URL=$(gcloud run services describe telecom-mcp-server --region ${REGION} --format="value(status.url)")
```

##### Step 5.6: Deploy CRM Agent Console (Wired to Loki)
```bash
gcloud run deploy telecom-crm-console \
    --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/hybrid-ai:latest \
    --platform managed \
    --region ${REGION} \
    --command="python3,-m,src.crm.app" \
    --port 8000 \
    --set-env-vars APP_ENV=production,CRM_PORT=8000,MCP_PORT=8001,MCP_AUTH_TOKEN="telecom-mcp-secret-token-change-in-prod-xyz987",LOKI_URL="${LOKI_URL}" \
    --allow-unauthenticated \
    --project ${PROJECT_ID}

CRM_URL=$(gcloud run services describe telecom-crm-console --region ${REGION} --format="value(status.url)")
```

---

#### 6. Accessing & Verifying the Services:

##### A. Service URLs Summary
| Component | Cloud Run URL | Credentials / Access |
| :--- | :--- | :--- |
| **Grafana** | `${GRAFANA_URL}` | User: `admin` / Password: `telecom_admin` |
| **Prometheus** | `${PROM_URL}` | Targets: `${PROM_URL}/targets` |
| **Loki** | `${LOKI_URL}` | Health: `${LOKI_URL}/ready` |
| **CRM Console** | `${CRM_URL}` | Frontline Agent Web Console |
| **FastMCP Server** | `${MCP_URL}` | SSE / JSON-RPC: `${MCP_URL}/mcp` |

##### B. Configure Invoker IAM Permissions (Domain-Restricted Orgs)
If your Google Cloud organization enforces Domain Restricted Sharing (e.g. `pradeesi.altostrat.com`):
```bash
for SVC in telecom-crm-console telecom-mcp-server telecom-grafana telecom-prometheus telecom-loki; do
    gcloud run services add-iam-policy-binding ${SVC} --region=${REGION} --member="domain:pradeesi.altostrat.com" --role="roles/run.invoker" --project=${PROJECT_ID} --quiet || true
    gcloud run services add-iam-policy-binding ${SVC} --region=${REGION} --member="user:$(gcloud config get-value account)" --role="roles/run.invoker" --project=${PROJECT_ID} --quiet || true
done
```

##### C. Verify Endpoints via CLI
```bash
TOKEN=$(gcloud auth print-identity-token)

# 1. Verify Grafana Health
curl -s -H "Authorization: Bearer ${TOKEN}" "${GRAFANA_URL}/api/health"
# Returns: {"commit":"...","database":"ok","version":"10.4.0"}

# 2. Verify Prometheus Health
curl -s -H "Authorization: Bearer ${TOKEN}" "${PROM_URL}/-/healthy"
# Returns: Prometheus Server is Healthy.

# 3. Verify FastMCP Server Health
curl -s -H "Authorization: Bearer ${TOKEN}" "${MCP_URL}/health"
# Returns: {"status":"healthy","service":"telecom-mcp-server","version":"1.0.0"}

# 4. Test MCP Tool Execution
curl -s -X POST "${MCP_URL}/mcp" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "X-MCP-Token: telecom-mcp-secret-token-change-in-prod-xyz987" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "search_customer", "arguments": {"query": "Elena"}}}' | jq .
```

##### D. Accessing Services in Your Web Browser (Authenticated Gateway)
Because Google Cloud Run IAM requires an HTTP `Authorization: Bearer` header that standard web browsers do not attach natively, a multi-port gateway bridge is provided at [`scripts/cloudrun_gateway.py`](scripts/cloudrun_gateway.py).

Run the gateway script on your local machine:
```bash
python3 scripts/cloudrun_gateway.py
```

This binds to your local ports, automatically signs requests with your active `gcloud` identity, and proxies traffic to the live Cloud Run instances with zero 403 Forbidden errors:
* **CRM Console**: 👉 [http://localhost:8000](http://localhost:8000) or [http://localhost:8080](http://localhost:8080)
* **Grafana Dashboards**: 👉 [http://localhost:3000](http://localhost:3000) *(Anonymous Admin enabled / Direct Access)*
* **Prometheus Web UI**: 👉 [http://localhost:9090](http://localhost:9090)
* **FastMCP Server**: 👉 [http://localhost:8001/mcp](http://localhost:8001/mcp)
* **Loki Log Ingestion**: 👉 [http://localhost:3100](http://localhost:3100)

---

## 7. Service Verification & Health Checks

Once the services are running, verify each endpoint:

| Endpoint | Method | Expected Output | Purpose |
| :--- | :--- | :--- | :--- |
| `http://localhost:8000/` | `GET` | HTML Dashboard | Frontline Agent Console & Customer Queue |
| `http://localhost:8000/health` | `GET` | `{"status":"healthy"}` | CRM Container Liveness Probe |
| `http://localhost:8001/health` | `GET` | `{"status":"healthy"}` | FastMCP Server Liveness Probe |
| `http://localhost:8001/metrics` | `GET` | Prometheus formatted text | Tool invocations & PII redaction counters |
| `http://localhost:8001/tools` | `GET` *(with Bearer token)* | JSON tool catalog | 8 telecom tool schemas |
| `http://localhost:8001/sse` | `GET` *(with Bearer token)* | `text/event-stream` | Live MCP Event Stream for Gemini Enterprise |

### Quick CLI Verification Script:
```bash
# 1. Test MCP health
curl -s http://localhost:8001/health

# 2. Test authenticated tool listing
curl -s -H "Authorization: Bearer telecom-mcp-secret-token-change-in-prod-xyz987" http://localhost:8001/tools

# 3. Test customer search tool via direct API
curl -s -X POST http://localhost:8001/execute \
  -H "Authorization: Bearer telecom-mcp-secret-token-change-in-prod-xyz987" \
  -H "Content-Type: application/json" \
  -d '{"name": "search_customer", "arguments": {"query": "Elena"}}'
```

---

## 8. Gemini Enterprise App Integration Guide

To connect **Google Gemini Enterprise (GE)** to this FastMCP server:

### Option A: Gemini Enterprise Custom MCP Data Store (StreamableHTTP)
1. In the **Gemini Enterprise Admin Console**, navigate to **Data stores** > **+ New Data store**.
2. Select **Custom MCP Server**:
   - **Data store name**: `TE MCP`
   - **Instance URL**: `https://telecom-mcp-server-<PROJECT_NUMBER>.<REGION>.run.app/mcp`
   - **Authentication**: Leave as default (or Bearer Token if configured).
3. **Grant Cloud Run Invoker to the Discovery Engine Service Agent**:
   When deployed on Cloud Run with IAM access control (Domain Restricted Sharing), grant the Discovery Engine service agent access:
   ```bash
   PROJECT_NUMBER=$(gcloud projects describe ${PROJECT_ID} --format='value(projectNumber)')

   gcloud run services add-iam-policy-binding telecom-mcp-server \
     --region=${REGION} \
     --member="serviceAccount:service-${PROJECT_NUMBER}@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
     --role="roles/run.invoker"
   ```
4. **Reload & Enable Actions**:
   - Navigate to the newly created data store > **Actions** tab.
   - Click **Reload custom actions** to fetch the 8 tool definitions.
   - Toggle the actions you want enabled in Gemini Enterprise to **Active / Enabled**.

### Option B: SSE Endpoint Connection
For clients supporting SSE transport:
- **Transport**: `Server-Sent Events (SSE)`
- **Server URL**: `https://telecom-mcp-server-<PROJECT_NUMBER>.<REGION>.run.app/sse`
- **HTTP Headers**:
  ```http
  Authorization: Bearer <your-mcp-auth-token>
  ```

### Available Custom MCP Actions:
- `search_customer`: Fast subscriber search by Mobile or Landline phone number (or account query). The phone number remains unmasked as the unique identifier; subscriber names and sensitive PII are strictly masked.
- `get_customer_360`: Full subscriber profile (subscriptions, devices, bills, tickets) anchored by Mobile/Landline phone number.
- `get_service_diagnostics`: Real-time ONT optical dBm signal & 5G telemetry by subscriber phone number or service UUID.
- `run_remote_device_action`: Remote ONT reboot or Wi-Fi optimization triggered by subscriber phone number or device UUID.
- `check_network_outages`: Check for area fiber cuts or tower repairs by subscriber phone number or postal code.
- `get_billing_breakdown`: Line-item charges, roaming fees, and dispute notes with masked card numbers.
- `get_upsell_recommendations`: Personalized Gigabit / 5G pass pitch scripts by subscriber phone number.
- `log_agent_interaction`: Saves call summary, duration, and resolution to CRM and logs audit trail.

### Security & Operational Audit Architecture (Identity Propagation & Request/Response Tracking)

In enterprise contact center operations, complete accountability and auditability require capturing full technical context: every customer lookup and hardware action records the exact agent identity, IP address, trace ID, tool parameters, and full request and response payloads.

#### Subscriber Identity Driven by Mobile / Landline Numbers:
- **Telecom Unique Identity**: In enterprise telecommunications, the subscriber's **Mobile or Landline phone number** serves as the primary unique identifier.
- **Strict Subscriber Privacy on AI Surface**: In MCP responses consumed by AI agents, subscriber names (`E**** R******`), emails (`e***@***.com`), street addresses, SSNs, and payment cards are strictly masked. Only the Mobile / Landline number is unmasked.
- **Independent CRM Console**: The human Contact Center Agent has access to the independent internal CRM console (`http://localhost:8000`), where full customer names and account details are displayed so the human agent can interact respectfully and address subscriber concerns.

#### Full Header, Message Body, Request & Response Logging in Grafana Loki:
Every incoming HTTP message and MCP tool invocation automatically captures and streams structured JSON to Grafana Loki:
- **`headers` & `body`**: Complete incoming HTTP headers and raw message body logged on every request (`event_type: MCP_HTTP_REQUEST`).
- **`jwt_claims`**: Decoded Google Cloud / Gemini Enterprise OIDC token claims (`email`, `sub`, `iss`, `aud`).
- **`request`**: The exact business parameters passed to the tool (e.g. `phone_number`, `query`).
- **`response`**: The complete sanitized output returned by the tool (`event_type: MCP_TOOL_EXECUTION`).
- **`caller`**: The authenticated Contact Center Agent's email automatically resolved from request headers/tokens (e.g. `admin@pradeesi.altostrat.com`).
- **`caller_type`**: `HUMAN_AGENT` vs `SERVICE_AGENT`.
- **`client_ip` & `trace_id`**: Real edge proxy IP and Google Cloud distributed trace context.

### Comprehensive Test Prompts for Gemini Enterprise Chat:

> [!NOTE]
> **Phone-Number-Driven Subscriber Identity**:
> All queries from the Gemini Enterprise App are anchored by the subscriber's **Mobile or Landline phone number**. The AI assistant resolves customer equipment and active plans directly from the phone number.

#### Synthetic Persona Reference Card for Contact Center Testing:
| Subscriber Phone Number | Masked Name | Account Number | Postal Code | Scenario Trigger |
| :--- | :--- | :--- | :--- | :--- |
| **`+1 (555) 234-5678`** | `E**** R******` | `TEL-ACC-88129` | `97477` | Optical line attenuation (-28.5 dBm) & ONT reboot |
| **`+1 (555) 987-1234`** | `M***** V****` | `TEL-ACC-99214` | `94102` | Throttled 5G line (cap exceeded) & upsell pitch |
| **`+1 (555) 678-4321`** | `A**** A*********` | `TEL-ACC-44910` | `10017` | Unexpected international roaming dispute ($85 fee) |
| **`+1 (555) 312-7890`** | `D**** C***` | `TEL-ACC-11029` | `77092` | High bandwidth usage qualifying for 1Gbps Fiber boost |

---

#### Scenario 1: Customer 360 Lookup & Deep Diagnostics (Phone `+1 (555) 234-5678`)
* **Phone-Driven Prompt**:
  > *"Customer with phone +1 (555) 234-5678 is on the line experiencing slow broadband speeds and intermittent buffering. Can you look up their profile, check line diagnostics, and recommend a resolution?"*
- **Tools Invoked Automatically**: `search_customer` &rarr; `get_customer_360` &rarr; `get_service_diagnostics`
- **Expected Outcome**: Identifies ONT hardware `ONT-HW-99281-FBR` for line `+1 (555) 234-5678`, notes optical signal degradation (-28.5 dBm) and packet loss (14.2%), and recommends a remote ONT reboot. Subscriber name is masked (`E**** R******`) while phone number is visible.

#### Scenario 2: Remote Hardware Remediation (Reboot ONT Terminal)
* **Phone-Driven Prompt**:
  > *"Run a remote reboot on the optical router for subscriber phone +1 (555) 234-5678 to restore optical levels."*
- **Tool Invoked Automatically**: `run_remote_device_action(phone_number="+1 (555) 234-5678", action="reboot")`
- **Expected Outcome**: Triggers remote device reboot, sets status to `HEALTHY`, logs security audit event with caller identity, and returns confirmation.

#### Scenario 3: Throttled 5G Plan & Upsell Offer (Phone `+1 (555) 987-1234`)
* **Phone-Driven Prompt**:
  > *"Subscriber calling from +1 (555) 987-1234 is asking why their 5G mobile data has slowed down. Check their usage and give me an upgrade offer I can pitch."*
- **Tools Invoked Automatically**: `search_customer` &rarr; `get_service_diagnostics` &rarr; `get_upsell_recommendations`
- **Expected Outcome**: Detects throttled mobile line (54.8 GB used against 50 GB cap on 5G Essentials), retrieves the `Unlimited 5G Priority Data Pass` offer with pricing, and generates an empathetic agent pitch script.

#### Scenario 4: Billing Dispute & Roaming Audit (Phone `+1 (555) 678-4321`)
* **Phone-Driven Prompt**:
  > *"Subscriber with phone +1 (555) 678-4321 is disputing an unexpected international roaming charge on their recent invoice. What charges were billed and what travel pass should they have used?"*
- **Tools Invoked Automatically**: `search_customer` &rarr; `get_billing_breakdown`
- **Expected Outcome**: Analyzes invoice line items, isolates $85 roaming data fee from London Heathrow, and suggests applying a one-time courtesy credit along with activating the Global Roaming Add-on ($25/mo). Card details are masked.

#### Scenario 5: Infrastructure & Area Outage Detection (Phone `+1 (555) 312-7890`)
* **Phone-Driven Prompt**:
  > *"Subscriber with phone +1 (555) 312-7890 is reporting connectivity drops. Are there any active fiber cuts or cell tower maintenance impacting their area?"*
- **Tools Invoked Automatically**: `search_customer` &rarr; `check_network_outages`
- **Expected Outcome**: Resolves subscriber postal code and returns area outage status.

#### Scenario 6: Call Interaction Logging & CRM Record
* **Prompt**:
  > *"Log this interaction for Elena Rostova (account TEL-ACC-88129): We diagnosed optical signal degradation, executed a remote reboot on her ONT terminal, confirmed line levels stabilized, and offered a Gigabit upgrade pitch. Call duration 240 seconds."*
- **Tool Invoked Automatically**: `log_agent_interaction` (`customer_id=...`, `issue_summary=...`, `resolution_summary=...`, `upsell_offered=True`)
- **Expected Outcome**: Persists structured call record into the database, updates CRM dashboard, and records audit trail entry.

---

### 8.1 Contact Center Executive Skill & Operations Playbook (`skills/telecom_customer_care_agent/SKILL.md`)

To bridge backend capabilities with frontline contact center policies, this repository includes a production-grade **Skill Playbook** located at [`skills/telecom_customer_care_agent/SKILL.md`](skills/telecom_customer_care_agent/SKILL.md).

#### Key Design Pillars of the Skill:
* **Interactive Turn-by-Turn Companion (Human-in-the-Loop)**:
  - Instead of resolving tickets autonomously in the background, Gemini Enterprise acts as a real-time co-pilot for the human agent.
  - Executes **at most ONE tool per turn**, presents a concise 2-3 bullet summary, provides a customer-facing script, and **pauses to ask the agent what to do next**.
* **Strict Zero Canvas / Inline Chat Only Policy**:
  - Contains explicit negative constraints preventing Gemini Enterprise from activating Canvas or generating standalone file reports. All interaction stays directly inside the active chat stream.
* **Standardized 3-Part Response Card**:
  Every response from the assistant follows a clean, scannable format:
  ```markdown
  **Status & Findings**:
  • [1-2 concise bullet points summarizing data retrieved or action taken]

  **Suggested Script for Customer**:
  > "[1-2 empathetic sentences the human agent can read directly to the caller]"

  **Next Step for Agent**:
  • [Clear recommendation or question asking the agent how they want to proceed]
  ```
* **Decoupled Governance & Zero-Downtime Policy Tuning**:
  - Non-technical stakeholders (**Operations Managers, Quality Assurance (QA) Auditors, and Compliance Officers**) can tweak customer scripts, courtesy credit thresholds (e.g. `$25` vs `$50`), or empathy guidelines directly in Markdown **without writing code, rebuilding containers, or redeploying Cloud Run**.

#### Turn-by-Turn Workflow Governed by the Skill:
1. **Step 1: Intake & Search** (`search_customer`): Disambiguates duplicate customer names using Account Number or Postal Code.
2. **Step 2: Customer 360 Verification** (`get_customer_360`): Surfaces active subscriptions and open tickets upon verification.
3. **Step 3: Line Diagnostics** (`get_service_diagnostics`): Runs real-time ONT or 5G telemetry (dBm attenuation, packet loss).
4. **Step 4: Device Remediation** (`restart_ont_modem` / `reprovision_esim_profile`): Triggers remote reboot only after customer gives consent.
5. **Step 5: Billing & Goodwill Credits** (`calculate_billing_breakdown` / `apply_goodwill_credit`): Audits charges and issues courtesy credits.
6. **Step 6: CRM Call Wrap-up** (`log_interaction_crm`): Submits structured call notes and resolution status to the database.

#### How to Add This Skill to Gemini Enterprise:
1. In the **Gemini Enterprise Admin Console**, select your Agent / App.
2. Navigate to **Agent Configuration / System Instructions / Playbooks**.
3. Copy the contents of [`skills/telecom_customer_care_agent/SKILL.md`](skills/telecom_customer_care_agent/SKILL.md) and paste it into the **System Instructions** or **Agent Playbook** editor.
4. Save the configuration. Gemini Enterprise will now automatically govern all 8 FastMCP tools using this interactive turn-by-turn protocol.

---

### 8.2 Google ADK Agent Deployment & Gemini Enterprise Registration (GCP Agent Runtime)

The repository provides a dedicated, enterprise-grade AI agent built with the **Google Agent Development Kit (ADK)** located in [`telecom-agent/`](telecom-agent/). The agent interfaces directly with the 8 FastMCP telecom tools and is architected for deployment to **Google Cloud Agent Runtime (Vertex AI Reasoning Engines)** and seamless publication into the **Gemini Enterprise App**.

#### Architectural & Operational Principles:

1. **Strict MCP Tool Boundary Enforcement**:
   - The agent operates exclusively across the 8 FastMCP telecom tools (`search_customer`, `get_customer_360`, `get_service_diagnostics`, `run_remote_device_action`, `check_network_outages`, `get_billing_breakdown`, `get_upsell_recommendations`, `log_agent_interaction`).
   - If an end user asks questions or makes requests outside telecom customer care, diagnostics, billing, network outages, or device remediation, the agent deterministically refuses:
     > *"I am dedicated exclusively to telecom customer care, diagnostics, billing, network outages, and subscriber operations. This requested functionality is not available."*
2. **Hybrid Cloud / On-Premises Topology**:
   - **ADK Agent**: Deployed **always on GCP** on Vertex AI Reasoning Engines (Agent Runtime).
   - **MCP Server & Support Tier**: Can reside either on **Google Cloud Run** or **On-Premises on a Proxmox VE server** (connected via Cloud VPN, Interconnect, or Secure API Gateway).
3. **Identity & Compliance Header Propagation**:
   - The ADK agent does **NOT** send metrics or logs directly to Prometheus or Loki; it uses GCP-native observability (Cloud Trace and Vertex AI Reasoning Engine monitoring).
   - When invoking MCP tools, the ADK agent dynamically enriches HTTP headers with critical audit context:
     - `X-Goog-Authenticated-User-Email`: Propagates the Gemini Enterprise authenticated user identity (e.g. `agent@enterprise.com`).
     - `X-Agent-Identity: telecom-customer-care-agent`: Identifies the calling agent framework.
     - `X-Cloud-Trace-Context`: Distributed trace identifier correlating the reasoning step with backend database calls.
   - The MCP server receives these headers, logs full request bodies, headers, and responses to **Grafana Loki**, and increments execution counters in **Prometheus**, maintaining an uncompromised audit trail for compliance officers.

---

#### 8.2.1 Installation & Local Agent Environment Setup

Prerequisites:
- Python 3.11+
- `uv` package manager (or `pip`)
- `agents-cli` installed and authenticated (`gcloud auth application-default login`)

```bash
# 1. Navigate to the agent directory
cd telecom-agent

# 2. Sync dependencies using uv (or pip)
uv sync

# 3. Configure runtime environment
# Create a local .env file or export variables:
export MCP_DEPLOYMENT_TARGET="cloud_run"       # Options: "cloud_run" or "on_prem"
export MCP_SERVER_URL="http://localhost:8001"   # Local gateway or Cloud Run URL
export MCP_BEARER_TOKEN="telecom-mcp-secret-token-change-in-prod-xyz987"
export GCP_PROJECT_ID="pradeesi-ai-demo"
export GCP_REGION="europe-west1"
export MODEL_NAME="gemini-2.5-flash"
```

---

#### 8.2.2 Running Automated Unit & Eval Tests

Run the unit test suite covering tool definitions, scope enforcement, and MCP client initialization:
```bash
cd telecom-agent
python3 -m pytest tests/unit/test_agent.py -v
```

All unit tests verify:
- ✅ Successful loading and schema conversion of the 8 MCP tools into the ADK runtime.
- ✅ Correct model parameterization (`gemini-2.5-flash`).
- ✅ Strict system instruction boundary prompt presence.
- ✅ Custom HTTP header propagation (`X-Goog-Authenticated-User-Email`, `X-Agent-Identity`).

---

#### 8.2.3 Local Interactive Testing with Agents CLI Playground

Before deploying to Google Cloud, launch the interactive web playground to simulate user conversations, inspect reasoning traces, and verify tool execution:

```bash
cd telecom-agent
agents-cli playground
```

1. Open the URL displayed in the terminal (default: `http://localhost:8080` or `http://localhost:8085`).
2. **Test In-Scope Telecom Scenario**:
   - Prompt: *"Customer with phone +1 (555) 234-5678 is experiencing optical line attenuation and high packet loss. Can you check diagnostics and reboot their ONT?"*
   - Verify: The agent invokes `search_customer`, runs `get_service_diagnostics`, detects `-28.5 dBm`, calls `run_remote_device_action(action="reboot")`, and presents the structured 3-part contact center card.
3. **Test Out-of-Scope Refusal**:
   - Prompt: *"Write a Python script to calculate Fibonacci numbers."* or *"What is the capital of France?"*
   - Verify: The agent immediately returns the mandatory scope refusal message without hallucinating or running arbitrary tools.

---

#### 8.2.4 Deploying to GCP Agent Runtime (Vertex AI Reasoning Engines)

Deploy the ADK agent container and reasoning engine to Google Cloud:

##### Scenario A: Connecting to Cloud Run MCP Backend
```bash
cd telecom-agent

agents-cli deploy \
  --deployment-target agent_runtime \
  --project-id pradeesi-ai-demo \
  --region europe-west1 \
  --env-var MCP_DEPLOYMENT_TARGET=cloud_run \
  --env-var MCP_SERVER_URL=https://telecom-mcp-server-1111937452.europe-west1.run.app \
  --env-var MCP_BEARER_TOKEN=telecom-mcp-secret-token-change-in-prod-xyz987 \
  --env-var MODEL_NAME=gemini-2.5-flash
```

##### Scenario B: Connecting to On-Premises Proxmox MCP Backend
If the MCP server, PostgreSQL, and Loki/Prometheus stack are running on-premises in a Proxmox VE private cluster:
```bash
cd telecom-agent

agents-cli deploy \
  --deployment-target agent_runtime \
  --project-id pradeesi-ai-demo \
  --region europe-west1 \
  --env-var MCP_DEPLOYMENT_TARGET=on_prem \
  --env-var MCP_SERVER_URL=https://mcp-gateway.onprem.yourdomain.com \
  --env-var MCP_BEARER_TOKEN=telecom-mcp-secret-token-change-in-prod-xyz987 \
  --env-var MODEL_NAME=gemini-2.5-flash
```

---

#### 8.2.5 Registering & Publishing to the Gemini Enterprise App

Once deployed to Agent Runtime, publish the agent so that frontline contact center specialists can invoke it directly within **Gemini Enterprise**:

```bash
cd telecom-agent

# Publish the ADK agent into Gemini Enterprise App Catalog
agents-cli publish gemini-enterprise \
  --name "Telecom Care Co-Pilot" \
  --description "Enterprise telecom agent for subscriber search, real-time ONT signal diagnostics, remote hardware remediation, billing dispute breakdown, and network outage triage." \
  --region europe-west1
```

Frontline contact center agents authenticated via **Google Workspace / Cloud Identity** will now see the "Telecom Care Co-Pilot" in their Gemini Enterprise interface.

---

#### 8.2.6 Audit Trail & Telemetry Verification in Grafana Loki

When the deployed ADK agent or Gemini Enterprise executes a tool, the compliance and security team can inspect the full end-to-end audit trail in Grafana Loki:

1. Open Grafana: 👉 [http://localhost:3000](http://localhost:3000) (or your Cloud Run / On-prem Grafana URL).
2. Navigate to **Explore** &rarr; select **Loki** datasource.
3. Run the following LogQL query:
   ```logql
   {app="telecom-mcp-server"} |= "MCP_TOOL_EXECUTION"
   ```
4. Expand any log entry to verify:
   - `caller`: Authenticated identity (`agent@yourdomain.com`).
   - `caller_type`: `HUMAN_AGENT` or `SERVICE_AGENT`.
   - `trace_id`: Correlating GCP distributed trace ID.
   - `request`: Full tool input arguments (e.g. `phone_number`, `action`).
   - `response`: Full sanitized tool output payload with masked customer PII.

---

## 9. Complete Environment Teardown & Targeted Cleanup Playbooks

> [!CAUTION]
> **Strict Isolation & Safety Guarantee**:
> The cleanup commands below are explicitly namespaced and targeted **only** to the services, databases, and containers created for this demo (`telecom-*`, `hybrid-ai-*`). They will **never** delete, modify, or impact any other applications, clusters, databases, or storage in your Google Cloud project or home lab.

---

### Teardown A: Local Standalone Python (Scoped to HybridAI Folder)
*Cleans up only the virtual environment and local database file within this directory:*
```bash
cd HybridAI

# 1. Stop background processes (Ctrl+C in terminal windows)

# 2. Deactivate and remove the project virtual environment
deactivate 2>/dev/null || true
rm -rf .venv/

# 3. Clean up local demo SQLite database files and caches
rm -f telecom_local.db* test_telecom.db*
find src tests -type d -name "__pycache__" -exec rm -rf {} +
```

---

### Teardown B: Home Lab / Proxmox (Docker Compose)
*Stops and purges only the 6 demo containers and their associated storage volumes, without affecting any other Docker containers running on the host:*
```bash
cd HybridAI

# 1. Stop and purge only the demo containers, internal network, and demo volumes:
docker compose down -v --rmi local --remove-orphans

# 2. (Optional) ONLY if you provisioned a dedicated Proxmox VM (e.g. VM ID 105) or LXC container (e.g. CT ID 200) exclusively for this demo:
# qm stop 105 && qm destroy 105
# pct stop 200 && pct destroy 200
```

---

### Teardown C: Google Cloud Run & Cloud SQL (Targeted GCP Cleanup)
*Deletes strictly the demo services created in Playbook C. Other Cloud Run services, Cloud SQL instances, or Artifact Registry repos in your GCP project are completely untouched:*
```bash
export PROJECT_ID="pradeesi-ai-demo"
export REGION="europe-west1"

# 1. Delete all 5 demo Cloud Run services:
for SVC in telecom-crm-console telecom-mcp-server telecom-grafana telecom-prometheus telecom-loki; do
    gcloud run services delete ${SVC} \
        --region=${REGION} \
        --project=${PROJECT_ID} \
        --quiet || true
done

# 2. Delete ONLY the demo Cloud SQL instance:
gcloud sql instances delete telecom-pg-instance \
    --project=${PROJECT_ID} \
    --quiet

# 3. Delete ONLY the demo Artifact Registry Docker repository:
gcloud artifacts repositories delete hybrid-ai-repo \
    --location=${REGION} \
    --project=${PROJECT_ID} \
    --quiet

# 4. Delete ONLY the demo Secret if created in Secret Manager:
gcloud secrets delete telecom-secrets \
    --project=${PROJECT_ID} \
    --quiet 2>/dev/null || true
```
