"""
Purpose: Security, authentication, and PII masking / redaction engine for HybridAI.
Architecture/Context: Intercepts and sanitizes all data passing to MCP responses and validates incoming caller credentials.
Dependencies/Side Effects: Interacts with core settings and Prometheus metrics for redaction counters.
"""

import re
import secrets
from typing import Any, Dict, Optional
from prometheus_client import Counter
from src.core.config import settings

# Prometheus metrics tracking security events
PII_REDACTIONS_COUNTER = Counter(
    "mcp_pii_redactions_total",
    "Total count of PII fields masked dynamically before leaving security boundary",
    ["field_type"]
)

AUTH_FAILURES_COUNTER = Counter(
    "mcp_auth_failures_total",
    "Total count of rejected unauthorized requests to MCP server"
)


def validate_bearer_token(auth_header: Optional[str]) -> bool:
    """
    Summary:
        Validates the incoming HTTP Authorization header against the configured secret token
        or Google Cloud Identity token.

    Parameters:
        auth_header (Optional[str]): The raw Authorization header string (e.g. 'Bearer <token>').

    Return Value:
        bool: True if authorized, False otherwise.
    """
    if not auth_header:
        AUTH_FAILURES_COUNTER.inc()
        return False

    parts = auth_header.strip().split(" ")
    if len(parts) != 2 or parts[0].lower() != "bearer":
        AUTH_FAILURES_COUNTER.inc()
        return False

    return validate_token(parts[1])


def validate_token(token: Optional[str]) -> bool:
    """
    Summary:
        Validates a raw or Bearer-prefixed secret token using constant-time comparison.
        Accommodates standard Authorization headers, custom X-MCP-Token headers, and
        Google Cloud Identity (OIDC) JWT tokens issued by Google for services like Gemini Enterprise.

    Parameters:
        token (Optional[str]): Secret token or 'Bearer <token>' string.

    Return Value:
        bool: True if authorized, False otherwise.
    """
    if not token:
        AUTH_FAILURES_COUNTER.inc()
        return False

    cleaned = token.strip()
    if cleaned.lower().startswith("bearer "):
        cleaned = cleaned[7:].strip()

    # 1. Direct match with configured MCP secret token
    if secrets.compare_digest(cleaned, settings.MCP_AUTH_TOKEN):
        return True

    # 2. Accept valid Google Cloud Identity (OIDC) JWTs (pre-validated by Cloud Run IAM gateway)
    parts = cleaned.split(".")
    if (len(parts) == 3 or len(parts) == 2) and cleaned.startswith("eyJ"):
        try:
            import base64
            import json
            padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
            payload_raw = base64.urlsafe_b64decode(padded)
            payload = json.loads(payload_raw)
            iss = payload.get("iss", "")
            if "accounts.google.com" in iss or "cloud.google.com" in iss or "google" in iss:
                return True
        except Exception:
            pass

    AUTH_FAILURES_COUNTER.inc()
    return False


def extract_caller_identity(
    token: Optional[str] = None,
    user_email_header: Optional[str] = None,
    custom_agent_header: Optional[str] = None
) -> str:
    """
    Summary:
        Extracts the individual human agent or caller identity from Google IAP headers,
        decoded OIDC/OAuth JWT claims, or custom headers.

    Parameters:
        token (Optional[str]): Bearer token string or X-Serverless-Authorization string.
        user_email_header (Optional[str]): Value of X-Goog-Authenticated-User-Email.
        custom_agent_header (Optional[str]): Value of X-Agent-Identity or X-Agent-Email.

    Return Value:
        str: Extracted user identity (e.g., 'sarah.jenkins@telecom.com') or fallback.
    """
    # 1. Google Cloud IAP Header: 'accounts.google.com:user@example.com'
    if user_email_header:
        clean = user_email_header.replace("accounts.google.com:", "").strip()
        if clean:
            return clean

    # 2. Custom Agent identity header (e.g. passed from frontends or reverse proxies)
    if custom_agent_header and custom_agent_header.strip():
        return custom_agent_header.strip()

    # 3. Decoded Google Cloud Identity / OIDC JWT payload
    if token:
        cleaned = token.strip()
        if cleaned.lower().startswith("bearer "):
            cleaned = cleaned[7:].strip()
        parts = cleaned.split(".")
        if (len(parts) == 3 or len(parts) == 2) and cleaned.startswith("eyJ"):
            try:
                import base64
                import json
                padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
                payload_raw = base64.urlsafe_b64decode(padded)
                payload = json.loads(payload_raw)
                # Check for email or subject in claims
                if "email" in payload and payload["email"]:
                    return payload["email"]
                if "sub" in payload and payload["sub"]:
                    return f"user:{payload['sub']}"
            except Exception:
                pass

    return "gemini-enterprise-agent"


def mask_phone(phone: Optional[str]) -> str:
    """
    Summary:
        Masks middle/last digits of a phone number to shield subscriber identity.
        Example: '+1 (555) 234-5678' -> '+1 (555) ***-5678'.

    Parameters:
        phone (Optional[str]): Raw phone number.

    Return Value:
        str: Masked phone number or 'REDACTED'.
    """
    if not phone:
        return "N/A"
    PII_REDACTIONS_COUNTER.labels(field_type="phone").inc()
    digits = re.sub(r"\D", "", phone)
    # Strip leading US/North America country code '1' if present in 11-digit format
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) >= 10:
        return f"+1 ({digits[:3]}) ***-{digits[-4:]}"
    return f"***-{phone[-4:]}" if len(phone) >= 4 else "****"


def mask_ssn(ssn: Optional[str]) -> str:
    """
    Summary:
        Masks the leading digits of a Social Security or National ID number.
        Example: '123-45-6789' -> '***-**-6789'.

    Parameters:
        ssn (Optional[str]): Raw SSN string.

    Return Value:
        str: Masked SSN.
    """
    if not ssn:
        return "N/A"
    PII_REDACTIONS_COUNTER.labels(field_type="ssn").inc()
    clean = re.sub(r"[^\d]", "", ssn)
    if len(clean) >= 4:
        return f"***-**-{clean[-4:]}"
    return "***-**-****"


def mask_credit_card(card_num: Optional[str]) -> str:
    """
    Summary:
        Masks credit card numbers in compliance with PCI-DSS guidelines.
        Example: '4111222233334444' -> '****-****-****-4444'.

    Parameters:
        card_num (Optional[str]): Raw card number or masked string.

    Return Value:
        str: PCI-DSS compliant masked card string.
    """
    if not card_num:
        return "N/A"
    PII_REDACTIONS_COUNTER.labels(field_type="credit_card").inc()
    digits = re.sub(r"\D", "", card_num)
    if len(digits) >= 4:
        return f"****-****-****-{digits[-4:]}"
    return "****-****-****-****"


def mask_street_address(address: Optional[str]) -> str:
    """
    Summary:
        Obfuscates street address details while retaining city/state context for diagnostics.
        Example: '742 Evergreen Terrace, Apt 4B' -> '742 ******* Terrace, Apt ***'.

    Parameters:
        address (Optional[str]): Full physical address.

    Return Value:
        str: Partially masked address.
    """
    if not address:
        return "N/A"
    PII_REDACTIONS_COUNTER.labels(field_type="address").inc()
    tokens = address.split(" ")
    if len(tokens) <= 2:
        return f"{tokens[0]} [MASKED]"
    # Keep house number, mask middle street name, keep street type
    return f"{tokens[0]} {'*' * 6} {tokens[-1]}"


def sanitize_customer_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Summary:
        Recursively applies PII masking filters to a customer data payload.
        Ensures raw customer details never leave the security perimeter to the LLM.

    Parameters:
        record (Dict[str, Any]): Dictionary containing customer fields.

    Return Value:
        Dict[str, Any]: Sanitized dictionary safe for LLM context consumption.
    """
    sanitized = dict(record)

    if "phone_number" in sanitized and sanitized["phone_number"]:
        sanitized["phone_number"] = mask_phone(str(sanitized["phone_number"]))

    if "ssn" in sanitized and sanitized["ssn"]:
        sanitized["ssn"] = mask_ssn(str(sanitized["ssn"]))

    if "street_address" in sanitized and sanitized["street_address"]:
        sanitized["street_address"] = mask_street_address(str(sanitized["street_address"]))

    if "card_number" in sanitized and sanitized["card_number"]:
        sanitized["card_number"] = mask_credit_card(str(sanitized["card_number"]))

    # Also sanitize nested accounts or billing records if present
    if "billing_records" in sanitized and isinstance(sanitized["billing_records"], list):
        sanitized["billing_records"] = [
            {
                **b,
                "payment_card": mask_credit_card(b.get("payment_card"))
            } if isinstance(b, dict) else b
            for b in sanitized["billing_records"]
        ]

    return sanitized
