# Recipe: drive a feature rollout with the Postgres flag adapter

This recipe focuses on the **execution plane** — the flag adapter that actually moves traffic. DataTool ships a Postgres-backed `FlagProvider` (`flag.postgres`) that assigns units to variants, sets allocation percentages, kills an experiment back to control, and reports assignment counts for the SRM check. It is the flag plane the live daemon uses, so a feature rollout driven by the daemon is driven through this adapter.

If you don't have a real feature-flag vendor wired up, this is the adapter to start with: it needs nothing but the Postgres you already run for DataTool.

## What the flag adapter does

The Postgres flag provider implements the [`FlagProvider`](../adapters.md#flagprovider--the-flag-plane-architecturemd-111) protocol:

- `assign(experiment_id, unit_id)` — deterministically maps a unit to a variant. The reference adapter hashes the unit id with SHA-256, so a unit's assignment is stable across daemon restarts (Python's built-in `hash` is salted per process and would not be).
- `set_allocation(experiment_id, allocations)` — the controller calls this on every ramp; the values sum to 100.
- `kill(experiment_id)` — routes 100% to control; used on revert.
- `get_assignment_counts(experiment_id, since)` — feeds the SRM check that runs before any inference.

You rarely call these yourself — the orchestrator does, clamped to the trust contract. Your job is to register the experiment and watch the rollout.

## Prerequisites

- A running Postgres and the schema applied:

  ```bash
  export DATATOOL_DATABASE_URL=postgresql+psycopg://datatool:datatool@localhost:5432/datatool
  uv run datatool init
  uv run datatool doctor
  ```

- The live daemon evaluates goal metrics through the PostHog metrics adapter, so set the `POSTHOG_*` variables as in the [PostHog recipe](landing-page-with-posthog.md). (If you only want to rehearse the flag mechanics against history, drive the experiment through the [simulator](replaying-historical-data.md) instead, which uses the CSV metrics source and needs no live warehouse.)

## 1. Define the rollout

A feature rollout is just an experiment with a control arm (feature off) and a treatment arm (feature on). The `allocation` block is where you set how far the controller may roll out on its own:

```yaml
experiment: new-search-ranking
surface: search
owner: search-team

variants:
  - name: control
    is_control: true
    source: existing
    payload: {}
  - name: treatment
    source: static
    payload: { type: feature_flag, ref: search.new_ranking }

contract:
  version: "1.0"
  scope:
    assignment_unit: user
  allocation:
    initial_canary_pct: 1.0
    ramp_schedule: [1, 5, 10, 25, 50]
    max_autonomous_pct: 25.0          # the controller may roll out to 25% on its own
    full_rollout_requires: human_approval
    min_step_dwell: PT4H
  guardrails:
    - name: error_rate
      source: metrics.posthog
      metric: $pageview_error_rate
      threshold: { type: relative_increase, value: 0.10 }
      window: PT10M
      severity: critical
      consecutive_breaches_to_trip: 2
  goal:
    source: metrics.posthog
    metric: search_click_through_rate
    direction: increase
    minimum_detectable_effect: 0.02
  statistics:
    alpha: 0.05
  reversion:
    strategy: instant
    cooldown: PT24H
  graduation: {}
  authorization:
    emergency_halt: ["any"]
```

The `assignment_unit: user` line tells the flag adapter to key assignment on the user — the same user always lands in the same arm.

## 2. Register and start the daemon

```bash
uv run datatool register new-search-ranking.yaml
# registered new-search-ranking (state: proposed)

uv run datatool daemon
```

The daemon constructs the Postgres flag provider once and hands it to the control loop. On the first tick the experiment moves to `canary` at 1%, and the flag adapter starts assigning that 1% of users to the treatment.

## 3. Watch the rollout climb

```bash
uv run datatool show new-search-ranking
```

`show` prints the current treatment allocation alongside the resolved contract, so you can see the rollout walking up `1 → 5 → 10 → 25`, dwelling at least `min_step_dwell` at each step. Each ramp is a `set_allocation` call on the flag adapter, clamped to `max_autonomous_pct` (25%) and logged:

```bash
uv run datatool why new-search-ranking
#   ... ramp   canary → ramping    cs lower bound 0.014 > 0; ramped to 5%
#   ... ramp   ramping → ramping    ramped to 25%; clamped to autonomous ceiling 25%
#   ... hold   ramping → holding    at ceiling 25%; awaiting approval
```

The clamp line is the trust contract doing its job — the ramp schedule's next step would have been 50%, but the contract's ceiling is 25%, so the action was clamped and the clamp recorded.

## 4. SRM protects the rollout

On every cycle the controller compares the flag adapter's `get_assignment_counts` against the configured allocation with a chi-squared test. If a caching bug or bot traffic skews assignment away from the configured split, the SRM check trips at `p < 0.001` and the experiment reverts with `srm_failed` — before any goal inference runs. See [`statistics.md`](../statistics.md#sample-ratio-mismatch-srm).

## 5. Complete or kill the rollout

- **Ship it:** once it's holding at the ceiling with a clean confidence sequence, approve full rollout:

  ```bash
  uv run datatool promote new-search-ranking
  ```

  The flag adapter sets treatment to 100%.

- **Pull it back:** at any time,

  ```bash
  uv run datatool revert new-search-ranking --reason "regression in tail latency"
  ```

  calls `kill` on the flag adapter, routing everyone back to control, and locks the surface for the cooldown.

## See also

- [`adapters.md`](../adapters.md) — the `FlagProvider` protocol and how to back it with a different vendor.
- [`trust_contract.md`](../trust_contract.md) — the `allocation` block and the autonomy ceiling.
- [`cli.md`](../cli.md) — `show`, `why`, `promote`, `revert`.
