"""
Purpose: Enterprise immutable audit logging engine with direct Grafana Loki streaming.
Architecture/Context: Dispatches structured JSON audit trails for every security event, data access, and MCP tool call.
Dependencies/Side Effects: Asynchronously ships log streams to Loki via HTTP; falls back to standard structured console logs.
"""

import asyncio
import json
import logging
import subprocess
import time
import urllib.request
from typing import Any, Dict, Optional
import httpx
from src.core.config import settings

# Configure structured system logger
logger = logging.getLogger("hybrid_ai.audit")
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
)


class LokiAuditClient:
    """
    Summary:
        Lightweight client for asynchronously pushing structured log streams into Grafana Loki.
        Guarantees that audit trails are persistent, indexed by labels, and queryable in real time.
    """

    def __init__(self, loki_url: Optional[str] = None):
        """
        Summary:
            Initializes the Loki client with endpoint URL and async HTTP client.

        Parameters:
            loki_url (Optional[str]): Base URL of the Loki service (e.g., http://localhost:3100).
        """
        self.loki_url = loki_url or settings.LOKI_URL
        self._push_endpoint = f"{self.loki_url.rstrip('/')}/loki/api/v1/push" if self.loki_url else None
        # HTTP client with short timeouts so audit streaming never impedes response latencies
        self._http_client = httpx.AsyncClient(timeout=2.0)
        self._cached_token = ""
        self._token_expiry = 0.0

    def _get_auth_header(self) -> Dict[str, str]:
        """
        Summary:
            Retrieves an Authorization header with GCP Bearer ID token if target is Cloud Run.

        Return Value:
            Dict[str, str]: Dictionary containing Authorization header if applicable.
        """
        if not self.loki_url or not self.loki_url.startswith("https://"):
            return {}

        now = time.time()
        if self._cached_token and now < self._token_expiry:
            return {"Authorization": f"Bearer {self._cached_token}"}

        # 1. Fetch from Cloud Run instance metadata server
        try:
            req = urllib.request.Request(
                f"http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/identity?audience={self.loki_url}",
                headers={"Metadata-Flavor": "Google"}
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                self._cached_token = resp.read().decode("utf-8").strip()
                self._token_expiry = now + 3000.0  # 50 minutes cache
                return {"Authorization": f"Bearer {self._cached_token}"}
        except Exception:
            pass

        # 2. Local fallback to gcloud if running on developer workstation
        try:
            res = subprocess.run(["gcloud", "auth", "print-identity-token"], capture_output=True, text=True, timeout=2.0)
            if res.returncode == 0:
                self._cached_token = res.stdout.strip()
                self._token_expiry = now + 3000.0
                return {"Authorization": f"Bearer {self._cached_token}"}
        except Exception:
            pass

        return {}

    async def log_event(
        self,
        event_type: str,
        caller_identity: str,
        tool_name: str,
        details: Optional[Dict[str, Any]] = None,
        status: str = "SUCCESS",
        customer_id: Optional[str] = None,
        security_context: Optional[Dict[str, Any]] = None,
        request_payload: Optional[Dict[str, Any]] = None,
        response_payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Summary:
            Emits an immutable structured audit log entry to Loki and local logs.
            Captures enterprise security context, client network telemetry,
            request payload, response payload, caller identity, and tool outcome.

        Parameters:
            event_type (str): Type of audit event (e.g. 'MCP_TOOL_EXECUTION', 'AUTH_FAILURE').
            caller_identity (str): Identity of the calling agent, user, or service.
            tool_name (str): Name of the invoked tool or operation.
            details (Optional[Dict[str, Any]]): Additional structured event details.
            status (str): Execution status ('SUCCESS', 'FAILED', 'DENIED').
            customer_id (Optional[str]): Target customer ID if applicable.
            security_context (Optional[Dict[str, Any]]): Comprehensive security attributes (IP, trace ID).
            request_payload (Optional[Dict[str, Any]]): Exact tool arguments sent by caller.
            response_payload (Optional[Dict[str, Any]]): Exact response payload returned to caller.

        Return Value:
            None
        """
        timestamp_ns = str(time.time_ns())
        timestamp_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        sec_ctx = security_context or {}
        caller = sec_ctx.get("caller", caller_identity or "gemini-enterprise-agent")
        caller_type = sec_ctx.get("caller_type", "AI_AGENT")
        end_user_email = sec_ctx.get("end_user_email", "admin@pradeesi.altostrat.com")
        end_user_id = sec_ctx.get("end_user_id", "N/A")
        client_ip = sec_ctx.get("client_ip", "N/A")
        user_agent = sec_ctx.get("user_agent", "N/A")
        trace_id = sec_ctx.get("trace_id", "N/A")
        auth_method = sec_ctx.get("auth_method", "BEARER_TOKEN")

        req = request_payload if request_payload is not None else (details or {})
        resp = response_payload if response_payload is not None else {}

        payload_data = {
            "timestamp": timestamp_iso,
            "event_type": event_type,
            "caller": caller,
            "caller_type": caller_type,
            "end_user_email": end_user_email,
            "end_user_id": end_user_id,
            "tool": tool_name,
            "status": status,
            "customer_id": customer_id or "N/A",
            "client_ip": client_ip,
            "user_agent": user_agent,
            "trace_id": trace_id,
            "auth_method": auth_method,
            "request": req,
            "response": resp,
            "details": details or {}
        }
        log_line = json.dumps(payload_data)

        # Log locally first for guaranteed container stdout collection
        logger.info("AUDIT_TRAIL: %s", log_line)

        # Ship asynchronously to Loki if endpoint is configured
        if not self._push_endpoint:
            return

        loki_payload = {
            "streams": [
                {
                    "stream": {
                        "job": "telecom-mcp-audit",
                        "app": "hybrid-ai",
                        "env": settings.APP_ENV,
                        "event_type": event_type,
                        "tool": tool_name,
                        "caller": caller,
                        "caller_type": caller_type,
                        "end_user": end_user_email,
                        "status": status,
                        "auth_method": auth_method
                    },
                    "values": [
                        [timestamp_ns, log_line]
                    ]
                }
            ]
        }

        headers = {"Content-Type": "application/json"}
        headers.update(self._get_auth_header())

        try:
            # Dispatch non-blocking HTTP push
            response = await self._http_client.post(
                self._push_endpoint,
                json=loki_payload,
                headers=headers
            )
            if response.status_code not in (200, 204):
                logger.warning("Loki returned non-success response %d: %s", response.status_code, response.text)
        except Exception as exc:
            # Fallback defensively: audit log is already recorded to stdout, avoid crashing caller
            logger.debug("Loki shipping bypassed (service unreachable): %s", exc)

    async def log_http_request(
        self,
        endpoint: str,
        method: str,
        headers: Dict[str, str],
        body: Any,
        caller_identity: str,
        security_context: Optional[Dict[str, Any]] = None,
        jwt_claims: Optional[Dict[str, Any]] = None,
        status: str = "RECEIVED"
    ) -> None:
        """
        Summary:
            Logs the complete incoming HTTP request including all headers and full message body to Loki.
            Allows operators to inspect raw Gemini Enterprise payloads, headers, and authentication claims.

        Parameters:
            endpoint (str): Request URL path (e.g. '/mcp', '/messages', '/execute').
            method (str): HTTP method ('POST', 'GET').
            headers (Dict[str, str]): Complete incoming HTTP request headers mapping.
            body (Any): Complete message body (parsed JSON or raw text).
            caller_identity (str): Resolved identity of the calling agent.
            security_context (Optional[Dict[str, Any]]): Security and network context (IP, trace ID, auth method).
            jwt_claims (Optional[Dict[str, Any]]): Decoded JWT claims from Authorization token if available.
            status (str): Processing status of the incoming message ('RECEIVED', 'PROCESSED', 'FAILED').

        Return Value:
            None
        """
        timestamp_ns = str(time.time_ns())
        timestamp_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        sec_ctx = security_context or {}
        caller = sec_ctx.get("caller", caller_identity or "gemini-enterprise-agent")
        caller_type = sec_ctx.get("caller_type", "AI_AGENT")
        end_user_email = sec_ctx.get("end_user_email", "admin@pradeesi.altostrat.com")
        end_user_id = sec_ctx.get("end_user_id", "N/A")
        client_ip = sec_ctx.get("client_ip", "N/A")
        user_agent = sec_ctx.get("user_agent", "N/A")
        trace_id = sec_ctx.get("trace_id", "N/A")
        auth_method = sec_ctx.get("auth_method", "BEARER_TOKEN")

        # Sanitize sensitive static server tokens from authorization header display if needed
        sanitized_headers = dict(headers)
        if "authorization" in sanitized_headers:
            auth_val = sanitized_headers["authorization"]
            if settings.MCP_AUTH_TOKEN and settings.MCP_AUTH_TOKEN in auth_val:
                sanitized_headers["authorization"] = "Bearer [STATIC_MCP_AUTH_TOKEN]"

        payload_data = {
            "timestamp": timestamp_iso,
            "event_type": "MCP_HTTP_REQUEST",
            "endpoint": endpoint,
            "method": method,
            "caller": caller,
            "caller_type": caller_type,
            "end_user_email": end_user_email,
            "end_user_id": end_user_id,
            "client_ip": client_ip,
            "user_agent": user_agent,
            "trace_id": trace_id,
            "auth_method": auth_method,
            "status": status,
            "headers": sanitized_headers,
            "jwt_claims": jwt_claims or {},
            "body": body
        }
        log_line = json.dumps(payload_data, default=str)

        # Log locally for guaranteed container stdout collection
        logger.info("AUDIT_HTTP_REQUEST: %s", log_line)

        # Ship asynchronously to Loki if endpoint is configured
        if not self._push_endpoint:
            return

        loki_payload = {
            "streams": [
                {
                    "stream": {
                        "job": "telecom-mcp-audit",
                        "app": "hybrid-ai",
                        "env": settings.APP_ENV,
                        "event_type": "MCP_HTTP_REQUEST",
                        "tool": endpoint,
                        "caller": caller,
                        "caller_type": caller_type,
                        "end_user": end_user_email,
                        "status": status,
                        "auth_method": auth_method
                    },
                    "values": [
                        [timestamp_ns, log_line]
                    ]
                }
            ]
        }

        req_headers = {"Content-Type": "application/json"}
        req_headers.update(self._get_auth_header())

        try:
            response = await self._http_client.post(
                self._push_endpoint,
                json=loki_payload,
                headers=req_headers
            )
            if response.status_code not in (200, 204):
                logger.warning("Loki returned non-success response %d for HTTP audit: %s", response.status_code, response.text)
        except Exception as exc:
            logger.debug("Loki HTTP shipping bypassed: %s", exc)

    async def close(self) -> None:
        """
        Summary:
            Closes internal HTTP resources cleanly.
        """
        await self._http_client.aclose()


# Singleton audit client instance
audit_client = LokiAuditClient()
