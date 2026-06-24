"""The shared, versioned telemetry event schema (spec §"Repository layout").

This module is the ONE allowed cross-unit dependency between the agent
(``datatool/telemetry``) and the hosted panel (``cloud/``). It is data-only: it
imports nothing from ``core``/``control``/``stats``/``persistence`` so the panel
can depend on it without pulling in customer-side business logic.

Versioning: ``SCHEMA_VERSION`` is bumped on any breaking change to the envelope.
The panel validates the version on ingest and rejects unknown majors.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

EventSource = Literal["decision", "action", "guardrail", "trust", "audit"]


class TelemetryEvent(BaseModel):
    """One normalized activity event, derived from an agent-side audit row."""

    model_config = ConfigDict(frozen=True)

    source: EventSource
    source_id: str  # the source row's UUID, as a string; the panel dedups on it
    kind: str
    summary: str  # human-readable one-liner for the console
    occurred_at: datetime
    experiment_id: str | None = None
    surface: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class TelemetryBatch(BaseModel):
    """A batch of events POSTed from the agent to the panel's ingest endpoint."""

    schema_version: int = SCHEMA_VERSION
    events: list[TelemetryEvent]
