# Recipe: test a landing-page change with PostHog

This walks through running a real landing-page experiment end to end, with PostHog as the metrics source. The controller ramps the change up to its autonomous ceiling, watches the goal metric with a confidence sequence and the guardrails on a fast loop, and either holds for your approval or reverts on a breach — all without you babysitting it.

The worked example is [`examples/pricing_page.yaml`](../../examples/pricing_page.yaml): a clearer value-prop headline on the pricing page, with PostHog serving the goal (`signup_completion_rate`) and three guardrails (error rate, p95 latency, checkout completion).

## Prerequisites

- A running Postgres (see the [README](../../README.md) or `docker-compose up`).
- A PostHog project, with these environment variables exported (the live daemon reads them when it builds the metrics adapter):

  ```bash
  export POSTHOG_HOST=https://us.posthog.com
  export POSTHOG_PROJECT_ID=12345
  export POSTHOG_API_KEY=phx_...
  export DATATOOL_DATABASE_URL=postgresql+psycopg://datatool:datatool@localhost:5432/datatool
  ```

## 1. Initialize and check

```bash
uv run datatool init
uv run datatool doctor
```

`init` creates the schema; `doctor` confirms settings, database connectivity, the config directory, and the bundled adapters all check out before you go further.

## 2. Write or adapt the experiment

The contract in `examples/pricing_page.yaml` is fully specified — read it next to [`trust_contract.md`](../trust_contract.md). The parts that matter for this recipe:

```yaml
goal:
  source: metrics.posthog
  metric: signup_completion_rate
  direction: increase
  minimum_detectable_effect: 0.02

guardrails:
  - name: error_rate
    source: metrics.posthog
    metric: $pageview_error_rate
    threshold: { type: relative_increase, value: 0.20 }
    window: PT10M
    severity: critical
    consecutive_breaches_to_trip: 2
  # ... latency_p95 (absolute), checkout_completion (relative_decrease)

allocation:
  initial_canary_pct: 1.0
  ramp_schedule: [1, 2.5, 5, 10, 25]
  max_autonomous_pct: 5.0
  full_rollout_requires: human_approval
```

The metric names must be metrics your PostHog project actually serves — the metrics adapter's `supports_metric` is what the controller relies on. The autonomy ceiling here is 5%: the controller will ramp on its own up to 5% of traffic, then hold and wait for a human to promote.

## 3. Register it

```bash
uv run datatool register examples/pricing_page.yaml
# registered pricing-headline-clarity (state: proposed)
```

Registration resolves the layered contract, materializes the variants, and stores the experiment in `proposed`. Nothing is live yet.

## 4. Start the control plane

```bash
uv run datatool daemon
# datatool daemon started: control loop (tick 60s) + read-only HTTP API + metrics on http://127.0.0.1:8080.
```

The daemon drives every non-terminal experiment on each tick. For this experiment it will, in order each cycle: run the SRM check (assignments vs. configured allocation — a mismatch reverts with `srm_failed`), evaluate the three guardrails on their windows, then evaluate the goal with a confidence sequence. The read-only JSON API and `/metrics` are on `http://localhost:8080`; run `datatool` in your project for the live terminal console.

## 5. Watch it work

In another shell:

```bash
uv run datatool status pricing-headline-clarity
# pricing-headline-clarity  ramping @ 2.5%  (pricing-page)

uv run datatool why pricing-headline-clarity
#   ... ramp   proposed → canary   started at 1% (canary)
#   ... ramp   canary → ramping    cs lower bound 0.018 > 0; ramped to 2.5%
#   ... hold   ramping → holding   reached autonomous ceiling 5%; awaiting approval
```

`datatool why` is the one to lean on — it shows the confidence-sequence bound the controller acted on at each step, read from the append-only audit log. See [`statistics.md`](../statistics.md) for what those bounds mean.

## 6. The two outcomes

- **The headline wins.** The confidence sequence excludes zero in the goal's direction, the guardrails stay clean, and the experiment ramps to the 5% ceiling and **holds** (because `full_rollout_requires: human_approval`). Approve the full rollout yourself:

  ```bash
  uv run datatool promote pricing-headline-clarity
  # promoted pricing-headline-clarity to full rollout
  ```

- **A guardrail trips.** If, say, `error_rate` rises more than 20% over control for two consecutive 10-minute windows, the critical guardrail trips and the controller reverts to control immediately, with the reason recorded:

  ```bash
  uv run datatool why pricing-headline-clarity
  #   ... revert  ramping → reverted  guardrail.error_rate.tripped
  ```

  A revert locks the surface for the contract's `cooldown` and notifies the configured channels.

## Optional: CUPED from PostHog history

Set `statistics.enable_cuped: true` and `cuped_pre_period` (e.g. `P14D`) to cut the goal metric's variance with each user's own pre-experiment behaviour. DataTool reads it straight from PostHog: for every in-experiment event it looks up the same person's (`person_id`) mean of that event's `value` over the pre-period, and estimates θ from the two pre-period windows before that — so keep at least `2 × cuped_pre_period` of history for the event, with the same `value` property. It needs `assignment_unit: user`; with other units, decisions record why CUPED was not applied. `datatool why` shows the frozen `cuped_theta` on every decision. CUPED pays off most for low-variance metrics with strong pre-period correlation — see [statistics](../statistics.md#cuped--variance-reduction-from-pre-period-data).

## Tip: rehearse on history first

Before running live, you can replay historical events through this experiment with the simulator to see how the controller would behave — see [replaying historical data](replaying-historical-data.md).

## See also

- [`trust_contract.md`](../trust_contract.md) — every field in the contract above.
- [`adapters.md`](../adapters.md) — the PostHog metrics adapter and how to write your own.
- [`cli.md`](../cli.md) — the commands used here.
