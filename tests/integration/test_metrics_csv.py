"""CSV metrics source (ARCHITECTURE.md §11.2).

Pins that the adapter aggregates events into the right per-variant (n, sum, sum_sq)
statistics, honors the metric and time-window filters, maps variant names to ids,
and fails loudly on a malformed file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from datatool.adapters import base as registry
from datatool.adapters.metrics.base import MetricsSource
from datatool.adapters.metrics.csv import CsvMetricsSource, register
from datatool.core.exceptions import AdapterError

CONTROL_ID = uuid4()
TREATMENT_ID = uuid4()
VARIANT_IDS = {"control": CONTROL_ID, "treatment": TREATMENT_ID}

WINDOW_START = datetime(2026, 6, 1, tzinfo=UTC)
WINDOW_END = datetime(2026, 6, 2, tzinfo=UTC)

_CSV = """unit_id,variant,metric,value,timestamp
u1,control,signup,1,2026-06-01T00:00:00Z
u2,control,signup,0,2026-06-01T01:00:00Z
u3,treatment,signup,1,2026-06-01T02:00:00Z
u4,treatment,signup,1,2026-06-01T03:00:00Z
u5,treatment,latency,200,2026-06-01T03:00:00Z
u6,control,signup,1,2026-05-01T00:00:00Z
u7,unknown_arm,signup,1,2026-06-01T04:00:00Z
"""


def _write(tmp_path, text=_CSV):
    path = tmp_path / "events.csv"
    path.write_text(text)
    return path


def _source(tmp_path, text=_CSV):
    return CsvMetricsSource(_write(tmp_path, text), VARIANT_IDS)


def test_source_satisfies_metrics_source_protocol(tmp_path):
    assert isinstance(_source(tmp_path), MetricsSource)


def test_query_aggregates_per_variant_within_window(tmp_path):
    samples = _source(tmp_path).query("signup", uuid4(), "user", WINDOW_START, WINDOW_END)
    by_variant = {s.variant_id: s for s in samples}

    control = by_variant[CONTROL_ID]
    assert (control.n, control.sum, control.sum_sq) == (2, 1.0, 1.0)  # values 1, 0

    treatment = by_variant[TREATMENT_ID]
    assert (treatment.n, treatment.sum, treatment.sum_sq) == (2, 2.0, 2.0)  # values 1, 1


def test_query_excludes_other_metrics_and_out_of_window_events(tmp_path):
    samples = _source(tmp_path).query("signup", uuid4(), "user", WINDOW_START, WINDOW_END)
    # u5 is a latency event, u6 is in May (out of window): neither inflates the counts.
    assert sum(s.n for s in samples) == 4  # u1, u2, u3, u4 only


def test_query_ignores_variants_not_in_the_mapping(tmp_path):
    samples = _source(tmp_path).query("signup", uuid4(), "user", WINDOW_START, WINDOW_END)
    assert all(s.variant_id in (CONTROL_ID, TREATMENT_ID) for s in samples)  # u7 dropped


def test_query_carries_experiment_id_and_metric(tmp_path):
    exp = uuid4()
    samples = _source(tmp_path).query("signup", exp, "user", WINDOW_START, WINDOW_END)
    assert all(s.experiment_id == exp and s.metric == "signup" for s in samples)


def test_supports_metric(tmp_path):
    source = _source(tmp_path)
    assert source.supports_metric("signup") is True
    assert source.supports_metric("latency") is True
    assert source.supports_metric("nonexistent") is False


def test_missing_file_raises(tmp_path):
    with pytest.raises(AdapterError):
        CsvMetricsSource(tmp_path / "nope.csv", VARIANT_IDS)


def test_missing_required_column_raises(tmp_path):
    bad = "unit_id,variant,metric,value\nu1,control,signup,1\n"  # no timestamp column
    with pytest.raises(AdapterError):
        _source(tmp_path, bad)


def test_malformed_value_raises(tmp_path):
    bad = (
        "unit_id,variant,metric,value,timestamp\n"
        "u1,control,signup,notanumber,2026-06-01T00:00:00Z\n"
    )
    with pytest.raises(AdapterError):
        _source(tmp_path, bad)


def test_empty_window_yields_no_samples(tmp_path):
    far_future = datetime(2030, 1, 1, tzinfo=UTC)
    samples = _source(tmp_path).query(
        "signup", uuid4(), "user", far_future, datetime(2030, 1, 2, tzinfo=UTC)
    )
    assert samples == []


def test_register_adds_factory_to_registry():
    registry.clear_registry()
    try:
        register()
        assert registry.get_adapter_factory("metrics.csv") is CsvMetricsSource
    finally:
        registry.clear_registry()
