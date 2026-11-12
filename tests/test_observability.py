"""Observability tests for Prometheus metrics, OpenTelemetry tracing, and Grafana dashboard."""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from services.checkout.main import app as checkout_app
from services.common.metrics import (
    DB_POOL_CONNECTIONS,
    HTTP_REQUESTS_ERRORS_TOTAL,
    HTTP_REQUESTS_IN_FLIGHT,
    SERVICE_QUEUE_LENGTH,
)
from services.common.tracing import (
    GLOBAL_TRACE_STORE,
    format_trace_tree,
    inject_carrier,
    setup_tracer,
    trace_span,
)
from services.inventory.main import app as inventory_app


def test_prometheus_extended_metrics() -> None:
    """Verifies extended Prometheus metrics (in-flight, errors, db pool, queue length)."""
    # Set sample values
    HTTP_REQUESTS_IN_FLIGHT.labels(service="test-service").set(3)
    HTTP_REQUESTS_ERRORS_TOTAL.labels(
        service="test-service", method="POST", endpoint="/test", error_type="5xx"
    ).inc()
    DB_POOL_CONNECTIONS.labels(service="test-service", state="checked_out").set(4)
    DB_POOL_CONNECTIONS.labels(service="test-service", state="available").set(6)
    SERVICE_QUEUE_LENGTH.labels(service="test-service", queue_name="orders_queue").set(12)

    client = TestClient(inventory_app)
    resp = client.get("/metrics")
    assert resp.status_code == 200
    metrics_text = resp.text

    assert "http_requests_in_flight" in metrics_text
    assert "http_requests_errors_total" in metrics_text
    assert "db_pool_connections" in metrics_text
    assert "service_queue_length" in metrics_text


def test_opentelemetry_tracing_and_propagation() -> None:
    """Tests OpenTelemetry trace context propagation and span tree hierarchy."""
    tracer = setup_tracer("test-coordinator")

    with tracer.start_as_current_span("parent.flow") as parent_span:
        carrier = inject_carrier({})
        assert "traceparent" in carrier

        # Child span simulating downstream microservice call
        child_tracer = setup_tracer("downstream-service")
        with trace_span(child_tracer, "child.work", carrier=carrier):
            pass

        trace_id = format(parent_span.get_span_context().trace_id, "032x")

    # Verify trace store recorded the spans
    spans = GLOBAL_TRACE_STORE.get_trace(trace_id)
    assert len(spans) >= 2
    span_names = [s["name"] for s in spans]
    assert "parent.flow" in span_names
    assert "child.work" in span_names

    # Verify formatted tree
    tree = format_trace_tree(trace_id)
    assert f"[Trace: {trace_id}]" in tree
    assert "parent.flow" in tree
    assert "child.work" in tree


def test_checkout_traces_endpoint() -> None:
    """Tests the /traces/latest and /traces/{id} endpoints on checkout service."""
    client = TestClient(checkout_app)

    # Empty or existing trace retrieval
    resp = client.get("/traces/latest")
    assert resp.status_code == 200
    assert "trace_id" in resp.json() or resp.json().get("status") == "no_traces"

    # Non-existent trace returns 404
    missing_resp = client.get("/traces/00000000000000000000000000000000")
    assert missing_resp.status_code == 404


def test_grafana_dashboard_validity() -> None:
    """Validates the provisioned Grafana dashboard JSON format and metric queries."""
    dashboard_path = Path(__file__).resolve().parent.parent / "dashboard" / "scopex_overview.json"
    assert dashboard_path.exists()

    data = json.loads(dashboard_path.read_text())
    assert data["title"] == "SCOPE-X Microservice Observability"
    assert data["uid"] == "scopex-observability"

    # Check panel queries
    panels = [p for p in data["panels"] if p.get("type") == "timeseries"]
    panel_titles = [p["title"] for p in panels]

    assert any("Throughput" in t for t in panel_titles)
    assert any("Latency" in t for t in panel_titles)
    assert any("Error Rate" in t for t in panel_titles)
    assert any("Database Pool" in t for t in panel_titles)
    assert any("In-Flight" in t for t in panel_titles)
