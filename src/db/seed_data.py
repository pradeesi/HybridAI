"""
Purpose: Synthetic data generator and database seeder for telecom personas, accounts, and telemetry.
Architecture/Context: Executed on application startup to ensure realistic test scenarios are pre-populated.
Dependencies/Side Effects: Commits synthetic customer, subscription, device, and outage records into the database.
"""

import logging
from datetime import datetime, timedelta
from sqlalchemy import select, func
from src.db.database import AsyncSessionLocal
from src.db.models import (
    Customer, Account, Subscription, Device,
    NetworkOutage, BillingRecord, SupportTicket, CallInteraction, UpsellOffer
)

logger = logging.getLogger("hybrid_ai.seed")


async def seed_synthetic_telecom_data() -> None:
    """
    Summary:
        Seeds comprehensive, realistic telecom synthetic data for call center scenarios.
        Idempotent: Only seeds if the Customer table is currently empty.
    """
    try:
        async with AsyncSessionLocal() as session:
            # Check existing records count
            result = await session.execute(select(func.count(Customer.id)))
            count = result.scalar() or 0

            if count > 0:
                logger.info("Database already contains %d customer records. Skipping seeding.", count)
                return

            logger.info("Seeding synthetic telecom database with rich scenario personas...")

        # -------------------------------------------------------------
        # 1. CATALOG: UPSELL OFFERS
        # -------------------------------------------------------------
        offers = [
            UpsellOffer(
                target_service_type="HOME_BROADBAND",
                title="Gigabit Fiber Speed Tier Boost",
                description="Upgrade to 1,000 Mbps symmetrical fiber with unconstrained low latency for 4K and gaming.",
                upgrade_tier="Gigabit Ultra (1000 Mbps)",
                promotional_price=79.99,
                pitch_script="I notice you are frequently utilizing your full 100Mbps bandwidth. For just $15 more per month, we can upgrade your line to 1Gbps Fiber with no activation fee."
            ),
            UpsellOffer(
                target_service_type="HOME_BROADBAND",
                title="Wi-Fi 6 Tri-Band Mesh Extender Pack",
                description="Eliminates home dead zones and mitigates dense apartment radio interference.",
                upgrade_tier="Mesh Coverage Add-on",
                promotional_price=9.99,
                pitch_script="To resolve the interference on your home network, our Wi-Fi 6 mesh node provides blanket whole-home signal and auto-steering."
            ),
            UpsellOffer(
                target_service_type="MOBILE_5G",
                title="Unlimited 5G Priority Data Pass",
                description="Removes high-speed data throttling threshold with unthrottled 5G Ultra Wideband access.",
                upgrade_tier="5G Unlimited Pro (No Cap)",
                promotional_price=65.00,
                pitch_script="You've reached your monthly 50GB cap. We can switch you immediately to the 5G Pro tier with zero throttling and 20GB hotspot for only $10 extra."
            ),
            UpsellOffer(
                target_service_type="MOBILE_5G",
                title="Global Explorer Roaming Bundle",
                description="10GB high-speed roaming data across 140 countries with unlimited voice/SMS.",
                upgrade_tier="Global Roamer Add-on",
                promotional_price=25.00,
                pitch_script="To protect against unexpected per-MB roaming rates when traveling, our Global Roaming pass covers you automatically across Europe and Asia."
            )
        ]
        session.add_all(offers)

        # -------------------------------------------------------------
        # 2. INFRASTRUCTURE: NETWORK OUTAGES
        # -------------------------------------------------------------
        outages = [
            NetworkOutage(
                region="Pacific Northwest Metro",
                postal_codes="98101, 98102, 98104",
                infrastructure_type="FIBER_BACKBONE",
                status="ACTIVE",
                description="Third-party construction excavation severed secondary distribution feeder line. Splicing crew on-site.",
                estimated_resolution="Estimated restoration within 90 minutes"
            ),
            NetworkOutage(
                region="Northeast Urban Corridor",
                postal_codes="10001, 10002",
                infrastructure_type="CELL_TOWER_5G",
                status="INVESTIGATING",
                description="Cell site sector C experiencing intermittent power fluctuation. Backup generator active.",
                estimated_resolution="Field technician ETA 45 minutes"
            )
        ]
        session.add_all(outages)

        # -------------------------------------------------------------
        # 3. PERSONA 1: Elena Rostova (Broadband Optical Degradation)
        # -------------------------------------------------------------
        cust_1 = Customer(
            first_name="Elena",
            last_name="Rostova",
            email="elena.rostova@example.com",
            phone_number="+15552345678",
            ssn="987-65-4321",
            street_address="742 Evergreen Terrace, Apt 4B",
            city="Springfield",
            state="OR",
            postal_code="97477",
            loyalty_tier="Gold"
        )
        session.add(cust_1)
        await session.flush()

        acc_1 = Account(
            customer_id=cust_1.id,
            account_number="TEL-ACC-88129",
            status="ACTIVE",
            balance_amount=69.99,
            payment_method_card="4111222233331111",
            billing_cycle_day=15
        )
        session.add(acc_1)
        await session.flush()

        sub_1 = Subscription(
            account_id=acc_1.id,
            service_type="HOME_BROADBAND",
            plan_name="Fiber High-Speed 500",
            speed_tier_mbps=500,
            data_cap_gb=0.0,  # Unlimited
            current_usage_gb=312.4,
            monthly_fee=69.99,
            status="ACTIVE"
        )
        session.add(sub_1)
        await session.flush()

        dev_1 = Device(
            subscription_id=sub_1.id,
            device_type="ONT_FIBER_ROUTER",
            serial_number="ONT-HW-99281-FBR",
            mac_address="00:1A:2B:3C:4D:5E",
            firmware_version="v4.2.1-prod",
            health_status="DEGRADED",
            live_telemetry={
                "optical_rx_power_dbm": -28.5,  # Normal is -14 to -24 dBm (Degraded)
                "optical_tx_power_dbm": 2.1,
                "packet_loss_percent": 14.2,
                "latency_ms": 68.4,
                "wifi_interference_score": "HIGH",
                "connected_clients": 9,
                "uptime_hours": 1420.5
            },
            last_reboot_time=datetime.utcnow() - timedelta(days=59)
        )
        session.add(dev_1)

        bill_1 = BillingRecord(
            account_id=acc_1.id,
            invoice_month="2026-08",
            base_charges=69.99,
            roaming_charges=0.0,
            extra_charges=0.0,
            taxes=5.20,
            total_amount=75.19,
            payment_status="PAID"
        )
        session.add(bill_1)

        ticket_1 = SupportTicket(
            customer_id=cust_1.id,
            title="Frequent intermittent Wi-Fi drops and buffering in evening",
            category="BROADBAND",
            priority="HIGH",
            status="OPEN"
        )
        session.add(ticket_1)

        # -------------------------------------------------------------
        # 4. PERSONA 2: Marcus Vance (Mobile 5G Throttled & Cap Reached)
        # -------------------------------------------------------------
        cust_2 = Customer(
            first_name="Marcus",
            last_name="Vance",
            email="marcus.vance@example.com",
            phone_number="+15559871234",
            ssn="123-45-6789",
            street_address="1200 Market Street, Suite 500",
            city="San Francisco",
            state="CA",
            postal_code="94102",
            loyalty_tier="Standard"
        )
        session.add(cust_2)
        await session.flush()

        acc_2 = Account(
            customer_id=cust_2.id,
            account_number="TEL-ACC-99214",
            status="ACTIVE",
            balance_amount=55.00,
            payment_method_card="5500000000002222",
            billing_cycle_day=5
        )
        session.add(acc_2)
        await session.flush()

        sub_2 = Subscription(
            account_id=acc_2.id,
            service_type="MOBILE_5G",
            plan_name="5G Unlimited Essentials (50GB High-Speed)",
            speed_tier_mbps=150,
            data_cap_gb=50.0,
            current_usage_gb=54.8,  # Cap exceeded!
            monthly_fee=55.00,
            status="THROTTLED"
        )
        session.add(sub_2)
        await session.flush()

        dev_2 = Device(
            subscription_id=sub_2.id,
            device_type="5G_SIM",
            serial_number="ICCID-890141032111",
            imei="354890123456789",
            firmware_version="Baseband-5G-v8",
            health_status="HEALTHY",
            live_telemetry={
                "signal_strength_rsrp_dbm": -82,
                "connected_tower_id": "SF-TWR-09",
                "current_throttled_speed_kbps": 128,
                "data_used_this_cycle_gb": 54.8,
                "roaming_status": "HOME_NETWORK"
            }
        )
        session.add(dev_2)

        # -------------------------------------------------------------
        # 5. PERSONA 3: Amina Al-Mansoor (International Roaming Bill Shock)
        # -------------------------------------------------------------
        cust_3 = Customer(
            first_name="Amina",
            last_name="Al-Mansoor",
            email="amina.mansoor@example.com",
            phone_number="+15556784321",
            ssn="345-67-8901",
            street_address="450 Lexington Avenue",
            city="New York",
            state="NY",
            postal_code="10017",
            loyalty_tier="Platinum"
        )
        session.add(cust_3)
        await session.flush()

        acc_3 = Account(
            customer_id=cust_3.id,
            account_number="TEL-ACC-44910",
            status="ACTIVE",
            balance_amount=168.50,
            payment_method_card="378282246310005",
            billing_cycle_day=20
        )
        session.add(acc_3)
        await session.flush()

        sub_3 = Subscription(
            account_id=acc_3.id,
            service_type="MOBILE_5G",
            plan_name="5G Premier Voice & Data",
            speed_tier_mbps=250,
            data_cap_gb=100.0,
            current_usage_gb=28.4,
            monthly_fee=75.00,
            status="ACTIVE"
        )
        session.add(sub_3)
        await session.flush()

        bill_3 = BillingRecord(
            account_id=acc_3.id,
            invoice_month="2026-08",
            base_charges=75.00,
            roaming_charges=85.00,  # Unplanned roaming charge!
            extra_charges=0.0,
            taxes=8.50,
            total_amount=168.50,
            payment_status="PENDING",
            dispute_status="UNDER_REVIEW",
            dispute_notes="Customer inquiring about $85 charge incurred during London airport layover without active travel pass."
        )
        session.add(bill_3)

        # -------------------------------------------------------------
        # 6. PERSONA 4: David Chen (Satisfied High-Bandwidth Candidate)
        # -------------------------------------------------------------
        cust_4 = Customer(
            first_name="David",
            last_name="Chen",
            email="david.chen@example.com",
            phone_number="+15553127890",
            ssn="567-89-0123",
            street_address="3200 North Loop West",
            city="Houston",
            state="TX",
            postal_code="77092",
            loyalty_tier="Standard"
        )
        session.add(cust_4)
        await session.flush()

        acc_4 = Account(
            customer_id=cust_4.id,
            account_number="TEL-ACC-11029",
            status="ACTIVE",
            balance_amount=49.99,
            payment_method_card="6011000000003333",
            billing_cycle_day=1
        )
        session.add(acc_4)
        await session.flush()

        sub_4 = Subscription(
            account_id=acc_4.id,
            service_type="HOME_BROADBAND",
            plan_name="Fiber Starter 100",
            speed_tier_mbps=100,
            data_cap_gb=0.0,
            current_usage_gb=890.5,  # Consistently high streaming/download usage
            monthly_fee=49.99,
            status="ACTIVE"
        )
        session.add(sub_4)
        await session.flush()

        dev_4 = Device(
            subscription_id=sub_4.id,
            device_type="ONT_FIBER_ROUTER",
            serial_number="ONT-HW-77312-FBR",
            mac_address="00:1A:2B:99:88:77",
            firmware_version="v4.2.1-prod",
            health_status="HEALTHY",
            live_telemetry={
                "optical_rx_power_dbm": -18.2,
                "packet_loss_percent": 0.1,
                "latency_ms": 11.4,
                "utilization_rate_percent": 94.6,
                "connected_clients": 16
            }
        )
        session.add(dev_4)

        await session.commit()
        logger.info("Successfully seeded synthetic personas, accounts, devices, and catalog offers.")
    except Exception as exc:
        logger.warning(
            "Encountered unexpected non-fatal exception during synthetic data seeding (%s). Continuing startup...",
            exc
        )
