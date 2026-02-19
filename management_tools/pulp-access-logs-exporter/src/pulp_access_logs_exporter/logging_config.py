"""Logging configuration for access logs exporter.

Detects whether stderr is a TTY:
- TTY: colored, human-readable ConsoleRenderer (structlog default)
- No TTY (e.g. OpenShift pod): JSON output for log collectors
"""

import logging
import sys

import structlog


def setup_logging(level: int = logging.INFO) -> None:
    """Configure structlog based on whether stderr is a TTY.

    Args:
        level: Logging level (default: INFO)
    """
    shared_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.set_exc_info,
    ]

    if sys.stderr.isatty():
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stderr),
    )
