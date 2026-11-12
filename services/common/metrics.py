"""Comprehensive Prometheus metrics collection for SCOPE-X services."""

import time
from collections.abc import Callable

from fastapi import Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from sqlalchemy import event
from sqlalchemy.engine import Engine
from starlette.middleware.base import BaseHTTPMiddleware

# 1. Request rate counter (by service, method, endpoint, status)
HTTP_REQUESTS_TOTAL = Counter(
    "http_requests_total",
    "Total HTTP requests handled by the service",
    ["service", "method", "endpoint", "status"],
)

# 2. Request latency histogram (with fine-grained buckets for p50, p95, p99 calculation)
HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["service", "method", "endpoint"],
    buckets=(
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        2.5,
        5.0,
        10.0,
    ),
)

# 3. Error rate counter
HTTP_REQUESTS_ERRORS_TOTAL = Counter(
    "http_requests_errors_total",
    "Total HTTP request errors handled by the service",
    ["service", "method", "endpoint", "error_type"],
)

# 4. In-flight requests gauge
HTTP_REQUESTS_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "Number of requests currently being processed",
    ["service"],
)

# 5. Database pool usage gauge (checked_out vs available)
DB_POOL_CONNECTIONS = Gauge(
    "db_pool_connections",
    "Database connection pool capacity and utilization",
    ["service", "state"],
)

# 6. Service / message queue length gauge
SERVICE_QUEUE_LENGTH = Gauge(
    "service_queue_length",
    "Current queue depth or pending task backlog",
    ["service", "queue_name"],
)


class PrometheusMiddleware(BaseHTTPMiddleware):
    """Middleware collecting request rates, latency, errors, and in-flight counts."""

    def __init__(self, app: Callable, service_name: str) -> None:
        super().__init__(app)
        self.service_name = service_name
        # Initialize in-flight to 0
        HTTP_REQUESTS_IN_FLIGHT.labels(service=self.service_name).set(0)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.url.path
        if path in ("/metrics", "/health"):
            return await call_next(request)

        endpoint = path
        method = request.method
        HTTP_REQUESTS_IN_FLIGHT.labels(service=self.service_name).inc()
        start_time = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
            if status_code >= 400:
                err_type = "4xx" if status_code < 500 else "5xx"
                HTTP_REQUESTS_ERRORS_TOTAL.labels(
                    service=self.service_name,
                    method=method,
                    endpoint=endpoint,
                    error_type=err_type,
                ).inc()
            return response
        except Exception as exc:
            HTTP_REQUESTS_ERRORS_TOTAL.labels(
                service=self.service_name,
                method=method,
                endpoint=endpoint,
                error_type=type(exc).__name__,
            ).inc()
            raise
        finally:
            duration = time.perf_counter() - start_time
            HTTP_REQUESTS_IN_FLIGHT.labels(service=self.service_name).dec()
            HTTP_REQUESTS_TOTAL.labels(
                service=self.service_name,
                method=method,
                endpoint=endpoint,
                status=str(status_code),
            ).inc()
            HTTP_REQUEST_DURATION_SECONDS.labels(
                service=self.service_name,
                method=method,
                endpoint=endpoint,
            ).observe(duration)


def instrument_db_pool(engine: Engine, service_name: str, pool_size: int = 10) -> None:
    """Attaches checkout and checkin event listeners to track DB connection pool metrics."""
    DB_POOL_CONNECTIONS.labels(service=service_name, state="available").set(pool_size)
    DB_POOL_CONNECTIONS.labels(service=service_name, state="checked_out").set(0)

    @event.listens_for(engine, "checkout")
    def on_checkout(*args: object, **kwargs: object) -> None:
        DB_POOL_CONNECTIONS.labels(service=service_name, state="checked_out").inc()
        DB_POOL_CONNECTIONS.labels(service=service_name, state="available").dec()

    @event.listens_for(engine, "checkin")
    def on_checkin(*args: object, **kwargs: object) -> None:
        DB_POOL_CONNECTIONS.labels(service=service_name, state="checked_out").dec()
        DB_POOL_CONNECTIONS.labels(service=service_name, state="available").inc()


def metrics_endpoint() -> Response:
    """Returns current Prometheus metrics in text format."""
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)
