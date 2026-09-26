<!--
  Purpose: Primary documentation, architectural specification, local execution instructions, and cloud/on-prem deployment playbooks for the Telecom Customer Care Hybrid AI Platform.
  Architecture/Context: Central guide for developers, AI engineers, and operators deploying the FastMCP server, CRM agent console, and observability stack.
  Dependencies/Side Effects: Governs environment configuration and containerized deployment across edge hypervisors and public clouds.
-->

# Telecom Customer Care Hybrid AI Platform & Secure FastMCP Server

An enterprise-grade, containerized Telecom Customer Care intelligence platform. Replicates modern telecommunications frontline operations (Home Broadband, Mobile 5G, Billing, Line Diagnostics, and Upsell Offers) and exposes a hardened **FastMCP (Server-Sent Events)** server consumed by the **Google Gemini Enterprise App (GE App)** to assist human call center agents in real time while minimizing Average Handle Time (AHT).

Built with **Python 3.11+ (FastAPI)**, **Jinja2**, **HTML5**, **locally stored Bootstrap 5** (zero external CDN dependencies), **PostgreSQL 16**, and an observability suite powered by **Prometheus**, **Grafana Loki**, and **Grafana**.

---

## 1. Application Architecture

The platform is designed around a **Dual-Pane Agent Workflow**:

1. **Telecom CRM Agent Console**: A desktop web interface displaying subscriber 360 profiles, live equipment telemetry (optical Rx power dBm, Wi-Fi congestion, packet loss), billing history, and one-click remote diagnostic triggers.
2. **Google Gemini Enterprise App (Copilot)**: Interacts seamlessly with the **FastMCP Server** over an encrypted SSE connection. It queries subscriber diagnostics, checks area outages, computes personalized upgrade pitches, and analyzes billing disputes.

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
            PG[(PostgreSQL 16 Telecom Relational DB)]
        end

        subgraph TelemetryTier["Observability & Compliance Tier"]
            Prometheus["Prometheus 2.50+ (Port 9090)"]
            Loki["Grafana Loki 3.0+ (Port 3100)"]
            Grafana["Grafana 10.4+ (Port 3000)"]
        end
    end

    Agent -->|Navigates Customer Profile| CRM_UI
    Agent -->|Queries AI Copilot| GE_App
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
- **Dynamic PII Masking & Sanitization**: Phone numbers, Social Security Numbers, physical addresses, and payment card numbers are automatically redacted before LLM context ingestion.
- **Immutable Audit Logging**: Every tool call (caller identity, tool parameters, accessed customer IDs, execution latency, and security status) is streamed to Grafana Loki.
- **Air-Gapped / Zero CDN Dependency**: Bootstrap 5 CSS, Bootstrap Icons, and JavaScript are vendored locally in `src/crm/static/` to ensure zero third-party tracking or CDN outages.
- **Universal Portability**: 100% containerized (OCI/Docker) with environment-driven configuration—deployable interchangeably on on-premise bare-metal hypervisors (Proxmox/x86 host) or Google Cloud (Cloud Run / GKE).

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
│           └── dashboard.html                 # Queue view, active outages banner, and GE Copilot helper
└── tests/
    └── test_telecom_mcp.py                    # Automated test suite (PII, Auth, DB seeding, MCP tools)
```

---

## 3. Frameworks & Libraries

- **Language & Runtime**: Python 3.11+
- **API & Web Framework**: [FastAPI](https://fastapi.tiangolo.com/) + [Uvicorn](https://www.uvicorn.org/) (High-performance ASGI)
- **Data Persistence**: [SQLAlchemy 2.0 Async](https://www.sqlalchemy.org/) + [asyncpg](https://github.com/MagicStack/asyncpg) + [aiosqlite](https://github.com/omnilib/aiosqlite)
- **Protocol**: FastMCP Server implementing [Model Context Protocol](https://modelcontextprotocol.io/) via Server-Sent Events (SSE)
- **Frontend & Styling**: Jinja2 HTML5 + Bootstrap 5.3.3 + Bootstrap Icons 1.11.3 (stored locally)
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
| `MCP_AUTH_TOKEN` | `string` | **Yes** | None (set in `.env`) | Secret Bearer token required by Gemini Enterprise App | `telecom-sec-xyz-987` |
| `DATABASE_URL` | `string` | No | Auto-configured in Compose | Asynchronous database connection string (PostgreSQL or SQLite) | `postgresql+asyncpg://user:pass@host:5432/db` |
| `POSTGRES_USER` | `string` | No | `telecom_user` | PostgreSQL superuser username | `telecom_user` |
| `POSTGRES_PASSWORD` | `string` | **Yes** | `telecom_secure_pass` | PostgreSQL superuser password | `SuperSecretPass123!` |
| `POSTGRES_DB` | `string` | No | `telecom_db` | Primary database name | `telecom_db` |
| `LOKI_URL` | `string` | No | `http://loki:3100` | HTTP push endpoint for Grafana Loki log ingestion | `http://localhost:3100` |
| `GRAFANA_ADMIN_USER` | `string` | No | `admin` | Initial administrator username for Grafana | `admin` |
| `GRAFANA_ADMIN_PASSWORD` | `string` | **Yes** | `telecom_admin` | Initial administrator password for Grafana | `GrafanaPass456!` |

---

## 5. No Hardcoding Rule

> [!IMPORTANT]
> **Strict Security Notice**: Absolutely zero sensitive data, database passwords, or API tokens are hardcoded within this repository. All credentials are injected via environment variables at container launch or read from a `.env` file (which is strictly excluded by `.gitignore`). Always use [`.env.example`](.env.example) as the template for environment configuration.

---

## 6. Local Execution Instructions

### Prerequisites
- Python 3.11+
- Git

### Quickstart (Standalone Mode with SQLite Fallback)

1. **Clone the repository and enter directory:**
   ```bash
   git clone https://github.com/pradeesi/HybridAI.git
   cd HybridAI
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env to set your desired MCP_AUTH_TOKEN and passwords
   ```

3. **Set up virtual environment and install dependencies:**
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. **Run the automated test suite:**
   ```bash
   python3 -m unittest discover tests/
   ```

5. **Start the FastMCP Server (Terminal 1):**
   ```bash
   python3 -m src.mcp.server
   # FastMCP Server listening on http://localhost:8001
   ```

6. **Start the CRM Agent Console (Terminal 2):**
   ```bash
   python3 -m src.crm.app
   # CRM Agent Console listening on http://localhost:8000
   ```

---

## 7. Home Lab & On-Prem Deployment Playbook (Docker Compose)

For bare-metal hypervisors (Proxmox LXC container or VM on an x86 host) or dedicated edge hardware:

1. **Provision Host Environment**:
   - Assign 4 vCPUs, 8 GB RAM, and 30 GB storage to an Ubuntu 22.04 or Debian 12 LXC / VM.
   - Install Docker Engine and the Docker Compose plugin.

2. **Configure Secure Remote Mesh Network (Tailscale / WireGuard)**:
   ```bash
   # Connect the host to your private mesh VPN
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   # Note the internal Tailscale IP: tailscale ip -4 (e.g. 100.x.y.z)
   ```

3. **Deploy the Complete Container Stack**:
   ```bash
   cd HybridAI
   cp .env.example .env
   docker compose up -d --build
   ```

4. **Verify Container Health**:
   ```bash
   docker compose ps
   ```

5. **Access Application & Observability Endpoints**:
   - **Telecom CRM Agent Console**: `http://<host-or-vpn-ip>:8000`
   - **FastMCP SSE Endpoint**: `http://<host-or-vpn-ip>:8001/sse`
   - **Grafana Dashboards**: `http://<host-or-vpn-ip>:3000` (User: `admin` / Password from `.env`)
   - **Prometheus UI**: `http://<host-or-vpn-ip>:9090`
   - **Loki Push API**: `http://<host-or-vpn-ip>:3100`

---

## 8. Cloud Deployment Playbooks

### Google Cloud Run

Google Cloud Run provides serverless, auto-scaling execution for containerized workloads.

1. **Authenticate and set Google Cloud project:**
   ```bash
   gcloud auth login
   gcloud config set project ${PROJECT_ID}
   ```

2. **Build and push image to Google Artifact Registry:**
   ```bash
   gcloud artifacts repositories create hybrid-ai-repo \
       --repository-format=docker \
       --location=us-central1

   gcloud builds submit --tag us-central1-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/hybrid-ai:latest .
   ```

3. **Deploy FastMCP Server to Cloud Run:**
   ```bash
   gcloud run deploy telecom-mcp-server \
       --image us-central1-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/hybrid-ai:latest \
       --platform managed \
       --region us-central1 \
       --command "python3,-m,src.mcp.server" \
       --port 8001 \
       --set-env-vars APP_ENV=production,MCP_PORT=8001,MCP_AUTH_TOKEN=${MCP_AUTH_TOKEN} \
       --allow-unauthenticated
   ```

4. **Deploy CRM Agent Console to Cloud Run:**
   ```bash
   gcloud run deploy telecom-crm-console \
       --image us-central1-docker.pkg.dev/${PROJECT_ID}/hybrid-ai-repo/hybrid-ai:latest \
       --platform managed \
       --region us-central1 \
       --command "python3,-m,src.crm.app" \
       --port 8000 \
       --set-env-vars APP_ENV=production,CRM_PORT=8000,MCP_PORT=8001 \
       --allow-unauthenticated
   ```

### Google Kubernetes Engine (GKE)

For orchestrating resilient enterprise clusters with internal ingress:

1. **Connect to GKE Cluster:**
   ```bash
   gcloud container clusters get-credentials ${CLUSTER_NAME} --region ${REGION} --project ${PROJECT_ID}
   ```

2. **Create Secret for Authentication Tokens:**
   ```bash
   kubectl create secret generic telecom-secrets \
       --from-literal=mcp-token="${MCP_AUTH_TOKEN}" \
       --from-literal=pg-password="${POSTGRES_PASSWORD}"
   ```

3. **Apply Kubernetes Deployment Manifest:**
   ```yaml
   # deploy/k8s-hybrid-ai.yaml
   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: telecom-mcp-server
     labels:
       app: telecom-mcp
   spec:
     replicas: 2
     selector:
       matchLabels:
         app: telecom-mcp
     template:
       metadata:
         labels:
           app: telecom-mcp
       spec:
         containers:
         - name: mcp-server
           image: us-central1-docker.pkg.dev/PROJECT_ID/hybrid-ai-repo/hybrid-ai:latest
           command: ["python3", "-m", "src.mcp.server"]
           ports:
           - containerPort: 8001
           env:
           - name: MCP_AUTH_TOKEN
             valueFrom:
               secretKeyRef:
                 name: telecom-secrets
                 key: mcp-token
           resources:
             requests:
               memory: "256Mi"
               cpu: "250m"
             limits:
               memory: "512Mi"
               cpu: "500m"
   ```

4. **Deploy to cluster:**
   ```bash
   kubectl apply -f deploy/k8s-hybrid-ai.yaml
   ```

---

## 9. Gemini Enterprise App Integration Guide

To connect **Google Gemini Enterprise App** to this FastMCP server:

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
