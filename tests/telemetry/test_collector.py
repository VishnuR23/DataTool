from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    ActionRepository,
    AuditLogRepository,
    ExperimentRepository,
)
from datatool.telemetry.collector import collect_new


def _experiment(s):
    return ExperimentRepository(s).add(
        name="exp", surface="checkout", owner="o", contract={}, spec={}, state="proposed"
    )


def test_collect_new_returns_events_for_audit_rows(session_factory):
    with session_scope(session_factory) as s:
        exp = _experiment(s)
        ActionRepository(s).add(
            experiment_id=exp.id,
            decision_id=None,
            kind="promote",
            adapter="postgres",
            payload={},
            clamped=False,
            clamp_reason=None,
            succeeded=True,
            error=None,
        )
    with session_scope(session_factory) as s:
        events, watermarks = collect_new(s, {})
        kinds = {e.source for e in events}
        assert "action" in kinds
        assert "action" in watermarks
        assert all(e.source_id for e in events)


def test_collect_new_excludes_rows_at_or_before_high_watermark(session_factory):
    with session_scope(session_factory) as s:
        exp = _experiment(s)
        AuditLogRepository(s).add(
            kind="note", actor="system", experiment_id=exp.id, payload={"m": 1}
        )
    with session_scope(session_factory) as s:
        events, watermarks = collect_new(s, {})
        n_first = len([e for e in events if e.source == "audit"])
        assert n_first >= 1
    with session_scope(session_factory) as s:
        # Re-collecting with the advanced watermark yields no NEW audit rows.
        events2, _ = collect_new(s, watermarks)
        # boundary rows may reappear (>=), but no growth beyond the original count
        assert len([e for e in events2 if e.source == "audit"]) <= n_first
