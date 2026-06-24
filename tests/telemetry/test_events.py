from datetime import UTC, datetime

from datatool.telemetry.events import SCHEMA_VERSION, TelemetryBatch, TelemetryEvent


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
    batch = TelemetryBatch(events=[event])
    restored = TelemetryBatch.model_validate_json(batch.model_dump_json())
    assert restored.schema_version == SCHEMA_VERSION
    assert restored.events[0] == event


def test_batch_defaults_schema_version_to_current():
    assert TelemetryBatch(events=[]).schema_version == SCHEMA_VERSION


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
