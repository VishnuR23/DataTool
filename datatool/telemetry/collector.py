"""Convert new agent-side audit rows into TelemetryEvents (spec §"Live streaming").

Agent-only: this is the one place that bridges the customer-side ORM to the shared
event schema, keeping ``events.py`` free of persistence imports. Each of the five
append-only tables maps to a normalized event with a human-readable ``summary`` for
the console. We read rows with ``created_at >= watermark`` (inclusive) and rely on
the panel's (org, source, source_id) dedup to absorb the re-read boundary rows.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from datatool.persistence import models as m
from datatool.telemetry.events import TelemetryEvent

_SOURCES = ("decision", "action", "guardrail", "trust", "audit")


def _parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def collect_new(
    session: Session, watermarks: dict[str, str]
) -> tuple[list[TelemetryEvent], dict[str, str]]:
    events: list[TelemetryEvent] = []
    new_watermarks = dict(watermarks)

    builders = {
        "decision": (_decisions, m.Decision),
        "action": (_actions, m.Action),
        "guardrail": (_guardrails, m.GuardrailEvaluation),
        "trust": (_trusts, m.TrustEvent),
        "audit": (_audits, m.AuditLog),
    }
    for source in _SOURCES:
        build, model = builders[source]
        since = _parse(watermarks.get(source))
        stmt = select(model)
        if since is not None:
            stmt = stmt.where(model.created_at >= since)
        stmt = stmt.order_by(model.created_at)
        rows = list(session.scalars(stmt))
        max_ts = since
        for row in rows:
            events.append(build(row))
            if max_ts is None or row.created_at > max_ts:
                max_ts = row.created_at
        if max_ts is not None:
            new_watermarks[source] = max_ts.isoformat()

    return events, new_watermarks


def _decisions(row: m.Decision) -> TelemetryEvent:
    return TelemetryEvent(
        source="decision",
        source_id=str(row.id),
        kind=row.kind,
        summary=f"decision {row.kind}: {row.reason}",
        occurred_at=row.created_at,
        experiment_id=str(row.experiment_id),
        detail={"inputs": row.inputs, "outputs": row.outputs},
    )


def _actions(row: m.Action) -> TelemetryEvent:
    return TelemetryEvent(
        source="action",
        source_id=str(row.id),
        kind=row.kind,
        summary=f"action {row.kind} via {row.adapter}" + (" (clamped)" if row.clamped else ""),
        occurred_at=row.created_at,
        experiment_id=str(row.experiment_id),
        detail={"clamped": row.clamped, "succeeded": row.succeeded, "error": row.error},
    )


def _guardrails(row: m.GuardrailEvaluation) -> TelemetryEvent:
    return TelemetryEvent(
        source="guardrail",
        source_id=str(row.id),
        kind="guardrail",
        summary=f"guardrail {row.guardrail_name}: " + ("breached" if row.breached else "ok"),
        occurred_at=row.created_at,
        experiment_id=str(row.experiment_id),
        detail={
            "breached": row.breached,
            "severity": row.severity,
            "value": row.value,
            "threshold": row.threshold,
        },
    )


def _trusts(row: m.TrustEvent) -> TelemetryEvent:
    return TelemetryEvent(
        source="trust",
        source_id=str(row.id),
        kind=row.kind,
        summary=f"trust {row.kind} on {row.surface}: {row.reason}",
        occurred_at=row.created_at,
        experiment_id=str(row.experiment_id) if row.experiment_id else None,
        surface=row.surface,
        detail={"delta": row.delta, "new_state": row.new_state},
    )


def _audits(row: m.AuditLog) -> TelemetryEvent:
    return TelemetryEvent(
        source="audit",
        source_id=str(row.id),
        kind=row.kind,
        summary=f"{row.actor}: {row.kind}",
        occurred_at=row.created_at,
        experiment_id=str(row.experiment_id) if row.experiment_id else None,
        detail=row.payload,
    )
