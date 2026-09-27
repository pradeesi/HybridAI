"""
Purpose: Automated test suite verifying PII redaction, token auth, tool execution, and database seeding.
Architecture/Context: Executed locally or in CI pipelines to validate security and business logic.
Dependencies/Side Effects: Initializes isolated database schema and tests all core modules.
"""

import asyncio
import os
import unittest

# Ensure tests use an isolated SQLite database file
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_telecom.db"
os.environ["MCP_AUTH_TOKEN"] = "test-secret-token-12345"

from src.core.security import (
    extract_caller_identity, mask_phone, mask_ssn, mask_credit_card, mask_street_address,
    validate_bearer_token, sanitize_customer_record
)
from src.db.database import init_db
from src.db.seed_data import seed_synthetic_telecom_data
from src.mcp.tools import (
    search_customer_tool, get_customer_360_tool, get_service_diagnostics_tool,
    run_remote_device_action_tool, check_network_outages_tool,
    get_upsell_recommendations_tool, log_agent_interaction_tool
)


class TestSecurityAndPII(unittest.TestCase):
    """
    Summary:
        Tests that PII redactions strictly obscure sensitive subscriber attributes.
    """

    def test_phone_masking(self):
        masked = mask_phone("+15552345678")
        self.assertIn("***", masked)
        self.assertTrue(masked.startswith("+1 (555)"))

    def test_ssn_masking(self):
        masked = mask_ssn("123-45-6789")
        self.assertEqual(masked, "***-**-6789")

    def test_credit_card_masking(self):
        masked = mask_credit_card("4111222233334444")
        self.assertEqual(masked, "****-****-****-4444")

    def test_street_address_masking(self):
        masked = mask_street_address("742 Evergreen Terrace, Apt 4B")
        self.assertIn("******", masked)
        self.assertTrue(masked.startswith("742"))

    def test_bearer_token_validation(self):
        # Valid token
        self.assertTrue(validate_bearer_token("Bearer test-secret-token-12345"))
        # Invalid token
        self.assertFalse(validate_bearer_token("Bearer wrong-token"))
        # Missing header
        self.assertFalse(validate_bearer_token(None))
        # Malformed header
        self.assertFalse(validate_bearer_token("Basic test-secret-token-12345"))

    def test_extract_caller_identity(self):
        # 1. Google Cloud IAP header with prefix
        iap_header = "accounts.google.com:sarah.jenkins@telecom.com"
        self.assertEqual(extract_caller_identity(user_email_header=iap_header), "sarah.jenkins@telecom.com")

        # 2. Custom Agent Identity header
        custom_header = "executive-mike@telecom.com"
        self.assertEqual(extract_caller_identity(custom_agent_header=custom_header), "executive-mike@telecom.com")

        # 3. Explicit payload user from tool argument or MCP _meta
        self.assertEqual(extract_caller_identity(payload_user="admin@pradeesi.altostrat.com"), "admin@pradeesi.altostrat.com")

        # 4. Fallback when no headers present
        self.assertEqual(extract_caller_identity(), "gemini-enterprise-agent")

    def test_extract_security_context(self):
        from src.core.security import extract_security_context
        headers = {
            "x-forwarded-for": "173.194.96.179, 10.0.0.1",
            "x-cloud-trace-context": "1d78db52281b503501f0dcc110a0d99b/12345;o=1",
            "user-agent": "python-httpx/0.27.0"
        }
        ctx = extract_security_context(
            headers=headers,
            payload_user="admin@pradeesi.altostrat.com"
        )
        self.assertEqual(ctx["caller_identity"], "admin@pradeesi.altostrat.com")
        self.assertEqual(ctx["caller_type"], "HUMAN_AGENT")
        self.assertEqual(ctx["client_ip"], "173.194.96.179")
        self.assertEqual(ctx["trace_id"], "1d78db52281b503501f0dcc110a0d99b")
        self.assertIn("CPNI-FCC-Part-64", ctx["compliance_regimes"])


class TestMCPToolsAndDatabase(unittest.IsolatedAsyncioTestCase):
    """
    Summary:
        Tests async database initialization, synthetic persona seeding, and MCP tool execution.
    """

    async def asyncSetUp(self):
        await init_db()
        await seed_synthetic_telecom_data()

    async def test_search_customer_tool(self):
        res = await search_customer_tool(query="Elena", caller_id="test-agent")
        self.assertEqual(res["status"], "success")
        self.assertGreaterEqual(res["count"], 1)
        first_match = res["customers"][0]
        self.assertIn("Elena", first_match["full_name"])
        self.assertIn("***", first_match["phone_masked"])

        # Test disambiguation by Account Number
        acc_res = await search_customer_tool(query="TEL-ACC-88129", caller_id="test-agent")
        self.assertEqual(acc_res["status"], "success")
        self.assertEqual(acc_res["count"], 1)
        self.assertIn("Elena", acc_res["customers"][0]["full_name"])

        # Test disambiguation by Phone Number
        phone_res = await search_customer_tool(query="5552345678", caller_id="test-agent")
        self.assertEqual(phone_res["status"], "success")
        self.assertEqual(phone_res["count"], 1)
        self.assertIn("Elena", phone_res["customers"][0]["full_name"])

    async def test_customer_360_and_diagnostics(self):
        # Find Elena Rostova
        search = await search_customer_tool(query="Rostova")
        customer_id = search["customers"][0]["customer_id"]

        # Run 360 profile
        c360 = await get_customer_360_tool(customer_id=customer_id)
        self.assertEqual(c360["status"], "success")
        profile = c360["customer_360"]
        self.assertEqual(profile["loyalty_tier"], "Gold")
        self.assertIn("***", profile["ssn_masked"])

        # Check diagnostics on active broadband subscription
        sub = profile["accounts"][0]["subscriptions"][0]
        service_id = sub["subscription_id"]
        diag = await get_service_diagnostics_tool(service_id=service_id)
        self.assertEqual(diag["status"], "success")
        dev_diag = diag["diagnostics"][0]
        self.assertEqual(dev_diag["root_cause_diagnosis"], "OPTICAL_SIGNAL_DEGRADATION")

        # Execute remote reboot action
        device_id = dev_diag["device_id"]
        action_res = await run_remote_device_action_tool(device_id=device_id, action="reboot")
        self.assertEqual(action_res["status"], "success")
        self.assertEqual(action_res["updated_health_status"], "HEALTHY")

    async def test_network_outage_tool(self):
        outage_res = await check_network_outages_tool(postal_code="98101")
        self.assertEqual(outage_res["status"], "success")
        self.assertTrue(outage_res["has_active_outage"])

    async def test_upsell_recommendations(self):
        # Marcus Vance (Throttled mobile 5G)
        search = await search_customer_tool(query="Marcus")
        customer_id = search["customers"][0]["customer_id"]

        upsell = await get_upsell_recommendations_tool(customer_id=customer_id)
        self.assertEqual(upsell["status"], "success")
        self.assertGreaterEqual(len(upsell["recommendations"]), 1)
        rec = upsell["recommendations"][0]
        self.assertIn("Unlimited 5G", rec["title"])


class TestMCPEndpoints(unittest.TestCase):
    """
    Summary:
        Tests FastAPI HTTP and JSON-RPC endpoints for MCP specification compliance.
    """

    def setUp(self):
        from fastapi.testclient import TestClient
        from src.mcp.server import app
        self.client = TestClient(app)
        self.auth_headers = {"Authorization": "Bearer test-secret-token-12345"}

    def test_mcp_initialize(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"}
        }
        res = self.client.post("/mcp", json=payload, headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["jsonrpc"], "2.0")
        self.assertEqual(data["id"], 1)
        self.assertEqual(data["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(data["result"]["serverInfo"]["name"], "telecom-mcp-server")

    def test_mcp_tools_list(self):
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        res = self.client.post("/mcp", json=payload, headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertIn("tools", data["result"])
        tool_names = [t["name"] for t in data["result"]["tools"]]
        self.assertIn("search_customer", tool_names)
        self.assertIn("get_customer_360", tool_names)

    def test_mcp_get_discovery(self):
        res = self.client.get("/mcp", headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["status"], "healthy")
        self.assertIn("tools", data)


if __name__ == "__main__":
    unittest.main()
