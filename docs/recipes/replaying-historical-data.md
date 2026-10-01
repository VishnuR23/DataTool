# Recipe: replay historical data through the simulator

Before you point the controller at live traffic, you can replay a slice of historical events through it and watch exactly what it would have done — cycle by cycle, decision by decision — without touching a single flag. This is the fastest way to build confidence in a contract, tune a guardrail, or demo the controller end to end.

The worked example is [`examples/simulation_demo.yaml`](../../examples/simulation_demo.yaml): a checkout-button-color test wired to the CSV metrics source, with short runtimes so a replay completes in one shot.

## How replay differs from live

The simulator drives the *same* control loop the daemon does (`control/loop.run_cycle`), with two deliberate differences:

- **The metrics source is CSV, not a live warehouse.** `metrics.csv` reads a flat event file and aggregates it into the same `Sample` statistics the live adapters produce.
- **SRM is off.** In replay the historical assignment split is fixed, so comparing it against a ramping allocation would flag a spurious mismatch every cycle. The live daemon runs SRM; replay does not. (See [`statistics.md`](../statistics.md#sample-ratio-mismatch-srm) for what SRM does when it *is* on.)

Replay is deterministic and runs instantly, so `--speed` is advisory only.

## 1. Prepare the event CSV

The CSV metrics source expects exactly these columns:

```
unit_id,variant,metric,value,timestamp
u1,treatment,conversion,1,2026-06-01T00:01:00
u2,control,conversion,0,2026-06-01T00:01:30
u3,treatment,error_rate,0,2026-06-01T00:02:00
u4,control,conversion,1,2026-06-01T00:02:30
```

- `variant` must match the variant names in your experiment (`control`, `treatment`). Rows naming a variant the experiment doesn't have are ignored.
- `metric` must match the `goal.metric` and `guardrail.metric` names in the contract — here `conversion` (the goal) and `error_rate` (the guardrail).
- `value` is the per-event metric value (for a rate metric, `1`/`0`). `timestamp` is ISO-8601; naive timestamps are assumed UTC.
- Malformed rows raise rather than being silently dropped — a parse error in the data is a loud failure.
- For CUPED (`enable_cuped: true`), add pre-experiment rows with an **empty** `variant` (units are not assigned yet), covering at least `2 × cuped_pre_period` before the first assigned event. They never count toward an arm or move the replay window; they become each unit's covariate.

The repo ships `examples/synthetic_events.csv` for the demo experiment; for your own surface, bring your own export (a quarter of historical events for the surface works well). Keep the time span at least as long as the contract's `max_runtime` so the experiment can reach a terminal decision within the data rather than running out of events first — `simulation_demo.yaml` sets `max_runtime: P2D` for exactly this reason.

## 2. Register the experiment

The experiment must be freshly registered (`proposed`) — the simulator replays from the start of the lifecycle.

```bash
uv run datatool init        # if you haven't already
uv run datatool register examples/simulation_demo.yaml
# registered checkout-button-color (state: proposed)
```

Note the contract uses an **absolute** guardrail:

```yaml
guardrails:
  - name: error_rate
    source: metrics.csv
    metric: error_rate
    threshold: { type: absolute, value: 0.20 }
    window: PT1H
    severity: critical
```

Absolute is the right choice here: a relative guardrail on a rare event would read a single blip off a tiny baseline as a huge swing — pure noise. See the guardrail notes in [`statistics.md`](../statistics.md#guardrails).

## 3. Replay

```bash
uv run datatool simulate checkout-button-color --data events.csv
```

The simulator steps its clock across the data, calling the control loop on each tick, and prints the trajectory: the cycle count, the final state and allocation, and the per-cycle decisions with their reasons. A winning experiment looks like:

```
simulate checkout-button-color  (18 cycles)

  2026-06-01 01:00  ramp      canary    1.0%   started at 1% (canary)
  2026-06-01 02:00  ramp      ramping   5.0%   cs lower bound 0.021 > 0; ramped to 5%
  2026-06-01 06:00  ramp      ramping  25.0%   ramped to 25%
  2026-06-01 12:00  promote   promoted 100.0%  autonomous full rollout; cs excludes 0
  final: promoted @ 100.0%
```

Because this contract sets `full_rollout_requires: autonomous` with a 50% ceiling, the controller can ship on its own once the confidence sequence is conclusive. A null or losing variant instead walks to `max_runtime` and **concludes** without shipping; a variant that pushes `error_rate` over `0.20` for two consecutive windows **reverts** with `guardrail.error_rate.tripped`.

## 4. Inspect the recorded run

The replay writes the same decisions and actions to the audit log that a live run would, so you can interrogate it afterward:

```bash
uv run datatool why checkout-button-color
```

This is the point of the rehearsal: read the reasoning, confirm the controller ramps, holds, promotes, or reverts where you expect, and adjust the contract before going live.

## See also

- [`cli.md`](../cli.md#simulate-name_or_id---data-csv) — the `simulate` command.
- [`statistics.md`](../statistics.md) — confidence sequences, SRM, and guardrails.
- [`adapters.md`](../adapters.md#metricssource--the-data-warehouse-architecturemd-112) — the CSV metrics source and the `Sample` shape it returns.
- [landing-page with PostHog](landing-page-with-posthog.md) — taking the same experiment live.
