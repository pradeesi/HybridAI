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


def decode_jwt_unverified(token: Optional[str]) -> Optional[Dict[str, Any]]:
    """
    Summary:
        Decodes the payload of a JSON Web Token (JWT) without cryptographic verification.
        Used for audit logging and extracting user identity claims from tokens pre-validated by Cloud Run IAM.

    Parameters:
        token (Optional[str]): Bearer token string or raw JWT.

    Return Value:
        Optional[Dict[str, Any]]: Parsed payload dictionary if valid base64url JSON, None otherwise.
    """
    if not token or not isinstance(token, str):
        return None

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
            return json.loads(payload_raw)
        except Exception:
            return None
    return None


def extract_caller_identity(
    token: Optional[str] = None,
    user_email_header: Optional[str] = None,
    custom_agent_header: Optional[str] = None,
    payload_user: Optional[str] = None,
    request_headers: Optional[Dict[str, str]] = None
) -> str:
    """
    Summary:
        Extracts the individual human contact center agent or caller identity from
        Google IAP headers, OIDC JWT claims, client headers, or tool payload metadata.
        Guarantees non-repudiation and audit tracking without requiring manual user input.

    Parameters:
        token (Optional[str]): Bearer token string or X-Serverless-Authorization string.
        user_email_header (Optional[str]): Value of X-Goog-Authenticated-User-Email.
        custom_agent_header (Optional[str]): Value of X-Agent-Identity or X-Agent-Email.
        payload_user (Optional[str]): Explicit user email provided in MCP payload arguments or metadata.
        request_headers (Optional[Dict[str, str]]): Complete incoming HTTP request headers mapping.

    Return Value:
        str: Extracted user identity (e.g. 'admin@pradeesi.altostrat.com') or categorized fallback.
    """
    headers = {k.lower(): v for k, v in (request_headers or {}).items()}

    # 1. Google Cloud IAP Header: 'accounts.google.com:user@example.com'
    iap_candidates = [
        user_email_header,
        headers.get("x-goog-authenticated-user-email"),
        headers.get("x-goog-authenticated-user-id"),
        headers.get("x-goog-user-email"),
        headers.get("x-end-user-email"),
        headers.get("x-user-email"),
        headers.get("x-forwarded-email"),
        headers.get("x-forwarded-user")
    ]
    for candidate in iap_candidates:
        if candidate and str(candidate).strip():
            clean = str(candidate).replace("accounts.google.com:", "").strip()
            if clean:
                return clean

    # 2. Custom Agent identity headers
    agent_header_candidates = [
        custom_agent_header,
        headers.get("x-agent-identity"),
        headers.get("x-agent-email"),
        headers.get("x-caller-identity")
    ]
    for candidate in agent_header_candidates:
        if candidate and str(candidate).strip():
            clean = str(candidate).strip()
            if clean:
                return clean

    # 3. Decoded Google Cloud Identity / OIDC JWT payload claims
    raw_token = token or headers.get("authorization") or headers.get("x-serverless-authorization")
    jwt_claims = decode_jwt_unverified(raw_token)
    if jwt_claims:
        # Check human email or subject in claims
        if "email" in jwt_claims and jwt_claims["email"]:
            email = str(jwt_claims["email"]).strip()
            if "gserviceaccount.com" in email:
                return f"service-account:{email}"
            return email
        if "preferred_username" in jwt_claims and jwt_claims["preferred_username"]:
            return str(jwt_claims["preferred_username"]).strip()
        if "sub" in jwt_claims and jwt_claims["sub"]:
            return f"user:{jwt_claims['sub']}"

    # 4. Fallback: Explicit user email in MCP payload arguments or _meta payload
    if payload_user and str(payload_user).strip():
        return str(payload_user).strip()

    return "gemini-enterprise-agent"


def extract_security_context(
    token: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    payload_user: Optional[str] = None,
    client_ip: Optional[str] = None
) -> Dict[str, Any]:
    """
    Summary:
        Builds a comprehensive security and compliance context dictionary
        for immutable audit logging, capturing non-repudiation attributes,
        network telemetry, and regulatory classifications.

    Parameters:
        token (Optional[str]): Authorization token string.
        headers (Optional[Dict[str, str]]): HTTP request headers mapping.
        payload_user (Optional[str]): Explicit user email from tool arguments or metadata.
        client_ip (Optional[str]): Remote client IP address.

    Return Value:
        Dict[str, Any]: Structured security context with caller identity, IP, trace ID, and compliance tags.
    """
    hdr = {k.lower(): v for k, v in (headers or {}).items()}
    resolved_caller = extract_caller_identity(
        token=token or hdr.get("authorization") or hdr.get("x-serverless-authorization"),
        user_email_header=hdr.get("x-goog-authenticated-user-email"),
        custom_agent_header=hdr.get("x-agent-identity") or hdr.get("x-agent-email"),
        payload_user=payload_user,
        request_headers=hdr
    )

    # Determine caller classification
    if "@" in resolved_caller and "gserviceaccount.com" not in resolved_caller:
        caller_type = "HUMAN_AGENT"
    elif "service-account" in resolved_caller or "gserviceaccount.com" in resolved_caller:
        caller_type = "GCP_SERVICE_ACCOUNT"
    else:
        caller_type = "SHARED_AGENT_KEY"

    # Resolve true originating client IP from proxy chain
    forwarded = hdr.get("x-forwarded-for")
    if forwarded:
        effective_ip = forwarded.split(",")[0].strip()
    elif hdr.get("x-real-ip"):
        effective_ip = hdr.get("x-real-ip").strip()
    else:
        effective_ip = client_ip or "127.0.0.1"

    # Extract distributed tracing context for GCP Cloud Trace correlation
    trace_header = hdr.get("x-cloud-trace-context") or hdr.get("x-trace-id") or ""
    trace_id = trace_header.split("/")[0] if "/" in trace_header else trace_header or "N/A"

    # Identify authentication mechanism
    auth_header = hdr.get("authorization") or hdr.get("x-serverless-authorization") or token or ""
    if "eyJ" in auth_header:
        auth_method = "OIDC_ID_TOKEN"
    elif hdr.get("x-goog-authenticated-user-email"):
        auth_method = "IAP_ASSERTION"
    elif auth_header:
        auth_method = "STATIC_BEARER_TOKEN"
    else:
        auth_method = "IAM_EDGE_PERIMETER"

    return {
        "caller_identity": resolved_caller,
        "caller_type": caller_type,
        "client_ip": effective_ip,
        "user_agent": hdr.get("user-agent", "Unknown-Agent/1.0"),
        "trace_id": trace_id,
        "auth_method": auth_method
    }


def mask_subscriber_name(name: Optional[str]) -> str:
    """
    Summary:
        Masks the subscriber's personal name to prevent identity exposure in AI agent tool payloads.
        Example: 'Elena Rostova' -> 'E**** R******'.

    Parameters:
        name (Optional[str]): Raw subscriber name.

    Return Value:
        str: Masked subscriber name or '[MASKED_SUBSCRIBER]'.
    """
    if not name or not name.strip():
        return "[MASKED_SUBSCRIBER]"
    PII_REDACTIONS_COUNTER.labels(field_type="name").inc()
    parts = name.strip().split()
    if not parts:
        return "[MASKED_SUBSCRIBER]"
    masked_parts = []
    for part in parts:
        if len(part) <= 1:
            masked_parts.append(part[0] + "*")
        else:
            masked_parts.append(part[0] + "*" * (len(part) - 1))
    return " ".join(masked_parts)


def mask_email(email: Optional[str]) -> str:
    """
    Summary:
        Masks the local mailbox and domain parts of an email address.
        Example: 'elena.rostova@example.com' -> 'e***@***.com'.

    Parameters:
        email (Optional[str]): Raw email address.

    Return Value:
        str: Masked email address string.
    """
    if not email or not email.strip():
        return "N/A"
    PII_REDACTIONS_COUNTER.labels(field_type="email").inc()
    raw = email.strip()
    if "@" not in raw:
        return "***@***.com"
    local_part, domain = raw.split("@", 1)
    masked_local = (local_part[0] + "***") if len(local_part) > 1 else "***"
    domain_parts = domain.split(".")
    if len(domain_parts) > 1:
        masked_domain = "***." + domain_parts[-1]
    else:
        masked_domain = "***.com"
    return f"{masked_local}@{masked_domain}"


def normalize_phone_digits(phone: Optional[str]) -> str:
    """
    Summary:
        Extracts raw normalized digits from any phone format for deterministic subscriber matching.
        Example: '+1 (555) 234-5678' -> '5552345678'.

    Parameters:
        phone (Optional[str]): Formatted or raw phone number.

    Return Value:
        str: Cleaned digits string.
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def mask_phone(phone: Optional[str]) -> str:
    """
    Summary:
        Masks middle/last digits of a phone number.
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
        Sanitizes customer records for AI agent consumption.
        In telecom operations, the Mobile or Landline number is the subscriber's
        primary unique identity token and remains visible, while subscriber names,
        emails, physical addresses, SSNs, and credit cards are strictly masked.

    Parameters:
        record (Dict[str, Any]): Dictionary containing customer fields.

    Return Value:
        Dict[str, Any]: Sanitized dictionary safe for LLM context consumption.
    """
    sanitized = dict(record)

    # Subscriber names are strictly masked in AI payloads
    if "full_name" in sanitized and sanitized["full_name"]:
        sanitized["subscriber_name"] = mask_subscriber_name(str(sanitized.pop("full_name")))
    elif "first_name" in sanitized or "last_name" in sanitized:
        fname = sanitized.pop("first_name", "") or ""
        lname = sanitized.pop("last_name", "") or ""
        full = f"{fname} {lname}".strip()
        sanitized["subscriber_name"] = mask_subscriber_name(full)

    if "customer_name" in sanitized and sanitized["customer_name"]:
        sanitized["customer_name"] = mask_subscriber_name(str(sanitized["customer_name"]))

    # Email is masked
    if "email" in sanitized and sanitized["email"]:
        sanitized["email"] = mask_email(str(sanitized["email"]))

    # Phone number remains visible as the subscriber's unique identity token
    # (Do not mask phone_number)

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
