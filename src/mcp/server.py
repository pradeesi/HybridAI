"""
Purpose: FastMCP Server implementing Server-Sent Events (SSE) and JSON-RPC 2.0 with Bearer Authentication.
Architecture/Context: External entry point for Gemini Enterprise App and AI Assistant clients.
Dependencies/Side Effects: Enforces token security, streams events to clients, exposes Prometheus metrics.
"""

import asyncio
import json
import logging
from typing import Any, Dict, Optional
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from src.core.audit import audit_client
from src.core.config import settings
from src.core.security import validate_bearer_token, validate_token
from src.db.database import init_db
from src.db.seed_data import seed_synthetic_telecom_data
from src.mcp.tools import (
    check_network_outages_tool,
    get_billing_breakdown_tool,
    get_customer_360_tool,
    get_service_diagnostics_tool,
    get_upsell_recommendations_tool,
    log_agent_interaction_tool,
    run_remote_device_action_tool,
    search_customer_tool,
)

logger = logging.getLogger("hybrid_ai.mcp_server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Summary:
        Handles startup and shutdown lifecycle events for the MCP server.
    """
    logger.info("Initializing MCP Server database and synthetic records...")
    await init_db()
    await seed_synthetic_telecom_data()
    yield
    logger.info("Shutting down MCP Server resources...")
    await audit_client.close()


app = FastAPI(
    title="Telecom Secure FastMCP Server",
    description="Enterprise MCP Server exposing secure customer care diagnostics and tools to Gemini Enterprise.",
    version="1.0.0",
    lifespan=lifespan
)


# Security Dependency
async def verify_auth_token(
    authorization: Optional[str] = Header(None),
    x_mcp_token: Optional[str] = Header(None, alias="X-MCP-Token")
) -> str:
    """
    Summary:
        FastAPI dependency enforcing strict Bearer token authentication.
        Supports both Authorization: Bearer <token> and custom X-MCP-Token: <token> headers
        (useful when deployed behind Google Cloud IAM authentication proxies).

    Parameters:
        authorization (Optional[str]): Incoming Authorization header.
        x_mcp_token (Optional[str]): Dedicated MCP auth header.

    Return Value:
        str: Validated token.

    Exceptions/Errors:
        HTTPException(401): If token is missing, malformed, or invalid.
    """
    if not (validate_token(x_mcp_token) or validate_bearer_token(authorization)):
        await audit_client.log_event(
            event_type="UNAUTHORIZED_ACCESS_ATTEMPT",
            caller_identity="unknown",
            tool_name="AUTH_GATEWAY",
            status="DENIED",
            details={"reason": "Invalid or missing Bearer/MCP token"}
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Authorization Bearer or X-MCP-Token.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    return x_mcp_token or authorization


# Tool Catalog Definition (MCP Specification standard)
TOOL_DEFINITIONS = [
    {
        "name": "search_customer",
        "description": "Finds telecom subscriber records by name, phone number, email, or account number with masked PII.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search keyword, subscriber phone, or account ID"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_customer_360",
        "description": "Returns consolidated customer profile: active broadband/mobile subscriptions, equipment, recent bills, and tickets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "Customer UUID"}
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "get_service_diagnostics",
        "description": "Runs remote line diagnostics for Home Broadband ONT or Mobile 5G lines. Returns dBm signal, packet loss, and recommended fix.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "service_id": {"type": "string", "description": "Subscription UUID"}
            },
            "required": ["service_id"]
        }
    },
    {
        "name": "run_remote_device_action",
        "description": "Executes remote maintenance commands on customer hardware: 'reboot', 'channel_optimization', or 'ping_sweep'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "device_id": {"type": "string", "description": "Device UUID"},
                "action": {"type": "string", "enum": ["reboot", "channel_optimization", "ping_sweep"], "description": "Action to execute"}
            },
            "required": ["device_id", "action"]
        }
    },
    {
        "name": "check_network_outages",
        "description": "Checks for ongoing infrastructure fiber cuts or 5G cell tower maintenance in a given postal area.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "postal_code": {"type": "string", "description": "Customer 5-digit postal code"}
            },
            "required": ["postal_code"]
        }
    },
    {
        "name": "get_billing_breakdown",
        "description": "Returns itemized invoice records, roaming fees, and payment status with PCI-DSS masked card numbers.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "account_id": {"type": "string", "description": "Account UUID or Account Number"}
            },
            "required": ["account_id"]
        }
    },
    {
        "name": "get_upsell_recommendations",
        "description": "Computes personalized upgrade offers (Gigabit fiber, Unlimited 5G pass, Roaming bundle) and provides agent pitch scripts.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "Customer UUID"}
            },
            "required": ["customer_id"]
        }
    },
    {
        "name": "log_agent_interaction",
        "description": "Logs agent call notes, resolution status, and upsell outcome directly into the CRM database and compliance audit trail.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "customer_id": {"type": "string", "description": "Customer UUID"},
                "agent_name": {"type": "string", "description": "Agent name or ID"},
                "issue_summary": {"type": "string", "description": "Problem described by customer"},
                "resolution_summary": {"type": "string", "description": "Solution provided"},
                "call_duration_sec": {"type": "integer", "description": "Call duration in seconds"},
                "upsell_offered": {"type": "boolean", "description": "Was an upsell offer pitched"},
                "upsell_accepted": {"type": "boolean", "description": "Did the customer accept"}
            },
            "required": ["customer_id", "issue_summary", "resolution_summary"]
        }
    }
]


# Dispatch table
TOOL_HANDLER_MAP = {
    "search_customer": search_customer_tool,
    "get_customer_360": get_customer_360_tool,
    "get_service_diagnostics": get_service_diagnostics_tool,
    "run_remote_device_action": run_remote_device_action_tool,
    "check_network_outages": check_network_outages_tool,
    "get_billing_breakdown": get_billing_breakdown_tool,
    "get_upsell_recommendations": get_upsell_recommendations_tool,
    "log_agent_interaction": log_agent_interaction_tool,
}


@app.get("/health")
async def health_check():
    """
    Summary:
        Health check endpoint for container orchestrators and load balancers.
    """
    return {"status": "healthy", "service": "telecom-mcp-server", "version": "1.0.0"}


@app.get("/metrics")
async def metrics():
    """
    Summary:
        Exposes Prometheus metrics for scraping.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/tools")
async def list_tools(auth: str = Depends(verify_auth_token)):
    """
    Summary:
        Returns list of available MCP tools and their JSON schemas.
    """
    return {"tools": TOOL_DEFINITIONS}


@app.post("/execute")
async def execute_tool(request: Request, auth: str = Depends(verify_auth_token)):
    """
    Summary:
        Direct JSON-RPC tool execution endpoint consumed by Gemini Enterprise or HTTP agent bridges.
    """
    body = await request.json()
    tool_name = body.get("name")
    arguments = body.get("arguments", {})
    caller_id = body.get("caller_id", "gemini-enterprise-app")

    if not tool_name or tool_name not in TOOL_HANDLER_MAP:
        raise HTTPException(status_code=400, detail=f"Tool '{tool_name}' is not recognized.")

    handler = TOOL_HANDLER_MAP[tool_name]
    result = await handler(**arguments, caller_id=caller_id)
    return {"result": result}


async def _process_single_mcp_message(payload: Dict[str, Any], caller_id: str = "gemini-enterprise-mcp") -> Optional[Dict[str, Any]]:
    """
    Summary:
        Processes a single JSON-RPC 2.0 MCP message according to the Model Context Protocol.

    Parameters:
        payload (Dict[str, Any]): Parsed JSON-RPC request dictionary.
        caller_id (str): Identifier of the caller for audit logging.

    Return Value:
        Optional[Dict[str, Any]]: JSON-RPC response object or None for notifications.
    """
    msg_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params", {}) or {}

    # 1. MCP Lifecycle: initialize
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False}
                },
                "serverInfo": {
                    "name": "telecom-mcp-server",
                    "version": "1.0.0"
                }
            }
        }

    # 2. MCP Lifecycle: notifications/initialized
    elif method == "notifications/initialized":
        if msg_id is not None:
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        return None

    # 3. MCP Ping
    elif method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {}
        }

    # 4. MCP Tools List
    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": TOOL_DEFINITIONS}
        }

    # 5. MCP Tools Call
    elif method == "tools/call":
        tool_name = params.get("name")
        tool_args = params.get("arguments", {}) or {}

        if tool_name not in TOOL_HANDLER_MAP:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {tool_name}"}
            }

        handler = TOOL_HANDLER_MAP[tool_name]
        try:
            tool_result = await handler(**tool_args, caller_id=caller_id)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(tool_result, default=str)
                        }
                    ]
                }
            }
        except Exception as err:
            logger.error("Error executing tool %s: %s", tool_name, str(err), exc_info=True)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32000, "message": f"Execution error: {str(err)}"}
            }

    # Fallback for unrecognized method
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": -32601, "message": f"Method '{method}' is not supported."}
    }


@app.get("/mcp")
@app.get("/sse")
@app.get("/")
async def mcp_sse_stream(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_mcp_token: Optional[str] = Header(None, alias="X-MCP-Token")
):
    """
    Summary:
        Server-Sent Events (SSE) and HTTP discovery endpoint implementing MCP connectivity.
        Supports streaming text/event-stream for SSE and direct JSON tool listing for HTTP callers.
    """
    if not (validate_token(x_mcp_token) or validate_bearer_token(authorization)):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized MCP connection.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    accept_header = request.headers.get("accept", "")
    if "text/event-stream" not in accept_header:
        return {
            "status": "healthy",
            "service": "telecom-mcp-server",
            "protocolVersion": "2024-11-05",
            "tools": TOOL_DEFINITIONS
        }

    async def event_generator():
        # Emit initial MCP handshake event with endpoint URI
        handshake_data = {
            "type": "endpoint",
            "endpoint": "/mcp"
        }
        yield f"event: endpoint\ndata: {json.dumps(handshake_data)}\n\n"

        # Stream periodic heartbeat keeping the connection active
        while True:
            if await request.is_disconnected():
                break
            await asyncio.sleep(15)
            yield f": ping {json.dumps({'timestamp': asyncio.get_event_loop().time()})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@app.post("/mcp")
@app.post("/messages")
@app.post("/")
async def handle_mcp_message(request: Request, auth: str = Depends(verify_auth_token)):
    """
    Summary:
        MCP JSON-RPC message endpoint. Dispatches initialize, ping, tools/list, or tools/call commands.
        Handles both individual and batch JSON-RPC payloads from Gemini Enterprise or AI agents.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in request body.")

    if isinstance(payload, list):
        responses = []
        for item in payload:
            if isinstance(item, dict):
                res = await _process_single_mcp_message(item, caller_id="gemini-enterprise-mcp")
                if res is not None:
                    responses.append(res)
        return responses

    elif isinstance(payload, dict):
        res = await _process_single_mcp_message(payload, caller_id="gemini-enterprise-mcp")
        if res is None:
            return Response(status_code=204)
        return res

    raise HTTPException(status_code=400, detail="Request body must be a JSON object or array.")


def run():
    """
    Summary:
        CLI runner for launching the MCP server standalone.
        Dynamically respects the PORT environment variable assigned by Cloud Run or container orchestrators.
    """
    import os
    import uvicorn
    port = int(os.environ.get("PORT", settings.PORT or settings.MCP_PORT))
    uvicorn.run("src.mcp.server:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    run()
