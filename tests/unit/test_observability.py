"""Observability wiring (ARCHITECTURE.md §16)."""

from __future__ import annotations

from datatool.observability.logging import configure_logging, get_logger
from datatool.observability.metrics import DECISIONS_TOTAL, REGISTRY


def test_configure_logging_and_log_does_not_raise():
    configure_logging("info")
    logger = get_logger("test")
    logger.info("event.happened", experiment_id="abc", kind="ramp")


def test_decision_counter_increments_and_is_readable():
    before = (
        REGISTRY.get_sample_value(
            "datatool_decisions_total", {"kind": "ramp", "surface": "pricing"}
        )
        or 0.0
    )
    DECISIONS_TOTAL.labels(kind="ramp", surface="pricing").inc()
    after = REGISTRY.get_sample_value(
        "datatool_decisions_total", {"kind": "ramp", "surface": "pricing"}
    )
    assert after == before + 1.0


def test_metrics_are_registered_on_the_private_registry():
    # Counters collect under their base name (the _total suffix is on the samples).
    names = {metric.name for metric in REGISTRY.collect()}
    assert "datatool_decisions" in names
    assert "datatool_actions" in names
    assert "datatool_loop_duration_seconds" in names
