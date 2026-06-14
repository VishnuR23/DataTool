"""Runtime data structures for the control loop.

The Pydantic models in ``core/models.py`` describe an experiment as *registered*
(its spec and contract). These dataclasses describe an experiment as *running* —
the mutable bookkeeping the control loop carries between evaluation cycles, plus
the per-cycle observations the decision engine consumes. They are plain dataclasses
(not Pydantic) because they are internal control-plane state, not a public schema,
and they are rebuilt from persistence each tick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from datatool.core.models import State
from datatool.stats.confidence_sequence import ArmStats


@dataclass
class ExperimentRuntime:
    """Mutable per-experiment state tracked across decision cycles."""

    experiment_id: UUID
    name: str
    surface: str
    state: State
    current_allocation_pct: float  # treatment allocation, in [0, 100]
    started_at: datetime
    last_decision_at: datetime | None = None
    last_ramp_at: datetime | None = None  # for the min_step_dwell check
    # Per-guardrail consecutive-breach counters (the anti-flapping state).
    guardrail_breach_counts: dict[str, int] = field(default_factory=dict)
    # True once the experiment has been promoted — distinguishes a plain revert
    # from a false-positive-ship revert when the ledger updates trust.
    has_promoted: bool = False
    # Whether the one-time "ready for human approval" notification has been sent.
    approval_notified: bool = False


@dataclass
class GoalObservation:
    """Per-arm aggregated goal-metric statistics for one cycle."""

    control: ArmStats
    treatment: ArmStats


@dataclass
class GuardrailObservation:
    """The measured value of one guardrail's metric in each arm for one cycle."""

    control_value: float
    treatment_value: float
    n_control: int
    n_treatment: int


@dataclass
class AssignmentObservation:
    """Assignment counts vs. configured ratios, for the SRM check."""

    counts: dict[str, int]  # variant name -> units assigned
    expected_ratios: dict[str, float]  # variant name -> configured allocation share
