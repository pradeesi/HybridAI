<!--
  Purpose: Primary documentation, architectural specification, local execution instructions, and cloud/on-prem deployment playbooks for the Telecom Customer Care Hybrid AI Platform.
  Architecture/Context: Central guide for developers, AI engineers, and operators deploying the FastMCP server, CRM agent console, and observability stack across any environment.
  Dependencies/Side Effects: Governs environment configuration and multi-target deployment (Bare-Metal Python, Docker Compose, Cloud Run, GKE).
-->

# Telecom Customer Care Hybrid AI Platform & Secure FastMCP Server

An enterprise-grade, containerized Telecom Customer Care platform replicating real-world telecommunications frontline operations (Home Broadband, Mobile 5G, Billing Disputes, Line Diagnostics, and Proactive Upselling). 

Exposes a hardened **FastMCP (Server-Sent Events)** server consumed by the **Google Gemini Enterprise App (GE App)** to assist human call center agents in real time while minimizing Average Handle Time (AHT).

Built with **Python 3.11+ (FastAPI)**, **Jinja2**, **HTML5**, **locally stored Bootstrap 5** (zero external CDN dependencies), **PostgreSQL 16** (with automatic local SQLite fallback), and a full observability suite (**Prometheus**, **Grafana Loki**, and **Grafana**).

---

## 1. Application Architecture

The platform supports a synchronized **Dual-Pane Agent Workflow**:

1. **Telecom CRM Agent Console**: Frontline desktop web interface displaying subscriber 360 profiles, live equipment telemetry (optical Rx power dBm, Wi-Fi interference, packet loss), billing history, and one-click remote diagnostic triggers.
2. **Google Gemini Enterprise App (GE App)**: Interacts with the **FastMCP Server** over an encrypted SSE connection. It queries subscriber diagnostics, checks area outages, computes personalized upgrade pitches, and analyzes billing disputes.

```mermaid
graph TD
    subgraph AgentWorkstation["Call Center Executive Workstation"]
        Agent["Human Frontline Agent (Voice / Screen)"]
        CRM_UI["Telecom CRM Console (Port 8000)"]
        GE_App["Google Gemini Enterprise App"]
    end

    subgraph PlatformPerimeter["Containerized Platform Boundary (Edge / Cloud)"]
        AuthGuard["Bearer Token Security & PII Redactor"]
        
        subgraph AppServices["Application Tier"]
            MCP_Server["FastMCP Server (Port 8001 / SSE & JSON-RPC)"]
            CRM_Backend["CRM Application Service"]
        end

        subgraph DataTier["Persistence Tier"]
            PG[(PostgreSQL 16 Telecom Relational DB / SQLite Fallback)]
        end

        subgraph TelemetryTier["Observability & Compliance Tier"]
            Prometheus["Prometheus 2.50+ (Port 9090)"]
            Loki["Grafana Loki 3.0+ (Port 3100)"]
            Grafana["Grafana 10.4+ (Port 3000)"]
        end
    end

    Agent -->|Navigates Customer Profile| CRM_UI
    Agent -->|Queries AI Assistant| GE_App
    CRM_UI --> CRM_Backend
    GE_App -->|SSE /tools/call + Bearer Auth| AuthGuard
    AuthGuard --> MCP_Server
    
    MCP_Server -->|Query & Device Actions| PG
    CRM_Backend -->|Query & Notes| PG
    
    MCP_Server -->|Structured Audit Trail| Loki
    CRM_Backend -->|Audit Trail| Loki
    MCP_Server -->|Metrics /metrics| Prometheus
    CRM_Backend -->|Metrics /metrics| Prometheus
    Prometheus --> Grafana
    Loki --> Grafana
```

### Architectural & Security Pillars
- **Zero-Trust Token Authentication**: All incoming MCP SSE streams and tool executions must present an `Authorization: Bearer <MCP_AUTH_TOKEN>` header.
- **Dynamic PII Masking & PCI Compliance**: Phone numbers, Social Security Numbers, physical addresses, and payment card numbers are automatically redacted before LLM context ingestion.
- **Immutable Audit Logging**: Every tool call (caller identity, tool parameters, accessed customer IDs, execution latency, and security status) is streamed to Grafana Loki.
- **Air-Gapped / Zero CDN Dependency**: Bootstrap 5 CSS, Bootstrap Icons, and JavaScript are vendored locally in `src/crm/static/` to ensure zero third-party tracking or CDN outages.
- **Universal Portability**: 100% environment-driven configuration—deployable with zero changes across standalone Python environments, local Docker, on-prem bare-metal hypervisors, and Google Cloud.

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
│   ├── grafana/
│   │   ├── dashboards/
│   │   │   ├── telecom_mcp_security.json      # Live audit stream, PII counters, and auth failure dashboard
│   │   │   └── telecom_operations.json        # AHT optimization, agent queue, and tool latency metrics
│   │   └── provisioning/
│   │       ├── dashboards/dashboards.yml      # Automated Grafana dashboard provider definition
│   │       └── datasources/datasources.yml    # Auto-wired Prometheus and Loki datasources
│   ├── loki/
│   │   └── loki-config.yml                    # Grafana Loki 3.0 TSDB storage & HTTP push configuration
│   └── prometheus/
│       └── prometheus.yml                     # 5s scrape configs for MCP server and CRM app
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
└── tests/
    └── test_telecom_mcp.py                    # Automated test suite (PII, Auth, DB seeding, MCP tools)
```

---

## 3. Frameworks & Libraries

- **Language & Runtime**: Python 3.11+
- **API & Web Framework**: [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) (High-performance ASGI)
- **Data Persistence**: [SQLAlchemy 2.0 Async](https://www.sqlalchemy.org/) + [asyncpg](https://github.com/MagicStack/asyncpg) + [aiosqlite](https://github.com/omnilib/aiosqlite)
- **Protocol**: FastMCP Server implementing [Model Context Protocol](https://modelcontextprotocol.io/) via Server-Sent Events (SSE)
- **Frontend & Styling**: Jinja2 HTML5 + Bootstrap 5.3.3 + Bootstrap Icons 1.11.3 (100% offline, locally stored)
- **Observability**: [Prometheus Client](https://github.com/prometheus/client_python), [Grafana Loki](https://grafana.com/oss/loki/), and [Grafana](https://grafana.com/)
- **Data Validation & Security**: Pydantic v2 Settings, Cryptography, Secrets

---

## 4. Environment Variables

All settings are externalized and dynamically loaded from the environment or `.env` file.

| Variable Name | Data Type | Required | Default Value | Description | Example |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `APP_ENV` | `string` | No | `development` | Deployment environment mode (`development`, `staging`, `production`) | `production` |
| `LOG_LEVEL` | `string` | No | `INFO` | Logging threshold (`DEBUG`, `INFO`, `WARN`, `ERROR`) | `INFO` |
| `CRM_PORT` | `integer` | No | `8000` | HTTP port for the Customer Care CRM Console | `8000` |
| `MCP_PORT` | `integer` | No | `8001` | HTTP & SSE port for the FastMCP Server | `8001` |
| `MCP_AUTH_TOKEN` | `string` | **Yes** | `telecom-mcp-secret-token-change-in-prod-xyz987` | Secret Bearer token required by Gemini Enterprise App | `telecom-sec-xyz-987` |
| `DATABASE_URL` | `string` | No | Auto-detected | Database connection string (PostgreSQL or automatic SQLite fallback) | `postgresql+asyncpg://user:pass@host:5432/db` |
| `POSTGRES_USER` | `string` | No | `telecom_user` | PostgreSQL superuser username | `telecom_user` |
| `POSTGRES_PASSWORD` | `string` | **Yes** | `telecom_secure_pass` | PostgreSQL superuser password | `SuperSecretPass123!` |
| `POSTGRES_DB` | `string` | No | `telecom_db` | Primary database name | `telecom_db` |
| `LOKI_URL` | `string` | No | `http://localhost:3100` | HTTP push endpoint for Grafana Loki log ingestion | `http://localhost:3100` |
| `GRAFANA_ADMIN_USER` | `string` | No | `admin` | Initial administrator username for Grafana | `admin` |
| `GRAFANA_ADMIN_PASSWORD` | `string` | **Yes** | `telecom_admin` | Initial administrator password for Grafana | `GrafanaPass456!` |

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

### Playbook B: Home Lab & Bare-Metal Edge Server (Docker Compose)

*Ideal for self-hosted production in a home lab (e.g. Proxmox LXC container or VM on an x86 host) with complete observability. **Docker Compose takes care of 100% of the installation, provisioning, and pre-wiring for PostgreSQL, Grafana, Prometheus, and Loki automatically—no manual package installation is required on the host.***

#### What Docker Compose Installs & Configures Automatically:
- **PostgreSQL 16 (`postgres:16-alpine`)**: Containerized database engine with persistent volume (`pg_data`) and automatic synthetic data bootstrapping.
- **Prometheus 2.50+ (`prom/prometheus:v2.50.1`)**: Automated metrics scraper pre-configured with [`deploy/prometheus/prometheus.yml`](deploy/prometheus/prometheus.yml).
- **Grafana Loki 3.0+ (`grafana/loki:3.0.0`)**: Centralized TSDB log engine pre-configured with [`deploy/loki/loki-config.yml`](deploy/loki/loki-config.yml).
- **Grafana 10.4+ (`grafana/grafana:10.4.0`)**: Pre-provisioned with Prometheus & Loki datasources and auto-loaded Security Audit & Call Center Ops dashboards.

---

#### Step-by-Step Instructions for Proxmox VE (x86 Bare-Metal Host):

1. **Step 1: Provision an Environment in Proxmox VE**
   *You can choose either a lightweight LXC container (fastest, lowest overhead) or a standard KVM Virtual Machine:*

   - **Option A: Lightweight LXC Container (Recommended)**:
     1. Open your Proxmox VE Web GUI (`https://<proxmox-ip>:8006`).
     2. Click **Create CT** (top right):
        - **General**: Set Hostname (e.g., `telecom-hybrid-ai`), uncheck *Unprivileged container* (or keep unprivileged with nesting enabled).
        - **Template**: Select `ubuntu-22.04-standard` (or `debian-12-standard`).
        - **Disks**: Allocate `30 GB` or more on your fast storage pool.
        - **CPU**: Allocate `4 Cores`.
        - **Memory**: Allocate `8192 MB` (8 GB RAM) and `1024 MB` swap.
        - **Network**: Bridge `vmbr0`, IPv4: DHCP or Static IP.
     3. **Crucial Docker Setting for Proxmox LXC**:
        - Before starting the container, click on the newly created CT -> **Options** -> double-click **Features** -> Check **Nesting** (`nesting=1`) and **keyctl** (`keyctl=1`).
        - Click **OK**, then click **Start**.

     *(Alternative: Run directly from the Proxmox Host Root Shell):*
     ```bash
     pct create 200 local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst \
       --hostname telecom-hybrid-ai \
       --cores 4 \
       --memory 8192 \
       --rootfs local-lvm:30 \
       --net0 name=eth0,bridge=vmbr0,ip=dhcp \
       --features nesting=1,keyctl=1 \
       --start 1
     ```

   - **Option B: Standard QEMU/KVM Virtual Machine**:
     - Click **Create VM**: Assign 4 vCPUs, 8 GB RAM, 32 GB SSD, and install Ubuntu Server 22.04 or Debian 12.

2. **Step 2: Install Docker Engine & Git Inside the Guest (LXC / VM)**:
   *Open the Proxmox Console for your container/VM and run:*
   ```bash
   apt update && apt install -y docker.io docker-compose-v2 git curl
   systemctl enable --now docker
   ```

3. **Step 3: (Recommended) Mesh VPN Configuration (Tailscale)**:
   *Gives your node a secure, private encrypted IP reachable from your laptop or cloud without opening any ports on your home router:*
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   tailscale up
   # Note your private node IP:
   tailscale ip -4
   ```

4. **Step 4: Clone & Launch Complete Platform**:
   ```bash
   git clone https://github.com/pradeesi/HybridAI.git
   cd HybridAI
   cp .env.example .env
   # Launch all 6 services with one command:
   docker compose up -d --build
   ```

4. **Verify All 6 Services are Healthy:**
   ```bash
   docker compose ps
   ```
   *Expected output: `telecom-postgres` (healthy), `telecom-mcp-server` (healthy), `telecom-crm-console` (healthy), `telecom-prometheus` (running), `telecom-loki` (running), `telecom-grafana` (running).*

5. **Access Pre-Wired Endpoints:**
   - **CRM Frontline Console**: `http://<tailscale-ip-or-host>:8000`
   - **FastMCP Server (SSE)**: `http://<tailscale-ip-or-host>:8001/sse`
   - **Grafana Security & Ops Dashboards**: `http://<tailscale-ip-or-host>:3000` (User: `admin` / Password: value from `.env` — Dashboards are pre-loaded!)
   - **Prometheus Metrics**: `http://<tailscale-ip-or-host>:9090`
   - **Loki Log API**: `http://<tailscale-ip-or-host>:3100`

6. **Managing the Stack:**
   ```bash
   # View unified live logs:
   docker compose logs -f mcp-server crm-app
   
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

#### 2. Provision Managed Database (Google Cloud SQL for PostgreSQL):
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

# 3. Retrieve the Cloud SQL Instance Connection Name
INSTANCE_CONNECTION_NAME=$(gcloud sql instances describe telecom-pg-instance --format='value(connectionName)')
echo "Cloud SQL Connection Name: ${INSTANCE_CONNECTION_NAME}"
```

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

# 3. Build and submit container image via Cloud Build (must be run from inside HybridAI folder)
gcloud builds submit --tag ${REGION}-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/hybrid-ai:latest .
```

#### 4. Deploy FastMCP Server to Cloud Run:
```bash
gcloud run deploy telecom-mcp-server \
    --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/hybrid-ai:latest \
    --platform managed \
    --region ${REGION} \
    --command "python3,-m,src.mcp.server" \
    --port 8001 \
    --add-cloudsql-instances ${INSTANCE_CONNECTION_NAME} \
    --set-env-vars APP_ENV=production,MCP_PORT=8001,MCP_AUTH_TOKEN="telecom-mcp-secret-token-change-in-prod-xyz987",DATABASE_URL="postgresql+asyncpg://telecom_user:telecom_secure_pass@/telecom_db?host=/cloudsql/${INSTANCE_CONNECTION_NAME}" \
    --allow-unauthenticated
```
*Note the generated HTTPS URL (e.g. `https://telecom-mcp-server-xyz-uc.a.run.app`). The SSE endpoint will be `https://telecom-mcp-server-xyz-uc.a.run.app/sse`.*

#### 5. Deploy CRM Agent Console to Cloud Run:
```bash
gcloud run deploy telecom-crm-console \
    --image ${REGION}-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/hybrid-ai:latest \
    --platform managed \
    --region ${REGION} \
    --command "python3,-m,src.crm.app" \
    --port 8000 \
    --add-cloudsql-instances ${INSTANCE_CONNECTION_NAME} \
    --set-env-vars APP_ENV=production,CRM_PORT=8000,MCP_PORT=8001,DATABASE_URL="postgresql+asyncpg://telecom_user:telecom_secure_pass@/telecom_db?host=/cloudsql/${INSTANCE_CONNECTION_NAME}" \
    --allow-unauthenticated
```

---

### Playbook D: Google Kubernetes Engine (GKE)

*Ideal for enterprise Kubernetes clusters with automated pod scaling, rolling updates, and internal load balancing.*

#### 1. Enable GKE API & Provision Cluster:
```bash
export PROJECT_ID="your-gcp-project-id"
export REGION="us-central1"
export CLUSTER_NAME="telecom-hybrid-cluster"

gcloud services enable container.googleapis.com

# Create a modern GKE Autopilot cluster
gcloud container clusters create-auto ${CLUSTER_NAME} \
    --region ${REGION} \
    --project ${PROJECT_ID}

# Connect kubectl to the cluster
gcloud container clusters get-credentials ${CLUSTER_NAME} --region ${REGION} --project ${PROJECT_ID}
```

#### 2. Deploy Database & Observability Stack via Helm:
```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

# 1. Install PostgreSQL in the cluster
helm install telecom-pg bitnami/postgresql \
    --set auth.username=telecom_user \
    --set auth.password=telecom_secure_pass \
    --set auth.database=telecom_db

# 2. Install Prometheus & Grafana stack
helm install telecom-prom prometheus-community/kube-prometheus-stack

# 3. Install Grafana Loki log aggregator
helm install telecom-loki grafana/loki-stack
```

#### 3. Create Secrets & Deploy Application Workloads:
```bash
# 1. Create Kubernetes Secret with credentials
kubectl create secret generic telecom-secrets \
    --from-literal=mcp-token="telecom-mcp-secret-token-change-in-prod-xyz987" \
    --from-literal=database-url="postgresql+asyncpg://telecom_user:telecom_secure_pass@telecom-pg-postgresql.default.svc.cluster.local:5432/telecom_db"

# 2. Update image in deployment manifest to your Artifact Registry tag and apply
IMAGE_PATH="${REGION}-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/hybrid-ai:latest"
sed -i "s|image: telecom-hybrid-ai:latest|image: ${IMAGE_PATH}|g" deploy/k8s/deployment.yaml

kubectl apply -f deploy/k8s/deployment.yaml
```

#### 4. Access Services on GKE:
```bash
# Check status of pods and external load balancer service:
kubectl get pods
kubectl get svc telecom-crm-service

# Forward Grafana port to view dashboards locally:
kubectl port-forward svc/telecom-prom-grafana 3000:80
```

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

To connect the **Google Gemini Enterprise App** to this FastMCP server:

1. In the Gemini Enterprise App Admin / Settings Console, navigate to **Agent Tools / External MCP Servers**.
2. Add a new **MCP SSE Server**:
   - **Transport**: `Server-Sent Events (SSE)`
   - **Server URL**: `http://<your-host-or-tailscale-ip>:8001/sse`
   - **HTTP Headers**:
     ```http
     Authorization: Bearer <your-mcp-auth-token>
     ```
3. The following 8 tools will be discovered automatically:
   - `search_customer`: Quick phone/name subscriber lookup.
   - `get_customer_360`: Full profile with PII masked (address, SSN, phone).
   - `get_service_diagnostics`: Real-time ONT optical dBm signal & 5G telemetry.
   - `run_remote_device_action`: Reboot ONT or optimize Wi-Fi channels remotely.
   - `check_network_outages`: Check for area fiber cuts or tower repairs by postal code.
   - `get_billing_breakdown`: Line-item charges, roaming fees, and dispute notes.
   - `get_upsell_recommendations`: Automated Gigabit / 5G pass pitch scripts.
   - `log_agent_interaction`: Saves call summary, duration, and resolution to CRM.

### Sample Prompts for Human Call Center Agent with GE App:
- *"Elena Rostova is calling about frequent buffering and slow Wi-Fi. Check her line diagnostics and run any necessary remote action."*
- *"Marcus Vance says his mobile internet is suddenly crawling. Check his 5G subscription and tell me what plan booster we can pitch to fix it."*
- *"Amina Al-Mansoor is disputing a roaming charge from London. What charges were billed and what travel pass should I offer her?"*
- *"David Chen is happy with his service. Does his usage pattern qualify him for a fiber speed tier upgrade?"*

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

# 2. (Optional) ONLY if you provisioned a dedicated Proxmox LXC container (e.g. CT ID 200) exclusively for this demo:
# pct stop 200 && pct destroy 200
```

---

### Teardown C: Google Cloud Run & Cloud SQL (Targeted GCP Cleanup)
*Deletes strictly the demo services created in Playbook C. Other Cloud Run services, Cloud SQL instances, or Artifact Registry repos in your GCP project are completely untouched:*
```bash
export PROJECT_ID="pradeesi-ai-demo"
export REGION="europe-west1"

# 1. Delete ONLY the two demo Cloud Run services:
gcloud run services delete telecom-mcp-server \
    --region=${REGION} \
    --project=${PROJECT_ID} \
    --quiet

gcloud run services delete telecom-crm-console \
    --region=${REGION} \
    --project=${PROJECT_ID} \
    --quiet

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

---

### Teardown D: Google Kubernetes Engine (GKE) (Scoped to Demo Resources)
*Deletes only the demo pods, services, secrets, and Helm releases. Other workloads and namespaces in your cluster remain intact:*
```bash
# 1. Delete ONLY the demo application deployments and services:
kubectl delete -f deploy/k8s/deployment.yaml --ignore-not-found
kubectl delete secret telecom-secrets --ignore-not-found

# 2. Uninstall ONLY the demo Helm releases:
helm uninstall telecom-pg 2>/dev/null || true
helm uninstall telecom-prom 2>/dev/null || true
helm uninstall telecom-loki 2>/dev/null || true

# 3. Delete ONLY the persistent volume claims created by the demo releases:
kubectl delete pvc -l app.kubernetes.io/instance=telecom-pg --ignore-not-found
kubectl delete pvc -l app.kubernetes.io/instance=telecom-prom --ignore-not-found
kubectl delete pvc -l app.kubernetes.io/instance=telecom-loki --ignore-not-found

# 4. Delete ONLY the demo Artifact Registry repository:
gcloud artifacts repositories delete hybrid-ai-repo \
    --location=${REGION} \
    --project=${PROJECT_ID} \
    --quiet

# 5. (OPTIONAL) ONLY if you created a dedicated GKE cluster exclusively for this demo:
# gcloud container clusters delete telecom-hybrid-cluster --region=${REGION} --project=${PROJECT_ID} --quiet
```
