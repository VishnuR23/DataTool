# The trust contract

The trust contract is the spine of DataTool and its single most important asset. It is a typed, versioned, inheritable description of how much autonomy the controller has over a surface and the safety bounds it must clamp every action to. Every action the orchestrator takes is clamped to a resolved contract, and every clamp is written to the append-only audit log. No code path mutates experiment state without going through `core/contract.py`.

This document is the field-by-field reference. The canonical models live in [`datatool/core/models.py`](../datatool/core/models.py) (Pydantic v2) and the resolution logic in [`datatool/core/contract.py`](../datatool/core/contract.py); `ARCHITECTURE.md §6` and `§7` are the spec. For the statistics fields, read [`statistics.md`](statistics.md) alongside this.

## Why it is shaped this way

Three design rules run through every model (`ARCHITECTURE.md §22`):

- **Strict by default.** Every model forbids unknown fields (`extra="forbid"`). A misspelled YAML key — `max_autonomus_pct` — is a loud parse error, not a silently ignored field that defaults to something dangerous. This is deliberate: the contract is a safety artifact, and quiet failure is exactly what the project exists to prevent.
- **Exhaustive validation at parse time.** A probability outside `(0, 1)`, a non-monotonic ramp, a duplicate guardrail name — all fail when the contract is loaded, not as controller misbehaviour in production.
- **Validated on assignment.** Models use `validate_assignment=True`, so mutating a field after construction re-runs validation.

## Resolution: org → surface → experiment

A contract is assembled from up to three layers, most specific winning (`ARCHITECTURE.md §15`, `core/contract.py`):

1. **Org defaults** — [`config/org_defaults.yaml`](../config/org_defaults.yaml). Conservative settings applied to every experiment. This is a *partial* contract: goal and guardrails are intentionally absent (they are experiment-specific), so it only validates once merged.
2. **Surface overrides** — e.g. [`config/surfaces/pricing-page.yaml`](../config/surfaces/pricing-page.yaml). Sits between org and experiment; tightens scope, declares forbidden components, carves out a holdout.
3. **Experiment contract** — the `contract:` block in the experiment YAML. The most specific layer.

The layers are deep-merged. **Lists are replaced, not concatenated** — an experiment that specifies `guardrails` replaces the inherited list rather than appending to it, so you always see the full set that applies. A merge that cannot produce a valid `TrustContract` raises `ContractResolutionError` rather than resolving to something half-specified. A fully self-contained experiment file (like [`examples/pricing_page.yaml`](../examples/pricing_page.yaml)) lists every section and validates on its own without needing the other layers.

Once resolved, the orchestrator clamps each proposed action to the contract — most importantly to the autonomy ceiling — and records the clamp (the original value, the clamped value, the reason) in the `actions` table (`ARCHITECTURE.md §9.3`).

## `TrustContract` — the top level

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `version` | `str` | `"1.0"` | Schema version of this contract. The versioning is part of the asset — the schema is meant to evolve compatibly. |
| `scope` | `Scope` | required | Where the experiment may act and how units are assigned. |
| `allocation` | `Allocation` | required | The ramp schedule and the autonomy ceiling. |
| `guardrails` | `list[Guardrail]` | `[]` | Safety constraints evaluated independently of the goal. |
| `goal` | `Goal` | required | The primary metric the experiment is trying to move. |
| `secondary_metrics` | `list[Goal]` | `[]` | Additional metrics tracked but not gating promotion. |
| `statistics` | `Statistics` | required | Sequential-inference configuration. |
| `reversion` | `ReversionPolicy` | required | What happens on a revert. |
| `graduation` | `Graduation` | required | How autonomy grows or shrinks over time. |
| `authorization` | `Authorization` | required | Who may modify the contract, grant autonomy, or halt. |

**Cross-field validation:** guardrail names must be unique within a contract. Names key the consecutive-breach counters and the decision reasons of the form `guardrail.<name>.tripped`, so duplicates would silently collide those counters.

## `scope` — where the experiment may act

| Field | Type | Default | Constraints / rationale |
|-------|------|---------|--------------------------|
| `allowed_routes` | `list[str]` | `[]` | Routes the experiment is permitted to touch. |
| `forbidden_components` | `list[str]` | `[]` | Components the variant must never modify (e.g. `PaymentForm`). Enforced when a variant is materialized — an LLM-generated variant that references one is rejected with a structured error. |
| `assignment_unit` | `"user" \| "session" \| "account"` | `"user"` | The unit randomization keys on. |
| `exclusivity_group` | `str \| None` | `None` | Experiments sharing a group must not run on the same units simultaneously. |
| `holdout_pct` | `float` | `0.0` | `0 ≤ x < 100`. A global holdout carved out of the surface and never exposed to the test. |

## `allocation` — the ramp and the autonomy ceiling

This is where the contract grants — and bounds — autonomy.

| Field | Type | Default | Constraints / rationale |
|-------|------|---------|--------------------------|
| `initial_canary_pct` | `float` | `1.0` | `0 < x ≤ 100`. Allocation the experiment starts at. |
| `ramp_schedule` | `list[float]` | `[1, 2.5, 5, 10, 25]` | Each step in `(0, 100]`, **strictly increasing**, at least one step. The allocation walks up these steps. |
| `max_autonomous_pct` | `float` | `5.0` | `0 < x ≤ 100`. **The clamp target.** The ceiling the controller may ramp to without human approval. Every ramp action is clamped to this. |
| `full_rollout_requires` | `"autonomous" \| "human_approval"` | `"human_approval"` | Whether 100% rollout needs a human, or the controller may ship on its own when the evidence and trust justify it. |
| `min_step_dwell` | `timedelta` | `4h` | Minimum time to hold at a step before ramping again. |

**Cross-field validation:** `initial_canary_pct` must not exceed `max_autonomous_pct` — the controller cannot autonomously start an experiment above its own ceiling, since that would be clamped to nothing sensible on the first tick.

> Durations throughout the contract use ISO-8601 in YAML: `PT4H` (4 hours), `PT30M` (30 minutes), `P14D` (14 days), `P30D` (30 days).

## `guardrails` — the fast safety loop

Each entry is a `Guardrail`. Guardrails are evaluated independently of the goal metric and trip only after consecutive breaches (anti-flapping). See [`statistics.md`](statistics.md#guardrails) for the evaluation semantics.

| Field | Type | Default | Constraints / rationale |
|-------|------|---------|--------------------------|
| `name` | `str` | required | Non-empty, unique within the contract. Appears in the trip reason `guardrail.<name>.tripped`. |
| `source` | `str` | required | Adapter id, e.g. `metrics.posthog`. |
| `metric` | `str` | required | The metric the source serves. |
| `threshold` | `Threshold` | required | The value plus how to interpret it (below). |
| `window` | `timedelta` | required | Trailing window the metric is evaluated over. Must be positive. |
| `min_samples_per_arm` | `int` | `100` | `≥ 1`. Below this per-arm count the guardrail returns `insufficient_data` rather than evaluating. |
| `severity` | `Severity` | `high` | `critical`/`high` → `REVERT` on trip; `medium` → `HOLD` + notify. |
| `consecutive_breaches_to_trip` | `int` | `2` | `≥ 1`. Anti-flapping: require this many consecutive breaches before tripping. |

### `Threshold`

| Field | Type | Constraints / rationale |
|-------|------|--------------------------|
| `type` | `ThresholdType` | `absolute`, `relative_increase`, or `relative_decrease`. |
| `value` | `float` | For `absolute`, the raw value compared against. For relative types, the fractional change vs. control (`0.20` == 20%). |

**Validation:** for the two relative types, `value` must be `> 0` — a relative threshold is a magnitude of change, so zero or negative has no interpretation. A breach is always a *strict* exceedance. See the [statistics doc](statistics.md#guardrails) for the exact comparison per type, including the rare-event caveat (prefer `absolute` for rare events).

## `goal` and `secondary_metrics`

Both are `Goal`. The `goal` gates promotion; `secondary_metrics` are tracked but not gating.

| Field | Type | Default | Constraints / rationale |
|-------|------|---------|--------------------------|
| `source` | `str` | required | Adapter id. |
| `metric` | `str` | required | The metric name. |
| `direction` | `"increase" \| "decrease"` | required | Which way is "better". |
| `minimum_detectable_effect` | `float` | `0.01` | `> 0`. The smallest effect worth detecting; powers the statistics engine (informs power, not the validity guarantee). |

## `statistics` — sequential inference

Configures the confidence sequence and the experiment's share of the org-level FDR budget. The methods and their guarantees are documented in full in [`statistics.md`](statistics.md).

| Field | Type | Default | Constraints / rationale |
|-------|------|---------|--------------------------|
| `method` | `"confidence_sequence" \| "msprt"` | `confidence_sequence` | The sequential test. Confidence sequences are the implemented primary method. |
| `alpha` | `float` | `0.05` | `0 < α < 1`. Per-experiment type-I error target. |
| `fdr_budget_share` | `float` | `0.10` | `0 < x ≤ 1`. Share of the org-level online-FDR (LORD) budget this experiment may consume. |
| `min_runtime` | `timedelta` | `24h` | Do not ship before this much wall-clock elapses. Must be positive. |
| `max_runtime` | `timedelta` | `14d` | Conclude without shipping after this. Must be positive. |
| `novelty_buffer` | `timedelta` | `6h` | Initial window during which decisions are suppressed (novelty effects). Must be non-negative. |
| `enable_cuped` | `bool` | `False` | CUPED variance reduction. Deferred — see [statistics](statistics.md#cuped-deferred). |
| `cuped_pre_period` | `timedelta \| None` | `None` | Pre-experiment window CUPED subtracts. |

**Cross-field validation (`_coherent`):** `max_runtime` must exceed `min_runtime`; `novelty_buffer` must be shorter than `max_runtime`; and if `enable_cuped` is true, `cuped_pre_period` must be set and positive — enabling CUPED without a pre-period is a configuration error, not a silent no-op.

## `reversion` — what a revert does

| Field | Type | Default | Constraints / rationale |
|-------|------|---------|--------------------------|
| `strategy` | `"instant" \| "gradual_5min" \| "gradual_1h"` | `instant` | How quickly traffic returns to control. |
| `cooldown` | `timedelta` | `24h` | How long the surface is locked after a revert. Must be non-negative. |
| `notify` | `list[str]` | `[]` | Channels to notify, e.g. `slack:#growth-experiments`, `webhook:https://…`. |
| `open_postmortem` | `str \| None` | `None` | Where to open a postmortem, e.g. `github:org/repo`. |
| `halt_related` | `bool` | `True` | Whether to halt experiments in the same exclusivity group on revert. |

## `graduation` — autonomy that earns itself

Autonomy is not static: a surface that keeps shipping clean wins earns a higher ceiling; one that ships a false positive loses ground. See `ARCHITECTURE.md §9.4` and the [trust ledger](cli.md#ledger-surface).

`Graduation`:

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `rules` | `list[GraduationRule]` | `[]` | Condition→action rules evaluated by the trust ledger. |
| `review_cadence` | `timedelta` | `7d` | Human review cadence. Must be positive. |

`GraduationRule` carries a small DSL as two non-empty dicts:

| Field | Type | Purpose |
|-------|------|---------|
| `when` | `dict` | The condition, e.g. `{clean_promotions_on_surface: ">=3", false_positive_ships: 0}`. |
| `action` | `dict` | The effect, e.g. `{field: max_autonomous_pct, op: increase, by: 5, up_to: 25}`. |

`when` and `action` are intentionally loose dicts: their structure is validated where it is interpreted (`control/ledger.py`), not in the schema, so the contract does not have to enumerate every operator the ledger supports. Both must be non-empty.

**When rules run.** After every promote or revert — from the daemon or a manual command — the controller evaluates the contract's rules against the surface's trust history. A rule fires when all of its `when` conditions hold and it is not cooling down. A fired rule writes a `graduate` or `demote` trust event with the signed delta, plus a `trust.graduated` / `trust.demoted` audit row. The written contract is never rewritten: the effective contract adds the surface's accumulated deltas at resolution time, clamped by `up_to` / `down_to`, to 0–100, and never below the initial canary. `datatool ledger SURFACE` shows the history.

## `authorization` — who may do what

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `modify_contract` | `list[str]` | `["admin"]` | Roles allowed to change the contract. |
| `grant_autonomy_increase` | `list[str]` | `["admin"]` | Roles allowed to raise the autonomy ceiling. |
| `emergency_halt` | `list[str]` | `["any"]` | Roles allowed to halt — `any` so anyone can pull the cord in an emergency. |

## Related shapes (carry a contract, but are not part of it)

These models surround the contract in the domain layer:

- **`ExperimentSpec`** — a full experiment definition: `name`, `surface`, `owner`, optional `description`, `variants`, and the `contract`. Enforces at the model boundary that there are **exactly two variants, exactly one of them control** (`ARCHITECTURE.md §20` lists more-than-two-variants as anti-scope for the MVP; this is the single place to relax when multi-variant work lands).
- **`VariantSpec`** — one arm: `name`, `is_control`, `source` (`existing`/`static`/`llm`/`external`), and an adapter-specific `payload`. See [`adapters.md`](adapters.md) for how a `source` maps to a variant adapter.
- **`Sample`** — an aggregated per-arm metric snapshot (`n`, `sum`, `sum_sq` over a window) — the input the statistics engine consumes. Metrics adapters return these.
- **`Decision`** — the decision engine's output for one cycle: `kind`, a human-readable `reason`, and a machine-readable `structured_reason` (e.g. `{cs_lower: 0.021, fdr_budget_consumed: 0.04}`). This is what `datatool why` reads back.

## The enums

- **`State`** — `proposed`, `canary`, `ramping`, `holding`, `promoting`, `promoted`, `reverted`, `concluded` (`ARCHITECTURE.md §10`, the state machine).
- **`DecisionKind`** — `continue`, `ramp`, `hold`, `promote`, `revert`.
- **`Severity`** — `critical`, `high`, `medium`.
- **`ThresholdType`** — `absolute`, `relative_increase`, `relative_decrease`.

## A complete example

[`examples/pricing_page.yaml`](../examples/pricing_page.yaml) is a fully-specified contract that validates on its own — read it next to this reference. [`examples/simulation_demo.yaml`](../examples/simulation_demo.yaml) is a smaller contract tuned for the simulator (autonomous full rollout, an absolute guardrail, short runtimes). For how the layered config composes, compare [`config/org_defaults.yaml`](../config/org_defaults.yaml) and [`config/surfaces/pricing-page.yaml`](../config/surfaces/pricing-page.yaml).
