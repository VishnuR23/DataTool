"""Core domain models for DataTool.

These Pydantic v2 models are the canonical in-memory shapes for everything the
controller reasons about. The most important type here is :class:`TrustContract`
(ARCHITECTURE.md §6, §7): it is *the* public API of the project — a typed,
versioned, inheritable description of how much autonomy the controller has over a
surface, and the safety bounds it must clamp every action to.

Design rules followed throughout (see ARCHITECTURE.md §22):
- Exhaustive validation. A malformed contract — a probability outside ``(0, 1)``,
  a non-monotonic ramp, a duplicate guardrail name — must fail loudly at parse
  time, not surface as a controller misbehaviour in production.
- Every field is documented in code. An engineer reading this module should be
  able to author a contract without leaving the file.
- Strict by default. ``extra="forbid"`` means a typo'd YAML key is an error, not
  a silently ignored field.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _Strict(BaseModel):
    """Base for all domain models: reject unknown fields, validate on assignment.

    Forbidding extra fields is deliberate — the trust contract is a safety
    artifact, and a misspelled key (``max_autonomus_pct``) silently defaulting
    would be exactly the kind of quiet failure this project exists to prevent.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #


class State(str, Enum):
    """Lifecycle state of an experiment (ARCHITECTURE.md §10)."""

    PROPOSED = "proposed"  # registered, not yet started
    CANARY = "canary"  # at initial_canary_pct
    RAMPING = "ramping"  # walking up the ramp schedule
    HOLDING = "holding"  # at ceiling, waiting for evidence or approval
    PROMOTING = "promoting"  # in transition to full rollout
    PROMOTED = "promoted"  # 100% rollout
    REVERTED = "reverted"  # killed; cooldown active
    CONCLUDED = "concluded"  # terminal: max_runtime reached without ship


class DecisionKind(str, Enum):
    """The output of the decision engine for one evaluation cycle."""

    CONTINUE = "continue"
    RAMP = "ramp"
    HOLD = "hold"
    PROMOTE = "promote"
    REVERT = "revert"


class Severity(str, Enum):
    """Guardrail severity. Governs what tripping does (ARCHITECTURE.md §8.4)."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"


class ThresholdType(str, Enum):
    """How a guardrail threshold value is interpreted."""

    ABSOLUTE = "absolute"  # compare the realized value directly
    RELATIVE_INCREASE = "relative_increase"  # fractional rise vs. control
    RELATIVE_DECREASE = "relative_decrease"  # fractional drop vs. control


# --------------------------------------------------------------------------- #
# Trust-contract building blocks
# --------------------------------------------------------------------------- #


class Threshold(_Strict):
    """A guardrail threshold: a value plus the rule for interpreting it."""

    type: ThresholdType
    value: float = Field(
        ...,
        allow_inf_nan=False,
        description=(
            "For 'absolute', the raw value the metric is compared against. For "
            "'relative_increase'/'relative_decrease', the fractional change vs. "
            "control that trips the guardrail (0.20 == 20%)."
        ),
    )

    @model_validator(mode="after")
    def _relative_values_are_positive(self) -> Threshold:
        # A relative threshold is a magnitude of change; zero or negative is
        # meaningless ("trip when the metric rises by -5%" has no interpretation).
        if self.type in (ThresholdType.RELATIVE_INCREASE, ThresholdType.RELATIVE_DECREASE):
            if self.value <= 0:
                raise ValueError(f"relative threshold value must be > 0, got {self.value}")
        return self


class Guardrail(_Strict):
    """A safety constraint evaluated independently of the goal metric (§8.4)."""

    name: str = Field(..., min_length=1, description="Unique within a contract.")
    source: str = Field(..., min_length=1, description="Adapter id, e.g. 'metrics.posthog'.")
    metric: str = Field(..., min_length=1)
    threshold: Threshold
    window: timedelta = Field(..., description="Trailing window the metric is evaluated over.")
    min_samples_per_arm: int = Field(
        100, ge=1, description="Below this per-arm sample count the guardrail is not evaluated."
    )
    severity: Severity = Severity.HIGH
    consecutive_breaches_to_trip: int = Field(
        2, ge=1, description="Anti-flapping: require this many consecutive breaches to trip."
    )

    @field_validator("window")
    @classmethod
    def _window_positive(cls, v: timedelta) -> timedelta:
        if v <= timedelta(0):
            raise ValueError(f"guardrail window must be positive, got {v}")
        return v


class Goal(_Strict):
    """The primary (or a secondary) metric an experiment is trying to move."""

    source: str = Field(..., min_length=1, description="Adapter id.")
    metric: str = Field(..., min_length=1)
    direction: Literal["increase", "decrease"]
    minimum_detectable_effect: float = Field(
        0.01,
        gt=0,
        allow_inf_nan=False,
        description="Smallest effect we care to detect; powers the stats engine.",
    )
    max_value: float = Field(
        1.0,
        gt=0,
        allow_inf_nan=False,
        description=(
            "A priori upper bound on any single observation; values must lie in "
            "[0, max_value]. It is the confidence sequence's sub-gamma scale c "
            "(Howard et al. 2021), so it must be a true cap, not the largest value "
            "seen. 1.0 fits rates and proportions; cap continuous metrics (e.g. "
            "revenue per visitor at a known maximum order value) before using them."
        ),
    )


class Statistics(_Strict):
    """Sequential-inference configuration (ARCHITECTURE.md §8)."""

    method: Literal["confidence_sequence", "msprt"] = "confidence_sequence"
    alpha: float = Field(
        0.05, gt=0, lt=1, allow_inf_nan=False, description="Per-experiment type-I error target."
    )
    fdr_budget_share: float = Field(
        0.10,
        gt=0,
        le=1,
        allow_inf_nan=False,
        description="Share of the org-level online-FDR (LORD) budget this experiment may consume.",
    )
    min_runtime: timedelta = Field(
        timedelta(hours=24), description="Do not ship before this much wall-clock has elapsed."
    )
    max_runtime: timedelta = Field(
        timedelta(days=14), description="Conclude without shipping after this much wall-clock."
    )
    novelty_buffer: timedelta = Field(
        timedelta(hours=6),
        description="Initial window during which decisions are suppressed (novelty effects).",
    )
    enable_cuped: bool = False
    cuped_pre_period: timedelta | None = Field(
        None, description="Pre-experiment window used for CUPED variance reduction."
    )

    @field_validator("min_runtime", "max_runtime")
    @classmethod
    def _runtimes_positive(cls, v: timedelta) -> timedelta:
        if v <= timedelta(0):
            raise ValueError(f"runtime must be positive, got {v}")
        return v

    @field_validator("novelty_buffer", "cuped_pre_period")
    @classmethod
    def _non_negative(cls, v: timedelta | None) -> timedelta | None:
        if v is not None and v < timedelta(0):
            raise ValueError(f"duration must be non-negative, got {v}")
        return v

    @model_validator(mode="after")
    def _coherent(self) -> Statistics:
        if self.max_runtime <= self.min_runtime:
            raise ValueError(
                f"max_runtime ({self.max_runtime}) must exceed min_runtime ({self.min_runtime})"
            )
        if self.novelty_buffer >= self.max_runtime:
            raise ValueError(
                f"novelty_buffer ({self.novelty_buffer}) must be shorter than "
                f"max_runtime ({self.max_runtime})"
            )
        # CUPED needs pre-experiment data to subtract; enabling it without a
        # window is a configuration error, not a silent no-op.
        if self.enable_cuped:
            if self.cuped_pre_period is None:
                raise ValueError("enable_cuped is true but cuped_pre_period is not set")
            if self.cuped_pre_period <= timedelta(0):
                raise ValueError("cuped_pre_period must be positive when CUPED is enabled")
        return self


class Scope(_Strict):
    """Where an experiment is allowed to act and how units are assigned."""

    allowed_routes: list[str] = Field(default_factory=list)
    forbidden_components: list[str] = Field(default_factory=list)
    assignment_unit: Literal["user", "session", "account"] = "user"
    exclusivity_group: str | None = None
    holdout_pct: float = Field(
        0.0,
        ge=0,
        lt=100,
        allow_inf_nan=False,
        description="Global holdout carved out of this surface and never exposed to the test.",
    )


class Allocation(_Strict):
    """The ramp schedule and the autonomous authority ceiling (§9.3)."""

    initial_canary_pct: float = Field(
        1.0, gt=0, le=100, allow_inf_nan=False, description="Allocation the experiment starts at."
    )
    ramp_schedule: list[float] = Field(
        default_factory=lambda: [1, 2.5, 5, 10, 25],
        description="Strictly increasing allocation steps, each in (0, 100].",
    )
    max_autonomous_pct: float = Field(
        5.0,
        gt=0,
        le=100,
        allow_inf_nan=False,
        description="Ceiling the controller may ramp to without human approval; clamp target.",
    )
    full_rollout_requires: Literal["autonomous", "human_approval"] = "human_approval"
    min_step_dwell: timedelta = Field(
        timedelta(hours=4), description="Minimum time to hold at a step before ramping again."
    )

    @field_validator("ramp_schedule")
    @classmethod
    def _schedule_monotonic_and_bounded(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("ramp_schedule must contain at least one step")
        for step in v:
            if not (0 < step <= 100):
                raise ValueError(f"ramp step {step} out of bounds; each step must be in (0, 100]")
        if any(b <= a for a, b in zip(v, v[1:], strict=False)):
            raise ValueError(f"ramp_schedule must be strictly increasing, got {v}")
        return v

    @field_validator("min_step_dwell")
    @classmethod
    def _dwell_non_negative(cls, v: timedelta) -> timedelta:
        if v < timedelta(0):
            raise ValueError(f"min_step_dwell must be non-negative, got {v}")
        return v

    @model_validator(mode="after")
    def _canary_within_ceiling(self) -> Allocation:
        # The controller cannot autonomously start an experiment above its own
        # autonomous ceiling; that would be clamped to nothing sensible on tick 1.
        if self.initial_canary_pct > self.max_autonomous_pct:
            raise ValueError(
                f"initial_canary_pct ({self.initial_canary_pct}) exceeds "
                f"max_autonomous_pct ({self.max_autonomous_pct})"
            )
        return self


class ReversionPolicy(_Strict):
    """What happens when an experiment is reverted (§9.3)."""

    strategy: Literal["instant", "gradual_5min", "gradual_1h"] = "instant"
    cooldown: timedelta = Field(
        timedelta(hours=24), description="How long the surface is locked after a revert."
    )
    notify: list[str] = Field(default_factory=list)
    open_postmortem: str | None = None
    halt_related: bool = True

    @field_validator("cooldown")
    @classmethod
    def _cooldown_non_negative(cls, v: timedelta) -> timedelta:
        if v < timedelta(0):
            raise ValueError(f"cooldown must be non-negative, got {v}")
        return v


class GraduationRule(_Strict):
    """A (condition, action) pair governing autonomy graduation (§9.4).

    ``when`` and ``action`` are intentionally loose dicts: they carry the small
    DSL evaluated by the trust ledger. Their structure is validated where it is
    interpreted (``control/ledger.py``), not here, so the contract schema does
    not have to enumerate every operator the ledger supports.
    """

    when: dict
    action: dict

    @field_validator("when", "action")
    @classmethod
    def _non_empty(cls, v: dict) -> dict:
        if not v:
            raise ValueError("graduation rule 'when'/'action' clauses must be non-empty")
        return v


class Graduation(_Strict):
    """Rules that grow or shrink autonomy over time, plus a human review cadence."""

    rules: list[GraduationRule] = Field(default_factory=list)
    review_cadence: timedelta = Field(timedelta(days=7))

    @field_validator("review_cadence")
    @classmethod
    def _cadence_positive(cls, v: timedelta) -> timedelta:
        if v <= timedelta(0):
            raise ValueError(f"review_cadence must be positive, got {v}")
        return v


class Authorization(_Strict):
    """Who may modify the contract, grant autonomy, or halt (§7)."""

    modify_contract: list[str] = Field(default_factory=lambda: ["admin"])
    grant_autonomy_increase: list[str] = Field(default_factory=lambda: ["admin"])
    emergency_halt: list[str] = Field(default_factory=lambda: ["any"])


class TrustContract(_Strict):
    """The typed, versioned, inheritable trust contract — the project's spine.

    Every action the orchestrator takes is clamped to this object, and every
    clamp is logged. See ARCHITECTURE.md §7 for the canonical YAML example and
    ``core/contract.py`` for three-layer resolution (org → surface → experiment).
    """

    version: str = Field("1.0", min_length=1)
    scope: Scope
    allocation: Allocation
    guardrails: list[Guardrail] = Field(default_factory=list)
    goal: Goal
    secondary_metrics: list[Goal] = Field(default_factory=list)
    statistics: Statistics
    reversion: ReversionPolicy
    graduation: Graduation
    authorization: Authorization

    @model_validator(mode="after")
    def _guardrail_names_unique(self) -> TrustContract:
        # Guardrails are keyed by name for consecutive-breach counters and for
        # decision reasons of the form 'guardrail.<name>.tripped'; duplicate
        # names would collide those counters silently.
        names = [g.name for g in self.guardrails]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate guardrail names in contract: {dupes}")
        return self


# --------------------------------------------------------------------------- #
# Experiment definition
# --------------------------------------------------------------------------- #


class VariantSpec(_Strict):
    """One arm of an experiment."""

    name: str = Field(..., min_length=1)
    is_control: bool = False
    source: Literal["existing", "static", "llm", "external"]
    payload: dict = Field(default_factory=dict, description="Adapter-specific variant reference.")


class ExperimentSpec(_Strict):
    """A full experiment definition: identity, variants, and its trust contract."""

    name: str = Field(..., min_length=1)
    surface: str = Field(..., min_length=1)
    owner: str = Field(..., min_length=1)
    description: str | None = None
    variants: list[VariantSpec]
    contract: TrustContract

    @model_validator(mode="after")
    def _two_arms_one_control(self) -> ExperimentSpec:
        names = [v.name for v in self.variants]
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            raise ValueError(f"duplicate variant names: {dupes}")
        # DECISION: enforce exactly two arms (one control, one treatment) at the
        # model boundary. ARCHITECTURE.md §20 lists >2 variants as anti-scope for
        # the MVP; this is the single place to relax when §23's multi-variant work
        # lands.
        if len(self.variants) != 2:
            raise ValueError(
                f"MVP experiments must have exactly two variants (control + treatment), "
                f"got {len(self.variants)}"
            )
        controls = [v for v in self.variants if v.is_control]
        if len(controls) != 1:
            raise ValueError(
                f"experiment must have exactly one control variant, got {len(controls)}"
            )
        return self


# --------------------------------------------------------------------------- #
# Runtime data shapes
# --------------------------------------------------------------------------- #


class Sample(_Strict):
    """An aggregated per-arm metric snapshot used as input to the stats engine."""

    experiment_id: UUID
    variant_id: UUID
    metric: str = Field(..., min_length=1)
    window_start: datetime
    window_end: datetime
    n: int = Field(..., ge=0, description="Observation count in the window.")
    sum: float = Field(..., allow_inf_nan=False, description="Sum of metric values.")
    sum_sq: float = Field(
        ..., ge=0, allow_inf_nan=False, description="Sum of squared values (for variance)."
    )

    @model_validator(mode="after")
    def _window_ordered(self) -> Sample:
        if self.window_end <= self.window_start:
            raise ValueError(
                f"window_end ({self.window_end}) must be after window_start ({self.window_start})"
            )
        return self


class CovariateSample(_Strict):
    """One arm's outcome ``y`` and pre-period covariate ``x`` cross-moments (CUPED, §8.5).

    ``x`` is each observation's unit's mean of the same metric over the pre-period
    window (0 when the unit has no pre-period data).
    """

    variant_id: UUID
    n: int = Field(..., ge=0)
    sum_y: float = Field(..., allow_inf_nan=False)
    sum_yy: float = Field(..., ge=0, allow_inf_nan=False)
    sum_x: float = Field(..., allow_inf_nan=False)
    sum_xx: float = Field(..., ge=0, allow_inf_nan=False)
    sum_xy: float = Field(..., allow_inf_nan=False)


class CupedData(_Strict):
    """What a metrics source returns for CUPED: per-arm cross-moments, plus unit-level
    pre-period pairs (``a`` = earlier window mean, ``b`` = later) for estimating theta."""

    arms: list[CovariateSample]
    pre_n: int = Field(..., ge=0, description="Units with data in both pre-period windows.")
    pre_sum_a: float = Field(..., allow_inf_nan=False)
    pre_sum_b: float = Field(..., allow_inf_nan=False)
    pre_sum_aa: float = Field(..., ge=0, allow_inf_nan=False)
    pre_sum_ab: float = Field(..., allow_inf_nan=False)


class Decision(_Strict):
    """The decision engine's output for one cycle, with structured reasoning."""

    experiment_id: UUID
    kind: DecisionKind
    reason: str = Field(..., min_length=1, description="Human-readable summary.")
    structured_reason: dict = Field(
        default_factory=dict,
        description="Machine-readable, e.g. {'cs_lower': 0.021, 'fdr_budget_consumed': 0.04}.",
    )
    suggested_action: dict | None = None
