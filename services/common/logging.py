"""Structured logging module for SCOPE-X services."""

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any


class StructuredJSONFormatter(logging.Formatter):
    """Custom formatter emitting logs as standard JSON documents."""

    def format(self, record: logging.LogRecord) -> str:
        log_payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service_name", "scopex-service"),
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Include structured extra fields if present
        if hasattr(record, "props") and isinstance(record.props, dict):
            log_payload.update(record.props)

        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_payload)


def setup_logger(service_name: str, level: str = "INFO") -> logging.Logger:
    """Configures and returns a structured logger for a given service."""
    logger = logging.getLogger(service_name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Avoid duplicate handlers on re-initialization
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredJSONFormatter())
        logger.addHandler(handler)

    # Set service_name attribute on logger records
    old_factory = logging.getLogRecordFactory()

    def record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = old_factory(*args, **kwargs)
        record.service_name = service_name
        return record

    logging.setLogRecordFactory(record_factory)
    return logger
