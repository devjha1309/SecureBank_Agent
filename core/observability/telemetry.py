import hashlib
from contextvars import ContextVar
from uuid import uuid4

from opentelemetry import trace
from prometheus_client import Counter, Histogram

trace_id: ContextVar[str] = ContextVar("trace_id", default="")
REQUESTS = Counter("securebank_requests_total", "HTTP requests", ["route", "status"])
LATENCY = Histogram("securebank_api_seconds", "HTTP latency", ["route"])
TOOLS = Histogram("securebank_mcp_seconds", "MCP latency", ["tool"])
FAILURES = Counter("securebank_tool_failures_total", "Tool failures", ["tool"])
SECURITY = Counter("securebank_security_failures_total", "Security failures", ["kind"])
AGENTS = Histogram("securebank_agent_seconds", "Coordinator latency")
COMPLETED = Counter("securebank_tasks_total", "Completed tasks", ["status"])
TOKENS = Counter("securebank_model_tokens_total", "Model tokens", ["provider"])
COST = Counter("securebank_model_cost_usd_total", "Estimated model cost", ["provider"])
MODEL_LATENCY = Histogram("securebank_model_seconds", "Model latency", ["provider"])
tracer = trace.get_tracer("securebank")


def customer_hash(customer_id: str | None) -> str:
    return hashlib.sha256((customer_id or "anonymous").encode()).hexdigest()[:20]


def audit(db, auth, tool: str, status: str, latency_ms: int = 0) -> None:
    from mock_bank.models.entities import ToolAuditLog

    db.add(
        ToolAuditLog(
            trace_id=trace_id.get() or uuid4().hex,
            customer_hash=customer_hash(auth.customer_id),
            tool=tool,
            status=status,
            latency_ms=latency_ms,
        )
    )


def configure_tracing() -> None:
    from core.config.settings import get_settings

    endpoint = get_settings().otel_exporter_otlp_endpoint
    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces")))
        trace.set_tracer_provider(provider)
