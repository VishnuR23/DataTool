# DataTool — Architecture & MVP Build Brief

> **Project name:** DataTool. The Python package is `datatool` (PEP 8 lowercase), the CLI command is `datatool`, env vars are prefixed `DATATOOL_`. Brand mentions in prose use `DataTool`; code references use `datatool` in backticks.
>
> **Audience for this document:** Claude Code (or any agentic coder) building the first-pass MVP. The intent is that nothing critical is left to inference — anywhere a judgment call is *acceptable*, it's marked. Everywhere else is specified.

---

## 1. What we're building

DataTool is an open-source autonomous experimentation controller. It sits on top of whatever feature-flagging, metrics, and variant-generation tools a team already uses, and drives experiments through their full lifecycle without a human in the loop — while honoring a declarative *trust contract* that bounds and audits its authority.

In one sentence: **a daemon that takes "here's a variant, here are the guardrails, here's the trust budget" and handles ramp, evaluate, promote, or revert — with statistically valid sequential inference and a provable safety contract.**

What it is *not*:
- Not a feature-flag system (uses yours)
- Not a metrics warehouse (queries yours)
- Not a variant generator (accepts variants from humans, LLMs, or Optimizely Opal)
- Not a marketing/visual editor (the target user is a data scientist / platform engineer)

## 2. Positioning & strategic constraints

This project is open-source-first; the long-term goal is broad community adoption and clean interoperability with existing experimentation stacks (feature-flag systems, metrics warehouses, variant tools). Design choices throughout favor:

- **Vendor neutrality** over depth in any one stack. Every external system is reached through an adapter; no business logic depends on a specific vendor.
- **Verifiable correctness** over feature breadth. The statistics engine must be right and demonstrably so (calibration tests with known-truth Monte Carlo runs are part of CI). Half the moat is "we get the stats right where DIY scripts and even some commercial tools don't."
- **The trust contract as the spine.** Every component in the system answers to the contract: the orchestrator clamps to it, the decision engine respects its windows, the ledger feeds its graduation, the CLI surfaces it. Without the trust contract, this project is "another experimentation tool."
- **No data ownership.** DataTool stores its own state (experiment registry, ledger, audit log) but does not own user events, flag definitions outside its own reference impl, or variant code. This keeps the project composable and dramatically reduces the operational surface area.

## 3. Tech stack (pick these, don't relitigate)

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python 3.11+ | Statistics libraries, audience, adapter ergonomics |
| Schema/validation | Pydantic v2 | Standard, fast, great IDE support |
| Database | PostgreSQL 15+ | One process + one Postgres is the install story |
| ORM/migrations | SQLAlchemy 2.0 + Alembic | Mature, well-understood |
| Terminal UI | Textual + plotext | The primary interface: live console + assistant (both MIT) |
| HTTP framework | FastAPI | Daemon's read-only HTTP API + Prometheus metrics (no browser dashboard) |
| Assistant / variant LLM | anthropic / openai (optional extra) | Conversational interface + variant generation; operator's own key |
| CLI | Typer | Click-based, type-driven, ergonomic |
| Testing | pytest + hypothesis | Property-based tests for the state machine |
| Statistics primitives | scipy.stats + custom CS impl | See §8; do not pull a heavy framework |
| Logging | structlog | Structured logs as a first-class concern |
| Metrics | prometheus_client | OpenMetrics export for the controller itself |
| Packaging | pyproject.toml + `uv` | Modern, fast |
| Deployment | Docker + docker-compose | Single-machine install must just work |
| License | Apache-2.0 | Permissive, broadly compatible OSS license |

**Single-process MVP.** Scheduler, decision engine, and orchestrator are modules inside one long-running Python process. Postgres is the only external dependency for the controller itself. Do not introduce Redis, RabbitMQ, Celery, Temporal, or Kubernetes for v1.

## 4. Repository structure

```
datatool/
├── README.md
├── LICENSE                          # Apache 2.0
├── pyproject.toml
├── docker-compose.yml
├── Dockerfile
├── alembic.ini
├── alembic/versions/
├── docs/
│   ├── architecture.md              # (this document, polished)
│   ├── trust_contract.md
│   ├── statistics.md
│   ├── adapters.md
│   ├── cli.md
│   └── recipes/                     # end-to-end how-tos
├── datatool/
│   ├── __init__.py
│   ├── core/
│   │   ├── models.py                # Pydantic domain models
│   │   ├── contract.py              # Trust contract + clamping
│   │   ├── state_machine.py
│   │   └── exceptions.py
│   ├── stats/
│   │   ├── confidence_sequence.py   # Howard et al. CS
│   │   ├── msprt.py                 # Optional mSPRT (deferred OK)
│   │   ├── fdr.py                   # Online FDR (LORD)
│   │   ├── guardrails.py
│   │   ├── srm.py                   # Sample ratio mismatch
│   │   └── cuped.py                 # Variance reduction (optional)
│   ├── control/
│   │   ├── scheduler.py
│   │   ├── decision_engine.py
│   │   ├── orchestrator.py
│   │   └── ledger.py
│   ├── adapters/
│   │   ├── base.py                  # Protocols
│   │   ├── flag/
│   │   │   ├── base.py
│   │   │   └── postgres.py          # MVP reference impl
│   │   ├── metrics/
│   │   │   ├── base.py
│   │   │   ├── posthog.py           # MVP
│   │   │   └── csv.py               # MVP (for testing/replay)
│   │   ├── variant/
│   │   │   ├── base.py
│   │   │   ├── static.py            # MVP
│   │   │   └── llm.py               # MVP (Claude / OpenAI)
│   │   └── notify/
│   │       ├── base.py
│   │       ├── slack.py             # MVP
│   │       └── webhook.py           # MVP
│   ├── persistence/
│   │   ├── db.py
│   │   ├── models.py                # SQLAlchemy models
│   │   └── repositories.py
│   ├── console/                    # the primary UI (terminal-native)
│   │   ├── app.py                   # live Textual console + chat pane
│   │   ├── assistant.py             # conversational assistant: gated tool-use over operations
│   │   ├── live.py                  # feed poller + experiment summaries
│   │   ├── feed.py                  # audit-tail → TelemetryEvent
│   │   └── events.py                # TelemetryEvent model
│   ├── api/
│   │   ├── app.py                   # FastAPI read-only HTTP API + Prometheus metrics
│   │   └── routes.py
│   ├── cli/
│   │   ├── main.py
│   │   ├── commands.py
│   │   └── formatters.py
│   ├── simulator/
│   │   ├── replay.py
│   │   └── synthetic.py             # Synthetic data generation
│   ├── observability/
│   │   ├── logging.py
│   │   └── metrics.py
│   └── config.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── stats/
│   │   ├── test_cs_calibration.py   # MUST PASS; FDR/coverage check
│   │   ├── test_cs_power.py
│   │   ├── test_fdr_calibration.py
│   │   └── test_guardrails.py
│   └── e2e/
├── examples/
│   ├── pricing_page.yaml            # the canonical §7 contract (PostHog metrics)
│   ├── simulation_demo.yaml         # CSV-driven demo for `datatool simulate`
│   ├── synthetic_events.csv         # bundled demo data (positive scenario)
│   └── llm_variant.yaml
└── benchmarks/
    └── stats_correctness.py         # Monte Carlo correctness suite
```

## 5. Database schema (PostgreSQL)

All timestamps are `TIMESTAMPTZ`. All IDs are UUIDs (`gen_random_uuid()`). Audit-relevant tables are append-only — updates only via new rows.

```sql
-- experiments: the registered experiment definitions
CREATE TABLE experiments (
    id              UUID PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    surface         TEXT NOT NULL,
    owner           TEXT NOT NULL,
    contract        JSONB NOT NULL,          -- the full TrustContract
    spec            JSONB NOT NULL,          -- variants, goal, scope, etc.
    state           TEXT NOT NULL,           -- see §10 state machine
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_experiments_state ON experiments(state);
CREATE INDEX idx_experiments_surface ON experiments(surface);

-- variants: distinct variants per experiment
CREATE TABLE variants (
    id              UUID PRIMARY KEY,
    experiment_id   UUID NOT NULL REFERENCES experiments(id),
    name            TEXT NOT NULL,           -- 'control', 'treatment', etc.
    is_control      BOOLEAN NOT NULL,
    payload         JSONB NOT NULL,          -- adapter-specific variant ref
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (experiment_id, name)
);

-- samples: aggregated metric snapshots used for decisions
CREATE TABLE samples (
    id              UUID PRIMARY KEY,
    experiment_id   UUID NOT NULL REFERENCES experiments(id),
    variant_id      UUID NOT NULL REFERENCES variants(id),
    metric          TEXT NOT NULL,
    window_start    TIMESTAMPTZ NOT NULL,
    window_end      TIMESTAMPTZ NOT NULL,
    n               BIGINT NOT NULL,         -- count
    sum             DOUBLE PRECISION,        -- sum of values
    sum_sq          DOUBLE PRECISION,        -- sum of squares (for variance)
    extra           JSONB,                   -- adapter-specific
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_samples_exp_metric ON samples(experiment_id, metric, window_end DESC);

-- decisions: every output of the decision engine
CREATE TABLE decisions (
    id              UUID PRIMARY KEY,
    experiment_id   UUID NOT NULL REFERENCES experiments(id),
    kind            TEXT NOT NULL,           -- 'continue'|'ramp'|'hold'|'promote'|'revert'
    reason          TEXT NOT NULL,           -- structured: see §9
    inputs          JSONB NOT NULL,          -- snapshot of stats inputs
    outputs         JSONB NOT NULL,          -- CS bounds, posterior, etc.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_decisions_exp ON decisions(experiment_id, created_at DESC);

-- actions: every action taken by the orchestrator on the execution plane
CREATE TABLE actions (
    id              UUID PRIMARY KEY,
    experiment_id   UUID NOT NULL REFERENCES experiments(id),
    decision_id    UUID REFERENCES decisions(id),
    kind            TEXT NOT NULL,           -- 'allocate'|'kill'|'promote'|'notify'
    adapter         TEXT NOT NULL,           -- 'flag.postgres', 'notify.slack', etc.
    payload         JSONB NOT NULL,
    clamped         BOOLEAN NOT NULL,        -- did the contract clamp us?
    clamp_reason    TEXT,
    succeeded       BOOLEAN NOT NULL,
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_actions_exp ON actions(experiment_id, created_at DESC);

-- guardrail_evaluations: every guardrail check
CREATE TABLE guardrail_evaluations (
    id              UUID PRIMARY KEY,
    experiment_id   UUID NOT NULL REFERENCES experiments(id),
    guardrail_name  TEXT NOT NULL,
    breached        BOOLEAN NOT NULL,
    value           DOUBLE PRECISION,
    threshold       DOUBLE PRECISION,
    severity        TEXT NOT NULL,           -- 'critical'|'high'|'medium'
    consecutive     INT NOT NULL,            -- consecutive breach count
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_guardrails_exp ON guardrail_evaluations(experiment_id, created_at DESC);

-- trust_events: graduation/demotion events on the trust ledger
CREATE TABLE trust_events (
    id              UUID PRIMARY KEY,
    surface         TEXT NOT NULL,
    kind            TEXT NOT NULL,           -- 'clean_promotion'|'false_positive_ship'|'graduate'|'demote'
    experiment_id   UUID REFERENCES experiments(id),
    delta           JSONB,                   -- e.g. {"max_autonomous_pct": +5}
    new_state       JSONB NOT NULL,          -- snapshot of ledger state for surface
    reason          TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_trust_surface ON trust_events(surface, created_at DESC);

-- audit_log: append-only firehose for everything else worth recording
CREATE TABLE audit_log (
    id              UUID PRIMARY KEY,
    kind            TEXT NOT NULL,           -- 'experiment.registered', 'config.modified', etc.
    actor           TEXT NOT NULL,           -- user, 'system', adapter name
    experiment_id   UUID REFERENCES experiments(id),
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_audit_kind ON audit_log(kind, created_at DESC);

-- assignments (used only by Postgres flag adapter as reference impl)
CREATE TABLE flag_assignments (
    experiment_id   UUID NOT NULL REFERENCES experiments(id),
    unit_id         TEXT NOT NULL,           -- user_id, session_id, etc.
    variant_id      UUID NOT NULL REFERENCES variants(id),
    assigned_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (experiment_id, unit_id)
);

-- allocations (current allocation state per experiment, used by Postgres flag adapter)
CREATE TABLE flag_allocations (
    experiment_id   UUID PRIMARY KEY REFERENCES experiments(id),
    allocations     JSONB NOT NULL,          -- {"control": 99, "treatment": 1}
    killed          BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- llm_variant_cache: caches outputs of the LLM variant adapter for determinism
CREATE TABLE llm_variant_cache (
    cache_key       TEXT PRIMARY KEY,        -- sha256 of (description + path + ref_hash + model + seed + extra)
    model           TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    response        JSONB NOT NULL,          -- {"summary", "rationale", "code"}
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hit_count       INT NOT NULL DEFAULT 0
);
```

## 6. Core domain model (Pydantic)

Hold every internal data structure to these shapes. The `TrustContract` model below is *the* public API of the project — design it carefully.

```python
# datatool/core/models.py
from datetime import datetime, timedelta
from enum import Enum
from typing import Literal, Optional
from uuid import UUID
from pydantic import BaseModel, Field

class State(str, Enum):
    PROPOSED = "proposed"          # registered, not yet started
    CANARY = "canary"              # at initial_canary_pct
    RAMPING = "ramping"            # walking up the ramp schedule
    HOLDING = "holding"            # at ceiling, waiting for evidence or approval
    PROMOTING = "promoting"        # in transition to full rollout
    PROMOTED = "promoted"          # 100% rollout
    REVERTED = "reverted"          # killed; cooldown active
    CONCLUDED = "concluded"        # terminal: max_runtime reached without ship

class DecisionKind(str, Enum):
    CONTINUE = "continue"
    RAMP = "ramp"
    HOLD = "hold"
    PROMOTE = "promote"
    REVERT = "revert"

class Severity(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"

class ThresholdType(str, Enum):
    ABSOLUTE = "absolute"
    RELATIVE_INCREASE = "relative_increase"
    RELATIVE_DECREASE = "relative_decrease"

class Threshold(BaseModel):
    type: ThresholdType
    value: float                   # interpretation depends on type
    
class Guardrail(BaseModel):
    name: str
    source: str                    # adapter id, e.g. 'metrics.posthog'
    metric: str
    threshold: Threshold
    window: timedelta              # e.g. PT10M
    min_samples_per_arm: int = 100
    severity: Severity = Severity.HIGH
    consecutive_breaches_to_trip: int = 2  # anti-flapping

class Goal(BaseModel):
    source: str                    # adapter id
    metric: str
    direction: Literal["increase", "decrease"]
    minimum_detectable_effect: float = 0.01

class Statistics(BaseModel):
    method: Literal["confidence_sequence", "msprt"] = "confidence_sequence"
    alpha: float = 0.05
    fdr_budget_share: float = 0.10
    min_runtime: timedelta = timedelta(hours=24)
    max_runtime: timedelta = timedelta(days=14)
    novelty_buffer: timedelta = timedelta(hours=6)
    enable_cuped: bool = False
    cuped_pre_period: Optional[timedelta] = None

class Scope(BaseModel):
    allowed_routes: list[str] = Field(default_factory=list)
    forbidden_components: list[str] = Field(default_factory=list)
    assignment_unit: Literal["user", "session", "account"] = "user"
    exclusivity_group: Optional[str] = None
    holdout_pct: float = 0.0       # global holdout from this surface

class Allocation(BaseModel):
    initial_canary_pct: float = 1.0
    ramp_schedule: list[float] = Field(default_factory=lambda: [1, 2.5, 5, 10, 25])
    max_autonomous_pct: float = 5.0
    full_rollout_requires: Literal["autonomous", "human_approval"] = "human_approval"
    min_step_dwell: timedelta = timedelta(hours=4)

class ReversionPolicy(BaseModel):
    strategy: Literal["instant", "gradual_5min", "gradual_1h"] = "instant"
    cooldown: timedelta = timedelta(hours=24)
    notify: list[str] = Field(default_factory=list)
    open_postmortem: Optional[str] = None
    halt_related: bool = True

class GraduationRule(BaseModel):
    when: dict                     # condition dict, see §9
    action: dict                   # action dict, see §9

class Graduation(BaseModel):
    rules: list[GraduationRule] = Field(default_factory=list)
    review_cadence: timedelta = timedelta(days=7)

class Authorization(BaseModel):
    modify_contract: list[str] = Field(default_factory=lambda: ["admin"])
    grant_autonomy_increase: list[str] = Field(default_factory=lambda: ["admin"])
    emergency_halt: list[str] = Field(default_factory=lambda: ["any"])

class TrustContract(BaseModel):
    version: str = "1.0"
    scope: Scope
    allocation: Allocation
    guardrails: list[Guardrail]
    goal: Goal
    secondary_metrics: list[Goal] = Field(default_factory=list)
    statistics: Statistics
    reversion: ReversionPolicy
    graduation: Graduation
    authorization: Authorization

class VariantSpec(BaseModel):
    name: str
    is_control: bool = False
    source: Literal["existing", "static", "llm", "external"]
    payload: dict                  # adapter-specific

class ExperimentSpec(BaseModel):
    name: str
    surface: str
    owner: str
    description: Optional[str] = None
    variants: list[VariantSpec]
    contract: TrustContract

class Sample(BaseModel):
    experiment_id: UUID
    variant_id: UUID
    metric: str
    window_start: datetime
    window_end: datetime
    n: int
    sum: float
    sum_sq: float

class Decision(BaseModel):
    experiment_id: UUID
    kind: DecisionKind
    reason: str                    # human-readable summary
    structured_reason: dict        # machine-readable, e.g. {"cs_lower": 0.021, "fdr_budget_consumed": 0.04}
    suggested_action: Optional[dict] = None
```

## 7. The trust contract (canonical example)

Every experiment ships with a contract. Here is the canonical full example that should live in `examples/pricing_page.yaml`:

```yaml
experiment: pricing-headline-clarity
surface: pricing-page
owner: growth-team
description: |
  Tests a clearer value-prop headline on the pricing page.

variants:
  - name: control
    is_control: true
    source: existing
    payload: {}
  - name: treatment
    source: static
    payload:
      type: react_component
      ref: variants/pricing-v2.tsx

contract:
  version: "1.0"

  scope:
    allowed_routes: ["/pricing", "/pricing/checkout"]
    forbidden_components: ["PaymentForm", "LegalDisclaimer", "TaxCalculator"]
    assignment_unit: user
    exclusivity_group: pricing
    holdout_pct: 5.0

  allocation:
    initial_canary_pct: 1.0
    ramp_schedule: [1, 2.5, 5, 10, 25]
    max_autonomous_pct: 5.0
    full_rollout_requires: human_approval
    min_step_dwell: PT4H

  guardrails:
    - name: error_rate
      source: metrics.posthog
      metric: $pageview_error_rate
      threshold: { type: relative_increase, value: 0.20 }
      window: PT10M
      min_samples_per_arm: 500
      severity: critical
      consecutive_breaches_to_trip: 2
    - name: latency_p95
      source: metrics.posthog
      metric: page_load_p95_ms
      threshold: { type: absolute, value: 2000 }
      window: PT5M
      severity: critical
      consecutive_breaches_to_trip: 2
    - name: checkout_completion
      source: metrics.posthog
      metric: checkout_completed_per_visitor
      threshold: { type: relative_decrease, value: 0.05 }
      window: PT1H
      severity: high
      consecutive_breaches_to_trip: 3

  goal:
    source: metrics.posthog
    metric: signup_completion_rate
    direction: increase
    minimum_detectable_effect: 0.02

  secondary_metrics:
    - source: metrics.posthog
      metric: time_on_page_seconds
      direction: increase

  statistics:
    method: confidence_sequence
    alpha: 0.05
    fdr_budget_share: 0.10
    min_runtime: PT24H
    max_runtime: P14D
    novelty_buffer: PT6H
    enable_cuped: false

  reversion:
    strategy: instant
    cooldown: PT24H
    notify: ["slack:#growth-experiments", "webhook:https://hooks.example.com/exp"]
    open_postmortem: "github:growth-team/experiments"
    halt_related: true

  graduation:
    rules:
      - when:
          clean_promotions_on_surface: ">=3"
          false_positive_ships: 0
        action:
          field: max_autonomous_pct
          op: increase
          by: 5
          up_to: 25
      - when:
          false_positive_ships: ">=1"
        action:
          field: max_autonomous_pct
          op: decrease
          by: 10
          cooldown: P30D
    review_cadence: P7D

  authorization:
    modify_contract: ["growth-lead", "eng-lead"]
    grant_autonomy_increase: ["growth-lead"]
    emergency_halt: ["any"]
```

**Contract inheritance.** Contracts compose in three layers:
1. **Org defaults** (`config/org_defaults.yaml`) — base values for all experiments.
2. **Surface defaults** (`config/surfaces/<surface>.yaml`) — overrides for a given surface.
3. **Experiment override** (the file above) — final overrides.

Resolution: deep merge with experiment > surface > org. Implement in `core/contract.py::resolve_contract()`. Tests in `tests/unit/test_contract_inheritance.py`.

## 8. Statistics engine (this is the differentiator — get it right)

This is the part of the system that earns respect, so the file you build first and break last is `datatool/stats/`. It must be (a) correct, (b) demonstrably correct via calibration tests, and (c) cite the papers it implements.

### 8.1 Confidence sequences (primary method)

Implement the **empirical-Bernstein confidence sequence** from:

> Howard, S. R., Ramdas, A., McAuliffe, J., & Sekhon, J. (2021). *Time-uniform, nonparametric, nonasymptotic confidence sequences.* Annals of Statistics, 49(2), 1055–1080.

For each evaluation cycle, given per-arm running statistics (n, sum, sum_sq, max value seen), compute a confidence sequence over the *difference* between treatment and control means.

```python
# datatool/stats/confidence_sequence.py
from dataclasses import dataclass
from math import log, sqrt

@dataclass
class CSBounds:
    lower: float
    upper: float
    point_estimate: float

@dataclass
class ArmStats:
    n: int
    sum: float
    sum_sq: float
    max_value: float  # bound for empirical Bernstein

    @property
    def mean(self) -> float:
        return self.sum / self.n if self.n > 0 else 0.0

    @property
    def variance(self) -> float:
        if self.n < 2:
            return 0.0
        return (self.sum_sq - self.n * self.mean ** 2) / (self.n - 1)

def confidence_sequence_diff(
    control: ArmStats,
    treatment: ArmStats,
    alpha: float = 0.05,
) -> CSBounds:
    """
    Empirical-Bernstein time-uniform CS for the difference in means.
    Implements Theorem 4 of Howard et al. (2021) for paired arms.
    """
    # NOTE TO CLAUDE CODE:
    # Implement the empirical-Bernstein CS using the running-variance
    # formulation. Key ingredients:
    #   - mixture distribution: use a polynomial mixture (rho = 1.4)
    #   - psi function: psi_E(lambda) for sub-exponential bounding
    #   - width formula: see Eq. 4.10 in the paper
    # Reference implementation to consult (NOT depend on): confseq R/Python package
    # by Howard. Re-derive in pure Python with explicit comments tying every line
    # to an equation number in the paper.
    ...
```

Calibration test (must pass; gates merges to `stats/`):

```python
# tests/stats/test_cs_calibration.py
def test_cs_type_i_error_at_or_below_alpha():
    """
    Generate 10,000 sequences under H0 (zero true effect).
    Run the CS at every step.
    Count the fraction of sequences where the CS ever excludes 0.
    Must be <= alpha (with a small slack for Monte Carlo noise).
    """
    alpha = 0.05
    n_runs = 10_000
    max_n = 5_000
    rejections = 0
    for _ in range(n_runs):
        control = ArmStats(n=0, sum=0, sum_sq=0, max_value=1.0)
        treatment = ArmStats(n=0, sum=0, sum_sq=0, max_value=1.0)
        rejected = False
        for step in range(1, max_n + 1):
            # Both arms drawn from same Bernoulli(0.1)
            ...
            if cs.lower > 0 or cs.upper < 0:
                rejected = True
                break
        if rejected:
            rejections += 1
    assert rejections / n_runs <= alpha + 0.005
```

Also implement `test_cs_power.py` to verify the CS detects real effects within reasonable sample sizes — not a correctness gate, but a regression guard.

### 8.2 Online FDR control

When many experiments run concurrently across the org, controlling the per-experiment alpha is not enough. Implement online FDR via:

> Javanmard, A. & Montanari, A. (2018). *Online rules for control of false discovery rate and false discovery exceedance.* Annals of Statistics, 46(2), 526–554.

Specifically: implement **LORD** (Levels based On Recent Discovery). Maintain a "wealth" budget at the project level. Each experiment's effective alpha is the contract's `fdr_budget_share` × current LORD allocation. When an experiment rejects (promotes), wealth is replenished by a fixed amount; over time, the wealth decays.

```python
# datatool/stats/fdr.py
class LORDController:
    def __init__(self, alpha: float = 0.05, w0: float = None):
        self.alpha = alpha
        self.w0 = w0 if w0 is not None else alpha / 2
        self.wealth = self.w0
        self.tests_seen = 0
        self.rejections = []  # list of test indices where we rejected
    
    def next_alpha(self) -> float:
        """Returns the alpha to use for the next test."""
        # gamma_t = c / (t^1.6) or similar; see paper for valid choices
        ...
    
    def record_outcome(self, rejected: bool) -> None:
        self.tests_seen += 1
        if rejected:
            self.rejections.append(self.tests_seen)
            self.wealth += self.alpha - self.w0  # replenish
        else:
            self.wealth -= self.next_alpha()
```

Calibration: a synthetic stream of experiments where 80% are null and 20% have real effects; verify the realized FDR ≤ target across 1000 simulations.

### 8.3 Sample ratio mismatch (SRM)

A standard A/B testing footgun: assignments don't match the configured allocations because of bot traffic, broken instrumentation, or cache pollution. SRM detection is a chi-squared test on assignment counts vs. expected. If the p-value < 0.001, the experiment is invalidated and reverted with a distinct "SRM" reason. This runs on every decision cycle, before any statistical inference. **No experiment can promote without passing SRM.**

```python
# datatool/stats/srm.py
def srm_p_value(observed: dict[str, int], expected_ratios: dict[str, float]) -> float:
    """Chi-squared test for sample ratio mismatch."""
    from scipy.stats import chisquare
    ...
```

### 8.4 Guardrail evaluation

Guardrails are evaluated independently of the goal-metric statistics, on a fast loop:

1. Pull the latest window of data for each guardrail's metric.
2. Compute the realized value (or relative change vs. control).
3. Compare to threshold per the contract.
4. Maintain a per-guardrail consecutive-breach counter; trip only after `consecutive_breaches_to_trip` consecutive breaches.
5. Tripping a `critical` guardrail emits an immediate `REVERT` decision. Tripping `high` emits `REVERT`; `medium` emits `HOLD` plus a notification.

Below `min_samples_per_arm`, guardrails are not evaluated (returns "insufficient data").

### 8.5 CUPED (optional variance reduction)

Implement, but feature-flag off by default:

> Deng, A., Xu, Y., Kohavi, R., & Walker, T. (2013). *Improving the sensitivity of online controlled experiments by utilizing pre-experiment data.* WSDM '13.

If `statistics.enable_cuped` is true and a pre-experiment window of data exists, compute the variance-reduction-adjusted metric and feed *that* into the confidence sequence. Document clearly that CUPED is most useful for low-variance metrics with strong pre-experiment correlation.

### 8.6 Test harness for the stats module

Required tests, all in `tests/stats/`:
- **CS calibration** (10k runs, type-I ≤ α)
- **CS power** (detect effects of size 2× MDE in ≤ expected sample size 80% of the time)
- **LORD FDR calibration** (1k streams, FDR ≤ target)
- **SRM correctness** (known-imbalance detection)
- **Guardrail anti-flapping** (single-breach doesn't trip; consecutive does)

These are CI gates. A failing stats test blocks merge.

## 9. Control loop

### 9.1 Scheduler (`control/scheduler.py`)

A `while True` loop with sleep. Every tick:

1. Query all experiments not in terminal states (`PROMOTED`, `REVERTED`, `CONCLUDED`).
2. For each, compute `due_at = last_decision_at + tick_interval` where `tick_interval` is min(guardrail windows, 1 minute). Skip if `now < due_at`.
3. Enqueue ready experiments onto a Python `Queue` consumed by the decision engine.

`tick_interval` defaults to 60s globally. Critical-severity guardrails effectively force evaluation every minute.

### 9.2 Decision Engine (`control/decision_engine.py`)

For each experiment dispatched by the scheduler:

1. **Hard pre-checks**:
   - Past `max_runtime`? → `CONCLUDED` (no ship).
   - Inside `novelty_buffer`? → `CONTINUE`.
   - SRM detected? → `REVERT` with reason `srm_failed`.

2. **Guardrails (fast loop)**: for each guardrail in the contract:
   - Pull latest window.
   - Evaluate breach.
   - Update consecutive-breach counter.
   - If tripped and severity ∈ {critical, high} → `REVERT` with reason `guardrail.<name>.tripped`. Skip remaining steps.

3. **Goal evaluation (sequential stats)**:
   - Pull goal metric samples.
   - Compute CS via `confidence_sequence_diff`.
   - Apply LORD alpha for this experiment.
   - If CS lower bound > 0 (and goal is `increase`):
     - If current allocation < ceiling → `RAMP` to next step.
     - If at ceiling → `HOLD` (waiting for approval or auto-conclude).
     - If `full_rollout_requires == "autonomous"` and CS is decisive → `PROMOTE`.
   - If CS upper bound < 0 (decisive loss) → `REVERT` with reason `goal.lost`.
   - Otherwise → `CONTINUE`.

4. Write the `Decision` row with full structured reasoning (CS bounds, FDR alpha used, sample sizes, guardrail values).

5. Dispatch to Orchestrator.

### 9.3 Orchestrator (`control/orchestrator.py`)

Takes a `Decision` and translates it into `Action`s. *This is where the trust contract is enforced.*

For `RAMP`:
1. Compute desired new allocation per `ramp_schedule`.
2. **Clamp**: if desired > `contract.allocation.max_autonomous_pct`, clamp to ceiling. Log `clamped=true` with reason.
3. Check `min_step_dwell` — if not satisfied, downgrade to `HOLD`.
4. Call `flag_provider.set_allocation(experiment_id, new_allocation)`.
5. Record `Action` with full payload.

For `REVERT`:
1. Call `flag_provider.kill(experiment_id)` immediately.
2. Update experiment state to `REVERTED`.
3. Trigger reversion side-effects per `contract.reversion`:
   - Send notifications.
   - Optionally open postmortem (`open_postmortem` field).
   - If `halt_related: true`, pause sibling experiments in the same exclusivity group.
4. Write `TrustEvent` with kind `false_positive_ship` if the revert was triggered after a previous promote, else just record the revert.
5. Set surface cooldown.

For `PROMOTE`:
1. Call `flag_provider.set_allocation(experiment_id, {treatment: 100})`.
2. Update state to `PROMOTED`.
3. Write `TrustEvent` of kind `clean_promotion`.
4. Notify.

For `HOLD`:
1. No flag changes. Just record the decision.
2. If at ceiling and contract says `full_rollout_requires: human_approval`, emit a one-time notification suggesting approval.

For `CONTINUE`: no action, just persist the decision.

### 9.4 Trust Ledger (`control/ledger.py`)

After every terminal action (promote, revert), compute whether graduation rules fire. Each rule is a (condition, action) pair:

**Conditions** (`when` clauses) are evaluated against the surface's history:
- `clean_promotions_on_surface` — count of `clean_promotion` trust events for this surface
- `false_positive_ships` — count of `false_positive_ship` events
- `days_since_last_revert` — derived
- Custom DSL operators: `>=`, `<=`, `==`, `>`, `<`

**Actions**:
- `increase` / `decrease` a contract field (typically `max_autonomous_pct`)
- `up_to` / `down_to` clamps the result
- `cooldown` prevents the same rule from re-firing for a period

The ledger does not modify contract files on disk — it writes a `TrustEvent` row whose `delta` is consulted at contract resolution time. Contracts as written are stable; effective contracts are computed.

### 9.5 Audit log

Every state transition, every contract clamp, every decision, every action, every adapter call (with timings), every notification — written to `audit_log` with a structured kind. Schema is intentionally flexible (JSONB payload). The CLI `datatool why` command synthesizes this into a human-readable narrative for any experiment.

## 10. State machine

```
            ┌──────────┐
            │ PROPOSED │
            └─────┬────┘
                  │ start
                  ▼
            ┌──────────┐
       ┌────│  CANARY  │────┐
       │    └─────┬────┘    │ guardrail trip
       │          │ ramp    ▼
       │          ▼      ┌─────────┐
       │    ┌──────────┐ │REVERTED │
       │  ┌─│ RAMPING  │ └─────────┘
       │  │ └─────┬────┘
       │  │       │ ceiling hit
       │  │       ▼
       │  │ ┌──────────┐
       │  │ │ HOLDING  │
       │  │ └────┬─────┘
       │  │      │ human approval / autonomous full
       │  │      ▼
       │  │ ┌──────────┐
       │  │ │PROMOTING │
       │  │ └────┬─────┘
       │  │      │
       │  │      ▼
       │  │ ┌──────────┐
       │  │ │ PROMOTED │
       │  │ └──────────┘
       │  │
       │  └────────────────► REVERTED (any non-terminal can transition)
       │
       └──── CONCLUDED (max_runtime, no ship)
```

Encode this in `core/state_machine.py` with explicit allowed transitions and a `transition(exp, to_state, reason)` function that writes to `audit_log`. Hypothesis property tests: from any state, only legal transitions succeed; revert is always reachable.

## 11. Adapters

All adapters are Python Protocols (PEP 544). Each adapter module exposes a `register()` function that adds itself to a global registry keyed by adapter id (e.g. `metrics.posthog`).

### 11.1 FlagProvider

```python
# datatool/adapters/flag/base.py
from typing import Protocol
from uuid import UUID

class FlagProvider(Protocol):
    adapter_id: str
    
    def assign(self, experiment_id: UUID, unit_id: str) -> str:
        """Return the variant name this unit is assigned to."""
        ...
    
    def set_allocation(
        self, experiment_id: UUID, allocations: dict[str, float]
    ) -> None:
        """Set the allocation percentages (must sum to 100)."""
        ...
    
    def kill(self, experiment_id: UUID) -> None:
        """Immediately set allocation to control: 100."""
        ...
    
    def get_allocation(self, experiment_id: UUID) -> dict[str, float]:
        ...
    
    def get_assignment_counts(
        self, experiment_id: UUID, since: datetime
    ) -> dict[str, int]:
        """For SRM: how many units have been assigned to each variant?"""
        ...
```

**MVP reference impl: `flag/postgres.py`** — uses the `flag_assignments` and `flag_allocations` tables. Assignment is deterministic via `hash(experiment_id + unit_id) % 10000 / 100.0` mapped to allocation buckets. This is the "no vendor needed" path that makes the project trivially adoptable.

Deferred (community contributable): `launchdarkly.py`, `statsig.py`, `posthog.py`, `growthbook.py`, `unleash.py`.

### 11.2 MetricsSource

```python
# datatool/adapters/metrics/base.py
class MetricsSource(Protocol):
    adapter_id: str
    
    def query(
        self,
        metric: str,
        experiment_id: UUID,
        variant_split_by: str,        # variant assignment unit
        window_start: datetime,
        window_end: datetime,
    ) -> list[Sample]:
        """Returns per-variant aggregated samples."""
        ...
    
    def supports_metric(self, metric: str) -> bool:
        ...
```

**MVP reference impls**:
- `metrics/posthog.py` — queries the PostHog Query API. Auth via env var.
- `metrics/csv.py` — reads a CSV with columns (unit_id, variant, metric, value, timestamp). Used for replay/testing.

Deferred: Snowflake, BigQuery, ClickHouse, Mixpanel, Amplitude.

### 11.3 VariantSource

```python
# datatool/adapters/variant/base.py
class VariantSource(Protocol):
    adapter_id: str
    
    def materialize(self, variant_spec: VariantSpec) -> dict:
        """Return adapter-specific payload to be stored with the variant."""
        ...
```

**MVP reference impls**:

- **`variant/static.py`** — variants are file paths in the user's repo or inline payloads in the YAML. No generation. This is the default path and what most production users will start with.

- **`variant/llm.py`** — calls Claude (default) or OpenAI to generate a variant from a surface description plus the contract scope. This is included in MVP because it carries the project's demo narrative: *describe what you want to test in plain English, DataTool generates a candidate, then safely tests and ships it (or reverts it) within the bounds of your trust contract.* Specifics:
  - **Auth**: `ANTHROPIC_API_KEY` (preferred) or `OPENAI_API_KEY`. Model is configurable per call (default: `claude-opus-5-5`).
  - **Refusals**: on Anthropic models that support server-side fallback (Opus 5.5/5, Fable 5.1/5, Sonnet 5.5), requests set `fallbacks: "default"` so a classifier refusal is re-run on Anthropic's recommended model for that category within the same call; a refusal that still comes back is a structured error, never empty output. `DATATOOL_LLM_REFUSAL_FALLBACK=false` pins the configured model. The console assistant uses the same setting.
  - **Input shape (from the YAML `payload`)**:
    ```yaml
    - name: treatment
      source: llm
      payload:
        surface_description: |
          Pricing page hero section: headline, subheading, CTA.
          Current headline is "Plans and pricing" — we want clearer value props.
        component_path: src/components/Pricing/Hero.tsx
        current_implementation_ref: variants/pricing-current.tsx   # optional
        model: claude-opus-5-5
        seed: 17                                                   # for determinism
        extra_instructions: "Keep the existing CTA button text."   # optional
    ```
  - **Output**: a single materialized variant payload (the generated component code or config diff). MVP is two-arm, so the adapter returns one variant per `materialize()` call.
  - **Scope enforcement**: the adapter reads the experiment's `contract.scope.forbidden_components` and includes them in the prompt as a hard constraint. After generation, it runs a static check on the output to verify no forbidden component is referenced. Generated output that fails this check is rejected with a structured error (the experiment cannot be registered).
  - **Determinism / caching**: `cache_key = sha256(surface_description + component_path + current_implementation_ref_hash + model + seed + extra_instructions)`. Cached outputs are stored in a `llm_variant_cache` table keyed by `cache_key`. Same inputs → same output, always. The cache is essential both for reproducibility and for keeping API costs predictable.
  - **Prompt template** lives at `datatool/adapters/variant/prompts/generate_variant.md`. It is plain text, version-controlled, and includes: the surface description, the component path, the current implementation (if provided), the contract scope (especially `forbidden_components`), and structured-output instructions requiring the model to return JSON with fields `{"summary": "...", "rationale": "...", "code": "..."}`.
  - **Failure modes**: invalid JSON → fail loudly with the raw model output captured in the audit log. Forbidden-component violation → fail loudly with the violating reference highlighted. API error → fail loudly with the upstream error and surface a retry suggestion. No silent fallbacks; this adapter is allowed to refuse to materialize.
  - **Dependencies**: `anthropic` and `openai` packages are *optional extras* (`pip install datatool[llm]`). The adapter is registered only if the extras are installed and the relevant env var is set.
  - **Tests**: a unit test suite uses a fake client to verify prompt construction, response parsing, scope-violation detection, and cache hits. An optional live test (skipped in CI) runs against the real API and is gated on `DATATOOL_RUN_LIVE_LLM_TESTS=1`.

Deferred: `variant/optimizely.py` (pulls candidate variants from Optimizely Opal — useful for users who already have a generation pipeline), `variant/llm_multi.py` (generates N candidates for multi-arm experiments, which themselves are post-MVP).

**Important framing**: `variant/llm.py` belongs in `adapters/`, *not* in `core/`. Generation is plugged in; the controller does not know or care how a variant came to exist. This separation is what keeps DataTool complementary to (rather than competitive with) generation tools, and what makes "Claude generates, DataTool safely ships" a story we can tell without becoming a generation product.

### 11.4 NotificationSink

```python
class NotificationSink(Protocol):
    adapter_id: str
    
    def send(self, event_kind: str, payload: dict) -> None:
        ...
```

**MVP reference impls**:
- `notify/slack.py` — webhook URL, formatted blocks for promote/revert/SRM/breach events.
- `notify/webhook.py` — generic POST to a configurable URL with JSON body.

## 12. CLI specification

Full command list. Implement with Typer. All commands operate on the local Postgres state.

```
datatool init                                  # creates DB, applies migrations
datatool register <file.yaml>                  # registers an experiment from a YAML file
datatool list [--state STATE] [--surface S]    # list experiments
datatool show <name|id>                        # show full state of an experiment
datatool status <name|id>                      # one-line status
datatool why <name|id>                         # human-readable narrative of decisions/actions
datatool pause <name|id>                       # transition to HOLDING (will not ramp further)
datatool resume <name|id>                      # resume from HOLDING
datatool revert <name|id> [--reason TEXT]      # manual revert
datatool promote <name|id> [--force]           # human approval to ship at 100%
datatool graduate <surface> [--by N]           # manually grant +N% autonomy
datatool ledger <surface>                      # show trust ledger history for a surface
datatool simulate <name|id> --data <path>      # replay against historical CSV
datatool daemon [--port 8080]                  # start the control plane + HTTP API
datatool version
datatool doctor                                # config + connectivity sanity check
```

Every command writes to `audit_log`. `datatool why` synthesizes audit events + decisions into prose with the structured reason fields inlined.

## 13. Terminal UX (console + assistant) + HTTP API

**The primary interface is the terminal**, not a browser. Running `datatool` in a project (like activating Claude Code) opens an interactive terminal built on two pieces, both in `datatool/console/`:

- **The live console (Textual).** A left panel of experiments and their state, and a live event feed tailing the append-only audit tables — one semantic glyph per kind (ramp ↑, hold ⏸, promote ✓, revert ⟲, guardrail ⚠). It watches the deterministic brain over the same Postgres it writes to (the DB is the bus — no new IPC); it never drives the brain. `plotext` renders guardrail/goal charts in-terminal.
- **The conversational assistant.** Interface + variant generation only — it never decides ramp/promote/revert. It exposes a small, fixed tool set mapped 1:1 to `control/operations.py`: reads (`list_experiments`, `status`, `why`) run freely and narrate from the audit log; mutating tools (`register`-adjacent `pause`, `resume`, `promote`, `revert`) route through the trust-contract-clamped, audited operations and require **explicit operator confirmation in the TUI** (reply `/yes` or `/no`) before they run. It uses the operator's own LLM key (`ANTHROPIC_API_KEY`); with no key the console still runs fully, just without the conversational layer. See §11.3 for the variant-generation adapter it reuses.

The brain runs headless (`datatool daemon`, survives terminal close); the TUI attaches to the same Postgres and can offer to start a brain locally if none is running.

The daemon also exposes a **read-only HTTP API + Prometheus metrics** (FastAPI, default port 8080) for programmatic and ops use — there is no browser dashboard. Endpoints:

```
GET  /healthz                             # liveness
GET  /readyz                              # readiness (DB connectivity)
GET  /metrics                             # Prometheus exposition
GET  /api/experiments                     # JSON list with state, allocation, last decision
GET  /api/experiments/{id}                # full detail
GET  /api/experiments/{id}/decisions      # paginated decision history
GET  /api/experiments/{id}/actions        # paginated action history
GET  /api/experiments/{id}/guardrails     # latest guardrail evaluations
GET  /api/surfaces/{surface}/ledger       # trust ledger history
POST /api/experiments/{id}/pause          # admin: pause
POST /api/experiments/{id}/promote        # admin: approve full rollout
POST /api/experiments/{id}/revert         # admin: manual revert
```

The CLI, YAML files, and the terminal console/assistant are the operator surfaces; the HTTP API is glanceable observability + programmatic access, not a control surface. The `POST` endpoints exist for automation; interactive humans use the CLI or the assistant (whose mutating actions are confirmation-gated and audited).

Auth: a single API key in `DATATOOL_API_KEY` env var, required on all `/api/*` POST endpoints. GET endpoints are open by default but can be locked behind the same key via `DATATOOL_REQUIRE_AUTH=true`. The daemon binds the API to `127.0.0.1` unless `--host` / `DATATOOL_API_HOST` widens it (the Docker image sets `0.0.0.0` for port mapping). Defer real OAuth/SSO.

## 14. Simulator

`datatool/simulator/replay.py` — point the controller at a CSV of historical events and let it run as if those events were arriving in real time, but accelerated.

```
datatool simulate pricing-headline-clarity --data ./historical/events.csv --speed 1000x
```

Reads the CSV (which has columns `timestamp, unit_id, variant, metric, value`), feeds events into the CSV metrics adapter, and runs the full decision loop with simulated time. At the end, prints every decision the controller would have made, including any reverts and promotes. This is your primary adoption tool: any team can run their last quarter's data and see what `datatool` *would have done*.

`datatool/simulator/synthetic.py` — generates synthetic event streams under known ground truth (null effect, positive effect, regression) for stats calibration tests.

## 15. Configuration

Environment variables (loaded by `config.py` via pydantic-settings):

```
DATATOOL_DATABASE_URL=postgresql://...
DATATOOL_API_KEY=...
DATATOOL_API_HOST=127.0.0.1                # daemon HTTP interface (image: 0.0.0.0)
DATATOOL_LOG_LEVEL=info
DATATOOL_TICK_INTERVAL_SECONDS=60
DATATOOL_CONFIG_DIR=./config
DATATOOL_EXPERIMENTS_DIR=./experiments
POSTHOG_API_KEY=...                       # for posthog metrics adapter
SLACK_WEBHOOK_URL=...                     # for slack notify adapter
ANTHROPIC_API_KEY=...                     # for llm variant adapter (preferred)
OPENAI_API_KEY=...                        # for llm variant adapter (fallback)
DATATOOL_LLM_DEFAULT_MODEL=claude-opus-5-5 # default model for llm variant adapter
DATATOOL_LLM_REFUSAL_FALLBACK=true        # retry refusals server-side on a fallback model
DATATOOL_RUN_LIVE_LLM_TESTS=0              # set to 1 to run live LLM tests in CI
```

Config files (loaded at startup, hot-reloaded on SIGHUP):
- `config/org_defaults.yaml` — base contract values
- `config/surfaces/<surface>.yaml` — per-surface overrides
- `experiments/<name>.yaml` — individual experiment definitions

## 16. Observability

**Structured logging** via `structlog`. Every log entry includes `experiment_id`, `decision_id` (if applicable), `adapter_id` (if applicable), `kind`, and a free-form `event`.

**Prometheus metrics** exposed at `/metrics`:
- `datatool_decisions_total{kind, experiment, surface}` — counter
- `datatool_actions_total{kind, adapter, succeeded}` — counter
- `datatool_guardrail_evaluations_total{guardrail, breached, severity}` — counter
- `datatool_experiments_active{state}` — gauge
- `datatool_adapter_call_duration_seconds{adapter, method}` — histogram
- `datatool_loop_duration_seconds` — histogram of full tick duration

**Audit log** is the primary forensic surface. `datatool why` reads it; the terminal console's live feed tails it; the assistant narrates from it; auditors and integrators reading the code will read it. Every action that touches an adapter or a flag emits an audit row.

## 17. Testing strategy

| Tier | Location | What it covers | CI gate? |
|---|---|---|---|
| Unit | `tests/unit/` | Pure functions, model validation, contract resolution | yes |
| Stats | `tests/stats/` | CS calibration, FDR calibration, SRM, guardrails | **yes — blocking** |
| Property | `tests/unit/test_state_machine.py` | State machine via hypothesis | yes |
| Integration | `tests/integration/` | Adapter contracts via mocks; DB via testcontainers | yes |
| E2E | `tests/e2e/` | Full daemon + Postgres + CSV adapter via simulator | yes |
| Benchmarks | `benchmarks/` | Stats correctness Monte Carlo (slow) | nightly |

The stats calibration suite is the project's most important test surface. A regression there means we ship false-positive winners. Treat it accordingly: explicit, well-commented, deterministic seeds, and clear failure messages that point to the equation in the paper that's now violated.

## 18. Build & dev workflow

```bash
# Clone, set up environment
git clone https://github.com/<user>/datatool
cd datatool
uv sync

# Start Postgres
docker-compose up -d postgres

# Init schema
uv run datatool init

# Register an example
uv run datatool register examples/pricing_page.yaml

# Start the daemon (dev mode, verbose)
DATATOOL_LOG_LEVEL=debug uv run datatool daemon

# In another shell:
uv run datatool list

# Replay the bundled demo (CSV-driven; pricing_page.yaml reads PostHog metrics)
uv run datatool register examples/simulation_demo.yaml
uv run datatool simulate checkout-button-color --data examples/synthetic_events.csv
```

CI (GitHub Actions): on every PR, run `uv sync && uv run pytest tests/unit tests/stats tests/integration tests/e2e` against Postgres in a service container. Nightly: `pytest benchmarks/`.

## 19. Documentation plan

These docs must exist and be high-quality before announcing:

- `README.md` — five-minute install + first experiment. Heavy on screenshots/casts.
- `docs/architecture.md` — this document, polished.
- `docs/trust_contract.md` — schema reference, every field explained with rationale.
- `docs/statistics.md` — the papers, the calibration tests, why we made these choices, what the failure modes are. Citeable.
- `docs/adapters.md` — how to write an adapter; the four protocols; sample skeletons.
- `docs/cli.md` — command reference.
- `docs/recipes/` — at least three end-to-end how-tos: (1) testing a landing-page change with PostHog, (2) testing a feature rollout with the Postgres flag adapter, (3) replaying historical data with the simulator.

The "why we did the stats this way" doc is disproportionately important for adoption among the data-science buyer. Be explicit, citeable, and a little proud of it.

## 20. Anti-scope (do NOT build in MVP)

These are not coming:

- **Variant generation as a core capability.** Generation lives in the `variant/llm.py` adapter — the controller itself does not generate variants, propose what to test, or know how variants came to exist. Keep this separation clean. The controller's job is to safely run experiments; generation is plugged in.
- **Multi-armed bandits.** Different stats discipline. Our system does progressive delivery, not adaptive allocation. State this explicitly in the README.
- **More than two variants per experiment.** Two arms (control + treatment) only in MVP. Multi-variant stats add complexity without adoption value at this stage.
- **Heterogeneous treatment effects / cohort splits.** Future work.
- **Recommendation engine for what to test next.** Optimizely Opal's lane. Stay complementary.
- **Visual editor / no-code authoring.** Wrong audience.
- **A second persistence backend.** Postgres only. Don't abstract storage.
- **Kubernetes operators, helm charts, multi-region.** Compose file only.
- **OAuth / SSO.** API key only for MVP.

## 21. MVP acceptance criteria

The MVP is "done" when:

1. `uv run datatool init && uv run datatool daemon` works against a fresh Postgres with zero manual SQL.
2. The included `examples/simulation_demo.yaml` can be registered, started, and driven through a full lifecycle by feeding it the included `examples/synthetic_events.csv` via `datatool simulate` (the canonical `examples/pricing_page.yaml` reads PostHog event names, so it is registered and inspected but not replayed from the CSV). Output shows the controller ramping, holding at ceiling, and either promoting (with human approval simulated) or reverting based on synthetic guardrail breaches.
3. `tests/stats/test_cs_calibration.py` passes with type-I error ≤ α + 0.005 at α=0.05, n=10,000 runs.
4. `tests/stats/test_fdr_calibration.py` passes with realized FDR ≤ target on a synthetic stream of 200 experiments, 80% null.
5. `tests/stats/test_srm.py` correctly detects 60/40 imbalance on configured 50/50 allocation at p < 0.001.
6. State-machine property tests (hypothesis) generate 1000+ random transition sequences with no illegal transitions or unreachable states.
7. All five reference adapters (Postgres flag, PostHog metrics, static variant, LLM variant, Slack notify + webhook notify) have integration tests that pass against real or recorded backends. The LLM adapter has both fake-client unit tests (CI-gated) and an optional live test (`DATATOOL_RUN_LIVE_LLM_TESTS=1`).
8. The LLM variant adapter: given a surface description + `forbidden_components`, produces a valid materialized variant; the same inputs (with a fixed seed) produce a cache hit on the second call; a generation that references a forbidden component is rejected with a structured error.
9. The terminal console renders an experiment list and a live audit feed; the conversational assistant answers `status`/`why` and carries out `pause`/`resume`/`promote`/`revert` behind an explicit in-TUI confirmation, degrading gracefully with no LLM key. `plotext` charts the goal metric in-terminal.
10. `datatool why <experiment>` produces readable English that explains every decision and action, citing CS bounds, FDR alpha used, guardrail values, and clamping events.
11. `docker-compose up` brings up the stack (Postgres + daemon) with one command and a sane default config; `datatool` opens the terminal console against it.
12. README walks a new user from zero to a running experiment in under five minutes.
13. License is Apache-2.0, contributing guide is present, code of conduct is present.

## 22. Extensibility & integration invariants

The properties below are *not* extra polish — they are what makes the codebase legible and reusable to anyone integrating it into an existing experimentation stack:

- **The trust contract as a typed, versioned, inheritable schema** with explicit clamping semantics. This is the core asset. Make the contract module pristine — exhaustive Pydantic validation, every field documented in code, dedicated tests for resolution and clamping. Someone reading `core/contract.py` should immediately see the clamping layer that raw flag/metrics tooling doesn't provide.
- **Statistics module with citations.** Every algorithm names its paper in the docstring. Calibration tests are CI gates with explicit error budgets. The `docs/statistics.md` document is written as if it were going to be cited in someone else's design doc. This is what credentials the project among data scientists and gives integrators confidence in the inference.
- **Adapter protocols, not adapter inheritance.** PEP 544 protocols mean anyone can drop in their own implementations without rewriting our code. This is also how community contributions stay healthy.
- **Audit log is structured, queryable, and complete.** Compliance/SRE reviewers always check this. Every state transition, every clamp, every adapter call. JSONB payloads with stable schemas.
- **The simulator.** It makes the system's behavior legible without running it in prod — for evaluators and contributors alike. Make sure it's prominently documented and demoed.
- **Tests that prove correctness, not just exercise code.** A high `pytest --cov` number is table stakes; what differentiates is the stats calibration suite where the test description reads like a theorem statement.
- **Clean separation between control plane and execution plane.** Anyone should be able to delete every file in `adapters/*/` except `base.py`, drop in their own implementations, and have a working product. Keep that property invariant.
- **Apache-2.0 license, CLA-free for now.** Keeps the barrier to adoption and contribution low. If the project takes off, a CLA is a later conversation, not a Day-1 one.
- **Don't accept feature contributions that violate the spine.** Variants, bandits, generation tools, and "smart suggestions" will all get proposed. Politely route them to be plugins/adapters or downstream projects. The thing being built is the controller, not a kitchen sink.

## 23. Roadmap signals (post-MVP, in priority order)

For the README's "what's next" section, signal the trajectory without committing to dates:

1. **More flag/metrics adapters**: LaunchDarkly, Statsig, PostHog flags, GrowthBook, Snowflake, BigQuery. Mostly community-contributable.
2. **Multi-variant experiments** (>2 arms) with appropriate multiple-comparisons handling. Pairs naturally with a `variant/llm_multi.py` that produces N candidates.
3. **Cohort/heterogeneous treatment effects.** Subgroup detection with multiple-testing correction.
4. **Variant generation adapters for other systems**: `variant/optimizely.py` (Opal), `variant/figma.py`, etc.
5. **Web UI for contract authoring** (still YAML-backed; just nicer to edit).
6. **OAuth/SSO.**
7. **CUPED++ / variance reduction methods** beyond basic CUPED.
8. **Replay against live tap.** Not just CSV — tap a live event stream in shadow mode.

---

## Final note to the implementer

The most common failure mode when building this is to get distracted by adapter breadth or UI polish and ship a weak statistics module. **Do not.** The order of priority for the MVP build is:

1. Domain model (`core/models.py`, `core/contract.py`) — the public API.
2. Statistics engine (`stats/`) with passing calibration tests.
3. Control loop (`control/`) with the state machine.
4. Postgres flag adapter + CSV metrics adapter — enough to demo end-to-end.
5. CLI.
6. Simulator.
7. PostHog metrics adapter + Slack/webhook notify.
8. Static variant adapter.
9. LLM variant adapter (with fake-client tests; live tests optional).
10. Terminal console + conversational assistant (`console/`); read-only HTTP API + metrics (`api/`).
11. The remaining adapters as community contributions.

If you find yourself running out of time, ship with fewer adapters and a beautiful statistics module + simulator. The first impression that matters is "the stats are right and the simulator proves it." Everything else can grow.
