"""
Purpose: Telecom domain tool definitions, business logic, diagnostics, and compliance sanitization.
Architecture/Context: Invoked by FastMCP SSE server and CRM controllers to perform customer care actions.
Dependencies/Side Effects: Queries database, records Prometheus metrics, streams Loki audit records, and redacts PII.
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional
from prometheus_client import Counter, Histogram
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from src.core.audit import audit_client
from src.core.security import (
    mask_credit_card, mask_phone, mask_ssn, mask_street_address, sanitize_customer_record
)
from src.db.database import AsyncSessionLocal
from src.db.models import (
    Account, BillingRecord, CallInteraction, Customer, Device,
    NetworkOutage, Subscription, SupportTicket, UpsellOffer
)

# Prometheus metrics for observability
TOOL_CALL_COUNTER = Counter(
    "mcp_tool_invocations_total",
    "Total count of MCP tool executions",
    ["tool_name", "status"]
)

TOOL_LATENCY_HISTOGRAM = Histogram(
    "mcp_tool_duration_seconds",
    "Execution duration of MCP tools in seconds",
    ["tool_name"]
)


def _resolve_effective_caller(
    caller_id: str,
    agent_email: Optional[str] = None,
    security_context: Optional[Dict[str, Any]] = None
) -> str:
    """
    Summary:
        Determines the most accurate identity for non-repudiation audit trails.
        Prioritizes verified user email from arguments, security context, or fallback caller ID.

    Parameters:
        caller_id (str): Default caller identifier.
        agent_email (Optional[str]): Explicit user email provided in tool call.
        security_context (Optional[Dict[str, Any]]): Request-level security context.

    Return Value:
        str: Resolved caller identity string.
    """
    if agent_email and agent_email.strip():
        return agent_email.strip()
    if security_context and security_context.get("caller_identity"):
        ctx_id = security_context["caller_identity"]
        if ctx_id and not ctx_id.startswith("gemini-enterprise-agent (Shared"):
            return ctx_id
    if agent_email:
        return agent_email
    return caller_id


async def search_customer_tool(
    query: str,
    caller_id: str = "gemini-enterprise",
    agent_email: Optional[str] = None,
    security_context: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Summary:
        Finds customer records matching a search query (name, phone number, email, or account number).
        Masks sensitive PII before returning.

    Parameters:
        query (str): Search term (e.g., customer name, phone, or account ID).
        caller_id (str): Identity of the calling agent or system.
        agent_email (Optional[str]): Authenticated email of human contact center agent.
        security_context (Optional[Dict[str, Any]]): Security and network context for compliance.

    Return Value:
        Dict[str, Any]: Search results payload with masked subscriber details.
    """
    start_time = time.time()
    tool_name = "search_customer"
    effective_caller = _resolve_effective_caller(caller_id, agent_email, security_context)

    async with AsyncSessionLocal() as session:
        try:
            clean_q = f"%{query.strip()}%"
            stmt = (
                select(Customer)
                .options(selectinload(Customer.accounts))
                .where(
                    or_(
                        Customer.first_name.ilike(clean_q),
                        Customer.last_name.ilike(clean_q),
                        Customer.email.ilike(clean_q),
                        Customer.phone_number.ilike(clean_q),
                        Customer.accounts.any(Account.account_number.ilike(clean_q))
                    )
                )
            )
            result = await session.execute(stmt)
            customers = result.scalars().all()

            results_list = []
            for c in customers:
                account_nums = [acc.account_number for acc in c.accounts]
                results_list.append({
                    "customer_id": c.id,
                    "full_name": f"{c.first_name} {c.last_name}",
                    "email": c.email,
                    "phone_masked": mask_phone(c.phone_number),
                    "postal_code": c.postal_code,
                    "loyalty_tier": c.loyalty_tier,
                    "accounts": account_nums
                })

            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="SUCCESS").inc()
            duration = time.time() - start_time
            TOOL_LATENCY_HISTOGRAM.labels(tool_name=tool_name).observe(duration)

            await audit_client.log_event(
                event_type="MCP_TOOL_EXECUTION",
                caller_identity=effective_caller,
                tool_name=tool_name,
                status="SUCCESS",
                security_context=security_context,
                details={"query": query, "matches_found": len(results_list)}
            )

            return {
                "status": "success",
                "count": len(results_list),
                "customers": results_list
            }
        except Exception as exc:
            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="FAILED").inc()
            await audit_client.log_event(
                event_type="MCP_TOOL_EXECUTION",
                caller_identity=effective_caller,
                tool_name=tool_name,
                status="FAILED",
                security_context=security_context,
                details={"query": query, "error": str(exc)}
            )
            return {"status": "error", "message": f"Customer lookup failed: {str(exc)}"}


async def get_customer_360_tool(
    customer_id: str,
    caller_id: str = "gemini-enterprise",
    agent_email: Optional[str] = None,
    security_context: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Summary:
        Retrieves a 360-degree consolidated profile of a telecom customer.
        Includes accounts, active subscriptions, devices, recent tickets, and billing health.
        Strictly applies PII masking on address, SSN, and phone.

    Parameters:
        customer_id (str): Unique customer UUID.
        caller_id (str): Identity of the calling agent.

    Return Value:
        Dict[str, Any]: PII-sanitized comprehensive customer record.
    """
    start_time = time.time()
    tool_name = "get_customer_360"

    async with AsyncSessionLocal() as session:
        try:
            stmt = (
                select(Customer)
                .options(
                    selectinload(Customer.accounts).selectinload(Account.subscriptions).selectinload(Subscription.devices),
                    selectinload(Customer.accounts).selectinload(Account.billing_records),
                    selectinload(Customer.tickets),
                    selectinload(Customer.interactions)
                )
                .where(Customer.id == customer_id)
            )
            result = await session.execute(stmt)
            c = result.scalar_one_or_none()

            if not c:
                TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="NOT_FOUND").inc()
                return {"status": "error", "message": f"Customer ID {customer_id} not found."}

            accounts_data = []
            for acc in c.accounts:
                subs_data = []
                for s in acc.subscriptions:
                    devs_data = []
                    for d in s.devices:
                        devs_data.append({
                            "device_id": d.id,
                            "device_type": d.device_type,
                            "serial_number": d.serial_number,
                            "health_status": d.health_status,
                            "live_telemetry": d.live_telemetry,
                            "last_reboot": d.last_reboot_time.isoformat() if d.last_reboot_time else None
                        })
                    subs_data.append({
                        "subscription_id": s.id,
                        "service_type": s.service_type,
                        "plan_name": s.plan_name,
                        "speed_tier_mbps": s.speed_tier_mbps,
                        "data_cap_gb": s.data_cap_gb,
                        "current_usage_gb": s.current_usage_gb,
                        "monthly_fee": s.monthly_fee,
                        "status": s.status,
                        "devices": devs_data
                    })

                bills_data = [
                    {
                        "invoice_month": b.invoice_month,
                        "total_amount": b.total_amount,
                        "roaming_charges": b.roaming_charges,
                        "payment_status": b.payment_status,
                        "dispute_status": b.dispute_status
                    }
                    for b in acc.billing_records
                ]

                accounts_data.append({
                    "account_number": acc.account_number,
                    "status": acc.status,
                    "balance_amount": acc.balance_amount,
                    "payment_card_masked": mask_credit_card(acc.payment_method_card),
                    "subscriptions": subs_data,
                    "recent_bills": bills_data
                })

            tickets_data = [
                {
                    "ticket_id": t.id,
                    "title": t.title,
                    "category": t.category,
                    "priority": t.priority,
                    "status": t.status,
                    "created_at": t.created_at.isoformat() if t.created_at else None
                }
                for t in c.tickets
            ]

            payload = {
                "customer_id": c.id,
                "first_name": c.first_name,
                "last_name": c.last_name,
                "email": c.email,
                "phone_masked": mask_phone(c.phone_number),
                "ssn_masked": mask_ssn(c.ssn),
                "address_masked": mask_street_address(c.street_address),
                "city": c.city,
                "state": c.state,
                "postal_code": c.postal_code,
                "loyalty_tier": c.loyalty_tier,
                "accounts": accounts_data,
                "open_tickets": tickets_data
            }

            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="SUCCESS").inc()
            duration = time.time() - start_time
            TOOL_LATENCY_HISTOGRAM.labels(tool_name=tool_name).observe(duration)
            effective_caller = _resolve_effective_caller(caller_id, agent_email, security_context)

            await audit_client.log_event(
                event_type="MCP_TOOL_EXECUTION",
                caller_identity=effective_caller,
                tool_name=tool_name,
                customer_id=c.id,
                status="SUCCESS",
                security_context=security_context,
                details={"accessed_profile": f"{c.first_name} {c.last_name}", "pii_sanitized": True}
            )

            return {"status": "success", "customer_360": payload}
        except Exception as exc:
            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="FAILED").inc()
            return {"status": "error", "message": f"Failed fetching customer 360: {str(exc)}"}


async def get_service_diagnostics_tool(
    service_id: str,
    caller_id: str = "gemini-enterprise",
    agent_email: Optional[str] = None,
    security_context: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Summary:
        Performs remote line diagnostics for a Home Broadband ONT or Mobile 5G connection.
        Returns optical dBm signal, packet loss, latency, and automated troubleshooting insight.

    Parameters:
        service_id (str): Subscription UUID.
        caller_id (str): Identity of the calling agent.
        agent_email (Optional[str]): Authenticated email of human contact center agent.
        security_context (Optional[Dict[str, Any]]): Security and network context for compliance.

    Return Value:
        Dict[str, Any]: Detailed telemetry and root-cause diagnostic assessment.
    """
    start_time = time.time()
    tool_name = "get_service_diagnostics"
    effective_caller = _resolve_effective_caller(caller_id, agent_email, security_context)

    async with AsyncSessionLocal() as session:
        try:
            stmt = (
                select(Subscription)
                .options(selectinload(Subscription.devices))
                .where(Subscription.id == service_id)
            )
            result = await session.execute(stmt)
            sub = result.scalar_one_or_none()

            if not sub:
                return {"status": "error", "message": f"Subscription {service_id} not found."}

            diagnostics = []
            for dev in sub.devices:
                telemetry = dev.live_telemetry or {}
                root_cause = "NORMAL"
                recommended_action = "No intervention needed."

                if dev.device_type == "ONT_FIBER_ROUTER":
                    optical_rx = telemetry.get("optical_rx_power_dbm", -18.0)
                    packet_loss = telemetry.get("packet_loss_percent", 0.0)

                    if optical_rx < -27.0:
                        root_cause = "OPTICAL_SIGNAL_DEGRADATION"
                        recommended_action = "Optical signal degraded (-28.5 dBm). Check fiber patch cable, schedule clean/splice or remote optical calibration."
                    elif packet_loss > 5.0:
                        root_cause = "WIFI_PACKET_LOSS_HIGH"
                        recommended_action = "Perform remote router reboot to clear memory leak and switch Wi-Fi to less congested channel."

                elif dev.device_type in ("5G_SIM", "ESIM"):
                    current_usage = sub.current_usage_gb
                    data_cap = sub.data_cap_gb

                    if data_cap > 0 and current_usage >= data_cap:
                        root_cause = "DATA_CAP_EXCEEDED"
                        recommended_action = f"Subscriber has used {current_usage}GB / {data_cap}GB cap. Speed throttled to 128kbps. Pitch 5G Unlimited Priority Pass."

                diagnostics.append({
                    "device_id": dev.id,
                    "device_type": dev.device_type,
                    "serial_number": dev.serial_number,
                    "health_status": dev.health_status,
                    "telemetry_metrics": telemetry,
                    "root_cause_diagnosis": root_cause,
                    "recommended_action": recommended_action
                })

            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="SUCCESS").inc()
            duration = time.time() - start_time
            TOOL_LATENCY_HISTOGRAM.labels(tool_name=tool_name).observe(duration)

            await audit_client.log_event(
                event_type="MCP_TOOL_EXECUTION",
                caller_identity=effective_caller,
                tool_name=tool_name,
                status="SUCCESS",
                security_context=security_context,
                details={"service_id": service_id, "service_type": sub.service_type}
            )

            return {
                "status": "success",
                "service_type": sub.service_type,
                "plan_name": sub.plan_name,
                "diagnostics": diagnostics
            }
        except Exception as exc:
            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="FAILED").inc()
            return {"status": "error", "message": f"Diagnostics error: {str(exc)}"}


async def run_remote_device_action_tool(
    device_id: str,
    action: str,
    caller_id: str = "gemini-enterprise",
    agent_email: Optional[str] = None,
    security_context: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Summary:
        Executes a remote operational command on customer premise equipment (ONT reboot, Wi-Fi channel reset, Ping).
        Updates device state in database and records a privileged audit log.

    Parameters:
        device_id (str): UUID of the target hardware device.
        action (str): Action command ('reboot', 'channel_optimization', 'ping_sweep').
        caller_id (str): Calling agent ID.
        agent_email (Optional[str]): Authenticated email of human contact center agent.
        security_context (Optional[Dict[str, Any]]): Security and network context for compliance.

    Return Value:
        Dict[str, Any]: Execution status, updated telemetry, and completion timestamp.
    """
    start_time = time.time()
    tool_name = "run_remote_device_action"
    effective_caller = _resolve_effective_caller(caller_id, agent_email, security_context)

    async with AsyncSessionLocal() as session:
        try:
            stmt = select(Device).where(Device.id == device_id)
            result = await session.execute(stmt)
            device = result.scalar_one_or_none()

            if not device:
                return {"status": "error", "message": f"Device {device_id} not found."}

            telemetry = dict(device.live_telemetry or {})

            if action.lower() == "reboot":
                device.last_reboot_time = datetime.utcnow()
                device.health_status = "HEALTHY"
                telemetry["packet_loss_percent"] = 0.05
                telemetry["latency_ms"] = 12.3
                telemetry["uptime_hours"] = 0.1
                telemetry["wifi_interference_score"] = "LOW"
                device.live_telemetry = telemetry
                message = f"Remote reboot initiated on {device.device_type} ({device.serial_number}). Optical link resynced and channel cleared."

            elif action.lower() == "channel_optimization":
                telemetry["wifi_interference_score"] = "OPTIMIZED"
                device.live_telemetry = telemetry
                message = "Wi-Fi channel steering algorithm executed. Migrated to 5GHz DFS low-noise channel."

            else:
                message = f"Executed diagnostic ping to {device.serial_number}: 0% packet loss, 12ms round-trip."

            await session.commit()

            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="SUCCESS").inc()
            duration = time.time() - start_time
            TOOL_LATENCY_HISTOGRAM.labels(tool_name=tool_name).observe(duration)

            await audit_client.log_event(
                event_type="PRIVILEGED_DEVICE_ACTION",
                caller_identity=effective_caller,
                tool_name=tool_name,
                status="SUCCESS",
                security_context=security_context,
                details={"device_id": device_id, "action": action, "serial": device.serial_number}
            )

            return {
                "status": "success",
                "action": action,
                "message": message,
                "device_id": device.id,
                "updated_health_status": device.health_status,
                "telemetry": telemetry
            }
        except Exception as exc:
            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="FAILED").inc()
            return {"status": "error", "message": f"Action execution failed: {str(exc)}"}


async def check_network_outages_tool(
    postal_code: str,
    caller_id: str = "gemini-enterprise",
    agent_email: Optional[str] = None,
    security_context: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Summary:
        Checks for active or investigating network infrastructure outages in a postal area.

    Parameters:
        postal_code (str): Customer ZIP/postal code.
        caller_id (str): Calling agent ID.
        agent_email (Optional[str]): Authenticated email of human contact center agent.
        security_context (Optional[Dict[str, Any]]): Security and network context for compliance.

    Return Value:
        Dict[str, Any]: List of active infrastructure incidents or clean status.
    """
    start_time = time.time()
    tool_name = "check_network_outages"
    effective_caller = _resolve_effective_caller(caller_id, agent_email, security_context)

    async with AsyncSessionLocal() as session:
        try:
            stmt = select(NetworkOutage).where(NetworkOutage.status.in_(["ACTIVE", "INVESTIGATING"]))
            result = await session.execute(stmt)
            all_outages = result.scalars().all()

            matching_outages = []
            for out in all_outages:
                if postal_code in out.postal_codes:
                    matching_outages.append({
                        "outage_id": out.id,
                        "region": out.region,
                        "infrastructure_type": out.infrastructure_type,
                        "status": out.status,
                        "description": out.description,
                        "estimated_resolution": out.estimated_resolution
                    })

            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="SUCCESS").inc()
            duration = time.time() - start_time
            TOOL_LATENCY_HISTOGRAM.labels(tool_name=tool_name).observe(duration)

            await audit_client.log_event(
                event_type="MCP_TOOL_EXECUTION",
                caller_identity=effective_caller,
                tool_name=tool_name,
                status="SUCCESS",
                security_context=security_context,
                details={"postal_code": postal_code, "outages_count": len(matching_outages)}
            )

            return {
                "status": "success",
                "postal_code": postal_code,
                "has_active_outage": len(matching_outages) > 0,
                "outages": matching_outages
            }
        except Exception as exc:
            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="FAILED").inc()
            return {"status": "error", "message": f"Failed checking outages: {str(exc)}"}


async def get_billing_breakdown_tool(
    account_id: str,
    caller_id: str = "gemini-enterprise",
    agent_email: Optional[str] = None,
    security_context: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Summary:
        Retrieves line-item breakdown of the most recent bills, roaming fees, and dispute notes.
        Masks all financial payment instruments.

    Parameters:
        account_id (str): Billing account ID or account number.
        caller_id (str): Calling agent ID.
        agent_email (Optional[str]): Authenticated email of human contact center agent.
        security_context (Optional[Dict[str, Any]]): Security and network context for compliance.

    Return Value:
        Dict[str, Any]: Itemized invoice breakdown.
    """
    start_time = time.time()
    tool_name = "get_billing_breakdown"
    effective_caller = _resolve_effective_caller(caller_id, agent_email, security_context)

    async with AsyncSessionLocal() as session:
        try:
            stmt = (
                select(Account)
                .options(selectinload(Account.billing_records))
                .where(or_(Account.id == account_id, Account.account_number == account_id))
            )
            result = await session.execute(stmt)
            acc = result.scalar_one_or_none()

            if not acc:
                return {"status": "error", "message": f"Account {account_id} not found."}

            records = [
                {
                    "invoice_month": b.invoice_month,
                    "base_charges": b.base_charges,
                    "roaming_charges": b.roaming_charges,
                    "extra_charges": b.extra_charges,
                    "taxes": b.taxes,
                    "total_amount": b.total_amount,
                    "payment_status": b.payment_status,
                    "dispute_status": b.dispute_status,
                    "dispute_notes": b.dispute_notes
                }
                for b in acc.billing_records
            ]

            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="SUCCESS").inc()
            duration = time.time() - start_time
            TOOL_LATENCY_HISTOGRAM.labels(tool_name=tool_name).observe(duration)

            await audit_client.log_event(
                event_type="MCP_TOOL_EXECUTION",
                caller_identity=effective_caller,
                tool_name=tool_name,
                status="SUCCESS",
                security_context=security_context,
                details={"account_number": acc.account_number}
            )

            return {
                "status": "success",
                "account_number": acc.account_number,
                "current_balance": acc.balance_amount,
                "payment_card_masked": mask_credit_card(acc.payment_method_card),
                "invoices": records
            }
        except Exception as exc:
            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="FAILED").inc()
            return {"status": "error", "message": f"Billing lookup error: {str(exc)}"}


async def get_upsell_recommendations_tool(
    customer_id: str,
    caller_id: str = "gemini-enterprise",
    agent_email: Optional[str] = None,
    security_context: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Summary:
        Analyzes customer subscription and usage metrics to compute personalized upsell offers
        complete with an agent pitch script to minimize handle time and maximize conversion.

    Parameters:
        customer_id (str): Customer UUID.
        caller_id (str): Calling agent ID.
        agent_email (Optional[str]): Authenticated email of human contact center agent.
        security_context (Optional[Dict[str, Any]]): Security and network context for compliance.

    Return Value:
        Dict[str, Any]: Ranked list of targeted promotional upgrades.
    """
    start_time = time.time()
    tool_name = "get_upsell_recommendations"
    effective_caller = _resolve_effective_caller(caller_id, agent_email, security_context)

    async with AsyncSessionLocal() as session:
        try:
            # Load customer subscriptions
            stmt = (
                select(Customer)
                .options(selectinload(Customer.accounts).selectinload(Account.subscriptions))
                .where(Customer.id == customer_id)
            )
            result = await session.execute(stmt)
            cust = result.scalar_one_or_none()

            if not cust:
                return {"status": "error", "message": f"Customer {customer_id} not found."}

            offers_stmt = select(UpsellOffer)
            offers_res = await session.execute(offers_stmt)
            catalog_offers = offers_res.scalars().all()

            recommendations = []
            for acc in cust.accounts:
                for sub in acc.subscriptions:
                    if sub.service_type == "HOME_BROADBAND" and sub.speed_tier_mbps < 1000:
                        for off in catalog_offers:
                            if off.title.startswith("Gigabit"):
                                recommendations.append({
                                    "title": off.title,
                                    "description": off.description,
                                    "promotional_price": off.promotional_price,
                                    "agent_pitch_script": off.pitch_script,
                                    "reason": f"Customer is on {sub.speed_tier_mbps}Mbps plan with high network utilization."
                                })

                    if sub.service_type == "MOBILE_5G":
                        for off in catalog_offers:
                            if "Unlimited 5G Priority" in off.title and sub.status == "THROTTLED":
                                recommendations.append({
                                    "title": off.title,
                                    "description": off.description,
                                    "promotional_price": off.promotional_price,
                                    "agent_pitch_script": off.pitch_script,
                                    "reason": "Customer exceeded 50GB cap and is actively throttled."
                                })

            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="SUCCESS").inc()
            duration = time.time() - start_time
            TOOL_LATENCY_HISTOGRAM.labels(tool_name=tool_name).observe(duration)

            await audit_client.log_event(
                event_type="MCP_TOOL_EXECUTION",
                caller_identity=effective_caller,
                tool_name=tool_name,
                customer_id=customer_id,
                status="SUCCESS",
                security_context=security_context,
                details={"recommendations_count": len(recommendations)}
            )

            return {
                "status": "success",
                "customer_name": f"{cust.first_name} {cust.last_name}",
                "recommendations": recommendations
            }
        except Exception as exc:
            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="FAILED").inc()
            return {"status": "error", "message": f"Upsell engine error: {str(exc)}"}


async def log_agent_interaction_tool(
    customer_id: str,
    agent_name: str,
    issue_summary: str,
    resolution_summary: str,
    call_duration_sec: int = 180,
    upsell_offered: bool = False,
    upsell_accepted: bool = False,
    caller_id: str = "gemini-enterprise",
    agent_email: Optional[str] = None,
    security_context: Optional[Dict[str, Any]] = None,
    **kwargs
) -> Dict[str, Any]:
    """
    Summary:
        Records the completed call interaction log in the CRM database and commits an audit entry.

    Parameters:
        customer_id (str): Customer UUID.
        agent_name (str): Name or ID of the human agent.
        issue_summary (str): Brief summary of reason for call.
        resolution_summary (str): Action taken and resolution.
        call_duration_sec (int): Total duration of call in seconds.
        upsell_offered (bool): Whether an upsell was proposed.
        upsell_accepted (bool): Whether customer agreed to upgrade.
        caller_id (str): Calling agent ID.
        agent_email (Optional[str]): Authenticated email of human contact center agent.
        security_context (Optional[Dict[str, Any]]): Security and network context for compliance.

    Return Value:
        Dict[str, Any]: Recorded interaction ID and status.
    """
    start_time = time.time()
    tool_name = "log_agent_interaction"
    effective_caller = _resolve_effective_caller(caller_id, agent_email or agent_name, security_context)

    async with AsyncSessionLocal() as session:
        try:
            interaction = CallInteraction(
                customer_id=customer_id,
                agent_name=agent_name,
                call_duration_sec=call_duration_sec,
                issue_summary=issue_summary,
                resolution_summary=resolution_summary,
                upsell_offered=upsell_offered,
                upsell_accepted=upsell_accepted
            )
            session.add(interaction)
            await session.commit()

            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="SUCCESS").inc()
            duration = time.time() - start_time
            TOOL_LATENCY_HISTOGRAM.labels(tool_name=tool_name).observe(duration)

            await audit_client.log_event(
                event_type="CALL_INTERACTION_RECORDED",
                caller_identity=effective_caller,
                tool_name=tool_name,
                customer_id=customer_id,
                status="SUCCESS",
                security_context=security_context,
                details={
                    "interaction_id": interaction.id,
                    "agent": agent_name,
                    "duration_sec": call_duration_sec,
                    "upsell_accepted": upsell_accepted
                }
            )

            return {
                "status": "success",
                "interaction_id": interaction.id,
                "message": "Call interaction logged successfully in CRM database."
            }
        except Exception as exc:
            TOOL_CALL_COUNTER.labels(tool_name=tool_name, status="FAILED").inc()
            return {"status": "error", "message": f"Failed logging interaction: {str(exc)}"}
