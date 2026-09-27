"""
Purpose: Automated test suite verifying PII redaction, token auth, tool execution, and database seeding.
Architecture/Context: Executed locally or in CI pipelines to validate security and business logic.
                      Validates subscriber phone-number driven identity, masked names, and request/response logging.
Dependencies/Side Effects: Initializes isolated database schema and tests all core modules.
"""

import asyncio
import os
import unittest

# Ensure tests use an isolated SQLite database file
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_telecom.db"
os.environ["MCP_AUTH_TOKEN"] = "test-secret-token-12345"

from src.core.security import (
    extract_caller_identity, mask_email, mask_phone, mask_ssn,
    mask_credit_card, mask_street_address, mask_subscriber_name,
    normalize_phone_digits, validate_bearer_token, sanitize_customer_record
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
        Tests that PII redactions strictly obscure sensitive subscriber attributes
        while preserving Mobile/Landline phone numbers as unique subscriber tokens.
    """

    def test_subscriber_name_masking(self):
        masked = mask_subscriber_name("Elena Rostova")
        self.assertEqual(masked, "E**** R******")
        self.assertNotIn("Elena", masked)
        self.assertNotIn("Rostova", masked)

    def test_email_masking(self):
        masked = mask_email("elena.rostova@example.com")
        self.assertEqual(masked, "e***@***.com")
        self.assertNotIn("elena.rostova", masked)

    def test_phone_normalization(self):
        digits = normalize_phone_digits("+1 (555) 234-5678")
        self.assertEqual(digits, "5552345678")

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

    def test_sanitize_customer_record(self):
        rec = {
            "full_name": "Elena Rostova",
            "phone_number": "+1 (555) 234-5678",
            "email": "elena.rostova@example.com",
            "ssn": "123-45-6789",
            "street_address": "742 Evergreen Terrace",
            "card_number": "4111222233334444"
        }
        sanitized = sanitize_customer_record(rec)
        self.assertEqual(sanitized["subscriber_name"], "E**** R******")
        self.assertNotIn("full_name", sanitized)
        # Phone remains unmasked as unique subscriber token
        self.assertEqual(sanitized["phone_number"], "+1 (555) 234-5678")
        self.assertEqual(sanitized["email"], "e***@***.com")
        self.assertEqual(sanitized["ssn"], "***-**-6789")

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
        self.assertEqual(ctx["auth_method"], "IAM_EDGE_PERIMETER")


class TestMCPToolsAndDatabase(unittest.IsolatedAsyncioTestCase):
    """
    Summary:
        Tests async database initialization, synthetic persona seeding, and MCP tool execution
        anchored on subscriber phone number identity and masked name responses.
    """

    async def asyncSetUp(self):
        await init_db()
        await seed_synthetic_telecom_data()

    async def test_search_customer_by_phone(self):
        # Search by exact phone number
        res = await search_customer_tool(phone_number="+1 (555) 234-5678", caller_id="test-agent")
        self.assertEqual(res["status"], "success")
        self.assertGreaterEqual(res["count"], 1)
        first_match = res["customers"][0]
        # Phone number must be unmasked
        self.assertIn("5552345678", first_match["phone_number"])
        # Subscriber name must be masked
        self.assertEqual(first_match["subscriber_name"], "E**** R******")
        self.assertNotIn("Elena", first_match["subscriber_name"])
        # Email must be masked
        self.assertEqual(first_match["email"], "e***@***.com")

        # Test search by query containing phone digits
        phone_res = await search_customer_tool(query="5552345678", caller_id="test-agent")
        self.assertEqual(phone_res["status"], "success")
        self.assertEqual(phone_res["count"], 1)
        self.assertEqual(phone_res["customers"][0]["subscriber_name"], "E**** R******")

        # Test search by Account Number
        acc_res = await search_customer_tool(query="TEL-ACC-88129", caller_id="test-agent")
        self.assertEqual(acc_res["status"], "success")
        self.assertEqual(acc_res["count"], 1)
        self.assertEqual(acc_res["customers"][0]["subscriber_name"], "E**** R******")

    async def test_customer_360_and_diagnostics_by_phone(self):
        # Run 360 profile directly with phone number
        c360 = await get_customer_360_tool(phone_number="+1 (555) 234-5678")
        self.assertEqual(c360["status"], "success")
        profile = c360["customer_360"]
        self.assertEqual(profile["loyalty_tier"], "Gold")
        self.assertIn("5552345678", profile["phone_number"])
        self.assertEqual(profile["subscriber_name"], "E**** R******")
        self.assertNotIn("Elena", profile["subscriber_name"])
        self.assertEqual(profile["email"], "e***@***.com")
        self.assertIn("***", profile["ssn"])

        # Check diagnostics driven directly by phone number
        diag = await get_service_diagnostics_tool(phone_number="+1 (555) 234-5678")
        self.assertEqual(diag["status"], "success")
        dev_diag = diag["diagnostics"][0]
        self.assertEqual(dev_diag["root_cause_diagnosis"], "OPTICAL_SIGNAL_DEGRADATION")

        # Execute remote reboot action directly by phone number
        action_res = await run_remote_device_action_tool(phone_number="+1 (555) 234-5678", action="reboot")
        self.assertEqual(action_res["status"], "success")
        self.assertEqual(action_res["updated_health_status"], "HEALTHY")

    async def test_network_outage_tool_by_phone(self):
        # Outage check driven by phone number (resolves 97477)
        outage_res = await check_network_outages_tool(phone_number="+1 (555) 234-5678")
        self.assertEqual(outage_res["status"], "success")

        # Outage check by explicit postal code
        outage_postal = await check_network_outages_tool(postal_code="98101")
        self.assertEqual(outage_postal["status"], "success")
        self.assertTrue(outage_postal["has_active_outage"])

    async def test_upsell_recommendations_by_phone(self):
        # Marcus Vance (+15559871234, throttled mobile 5G)
        upsell = await get_upsell_recommendations_tool(phone_number="+1 (555) 987-1234")
        self.assertEqual(upsell["status"], "success")
        self.assertIn("5559871234", upsell["phone_number"])
        self.assertEqual(upsell["subscriber_name"], "M***** V****")
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

    def test_execute_endpoint_by_phone(self):
        payload = {
            "name": "search_customer",
            "arguments": {
                "phone_number": "+1 (555) 234-5678",
                "agent_email": "admin@pradeesi.altostrat.com"
            }
        }
        res = self.client.post("/execute", json=payload, headers=self.auth_headers)
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["result"]["status"], "success")
        self.assertGreaterEqual(data["result"]["count"], 1)
        cust = data["result"]["customers"][0]
        self.assertIn("5552345678", cust["phone_number"])
        self.assertEqual(cust["subscriber_name"], "E**** R******")


if __name__ == "__main__":
    unittest.main()
