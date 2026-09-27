"""
Purpose: Telecom domain tool integrations bridging Google ADK Agent to live FastMCP server.
Architecture/Context: Dispatches tool calls over HTTP JSON-RPC to Cloud Run or On-Prem Proxmox MCP server.
                      Enriches every tool invocation with Google Identity credentials, trace context,
                      and caller metadata for compliance audit logging in Loki and Prometheus.
Dependencies/Side Effects: Interacts with Google Auth, httpx for async/sync HTTP dispatch,
                            and Google ADK ToolContext for user and session inspection.
"""

import json
import logging
import subprocess
import time
import urllib.request
from typing import Any, Dict, List, Optional

import httpx
from google.adk.tools import ToolContext
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from app.config import agent_settings

logger = logging.getLogger("telecom_agent.tools")

# In-memory cached IAM token for Cloud Run requests
_cached_token = ""
_token_expiry = 0.0


def _get_cloud_run_id_token(target_audience: str) -> str:
    """
    Summary:
        Retrieves a valid Google Cloud Identity (OIDC) ID token for authenticating
        to Google Cloud Run endpoints. Employs instance metadata server when running
        on GCP (Agent Runtime), falling back to gcloud CLI in local environments.

    Parameters:
        target_audience (str): Target audience URL for the ID token (e.g. MCP Cloud Run URL).

    Return Value:
        str: Valid Google OIDC bearer token string.

    Exceptions/Errors:
        Returns empty string if token acquisition fails.
    """
    global _cached_token, _token_expiry
    now = time.time()
    if _cached_token and now < _token_expiry:
        return _cached_token

    # 1. Attempt token acquisition via GCP Compute/Cloud Run metadata server
    try:
        url = f"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience={target_audience}"
        req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            token = resp.read().decode("utf-8").strip()
            if token:
                _cached_token = token
                _token_expiry = now + 3000.0  # Cache for 50 minutes
                return token
    except Exception:
        pass

    # 2. Local fallback using active gcloud authentication
    try:
        res = subprocess.run(
            ["gcloud", "auth", "print-identity-token"],
            capture_output=True,
            text=True,
            timeout=3.0
        )
        if res.returncode == 0 and res.stdout.strip():
            token = res.stdout.strip()
            _cached_token = token
            _token_expiry = now + 3000.0
            return token
    except Exception as exc:
        logger.debug("Local gcloud token retrieval skipped: %s", exc)

    return ""


def build_mcp_headers(
    user_email: Optional[str] = None,
    trace_id: Optional[str] = None,
    caller_agent: Optional[str] = None
) -> Dict[str, str]:
    """
    Summary:
        Builds the complete set of authentication, identity, and compliance audit headers
        required by the MCP server for Loki logging and Prometheus metrics.

    Parameters:
        user_email (Optional[str]): Authenticated human user email from Gemini Enterprise App.
        trace_id (Optional[str]): Distributed execution trace ID.
        caller_agent (Optional[str]): Agent name or identity string.

    Return Value:
        Dict[str, str]: Map of HTTP headers to include in the MCP request.
    """
    mcp_url = agent_settings.MCP_SERVER_URL.rstrip("/")
    is_cloud_run = (
        agent_settings.MCP_DEPLOYMENT_TARGET.lower() == "cloud_run"
        or (mcp_url.startswith("https://") and "run.app" in mcp_url)
        or "8001" in mcp_url
    )

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": agent_settings.USER_AGENT,
        "X-Agent-Identity": agent_settings.AGENT_IDENTITY,
        "X-Agent-Name": caller_agent or agent_settings.AGENT_NAME,
    }

    # Propagate Google Identity end-user email for compliance audit trail in Loki
    effective_user = user_email or "admin@pradeesi.altostrat.com"
    headers["X-Goog-Authenticated-User-Email"] = effective_user
    headers["X-End-User-Email"] = effective_user
    headers["X-User-Email"] = effective_user

    # Propagate distributed trace context for GCP Cloud Trace and Loki correlation
    if trace_id:
        headers["X-Cloud-Trace-Context"] = f"{trace_id}/0;o=1"
        headers["X-Trace-ID"] = trace_id

    # Configure Authentication Header based on deployment type
    if is_cloud_run:
        id_token = _get_cloud_run_id_token(mcp_url)
        if id_token:
            headers["Authorization"] = f"Bearer {id_token}"
            headers["X-Serverless-Authorization"] = f"Bearer {id_token}"
    else:
        # On-Prem Proxmox deployment: Uses static bearer token
        headers["Authorization"] = f"Bearer {agent_settings.MCP_AUTH_TOKEN}"
        headers["X-MCP-Token"] = agent_settings.MCP_AUTH_TOKEN

    return headers


async def _dispatch_mcp_call(
    tool_name: str,
    arguments: Dict[str, Any],
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """
    Summary:
        Executes a remote tool call against the MCP server using standard JSON-RPC 2.0.
        Extracts user identity from ADK ToolContext to populate audit headers.

    Parameters:
        tool_name (str): Name of the MCP tool to execute.
        arguments (Dict[str, Any]): Dictionary of arguments for the tool.
        tool_context (Optional[ToolContext]): ADK execution context containing user ID and trace ID.

    Return Value:
        Dict[str, Any]: Parsed result dictionary returned by the MCP tool.

    Exceptions/Errors:
        Returns structured error dict with status="error" on failure.
    """
    user_email = None
    trace_id = None
    agent_name = None

    if tool_context:
        user_email = getattr(tool_context, "user_id", None)
        trace_id = getattr(tool_context, "invocation_id", None)
        agent_name = getattr(tool_context, "agent_name", None)

    headers = build_mcp_headers(
        user_email=user_email,
        trace_id=trace_id,
        caller_agent=agent_name
    )

    mcp_endpoint = f"{agent_settings.MCP_SERVER_URL.rstrip('/')}/mcp"
    payload = {
        "jsonrpc": "2.0",
        "id": int(time.time() * 1000) % 1000000,
        "method": "tools/call",
        "params": {
            "name": tool_name,
            "arguments": arguments,
            "_meta": {
                "user_email": user_email or "admin@pradeesi.altostrat.com",
                "caller_identity": agent_settings.AGENT_IDENTITY
            }
        }
    }

    try:
        async with httpx.AsyncClient(timeout=agent_settings.MCP_TIMEOUT_SECONDS) as client:
            resp = await client.post(mcp_endpoint, json=payload, headers=headers)
            if resp.status_code != 200:
                logger.error(
                    "MCP server returned HTTP %d for tool %s: %s",
                    resp.status_code, tool_name, resp.text
                )
                return {
                    "status": "error",
                    "code": resp.status_code,
                    "message": f"MCP server responded with HTTP status {resp.status_code}: {resp.text[:200]}"
                }

            response_data = resp.json()
            if "error" in response_data:
                err = response_data["error"]
                return {"status": "error", "error": err.get("message", "Unknown MCP execution error")}

            # Handle standard MCP content response wrapper
            result_obj = response_data.get("result", {})
            content_list = result_obj.get("content", [])
            if content_list and isinstance(content_list, list):
                first_item = content_list[0]
                if isinstance(first_item, dict) and first_item.get("type") == "text":
                    text_val = first_item.get("text", "{}")
                    try:
                        return json.loads(text_val)
                    except json.JSONDecodeError:
                        return {"result": text_val}

            return result_obj
    except httpx.ConnectError as conn_err:
        logger.warning("Could not connect to MCP server at %s: %s", mcp_endpoint, conn_err)
        return {
            "status": "error",
            "message": f"MCP server at {mcp_endpoint} is currently unreachable. Please check network connectivity or deployment status."
        }
    except Exception as exc:
        logger.exception("Unexpected error executing MCP tool %s: %s", tool_name, exc)
        return {"status": "error", "message": f"Execution error: {str(exc)}"}


# ==============================================================================
# The 8 Strict Telecom MCP Tools Exposed to Google ADK Agent
# ==============================================================================


async def search_customer(
    phone_number: str = "",
    query: str = "",
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """Finds subscriber records by Mobile or Landline phone number (or account query). The phone number is unmasked as the unique subscriber identity; subscriber names and personal PII are strictly masked.

    Args:
        phone_number: Subscriber Mobile or Landline phone number (e.g. '+1 (555) 234-5678' or digits '5552345678'). Primary unique identity.
        query: Alternative search keyword: phone number, account ID, or general search.

    Returns:
        Dictionary containing matched subscriber records and status.
    """
    args: Dict[str, Any] = {}
    if phone_number:
        args["phone_number"] = phone_number
    if query:
        args["query"] = query
    return await _dispatch_mcp_call("search_customer", args, tool_context)


async def get_customer_360(
    phone_number: str = "",
    customer_id: str = "",
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """Returns consolidated subscriber profile (active broadband/mobile subscriptions, equipment, recent bills, and tickets) driven by Mobile or Landline phone number. Subscriber name is strictly masked.

    Args:
        phone_number: Subscriber Mobile or Landline phone number used as primary identity.
        customer_id: Internal Customer UUID (optional if phone_number is supplied).

    Returns:
        Dictionary containing 360-degree customer profile including plans, equipment, and recent bills.
    """
    args: Dict[str, Any] = {}
    if phone_number:
        args["phone_number"] = phone_number
    if customer_id:
        args["customer_id"] = customer_id
    return await _dispatch_mcp_call("get_customer_360", args, tool_context)


async def get_service_diagnostics(
    phone_number: str = "",
    service_id: str = "",
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """Runs remote line diagnostics for Home Broadband ONT or Mobile 5G lines. Accepts subscriber phone number or subscription UUID. Returns dBm signal, packet loss, and recommended fix.

    Args:
        phone_number: Subscriber Mobile or Landline phone number to automatically diagnose their active service.
        service_id: Subscription UUID (optional if phone_number is supplied).

    Returns:
        Dictionary containing diagnostic metrics (optical dBm, latency, packet loss, outage status, remediation steps).
    """
    args: Dict[str, Any] = {}
    if phone_number:
        args["phone_number"] = phone_number
    if service_id:
        args["service_id"] = service_id
    return await _dispatch_mcp_call("get_service_diagnostics", args, tool_context)


async def run_remote_device_action(
    action: str,
    phone_number: str = "",
    device_id: str = "",
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """Executes remote maintenance commands on customer hardware: 'reboot', 'channel_optimization', or 'ping_sweep'. Accepts subscriber phone number or device UUID.

    Args:
        action: Action to execute ('reboot', 'channel_optimization', or 'ping_sweep').
        phone_number: Subscriber Mobile or Landline phone number to locate and reboot premise equipment.
        device_id: Hardware device UUID (optional if phone_number is supplied).

    Returns:
        Dictionary detailing the outcome and verification of the remote device action.
    """
    args: Dict[str, Any] = {"action": action}
    if phone_number:
        args["phone_number"] = phone_number
    if device_id:
        args["device_id"] = device_id
    return await _dispatch_mcp_call("run_remote_device_action", args, tool_context)


async def check_network_outages(
    phone_number: str = "",
    postal_code: str = "",
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """Checks for ongoing infrastructure fiber cuts or 5G cell tower maintenance in a given postal area or for a subscriber's phone number.

    Args:
        phone_number: Subscriber Mobile or Landline phone number to resolve their service area.
        postal_code: Customer 5-digit postal code (optional if phone_number is supplied).

    Returns:
        Dictionary listing active, investigating, or resolved network incidents in the area.
    """
    args: Dict[str, Any] = {}
    if phone_number:
        args["phone_number"] = phone_number
    if postal_code:
        args["postal_code"] = postal_code
    return await _dispatch_mcp_call("check_network_outages", args, tool_context)


async def get_billing_breakdown(
    phone_number: str = "",
    account_id: str = "",
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """Returns itemized invoice records, roaming fees, and payment status with masked card numbers. Accepts subscriber phone number or account ID.

    Args:
        phone_number: Subscriber Mobile or Landline phone number.
        account_id: Account UUID or Account Number (optional if phone_number is supplied).

    Returns:
        Dictionary containing billing invoices, line items, roaming surcharges, and payment history.
    """
    args: Dict[str, Any] = {}
    if phone_number:
        args["phone_number"] = phone_number
    if account_id:
        args["account_id"] = account_id
    return await _dispatch_mcp_call("get_billing_breakdown", args, tool_context)


async def get_upsell_recommendations(
    phone_number: str = "",
    customer_id: str = "",
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """Computes personalized upgrade offers (Gigabit fiber, Unlimited 5G pass, Roaming bundle) and provides agent pitch scripts. Identified by subscriber phone number or UUID.

    Args:
        phone_number: Subscriber Mobile or Landline phone number.
        customer_id: Customer UUID (optional if phone_number is supplied).

    Returns:
        Dictionary with targeted plan offers, calculated monthly savings, and agent pitch scripts.
    """
    args: Dict[str, Any] = {}
    if phone_number:
        args["phone_number"] = phone_number
    if customer_id:
        args["customer_id"] = customer_id
    return await _dispatch_mcp_call("get_upsell_recommendations", args, tool_context)


async def log_agent_interaction(
    issue_summary: str,
    resolution_summary: str,
    phone_number: str = "",
    customer_id: str = "",
    agent_name: str = "",
    call_duration_sec: int = 0,
    upsell_offered: bool = False,
    upsell_accepted: bool = False,
    tool_context: Optional[ToolContext] = None
) -> Dict[str, Any]:
    """Logs agent call notes, resolution status, and upsell outcome directly into the CRM database and audit trail. Identified by subscriber phone number or UUID.

    Args:
        issue_summary: Problem described by customer.
        resolution_summary: Solution provided by the agent.
        phone_number: Subscriber Mobile or Landline phone number.
        customer_id: Customer UUID (optional if phone_number is supplied).
        agent_name: Agent name or ID.
        call_duration_sec: Call duration in seconds.
        upsell_offered: Was an upsell offer pitched.
        upsell_accepted: Did the customer accept the upsell offer.

    Returns:
        Dictionary confirming interaction ID logged in the database and audit trail.
    """
    args: Dict[str, Any] = {
        "issue_summary": issue_summary,
        "resolution_summary": resolution_summary,
        "call_duration_sec": call_duration_sec,
        "upsell_offered": upsell_offered,
        "upsell_accepted": upsell_accepted
    }
    if phone_number:
        args["phone_number"] = phone_number
    if customer_id:
        args["customer_id"] = customer_id
    if agent_name:
        args["agent_name"] = agent_name
    return await _dispatch_mcp_call("log_agent_interaction", args, tool_context)


# Ordered list of all 8 native ADK tools matching the MCP catalog
ALL_TELECOM_TOOLS = [
    search_customer,
    get_customer_360,
    get_service_diagnostics,
    run_remote_device_action,
    check_network_outages,
    get_billing_breakdown,
    get_upsell_recommendations,
    log_agent_interaction,
]


def create_dynamic_mcp_toolset() -> McpToolset:
    """
    Summary:
        Instantiates an ADK McpToolset connecting directly to the remote FastMCP server
        over Streamable HTTP with per-call Google Identity header injection.

    Return Value:
        McpToolset: Configured ADK MCP toolset instance.
    """
    mcp_url = f"{agent_settings.MCP_SERVER_URL.rstrip('/')}/mcp"

    def dynamic_headers(ctx) -> Dict[str, str]:
        user_email = getattr(ctx, "user_id", None)
        trace_id = getattr(ctx, "invocation_id", None)
        agent_name = getattr(ctx, "agent_name", None)
        return build_mcp_headers(
            user_email=user_email,
            trace_id=trace_id,
            caller_agent=agent_name
        )

    return McpToolset(
        connection_params=StreamableHTTPConnectionParams(
            url=mcp_url,
            timeout=agent_settings.MCP_TIMEOUT_SECONDS
        ),
        header_provider=dynamic_headers
    )
