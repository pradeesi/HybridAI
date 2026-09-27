"""
Purpose: Centralized runtime configuration and environment variable validation for Telecom ADK Agent.
Architecture/Context: Supplies MCP server endpoint coordinates, authentication modes (Cloud Run IAM vs On-Prem static token),
                      and Gemini model parameters to ADK agent components.
Dependencies/Side Effects: Reads configuration from system environment variables and .env file.
"""

import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """
    Summary:
        Typed configuration model managing runtime parameters for the Telecom ADK Agent.

    Parameters:
        None (instantiated from environment variables).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Agent Identification & Model
    # Note: AGENT_NAME must be a valid Python identifier for ADK graph nodes
    AGENT_NAME: str = "telecom_agent"
    AGENT_DISPLAY_NAME: str = "Telecom Customer Care & Network Diagnostics Agent"
    AGENT_DESCRIPTION: str = "Enterprise AI Agent for frontline telecom support, line diagnostics, billing, and outages."
    MODEL_NAME: str = "gemini-2.5-flash"

    # Google Cloud & Vertex AI Runtime
    GOOGLE_GENAI_USE_VERTEXAI: bool = True
    GOOGLE_CLOUD_PROJECT: str = os.getenv("GOOGLE_CLOUD_PROJECT", "pradeesi-ai-demo")
    GOOGLE_CLOUD_LOCATION: str = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

    # MCP Server Target Deployment (Cloud Run or On-Prem Proxmox)
    # Default points to live Cloud Run service; can be pointed to local gateway (http://localhost:8001) or on-prem Proxmox host
    MCP_SERVER_URL: str = os.getenv(
        "MCP_SERVER_URL",
        "https://telecom-mcp-server-1111937452.europe-west1.run.app"
    )
    # Static auth token required when connecting to On-Prem Proxmox MCP server
    MCP_AUTH_TOKEN: str = os.getenv(
        "MCP_AUTH_TOKEN",
        "telecom-mcp-secret-token-change-in-prod-xyz987"
    )
    # Target deployment type: 'cloud_run' (OIDC ID token) or 'on_prem' (static Bearer token)
    MCP_DEPLOYMENT_TARGET: str = os.getenv("MCP_DEPLOYMENT_TARGET", "cloud_run")

    # Audit & Compliance Telemetry Headers
    AGENT_IDENTITY: str = "telecom-customer-care-agent"
    USER_AGENT: str = "Google-ADK-TelecomAgent/1.0"
    MCP_TIMEOUT_SECONDS: float = 30.0


# Global singleton settings instance
agent_settings = AgentSettings()
