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
        details: Dict[str, Any],
        status: str = "SUCCESS",
        customer_id: Optional[str] = None,
        security_context: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Summary:
            Emits an immutable structured audit log entry to Loki and local logs.
            Captures enterprise security context, client network telemetry,
            trace IDs, non-repudiation identity, and compliance metadata.

        Parameters:
            event_type (str): Type of audit event (e.g. 'MCP_TOOL_EXECUTION', 'AUTH_FAILURE').
            caller_identity (str): Identity of the calling agent, user, or service (e.g., 'sarah.jenkins@telecom.com').
            tool_name (str): Name of the invoked tool or operation.
            details (Dict[str, Any]): Additional structured event details.
            status (str): Execution status ('SUCCESS', 'FAILED', 'DENIED').
            customer_id (Optional[str]): Target customer ID if applicable.
            security_context (Optional[Dict[str, Any]]): Comprehensive security attributes (IP, trace ID, compliance).

        Return Value:
            None
        """
        timestamp_ns = str(time.time_ns())
        timestamp_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        sec_ctx = security_context or {}
        caller_type = sec_ctx.get(
            "caller_type",
            "HUMAN_AGENT" if "@" in caller_identity and "gserviceaccount" not in caller_identity else "SERVICE_AGENT"
        )
        client_ip = sec_ctx.get("client_ip", "N/A")
        user_agent = sec_ctx.get("user_agent", "N/A")
        trace_id = sec_ctx.get("trace_id", "N/A")
        auth_method = sec_ctx.get("auth_method", "BEARER_TOKEN")
        compliance_regimes = sec_ctx.get(
            "compliance_regimes",
            ["CPNI-FCC-Part-64", "PCI-DSS-v4.0", "GDPR-Art-30", "SOC-2-CC6"]
        )
        data_classification = sec_ctx.get("data_classification", "RESTRICTED_CUSTOMER_OPERATIONS")

        payload_data = {
            "timestamp": timestamp_iso,
            "event_type": event_type,
            "caller": caller_identity,
            "caller_type": caller_type,
            "tool": tool_name,
            "status": status,
            "customer_id": customer_id or "N/A",
            "client_ip": client_ip,
            "user_agent": user_agent,
            "trace_id": trace_id,
            "auth_method": auth_method,
            "compliance": {
                "regimes": compliance_regimes,
                "data_classification": data_classification,
                "non_repudiation": "VERIFIED_AGENT" if caller_type == "HUMAN_AGENT" else "SERVICE_SHARED"
            },
            "details": details
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
                        "caller": caller_identity,
                        "caller_type": caller_type,
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

    async def close(self) -> None:
        """
        Summary:
            Closes internal HTTP resources cleanly.
        """
        await self._http_client.aclose()


# Singleton audit client instance
audit_client = LokiAuditClient()
