"""Logging configuration.

Single entry point: `configure_logging(settings)`. Call once on app startup.

Always emit JSON in non-local environments. Contextvars (`tenant_id`,
`investigation_id`) are bound by middleware / orchestrator code via
`structlog.contextvars.bind_contextvars(...)`.
"""

from __future__ import annotations

import logging
import sys
from typing import Any

import structlog


def _add_trace_context(
    _logger: Any, _method_name: str, event_dict: structlog.types.EventDict
) -> structlog.types.EventDict:
    """Stamp the active OTel ``trace_id``/``span_id`` onto every log line
    (NFR-7.3, spec 0017). No-op when no span is recording or the OTel API
    isn't installed."""
    try:
        from opentelemetry import trace
    except ImportError:  # pragma: no cover - otel is a declared dependency
        return event_dict
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict.setdefault("trace_id", format(span_context.trace_id, "032x"))
        event_dict.setdefault("span_id", format(span_context.span_id, "016x"))
    return event_dict


def configure_logging(settings: Any) -> None:
    """Configure structlog. `settings` is `ai_sre.config.AppSettings`.

    Typed loosely to avoid an import cycle with `config`.
    """
    is_local = getattr(settings, "env", "local") == "local"
    level = logging.DEBUG if getattr(settings, "debug", False) else logging.INFO

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_trace_context,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        timestamper,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if is_local:
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer(colors=True)
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Route stdlib logs through structlog as well.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Get a logger. Prefer `get_logger(__name__)` at module level."""
    return structlog.get_logger(name)
