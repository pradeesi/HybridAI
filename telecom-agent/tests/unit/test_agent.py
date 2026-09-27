"""
Purpose: Unit tests verifying Telecom ADK Agent configuration, tools catalog, and headers generation.
Architecture/Context: Executed via pytest during CI/CD presubmits and local developer testing.
Dependencies/Side Effects: Imports telecom-agent modules; mocks HTTP requests to MCP server.
"""

import pytest
from unittest.mock import AsyncMock, patch

from app.agent import root_agent, TELECOM_AGENT_INSTRUCTION
from app.config import agent_settings
from app.tools import (
    ALL_TELECOM_TOOLS,
    build_mcp_headers,
    search_customer,
    get_customer_360,
    get_service_diagnostics,
    run_remote_device_action,
    check_network_outages,
    get_billing_breakdown,
    get_upsell_recommendations,
    log_agent_interaction
)


def test_agent_structure_and_tools():
    """
    Summary:
        Verifies the ADK Agent name, tool count, and exact registered tool definitions.
    """
    assert root_agent.name == "telecom_agent"
    assert len(root_agent.tools) == 8

    expected_tool_names = {
        "search_customer",
        "get_customer_360",
        "get_service_diagnostics",
        "run_remote_device_action",
        "check_network_outages",
        "get_billing_breakdown",
        "get_upsell_recommendations",
        "log_agent_interaction"
    }
    actual_tool_names = {t.__name__ for t in root_agent.tools}
    assert actual_tool_names == expected_tool_names


def test_strict_scope_instruction():
    """
    Summary:
        Ensures the system instruction mandates strict domain boundaries and rejection
        of out-of-scope requests.
    """
    assert "STRICT OPERATIONAL DOMAIN & SCOPE BOUNDARY" in TELECOM_AGENT_INSTRUCTION
    assert "This requested functionality is not available" in TELECOM_AGENT_INSTRUCTION
    assert "search_customer" in TELECOM_AGENT_INSTRUCTION
    assert "log_agent_interaction" in TELECOM_AGENT_INSTRUCTION


def test_build_mcp_headers():
    """
    Summary:
        Tests that audit and identity headers are correctly synthesized for Loki and Prometheus compliance.
    """
    headers = build_mcp_headers(
        user_email="operator@telecom.com",
        trace_id="trace-test-12345",
        caller_agent="telecom_agent"
    )

    assert headers["X-Agent-Identity"] == "telecom-customer-care-agent"
    assert headers["X-Agent-Name"] == "telecom_agent"
    assert headers["X-Goog-Authenticated-User-Email"] == "operator@telecom.com"
    assert headers["X-End-User-Email"] == "operator@telecom.com"
    assert headers["X-Cloud-Trace-Context"] == "trace-test-12345/0;o=1"
    assert headers["X-Trace-ID"] == "trace-test-12345"
    assert "Authorization" in headers


@pytest.mark.asyncio
async def test_tool_dispatch_mocked():
    """
    Summary:
        Tests that tool functions serialize inputs and unpack JSON-RPC responses cleanly.
    """
    mock_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "content": [
                {
                    "type": "text",
                    "text": '{"status": "success", "count": 1, "customers": [{"customer_id": "test-uuid"}]}'
                }
            ]
        }
    }

    import httpx
    mock_resp = httpx.Response(200, json=mock_response)
    with patch.object(httpx.AsyncClient, "post", return_value=mock_resp):
        res = await search_customer(phone_number="+1-555-0199")
        assert res["status"] == "success"
        assert res["customers"][0]["customer_id"] == "test-uuid"
