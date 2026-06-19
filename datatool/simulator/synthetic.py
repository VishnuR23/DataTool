"""Synthetic event-stream generation under known ground truth (ARCHITECTURE.md §14).

Produces a CSV of events (``timestamp, unit_id, variant, metric, value``) for one of
three scenarios with a *known* answer, so the simulator's behavior can be checked
against truth:

- ``null``       — treatment equals control: no win, no breach (should conclude).
- ``positive``   — treatment's goal metric is genuinely better (should ramp/ship).
- ``regression`` — treatment's guardrail metric is worse (should revert).

Each unit emits one goal event and one guardrail event (both Bernoulli) per bucket,
spread evenly across the duration. Deterministic given ``seed``.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

DEFAULT_START = datetime(2026, 1, 1, tzinfo=UTC)

# scenario -> (goal lift added to control rate, guardrail relative lift on control rate).
# The regression lift takes the treatment guardrail rate well above a typical absolute
# limit (0.10 -> 0.30) so an absolute guardrail trips cleanly without relying on the
# noisy ratio of two small rare-event counts.
_SCENARIOS = {
    "null": (0.0, 0.0),
    "positive": (0.10, 0.0),
    "regression": (0.0, 2.0),
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def generate_synthetic_csv(
    path: str | Path,
    *,
    scenario: str,
    seed: int = 7,
    start: datetime = DEFAULT_START,
    duration: timedelta = timedelta(days=3),
    bucket: timedelta = timedelta(minutes=30),
    units_per_bucket_per_arm: int = 30,
    goal_metric: str = "conversion",
    guardrail_metric: str = "error_rate",
    control_goal_rate: float = 0.10,
    control_guardrail_rate: float = 0.10,
) -> Path:
    """Write a synthetic event CSV for ``scenario`` and return the path."""
    if scenario not in _SCENARIOS:
        raise ValueError(f"unknown scenario {scenario!r}; choose from {sorted(_SCENARIOS)}")
    goal_lift, guardrail_lift = _SCENARIOS[scenario]

    rates = {
        ("control", goal_metric): control_goal_rate,
        ("treatment", goal_metric): _clamp01(control_goal_rate + goal_lift),
        ("control", guardrail_metric): control_guardrail_rate,
        ("treatment", guardrail_metric): _clamp01(control_guardrail_rate * (1.0 + guardrail_lift)),
    }

    rng = np.random.default_rng(seed)
    n_buckets = int(duration / bucket)
    path = Path(path)

    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "unit_id", "variant", "metric", "value"])
        for b in range(n_buckets):
            bucket_start = start + b * bucket
            for arm in ("control", "treatment"):
                for i in range(units_per_bucket_per_arm):
                    ts = bucket_start + (bucket * i) / units_per_bucket_per_arm
                    unit_id = f"{arm[0]}{b}_{i}"
                    for metric in (goal_metric, guardrail_metric):
                        value = int(rng.random() < rates[(arm, metric)])
                        writer.writerow([ts.isoformat(), unit_id, arm, metric, value])
    return path
