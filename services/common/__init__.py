"""Common utilities shared across SCOPE-X microservices."""

from services.common.cache import CacheClient
from services.common.database import Base, create_db_engine, get_database_url, get_db_pool_size
from services.common.logging import setup_logger
from services.common.messaging import BROKER, QUEUE_NAME, MessageBroker
from services.common.metrics import (
    DB_POOL_CONNECTIONS,
    HTTP_REQUEST_DURATION_SECONDS,
    HTTP_REQUESTS_ERRORS_TOTAL,
    HTTP_REQUESTS_IN_FLIGHT,
    HTTP_REQUESTS_TOTAL,
    SERVICE_QUEUE_LENGTH,
    PrometheusMiddleware,
    instrument_db_pool,
    metrics_endpoint,
)
from services.common.tracing import (
    GLOBAL_TRACE_STORE,
    extract_carrier,
    format_trace_tree,
    inject_carrier,
    setup_tracer,
    trace_router,
    trace_span,
)

__all__ = [
    "BROKER",
    "Base",
    "CacheClient",
    "DB_POOL_CONNECTIONS",
    "GLOBAL_TRACE_STORE",
    "HTTP_REQUESTS_ERRORS_TOTAL",
    "HTTP_REQUESTS_IN_FLIGHT",
    "HTTP_REQUESTS_TOTAL",
    "HTTP_REQUEST_DURATION_SECONDS",
    "MessageBroker",
    "PrometheusMiddleware",
    "QUEUE_NAME",
    "SERVICE_QUEUE_LENGTH",
    "create_db_engine",
    "extract_carrier",
    "format_trace_tree",
    "get_database_url",
    "get_db_pool_size",
    "inject_carrier",
    "instrument_db_pool",
    "metrics_endpoint",
    "setup_logger",
    "setup_tracer",
    "trace_router",
    "trace_span",
]
