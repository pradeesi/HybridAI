"""
Purpose: Configure OpenTelemetry tracing and GenAI prompt/response telemetry for Telecom Agent.
Architecture/Context: Attaches Cloud Trace and Cloud Logging instrumentation to ADK runner calls.
Dependencies/Side Effects: Sets environment flags for telemetry capture and configures tracer providers.

DEMO SOFTWARE DISCLAIMER:
This code is provided strictly as a demonstration and reference implementation.
It comes with NO WARRANTY, NO GUARANTEE, and NO SUPPORT of any kind, either expressed or implied.
Use and deployment in any environment is entirely at your own discretion and risk.
"""

import logging
import os


def setup_telemetry() -> str | None:
    """Configure GenAI prompt/response logging via OpenTelemetry."""
    # Keep full prompts/responses out of trace span attributes (use GenAI logging instead).
    os.environ.setdefault("ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS", "false")
    os.environ.setdefault("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "true")

    bucket = os.environ.get("LOGS_BUCKET_NAME")
    capture_content = os.environ.get(
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "false"
    )
    if bucket and capture_content != "false":
        logging.info(
            "Prompt-response logging enabled - mode: NO_CONTENT (metadata only, no prompts/responses)"
        )
        os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "NO_CONTENT"
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_UPLOAD_FORMAT", "jsonl")
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_COMPLETION_HOOK", "upload")
        os.environ.setdefault(
            "OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental"
        )
        commit_sha = os.environ.get("COMMIT_SHA", "dev")
        os.environ.setdefault(
            "OTEL_RESOURCE_ATTRIBUTES",
            f"service.namespace=telecom-agent,service.version={commit_sha}",
        )
        path = os.environ.get("GENAI_TELEMETRY_PATH", "completions")
        os.environ.setdefault(
            "OTEL_INSTRUMENTATION_GENAI_UPLOAD_BASE_PATH",
            f"gs://{bucket}/{path}",
        )
    else:
        logging.info(
            "Prompt-response logging disabled (set LOGS_BUCKET_NAME=gs://your-bucket and OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=NO_CONTENT to enable)"
        )

    return bucket


def setup_agent_engine_telemetry() -> None:
    """Install the Agent Engine tracer provider (traces/logs to the customer project).

    Tags spans with the reasoningEngine resource. The OTel resource is fixed at
    provider creation, so this must run before get_fast_api_app to set the tags.
    No-op unless GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY is set.
    """
    if os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY", "").lower() not in (
        "true",
        "1",
    ):
        return

    import google.auth
    from vertexai.agent_engines.templates.adk import _default_instrumentor_builder

    _, project_id = google.auth.default()
    _default_instrumentor_builder(project_id, enable_tracing=True, enable_logging=True)
