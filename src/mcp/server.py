"""
Purpose: FastMCP Server implementing Server-Sent Events (SSE) and JSON-RPC 2.0 with Bearer Authentication.
Architecture/Context: External entry point for Gemini Enterprise App and AI Assistant clients.
Dependencies/Side Effects: Enforces token security, streams events to clients, exposes Prometheus metrics.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, Optional
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

from src.core.audit import audit_client
from src.core.config import settings
from src.core.security import (
    AUTH_FAILURES_COUNTER,
    PII_REDACTIONS_COUNTER,
    decode_jwt_unverified,
    extract_caller_identity,
    extract_security_context,
    validate_bearer_token,
    validate_token,
)
from src.db.database import init_db
from src.db.seed_data import seed_synthetic_telecom_data
from src.mcp.tools import (
    TOOL_CALL_COUNTER,
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
        Initializes SQLite database tables, seeds synthetic enterprise records,
        and primes baseline Prometheus metric families for instant dashboard visibility.
    """
    logger.info("Initializing MCP Server database and synthetic records...")
    await init_db()
    await seed_synthetic_telecom_data()

    # Prime Prometheus metric series with 0-values so metrics appear immediately on cold boots
    TOOL_CALL_COUNTER.labels(tool_name="system_ready", status="SUCCESS").inc(0)
    PII_REDACTIONS_COUNTER.labels(field_type="init").inc(0)
    AUTH_FAILURES_COUNTER.inc(0)

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
    request: Request,
    authorization: Optional[str] = Header(None),
    x_mcp_token: Optional[str] = Header(None, alias="X-MCP-Token"),
    x_serverless_auth: Optional[str] = Header(None, alias="X-Serverless-Authorization"),
    x_goog_user_email: Optional[str] = Header(None, alias="X-Goog-Authenticated-User-Email"),
    x_agent_identity: Optional[str] = Header(None, alias="X-Agent-Identity")
) -> str:
    """
    Summary:
        FastAPI dependency enforcing authentication and extracting verified caller identity.
        Resolves individual user emails from Google IAP headers, OIDC JWT claims,
        or custom agent identity headers for non-repudiation and audit tracking.

    Parameters:
        request (Request): FastAPI request context for analyzing headers and environment.
        authorization (Optional[str]): Incoming Authorization header.
        x_mcp_token (Optional[str]): Dedicated MCP auth header.
        x_serverless_auth (Optional[str]): Serverless proxy authorization header.
        x_goog_user_email (Optional[str]): Google IAP authenticated user header.
        x_agent_identity (Optional[str]): Custom contact center agent identity header.

    Return Value:
        str: Resolved caller identity (e.g. 'sarah.jenkins@telecom.com').

    Exceptions/Errors:
        HTTPException(401): If request cannot be authenticated.
    """
    token_candidate = authorization or x_serverless_auth or x_mcp_token
    security_context = extract_security_context(
        token=token_candidate,
        headers=dict(request.headers),
        client_ip=request.client.host if request.client else None
    )
    request.state.security_context = security_context
    caller_id = security_context["caller_identity"]

    # 1. Check direct token in Authorization, X-MCP-Token, or X-Serverless-Authorization
    if (
        validate_token(x_mcp_token)
        or validate_bearer_token(authorization)
        or validate_bearer_token(x_serverless_auth)
    ):
        return caller_id

    # 2. In Google Cloud Run (where IAM access control is enforced at the network perimeter),
    # requests carrying Google Cloud trace context have already been verified by Cloud Run IAM
    is_cloud_run = bool(os.environ.get("K_SERVICE"))
    has_gcp_trace = bool(request.headers.get("x-cloud-trace-context"))

    if is_cloud_run and has_gcp_trace:
        logger.info(
            "Request authorized via Cloud Run IAM edge proxy (caller: %s, user-agent: %s)",
            caller_id,
            request.headers.get("user-agent", "unknown")
        )
        return caller_id

    await audit_client.log_event(
        event_type="UNAUTHORIZED_ACCESS_ATTEMPT",
        caller_identity="unknown",
        tool_name="AUTH_GATEWAY",
        status="DENIED",
        security_context=security_context,
        details={"reason": "Invalid or missing Bearer/MCP token"}
    )
    # Record actual unauthorized access attempt in Prometheus security telemetry
    AUTH_FAILURES_COUNTER.inc()
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing Authorization Bearer or X-MCP-Token.",
        headers={"WWW-Authenticate": "Bearer"}
    )


# Tool Catalog Definition (MCP Specification standard)
# Note: Agent identity is extracted automatically from request headers and JWT tokens,
# NOT requested as a tool parameter from the user.
TOOL_DEFINITIONS = [
    {
        "name": "search_customer",
        "description": "Finds subscriber records by Mobile or Landline phone number (or account query). The phone number is unmasked as the unique subscriber identity; subscriber names and personal PII are strictly masked.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Subscriber Mobile or Landline phone number (e.g. '+1 (555) 234-5678' or digits '5552345678'). Primary unique identity."},
                "query": {"type": "string", "description": "Alternative search keyword: phone number, account ID, or general search."}
            }
        }
    },
    {
        "name": "get_customer_360",
        "description": "Returns consolidated subscriber profile (active broadband/mobile subscriptions, equipment, recent bills, and tickets) driven by Mobile or Landline phone number. Subscriber name is strictly masked.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Subscriber Mobile or Landline phone number used as primary identity."},
                "customer_id": {"type": "string", "description": "Internal Customer UUID (optional if phone_number is supplied)."}
            }
        }
    },
    {
        "name": "get_service_diagnostics",
        "description": "Runs remote line diagnostics for Home Broadband ONT or Mobile 5G lines. Accepts subscriber phone number or subscription UUID. Returns dBm signal, packet loss, and recommended fix.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Subscriber Mobile or Landline phone number to automatically diagnose their active service."},
                "service_id": {"type": "string", "description": "Subscription UUID (optional if phone_number is supplied)."}
            }
        }
    },
    {
        "name": "run_remote_device_action",
        "description": "Executes remote maintenance commands on customer hardware: 'reboot', 'channel_optimization', or 'ping_sweep'. Accepts subscriber phone number or device UUID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Subscriber Mobile or Landline phone number to locate and reboot premise equipment."},
                "device_id": {"type": "string", "description": "Hardware device UUID (optional if phone_number is supplied)."},
                "action": {"type": "string", "enum": ["reboot", "channel_optimization", "ping_sweep"], "description": "Action to execute"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "check_network_outages",
        "description": "Checks for ongoing infrastructure fiber cuts or 5G cell tower maintenance in a given postal area or for a subscriber's phone number.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Subscriber Mobile or Landline phone number to resolve their service area."},
                "postal_code": {"type": "string", "description": "Customer 5-digit postal code (optional if phone_number is supplied)."}
            }
        }
    },
    {
        "name": "get_billing_breakdown",
        "description": "Returns itemized invoice records, roaming fees, and payment status with masked card numbers. Accepts subscriber phone number or account ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Subscriber Mobile or Landline phone number."},
                "account_id": {"type": "string", "description": "Account UUID or Account Number (optional if phone_number is supplied)."}
            }
        }
    },
    {
        "name": "get_upsell_recommendations",
        "description": "Computes personalized upgrade offers (Gigabit fiber, Unlimited 5G pass, Roaming bundle) and provides agent pitch scripts. Identified by subscriber phone number or UUID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Subscriber Mobile or Landline phone number."},
                "customer_id": {"type": "string", "description": "Customer UUID (optional if phone_number is supplied)."}
            }
        }
    },
    {
        "name": "log_agent_interaction",
        "description": "Logs agent call notes, resolution status, and upsell outcome directly into the CRM database and audit trail. Identified by subscriber phone number or UUID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "phone_number": {"type": "string", "description": "Subscriber Mobile or Landline phone number."},
                "customer_id": {"type": "string", "description": "Customer UUID (optional if phone_number is supplied)."},
                "agent_name": {"type": "string", "description": "Agent name or ID"},
                "issue_summary": {"type": "string", "description": "Problem described by customer"},
                "resolution_summary": {"type": "string", "description": "Solution provided"},
                "call_duration_sec": {"type": "integer", "description": "Call duration in seconds"},
                "upsell_offered": {"type": "boolean", "description": "Was an upsell offer pitched"},
                "upsell_accepted": {"type": "boolean", "description": "Did the customer accept"}
            },
            "required": ["issue_summary", "resolution_summary"]
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
async def list_tools(request: Request, auth: str = Depends(verify_auth_token)):
    """
    Summary:
        Returns list of available MCP tools and their JSON schemas.
    """
    raw_headers = dict(request.headers)
    token_candidate = raw_headers.get("authorization") or raw_headers.get("x-serverless-authorization") or raw_headers.get("x-mcp-token")
    jwt_claims = decode_jwt_unverified(token_candidate)
    security_context = getattr(
        request.state,
        "security_context",
        extract_security_context(
            headers=raw_headers,
            client_ip=request.client.host if request.client else None
        )
    )
    await audit_client.log_http_request(
        endpoint=request.url.path,
        method=request.method,
        headers=raw_headers,
        body={"action": "list_tools", "count": len(TOOL_DEFINITIONS)},
        caller_identity=auth,
        security_context=security_context,
        jwt_claims=jwt_claims,
        status="LISTED"
    )
    return {"tools": TOOL_DEFINITIONS}


@app.post("/execute")
async def execute_tool(request: Request, auth: str = Depends(verify_auth_token)):
    """
    Summary:
        Direct JSON-RPC tool execution endpoint consumed by Gemini Enterprise or HTTP agent bridges.
    """
    raw_headers = dict(request.headers)
    token_candidate = raw_headers.get("authorization") or raw_headers.get("x-serverless-authorization") or raw_headers.get("x-mcp-token")
    jwt_claims = decode_jwt_unverified(token_candidate)
    security_context = getattr(
        request.state,
        "security_context",
        extract_security_context(
            headers=raw_headers,
            client_ip=request.client.host if request.client else None
        )
    )

    try:
        body = await request.json()
    except Exception:
        raw_bytes = await request.body()
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        await audit_client.log_http_request(
            endpoint=request.url.path,
            method=request.method,
            headers=raw_headers,
            body=raw_text,
            caller_identity=auth,
            security_context=security_context,
            jwt_claims=jwt_claims,
            status="INVALID_JSON"
        )
        raise HTTPException(status_code=400, detail="Invalid JSON in request body.")

    await audit_client.log_http_request(
        endpoint=request.url.path,
        method=request.method,
        headers=raw_headers,
        body=body,
        caller_identity=auth,
        security_context=security_context,
        jwt_claims=jwt_claims,
        status="RECEIVED"
    )

    tool_name = body.get("name")
    arguments = body.get("arguments", {})
    caller_id = arguments.get("agent_email") or body.get("caller_id") or auth

    if not tool_name or tool_name not in TOOL_HANDLER_MAP:
        raise HTTPException(status_code=400, detail=f"Tool '{tool_name}' is not recognized.")

    handler = TOOL_HANDLER_MAP[tool_name]
    result = await handler(**arguments, caller_id=caller_id, security_context=security_context)
    return {"result": result}


async def _process_single_mcp_message(
    payload: Dict[str, Any],
    caller_id: str = "gemini-enterprise-mcp",
    security_context: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """
    Summary:
        Processes a single JSON-RPC 2.0 MCP message according to the Model Context Protocol.

    Parameters:
        payload (Dict[str, Any]): Parsed JSON-RPC request dictionary.
        caller_id (str): Identifier of the caller for audit logging.
        security_context (Optional[Dict[str, Any]]): Security and network context for compliance.

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

        # Check for human operator metadata in arguments or MCP _meta payload
        meta = params.get("_meta", {}) or payload.get("_meta", {}) or {}
        explicit_user = (
            meta.get("user")
            or meta.get("user_email")
            or meta.get("agent_email")
            or tool_args.get("agent_email")
            or tool_args.get("caller_user")
        )
        if explicit_user and isinstance(explicit_user, str) and explicit_user.strip():
            if security_context:
                security_context["end_user_email"] = explicit_user.strip()

        # The direct caller is strictly the AI Agent invoking this tool
        agent_caller = security_context.get("caller", "gemini-enterprise-agent") if security_context else "gemini-enterprise-agent"

        handler = TOOL_HANDLER_MAP[tool_name]
        try:
            tool_result = await handler(
                **tool_args,
                caller_id=agent_caller,
                security_context=security_context
            )
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
    auth: str = Depends(verify_auth_token)
):
    """
    Summary:
        Server-Sent Events (SSE) and HTTP discovery endpoint implementing MCP connectivity.
        Supports streaming text/event-stream for SSE and direct JSON tool listing for HTTP callers.
    """
    raw_headers = dict(request.headers)
    token_candidate = raw_headers.get("authorization") or raw_headers.get("x-serverless-authorization") or raw_headers.get("x-mcp-token")
    jwt_claims = decode_jwt_unverified(token_candidate)
    security_context = getattr(
        request.state,
        "security_context",
        extract_security_context(
            headers=raw_headers,
            client_ip=request.client.host if request.client else None
        )
    )

    await audit_client.log_http_request(
        endpoint=request.url.path,
        method=request.method,
        headers=raw_headers,
        body={"action": "sse_connect", "accept": request.headers.get("accept", "")},
        caller_identity=auth,
        security_context=security_context,
        jwt_claims=jwt_claims,
        status="CONNECTED"
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
        Logs every incoming request header and message body payload to Loki for audit and security analysis.
    """
    raw_headers = dict(request.headers)
    token_candidate = raw_headers.get("authorization") or raw_headers.get("x-serverless-authorization") or raw_headers.get("x-mcp-token")
    jwt_claims = decode_jwt_unverified(token_candidate)

    security_context = getattr(
        request.state,
        "security_context",
        extract_security_context(
            headers=raw_headers,
            client_ip=request.client.host if request.client else None
        )
    )

    try:
        payload = await request.json()
    except Exception:
        raw_bytes = await request.body()
        raw_text = raw_bytes.decode("utf-8", errors="replace")
        await audit_client.log_http_request(
            endpoint=request.url.path,
            method=request.method,
            headers=raw_headers,
            body=raw_text,
            caller_identity=auth,
            security_context=security_context,
            jwt_claims=jwt_claims,
            status="INVALID_JSON"
        )
        raise HTTPException(status_code=400, detail="Invalid JSON in request body.")

    # Log complete headers and body of the message to Loki
    await audit_client.log_http_request(
        endpoint=request.url.path,
        method=request.method,
        headers=raw_headers,
        body=payload,
        caller_identity=auth,
        security_context=security_context,
        jwt_claims=jwt_claims,
        status="RECEIVED"
    )

    if isinstance(payload, list):
        responses = []
        for item in payload:
            if isinstance(item, dict):
                res = await _process_single_mcp_message(
                    item,
                    caller_id=auth,
                    security_context=security_context
                )
                if res is not None:
                    responses.append(res)
        return responses

    elif isinstance(payload, dict):
        res = await _process_single_mcp_message(
            payload,
            caller_id=auth,
            security_context=security_context
        )
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
