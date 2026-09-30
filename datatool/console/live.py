"""Live data layer for the terminal console — read-only over the running brain.

``FeedPoller`` turns repeated audit-tail reads into a stream of genuinely-new
events (the ``collect_new`` watermark is inclusive, so it re-reads the boundary
row; we dedup by ``(source, source_id)`` to absorb that). ``experiment_summaries``
gives the console its left-panel snapshot, and ``goal_trace`` the per-experiment
detail view its goal-metric series. All are read-only — the console never
writes; actions go through the trust-contract-clamped operations elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session, sessionmaker

from datatool.console.events import TelemetryEvent
from datatool.console.feed import collect_new
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import DecisionRepository, ExperimentRepository


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


@dataclass(frozen=True)
class GoalPoint:
    """One evaluated cycle: the confidence sequence on the goal lift at that moment."""

    at: datetime
    lower: float
    estimate: float
    upper: float


@dataclass(frozen=True)
class GoalTrace:
    name: str
    state: str
    surface: str
    metric: str
    points: list[GoalPoint]  # oldest first; cycles that never reached goal stats are skipped
    latest_reason: str | None
    latest_outputs: dict


def goal_trace(
    session_factory: sessionmaker[Session], name: str, *, limit: int = 500
) -> GoalTrace | None:
    """The goal metric's confidence sequence over time, read from the decisions log."""
    with session_scope(session_factory) as session:
        exp = ExperimentRepository(session).get_by_name(name)
        if exp is None:
            return None
        decisions = DecisionRepository(session).list_for(exp.id, limit=limit)  # newest first
        points = [
            GoalPoint(
                at=d.created_at,
                lower=d.outputs["cs_lower"],
                estimate=d.outputs["cs_point_estimate"],
                upper=d.outputs["cs_upper"],
            )
            for d in reversed(decisions)
            if "cs_point_estimate" in d.outputs
        ]
        latest = decisions[0] if decisions else None
        return GoalTrace(
            name=exp.name,
            state=exp.state,
            surface=exp.surface,
            metric=(exp.contract.get("goal") or {}).get("metric", "goal"),
            points=points,
            latest_reason=latest.reason if latest else None,
            latest_outputs=dict(latest.outputs) if latest else {},
        )
