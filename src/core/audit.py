"""
Purpose: Enterprise immutable audit logging engine with direct Grafana Loki streaming.
Architecture/Context: Dispatches structured JSON audit trails for every security event, data access, and MCP tool call.
Dependencies/Side Effects: Asynchronously ships log streams to Loki via HTTP; falls back to standard structured console logs.
"""

import asyncio
import json
import logging
import time
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

    async def log_event(
        self,
        event_type: str,
        caller_identity: str,
        tool_name: str,
        details: Dict[str, Any],
        status: str = "SUCCESS",
        customer_id: Optional[str] = None
    ) -> None:
        """
        Summary:
            Emits an immutable structured audit log entry to Loki and local logs.

        Parameters:
            event_type (str): Type of audit event (e.g. 'MCP_TOOL_EXECUTION', 'AUTH_FAILURE').
            caller_identity (str): Identity of the calling agent or service (e.g., 'gemini-enterprise-app').
            tool_name (str): Name of the invoked tool or operation.
            details (Dict[str, Any]): Additional structured event details.
            status (str): Execution status ('SUCCESS', 'FAILED', 'DENIED').
            customer_id (Optional[str]): Target customer ID if applicable.

        Return Value:
            None
        """
        timestamp_ns = str(time.time_ns())
        timestamp_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        payload_data = {
            "timestamp": timestamp_iso,
            "event_type": event_type,
            "caller": caller_identity,
            "tool": tool_name,
            "status": status,
            "customer_id": customer_id or "N/A",
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
                        "status": status
                    },
                    "values": [
                        [timestamp_ns, log_line]
                    ]
                }
            ]
        }

        try:
            # Dispatch non-blocking HTTP push
            response = await self._http_client.post(
                self._push_endpoint,
                json=loki_payload,
                headers={"Content-Type": "application/json"}
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
