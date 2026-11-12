"""OpenTelemetry distributed tracing setup and context propagation for SCOPE-X."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException, status
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


class InMemoryTraceStore(SpanExporter):
    """Thread-safe in-memory exporter retaining recent spans for inspection."""

    def __init__(self, max_spans: int = 1000) -> None:
        self.max_spans = max_spans
        self.spans_by_trace: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self.recent_traces: list[str] = []

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            ctx = span.get_span_context()
            trace_id = format(ctx.trace_id, "032x")
            span_id = format(ctx.span_id, "016x")
            parent_id = format(span.parent.span_id, "016x") if span.parent else None

            duration_ms = 0.0
            if span.end_time and span.start_time:
                duration_ms = round((span.end_time - span.start_time) / 1_000_000, 2)

            service_name = "unknown"
            if span.resource and "service.name" in span.resource.attributes:
                service_name = str(span.resource.attributes["service.name"])

            span_record = {
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_id": parent_id,
                "name": span.name,
                "service": service_name,
                "duration_ms": duration_ms,
                "attributes": dict(span.attributes or {}),
                "status": span.status.status_code.name,
            }
            self.spans_by_trace[trace_id].append(span_record)
            if trace_id not in self.recent_traces:
                self.recent_traces.append(trace_id)
                if len(self.recent_traces) > 200:
                    removed = self.recent_traces.pop(0)
                    self.spans_by_trace.pop(removed, None)

        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.spans_by_trace.clear()

    def get_trace(self, trace_id: str) -> list[dict[str, Any]]:
        return self.spans_by_trace.get(trace_id, [])

    def get_latest_trace_id(self) -> str | None:
        return self.recent_traces[-1] if self.recent_traces else None


# Shared trace store singleton
GLOBAL_TRACE_STORE = InMemoryTraceStore()
_PROPAGATOR = TraceContextTextMapPropagator()


def setup_tracer(service_name: str) -> trace.Tracer:
    """Configures OpenTelemetry TracerProvider with in-memory exporter and resource tags."""
    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(GLOBAL_TRACE_STORE))
    return provider.get_tracer(service_name)


def inject_carrier(carrier: dict[str, str], context: trace.Context | None = None) -> dict[str, str]:
    """Injects W3C traceparent headers into carrier dict for outgoing requests."""
    _PROPAGATOR.inject(carrier, context=context)
    return carrier


def extract_carrier(carrier: dict[str, str]) -> trace.Context:
    """Extracts W3C traceparent headers from incoming request headers."""
    # Normalize header keys to lowercase
    normalized = {k.lower(): str(v) for k, v in carrier.items()}
    return _PROPAGATOR.extract(normalized)


@contextmanager
def trace_span(
    tracer: trace.Tracer,
    name: str,
    carrier: dict[str, str] | None = None,
    attributes: dict[str, Any] | None = None,
) -> Generator[trace.Span, None, None]:
    """Context manager creating a trace span with automatic parent extraction."""
    parent_ctx = extract_carrier(carrier) if carrier else None
    with tracer.start_as_current_span(name, context=parent_ctx) as span:
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        yield span


def format_trace_tree(trace_id: str, span_list: list[dict[str, Any]] | None = None) -> str:
    """Formats recorded spans in a trace into an ASCII tree structure."""
    spans = span_list if span_list is not None else GLOBAL_TRACE_STORE.get_trace(trace_id)
    if not spans:
        return f"[Trace {trace_id}: No spans found]"

    # Build hierarchy map
    children: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    known_span_ids = {s["span_id"] for s in spans}

    for s in spans:
        parent = s["parent_id"]
        # If parent is not among the collected spans, treat as root child
        if parent not in known_span_ids:
            parent = None
        children[parent].append(s)

    lines = [f"[Trace: {trace_id}]"]

    def render_subtree(parent_id: str | None, prefix: str = "") -> None:
        nodes = children.get(parent_id, [])
        for i, node in enumerate(nodes):
            is_last = i == len(nodes) - 1
            connector = "└── " if is_last else "├── "
            duration = f"{node['duration_ms']:.2f}ms"
            service = node["service"]
            line = f"{prefix}{connector}{node['name']} ({service}, {duration})"
            lines.append(line)
            new_prefix = prefix + ("    " if is_last else "│   ")
            render_subtree(node["span_id"], new_prefix)

    render_subtree(None)
    return "\n".join(lines)


trace_router = APIRouter(tags=["traces"])


@trace_router.get("/traces/latest")
def get_service_latest_trace() -> dict[str, Any]:
    trace_id = GLOBAL_TRACE_STORE.get_latest_trace_id()
    if not trace_id:
        return {"status": "no_traces", "message": "No traces recorded yet."}
    spans = GLOBAL_TRACE_STORE.get_trace(trace_id)
    return {
        "trace_id": trace_id,
        "formatted_tree": format_trace_tree(trace_id, spans),
        "spans": spans,
    }


@trace_router.get("/traces/{trace_id}")
def get_service_trace(trace_id: str) -> dict[str, Any]:
    spans = GLOBAL_TRACE_STORE.get_trace(trace_id)
    if not spans:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Trace {trace_id} not found in this service",
        )
    return {
        "trace_id": trace_id,
        "formatted_tree": format_trace_tree(trace_id, spans),
        "spans": spans,
    }
