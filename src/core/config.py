"""
Purpose: Centralized application configuration and environment validation for HybridAI.
Architecture/Context: Imported across the entire codebase to access typed runtime settings.
Dependencies/Side Effects: Loads values from environment variables or .env file with defensive defaults.
"""

import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Summary:
        System settings model loaded from environment variables.
        Validates configuration boundaries and provides sensible defaults for both
        containerized and bare-metal environments.
    """
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Core Application Settings
    APP_NAME: str = "Telecom HybridAI Platform"
    APP_ENV: str = "development"
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"

    # Network Ports
    PORT: Optional[int] = None
    CRM_PORT: int = 8000
    MCP_PORT: int = 8001

    # Security & Authentication
    # Used to authenticate Gemini Enterprise App and other MCP clients via HTTP Bearer token
    MCP_AUTH_TOKEN: str = "telecom-mcp-secret-token-change-in-prod-xyz987"

    # Database Configuration
    # Uses asyncpg driver for PostgreSQL; supports SQLite fallback for local test execution
    DATABASE_URL: str = "postgresql+asyncpg://telecom_user:telecom_secure_pass@localhost:5432/telecom_db"
    DATABASE_URL_SYNC: str = "postgresql+psycopg2://telecom_user:telecom_secure_pass@localhost:5432/telecom_db"

    # Observability Configuration
    LOKI_URL: Optional[str] = "http://localhost:3100"
    PROMETHEUS_METRICS_ENABLED: bool = True


# Global singleton settings instance
settings = Settings()
