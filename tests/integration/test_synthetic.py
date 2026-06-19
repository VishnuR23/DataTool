"""Synthetic event generation (ARCHITECTURE.md §14).

Pins that each scenario produces a CSV with the right columns and the ground-truth
rates the simulator is meant to react to.
"""

from __future__ import annotations

import csv
from collections import defaultdict

import pytest

from datatool.simulator.synthetic import generate_synthetic_csv


def _rates(path) -> dict[tuple[str, str], float]:
    sums: dict[tuple[str, str], float] = defaultdict(float)
    counts: dict[tuple[str, str], int] = defaultdict(int)
    with open(path, newline="") as handle:
        reader = csv.DictReader(handle)
        assert reader.fieldnames == ["timestamp", "unit_id", "variant", "metric", "value"]
        for row in reader:
            key = (row["variant"], row["metric"])
            sums[key] += float(row["value"])
            counts[key] += 1
    return {key: sums[key] / counts[key] for key in counts}


def test_null_scenario_has_no_treatment_effect(tmp_path):
    rates = _rates(generate_synthetic_csv(tmp_path / "null.csv", scenario="null"))
    # Treatment and control match (within sampling noise) on both metrics.
    assert abs(rates[("treatment", "conversion")] - rates[("control", "conversion")]) < 0.02
    assert abs(rates[("treatment", "error_rate")] - rates[("control", "error_rate")]) < 0.02


def test_positive_scenario_lifts_the_goal_only(tmp_path):
    rates = _rates(generate_synthetic_csv(tmp_path / "pos.csv", scenario="positive"))
    assert rates[("treatment", "conversion")] - rates[("control", "conversion")] > 0.07
    # The guardrail metric is unchanged.
    assert abs(rates[("treatment", "error_rate")] - rates[("control", "error_rate")]) < 0.02


def test_regression_scenario_worsens_the_guardrail_only(tmp_path):
    rates = _rates(generate_synthetic_csv(tmp_path / "reg.csv", scenario="regression"))
    # Treatment error rate is well above an absolute 0.20 limit; goal is unchanged.
    assert rates[("treatment", "error_rate")] > 0.20
    assert abs(rates[("treatment", "conversion")] - rates[("control", "conversion")]) < 0.02


def test_unknown_scenario_raises(tmp_path):
    with pytest.raises(ValueError):
        generate_synthetic_csv(tmp_path / "x.csv", scenario="bogus")


def test_generation_is_deterministic(tmp_path):
    a = _rates(generate_synthetic_csv(tmp_path / "a.csv", scenario="positive", seed=11))
    b = _rates(generate_synthetic_csv(tmp_path / "b.csv", scenario="positive", seed=11))
    assert a == b
