"""Live data layer for the terminal console — read-only over the running brain.

``FeedPoller`` turns repeated audit-tail reads into a stream of genuinely-new
events (the ``collect_new`` watermark is inclusive, so it re-reads the boundary
row; we dedup by ``(source, source_id)`` to absorb that). ``experiment_summaries``
gives the console its left-panel snapshot. Both are read-only — the console never
writes; actions go through the trust-contract-clamped operations elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from datatool.console.events import TelemetryEvent
from datatool.console.feed import collect_new
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import ExperimentRepository


class FeedPoller:
    """Stateful tail of the audit log: each ``poll()`` returns only new events."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory
        self._watermarks: dict[str, str] = {}
        self._seen: set[tuple[str, str]] = set()

    def poll(self) -> list[TelemetryEvent]:
        with session_scope(self._factory) as session:
            events, self._watermarks = collect_new(session, self._watermarks)
        fresh: list[TelemetryEvent] = []
        for event in events:
            key = (event.source, event.source_id)
            if key not in self._seen:
                self._seen.add(key)
                fresh.append(event)
        return fresh


@dataclass(frozen=True)
class ExperimentSummary:
    name: str
    state: str
    surface: str


def experiment_summaries(session_factory: sessionmaker[Session]) -> list[ExperimentSummary]:
    with session_scope(session_factory) as session:
        rows = ExperimentRepository(session).list()
        return [ExperimentSummary(name=e.name, state=e.state, surface=e.surface) for e in rows]
