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
        
        # Filter hop-by-hop headers
        excluded = {"host", "connection", "content-length"}
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


async def sync_grafana_datasources() -> None:
    """
    Summary:
        Periodically provisions and updates Grafana datasources (Loki & Prometheus)
        with fresh Google IAM Bearer tokens to prevent authorization errors.
        Dynamically discovers datasource IDs and current versions to eliminate 409 Conflict errors.
    """
    # Wait for the local gateway proxy servers to initialize
    await asyncio.sleep(2)
    while True:
        try:
            token = get_id_token()
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get("http://127.0.0.1:3000/api/datasources")
                if res.status_code == 200:
                    datasources = res.json()
                    for ds in datasources:
                        ds_id = ds.get("id")
                        ds_name = ds.get("name")
                        ds_type = ds.get("type")
                        ds_ver = ds.get("version", 1)
                        ds_uid = ds.get("uid")

                        if ds_type == "loki" or ds_name == "Loki":
                            put_payload = {
                                "id": ds_id,
                                "uid": ds_uid,
                                "orgId": 1,
                                "name": "Loki",
                                "type": "loki",
                                "access": "proxy",
                                "url": SERVICES[3100],
                                "jsonData": {"httpHeaderName1": "Authorization"},
                                "secureJsonData": {"httpHeaderValue1": f"Bearer {token}"},
                                "version": ds_ver
                            }
                            resp = await client.put(f"http://127.0.0.1:3000/api/datasources/{ds_id}", json=put_payload)
                            if resp.status_code not in (200, 204):
                                print(f"[Gateway] Loki datasource update notice: {resp.status_code}")

                        elif ds_type == "prometheus" or ds_name == "Prometheus":
                            put_payload = {
                                "id": ds_id,
                                "uid": ds_uid,
                                "orgId": 1,
                                "name": "Prometheus",
                                "type": "prometheus",
                                "access": "proxy",
                                "url": SERVICES[9090],
                                "jsonData": {"httpHeaderName1": "Authorization"},
                                "secureJsonData": {"httpHeaderValue1": f"Bearer {token}"},
                                "version": ds_ver
                            }
                            resp = await client.put(f"http://127.0.0.1:3000/api/datasources/{ds_id}", json=put_payload)
                            if resp.status_code not in (200, 204):
                                print(f"[Gateway] Prometheus datasource update notice: {resp.status_code}")

                    print("[Gateway] Synced Grafana datasources (Loki & Prometheus) with fresh IAM token.")
        except Exception as exc:
            print(f"[Gateway] Background datasource sync warning: {exc}")

        # Automatically refresh every 2 minutes so tokens never expire
        await asyncio.sleep(120)


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
        
    servers.append(sync_grafana_datasources())
    await asyncio.gather(*servers)

if __name__ == "__main__":
    asyncio.run(main())
