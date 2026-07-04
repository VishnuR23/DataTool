"""Normalized console event model.

A ``TelemetryEvent`` is one activity event derived from an append-only audit row
(decision / action / guardrail / trust / audit). Data-only: it imports nothing
from ``core``/``control``/``stats``/``persistence``, so the live console feed can
depend on it without pulling in decision logic. ``feed.collect_new`` produces
these; the terminal console renders them.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EventSource = Literal["decision", "action", "guardrail", "trust", "audit"]


class TelemetryEvent(BaseModel):
    """One normalized activity event, derived from an audit row."""

    model_config = ConfigDict(frozen=True)

    source: EventSource
    source_id: str  # the source row's UUID, as a string; the feed dedups on it
    kind: str
    summary: str  # human-readable one-liner for the console
    occurred_at: datetime
    experiment_id: str | None = None
    surface: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)
