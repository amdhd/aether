"""OpenTelemetry distributed tracing.

Off by default (like the EMF metrics) so local dev and tests stay quiet. Enable
with ``TRACING_ENABLED``; the exporter is chosen by ``OTEL_EXPORTER_OTLP_ENDPOINT``:

* **unset** → spans print to stdout (``ConsoleSpanExporter``) for local inspection.
* **set** → OTLP/gRPC to that endpoint. In the deployed stack that's the ADOT
  collector sidecar on ``localhost:4317``, which forwards traces to **AWS X-Ray**.

When enabled it auto-instruments the FastAPI app, the SQLAlchemy engine, and
outbound HTTPX calls (the DeepSeek/OpenAI/Tavily clients), so a single request
shows up as one trace spanning HTTP → DB → external LLM. X-Ray-compatible trace
IDs and propagation are configured so the spans land correctly in X-Ray.

The OpenTelemetry packages are imported lazily inside :func:`configure_tracing`
so nothing loads (and no globals are touched) on the default disabled path —
which is what keeps the test suite unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = get_logger(__name__)

_configured = False


def tracing_enabled() -> bool:
    return settings.TRACING_ENABLED


def configure_tracing(app: "FastAPI", engine: "AsyncEngine") -> None:
    """Set up tracing and instrument the app, DB engine, and HTTPX. No-op when
    tracing is disabled or already configured, so it's safe to call at import."""
    global _configured
    if _configured or not tracing_enabled():
        return

    from opentelemetry import trace
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
    from opentelemetry.propagate import set_global_textmap
    from opentelemetry.propagators.aws import AwsXRayPropagator
    from opentelemetry.sdk.extension.aws.trace import AwsXRayIdGenerator
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

    resource = Resource.create(
        {
            "service.name": settings.OTEL_SERVICE_NAME or settings.PROJECT_NAME,
            "deployment.environment": settings.ENVIRONMENT,
        }
    )
    # AwsXRayIdGenerator makes trace IDs X-Ray-compatible (time-prefixed); the
    # propagator reads/writes the X-Amzn-Trace-Id header so traces stitch together
    # across the ALB and any downstream that speaks X-Ray.
    provider = TracerProvider(resource=resource, id_generator=AwsXRayIdGenerator())

    if settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

        # insecure=True: the hop to the in-task ADOT sidecar is loopback, not the
        # network, so mTLS would be ceremony with no security benefit.
        exporter = OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT, insecure=True)
        exporter_name = "otlp"
    else:
        exporter = ConsoleSpanExporter()
        exporter_name = "console"

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    set_global_textmap(AwsXRayPropagator())

    FastAPIInstrumentor.instrument_app(app)
    # SQLAlchemy's async engine wraps a sync engine that emits the DBAPI events
    # the instrumentation hooks, so point it at engine.sync_engine.
    SQLAlchemyInstrumentor().instrument(engine=engine.sync_engine)
    HTTPXClientInstrumentor().instrument()

    _configured = True
    logger.info("tracing.enabled exporter=%s service=%s", exporter_name, resource.attributes["service.name"])
