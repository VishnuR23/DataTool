"""Guardrail evaluation (ARCHITECTURE.md §8.4).

Guardrails are the fast safety loop, evaluated independently of the goal-metric
statistics. Each guardrail compares a metric against a threshold and, to avoid
reacting to a single noisy blip, only *trips* after a configured number of
**consecutive** breaches (anti-flapping). Tripping maps to a controller decision
by severity: critical/high -> REVERT, medium -> HOLD plus a notification.

Threshold semantics (the three :class:`ThresholdType` cases):

- ``absolute``: an upper limit on the treatment metric (latency, error count).
  Breaches when ``treatment_value > threshold`` (strictly; exactly at the limit
  is not a breach).
- ``relative_increase``: breaches when treatment rose above control by more than
  the threshold fraction, i.e. ``(treatment - control) / control > threshold``.
- ``relative_decrease``: breaches when treatment fell below control by more than
  the threshold fraction, i.e. ``(control - treatment) / control > threshold``.

This module is a pure function: the consecutive-breach counter is passed in and
returned, so the caller (the decision engine) owns persistence (the
``guardrail_evaluations.consecutive`` column) and the evaluation itself has no
hidden state — which is what makes it trivially testable and auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from datatool.core.exceptions import StatisticsError
from datatool.core.models import DecisionKind, Guardrail, Severity, ThresholdType


class GuardrailStatus(str, Enum):
    """Outcome of one guardrail evaluation cycle."""

    INSUFFICIENT_DATA = "insufficient_data"  # below min_samples_per_arm; not evaluated
    OK = "ok"  # evaluated, no breach
    BREACHED = "breached"  # breached this cycle, not yet enough consecutive to trip
    TRIPPED = "tripped"  # consecutive breaches reached the trip count


@dataclass
class GuardrailEvaluation:
    """The result of evaluating a guardrail for one cycle.

    Mirrors the ``guardrail_evaluations`` table (§5): ``value`` and ``threshold``
    are recorded for the audit log, and ``consecutive_breaches`` is the updated
    counter the caller persists and feeds back next cycle.
    """

    guardrail_name: str
    status: GuardrailStatus
    breached: bool
    tripped: bool
    value: float | None  # realized comparison quantity; None when insufficient data
    threshold: float
    severity: Severity
    consecutive_breaches: int
    suggested_decision: DecisionKind | None  # REVERT / HOLD / None


def _comparison(
    threshold_type: ThresholdType,
    threshold_value: float,
    control_value: float,
    treatment_value: float,
) -> tuple[float, bool]:
    """Return the realized comparison quantity and whether it breaches the threshold.

    A breach is a *strict* exceedance, so a value sitting exactly on the threshold
    is treated as within limits.
    """
    if threshold_type is ThresholdType.ABSOLUTE:
        # Upper-limit semantics: the treatment metric must not exceed the limit.
        return treatment_value, treatment_value > threshold_value

    if threshold_type is ThresholdType.RELATIVE_INCREASE:
        if control_value == 0:
            # Any positive treatment is an unbounded increase off a zero baseline.
            realized = float("inf") if treatment_value > 0 else 0.0
            return realized, treatment_value > 0
        realized = (treatment_value - control_value) / control_value
        return realized, realized > threshold_value

    if threshold_type is ThresholdType.RELATIVE_DECREASE:
        if control_value == 0:
            # Nothing can decrease below a zero baseline; never a decrease-breach.
            return 0.0, False
        realized = (control_value - treatment_value) / control_value
        return realized, realized > threshold_value

    raise StatisticsError(f"unknown threshold type: {threshold_type}")  # defensive


def _decision_for_trip(severity: Severity) -> DecisionKind:
    """Map a tripped guardrail's severity to a controller decision (§8.4)."""
    if severity in (Severity.CRITICAL, Severity.HIGH):
        return DecisionKind.REVERT
    return DecisionKind.HOLD  # medium: hold and notify


def evaluate_guardrail(
    guardrail: Guardrail,
    *,
    control_value: float,
    treatment_value: float,
    n_control: int,
    n_treatment: int,
    prior_consecutive_breaches: int = 0,
) -> GuardrailEvaluation:
    """Evaluate one guardrail for one cycle.

    Below ``min_samples_per_arm`` in either arm, the guardrail is not evaluated and
    the prior consecutive-breach count is carried forward unchanged (a gap in data
    is neither a breach nor a recovery). Otherwise the metric is compared to the
    threshold; a breach increments the consecutive counter and a non-breach resets
    it to zero. The guardrail trips only once the counter reaches
    ``consecutive_breaches_to_trip``.

    Raises :class:`StatisticsError` on structurally invalid input (negative sample
    counts or non-finite metric values).
    """
    if n_control < 0 or n_treatment < 0:
        raise StatisticsError(
            f"sample counts must be non-negative, got n_control={n_control}, "
            f"n_treatment={n_treatment}"
        )
    for label, v in (("control_value", control_value), ("treatment_value", treatment_value)):
        if v != v or v in (float("inf"), float("-inf")):  # NaN or +/-inf
            raise StatisticsError(f"{label} must be finite, got {v}")

    threshold_value = guardrail.threshold.value

    if n_control < guardrail.min_samples_per_arm or n_treatment < guardrail.min_samples_per_arm:
        return GuardrailEvaluation(
            guardrail_name=guardrail.name,
            status=GuardrailStatus.INSUFFICIENT_DATA,
            breached=False,
            tripped=False,
            value=None,
            threshold=threshold_value,
            severity=guardrail.severity,
            consecutive_breaches=prior_consecutive_breaches,
            suggested_decision=None,
        )

    realized, breached = _comparison(
        guardrail.threshold.type, threshold_value, control_value, treatment_value
    )

    # Anti-flapping: only consecutive breaches count; a single clean cycle resets.
    consecutive = prior_consecutive_breaches + 1 if breached else 0
    tripped = breached and consecutive >= guardrail.consecutive_breaches_to_trip

    if tripped:
        status = GuardrailStatus.TRIPPED
        suggested = _decision_for_trip(guardrail.severity)
    elif breached:
        status = GuardrailStatus.BREACHED
        suggested = None
    else:
        status = GuardrailStatus.OK
        suggested = None

    return GuardrailEvaluation(
        guardrail_name=guardrail.name,
        status=status,
        breached=breached,
        tripped=tripped,
        value=realized,
        threshold=threshold_value,
        severity=guardrail.severity,
        consecutive_breaches=consecutive,
        suggested_decision=suggested,
    )
