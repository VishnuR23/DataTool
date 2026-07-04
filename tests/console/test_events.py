from datetime import UTC, datetime

from datatool.console.events import TelemetryEvent


def test_event_roundtrips_through_json_preserving_fields():
    event = TelemetryEvent(
        source="action",
        source_id="abc-123",
        kind="promote",
        summary="promoted checkout-button to 100%",
        occurred_at=datetime(2026, 6, 22, 12, 0, tzinfo=UTC),
        experiment_id="exp-1",
        surface="checkout",
        detail={"clamped": False},
    )
    restored = TelemetryEvent.model_validate_json(event.model_dump_json())
    assert restored == event


def test_event_rejects_unknown_source():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TelemetryEvent(
            source="nope",
            source_id="x",
            kind="k",
            summary="s",
            occurred_at=datetime.now(UTC),
        )
