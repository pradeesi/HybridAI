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
    8000: "https://telecom-crm-console-eniyhtobkq-ew.a.run.app",
    3000: "https://telecom-grafana-eniyhtobkq-ew.a.run.app",
    9090: "https://telecom-prometheus-eniyhtobkq-ew.a.run.app",
    8001: "https://telecom-mcp-server-eniyhtobkq-ew.a.run.app",
    3100: "https://telecom-loki-eniyhtobkq-ew.a.run.app",
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

async def main():
    service_names = {
        8000: "CRM Console",
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
        
    await asyncio.gather(*servers)

if __name__ == "__main__":
    asyncio.run(main())
