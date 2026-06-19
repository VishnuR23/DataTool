"""Structured logging via structlog (ARCHITECTURE.md §16).

Every log entry is a structured event with the context the operator needs
(``experiment_id``, ``decision_id``, ``adapter_id``, ``kind``). ``configure_logging``
is called once at process start; ``get_logger`` returns a bound logger anywhere.
"""

from __future__ import annotations

import logging

import structlog

_LEVELS = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def configure_logging(level: str = "info", *, json: bool = False) -> None:
    """Configure structlog. JSON renderer for production, console for local use."""
    logging.basicConfig(format="%(message)s", level=_LEVELS.get(level.lower(), logging.INFO))
    renderer = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            _LEVELS.get(level.lower(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger, optionally bound to a component name."""
    logger = structlog.get_logger()
    return logger.bind(component=name) if name else logger
