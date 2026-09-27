"""
Purpose: Multi-Service Authenticated Cloud Run Gateway Proxy.
Architecture/Context: Bridges local browser requests on ports 8000, 3000, 9090, 8001, and 3100
                      to live Google Cloud Run services, injecting active gcloud IAM tokens.
Dependencies/Side Effects: Binds to 127.0.0.1 on ports 8000 (CRM), 3000 (Grafana), 9090 (Prometheus), 8001 (MCP), 3100 (Loki).
"""
import asyncio
import subprocess
import time
from typing import Dict
from fastapi import FastAPI, Request, Response
import httpx
import uvicorn

# Map local port to target Cloud Run service URL
SERVICES: Dict[int, str] = {
    8000: "https://telecom-crm-console-1111937452.europe-west1.run.app",
    8080: "https://telecom-crm-console-1111937452.europe-west1.run.app",
    3000: "https://telecom-grafana-1111937452.europe-west1.run.app",
    9090: "https://telecom-prometheus-1111937452.europe-west1.run.app",
    8001: "https://telecom-mcp-server-1111937452.europe-west1.run.app",
    3100: "https://telecom-loki-1111937452.europe-west1.run.app",
}

# In-memory cached token with timestamp
_cached_token = ""
_token_expiry = 0.0

def get_id_token() -> str:
    """Retrieves and caches a Google IAM Identity Token via gcloud for 10 minutes."""
    global _cached_token, _token_expiry
    now = time.time()
    if not _cached_token or now >= _token_expiry:
        res = subprocess.run(["gcloud", "auth", "print-identity-token"], capture_output=True, text=True, check=True)
        _cached_token = res.stdout.strip()
        _token_expiry = now + 600.0  # 10 minutes cache
    return _cached_token

def create_proxy_app(service_name: str, target_url: str) -> FastAPI:
    """Creates a reverse-proxy FastAPI application for a specific Cloud Run target."""
    app = FastAPI(title=f"Proxy for {service_name}", docs_url=None, redoc_url=None)

    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"])
    async def proxy_handler(request: Request, path: str):
        token = get_id_token()
        url = f"{target_url}/{path}"
        
        # Filter hop-by-hop headers and incoming authorization so proxy's valid Cloud Run token is used
        excluded = {"host", "connection", "content-length", "authorization", "x-serverless-authorization"}
        forward_headers = {k: v for k, v in request.headers.items() if k.lower() not in excluded}
        forward_headers["Authorization"] = f"Bearer {token}"
        
        body = await request.body()
        
        async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
            resp = await client.request(
                method=request.method,
                url=url,
                headers=forward_headers,
                params=dict(request.query_params),
                content=body
            )
            
            resp_headers = dict(resp.headers)
            resp_headers.pop("content-encoding", None)
            resp_headers.pop("transfer-encoding", None)
            
            return Response(content=resp.content, status_code=resp.status_code, headers=resp_headers)

    return app


async def sync_grafana_iam_tokens():
    """
    Summary:
        Periodically mints audience-specific ID tokens for Prometheus and Loki using the project
        compute service account (roles/run.invoker) and updates Grafana datasources via its REST API.
    """
    await asyncio.sleep(4)  # Allow local proxy to establish connections
    sa_email = "1111937452-compute@developer.gserviceaccount.com"
    prom_url = SERVICES[9090]
    loki_url = SERVICES[3100]

    while True:
        try:
            # Mint audience token for Prometheus
            prom_cmd = ["gcloud", "auth", "print-identity-token", f"--impersonate-service-account={sa_email}", f"--audiences={prom_url}"]
            prom_res = subprocess.run(prom_cmd, capture_output=True, text=True)
            prom_token = prom_res.stdout.strip().splitlines()[-1] if prom_res.returncode == 0 else ""

            # Mint audience token for Loki
            loki_cmd = ["gcloud", "auth", "print-identity-token", f"--impersonate-service-account={sa_email}", f"--audiences={loki_url}"]
            loki_res = subprocess.run(loki_cmd, capture_output=True, text=True)
            loki_token = loki_res.stdout.strip().splitlines()[-1] if loki_res.returncode == 0 else ""

            async with httpx.AsyncClient(timeout=15.0) as client:
                if prom_token:
                    await client.put(
                        "http://127.0.0.1:3000/api/datasources/uid/PBFA97CFB590B2093",
                        auth=("admin", "telecom_admin"),
                        json={
                            "name": "Prometheus",
                            "type": "prometheus",
                            "access": "proxy",
                            "url": prom_url,
                            "jsonData": {"httpHeaderName1": "Authorization"},
                            "secureJsonData": {"httpHeaderValue1": f"Bearer {prom_token}"}
                        }
                    )
                if loki_token:
                    await client.put(
                        "http://127.0.0.1:3000/api/datasources/uid/P8E80F9AEF21F6940",
                        auth=("admin", "telecom_admin"),
                        json={
                            "name": "Loki",
                            "type": "loki",
                            "access": "proxy",
                            "url": loki_url,
                            "jsonData": {"httpHeaderName1": "Authorization"},
                            "secureJsonData": {"httpHeaderValue1": f"Bearer {loki_token}"}
                        }
                    )
            print("[Gateway] Synchronized Grafana IAM tokens for Prometheus & Loki datasources (via UID).")
        except Exception as e:
            print(f"[Gateway] Warning: Failed to sync Grafana IAM tokens: {e}")

        # Refresh every 30 minutes (OIDC tokens are valid for 60 minutes)
        await asyncio.sleep(1800)


async def main():
    service_names = {
        8000: "CRM Console",
        8080: "CRM Console (Port 8080)",
        3000: "Grafana",
        9090: "Prometheus",
        8001: "FastMCP Server",
        3100: "Loki"
    }
    
    servers = []
    for port, target in SERVICES.items():
        name = service_names.get(port, f"Service-{port}")
        app = create_proxy_app(name, target)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        server = uvicorn.Server(config)
        servers.append(server.serve())
        print(f"[Gateway] http://localhost:{port} -> {name} ({target})")
        
    await asyncio.gather(*servers, sync_grafana_iam_tokens())

if __name__ == "__main__":
    asyncio.run(main())
