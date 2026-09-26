# Purpose: Production OCI Docker container definition for HybridAI services.
# Architecture/Context: Builds portable, hardened container images running CRM and FastMCP services.
# Dependencies/Side Effects: Runs as unprivileged non-root user (telecom) for strict security compliance.

FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output for real-time logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install minimal OS dependencies for network operations and postgres connectivity
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code and configuration
COPY pyproject.toml .
COPY src/ ./src/

# Create unprivileged application user for security hardening
RUN groupadd -g 10001 telecom && \
    useradd -u 10001 -g telecom -s /bin/bash -m telecom && \
    chown -R telecom:telecom /app

USER telecom

EXPOSE 8000 8001

# Default command can be overridden in docker-compose.yml
CMD ["python3", "-m", "src.crm.app"]
