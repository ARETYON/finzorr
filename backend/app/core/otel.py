"""Optional OpenTelemetry tracing — $0, self-hosted, env-gated.

Enabled only when OTEL_EXPORTER_OTLP_ENDPOINT is set (e.g. a self-hosted
Arize Phoenix at http://localhost:6006/v1/traces). Disabled, every span call
is a no-op — zero overhead, no SDK objects created. Span taxonomy:
`turn` (route, session) > `llm.call` (provider, model, tokens) + `tool`
(name, ok).
"""

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Protocol

from app.core.config import settings
from app.core.logging import log

_enabled = False


class SpanLike(Protocol):
    def set_attribute(self, key: str, value: Any) -> Any: ...


class _NoopSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        return


def setup_tracing() -> None:
    """Install the OTLP tracer provider if an endpoint is configured."""
    global _enabled  # noqa: PLW0603
    if not settings.OTEL_EXPORTER_OTLP_ENDPOINT:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": "finzorr-api", "env": settings.APP_ENV})
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT)
            )
        )
        trace.set_tracer_provider(provider)
        _enabled = True
        log.info("otel.enabled", endpoint=settings.OTEL_EXPORTER_OTLP_ENDPOINT)
    except Exception as exc:  # noqa: BLE001 — tracing must never block startup
        log.warning("otel.setup_failed", error=str(exc))


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[SpanLike]:
    """Trace a block; yields the span (or a no-op) for late attributes."""
    if not _enabled:
        yield _NoopSpan()
        return
    from opentelemetry import trace

    with trace.get_tracer("finzorr").start_as_current_span(name) as live:
        for key, value in attributes.items():
            live.set_attribute(key, value)
        yield live
