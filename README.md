# DataTool

[![CI](https://github.com/VishnuR23/DataTool/actions/workflows/ci.yml/badge.svg)](https://github.com/VishnuR23/DataTool/actions/workflows/ci.yml)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

An open-source autonomous experimentation controller.

DataTool sits on top of whatever feature-flagging, metrics, and variant-generation
tools a team already uses, and drives experiments through their full lifecycle —
ramp, evaluate, promote, or revert — without a human in the loop, while honoring a
declarative **trust contract** that bounds and audits its authority.

In one sentence: a daemon that takes "here's a variant, here are the guardrails,
here's the trust budget" and handles ramp, evaluate, promote, or revert — with
statistically valid sequential inference and a provable safety contract.

> **Status:** early development. [`ARCHITECTURE.md`](ARCHITECTURE.md) is the full
> build brief and the source of truth for the project.

## Why it's different

- **It actually decides.** Most experimentation tools show you a dashboard and wait
  for you to act. DataTool acts — ramping, holding, promoting, or reverting on its
  own — and writes down why.
- **It can't peek itself into false positives.** Because it monitors continuously,
  it uses [confidence sequences](docs/statistics.md), which stay valid no matter how
  often you look. The calibration tests that prove this gate every build.
- **Its authority is a typed contract.** Every action is clamped to a versioned
  [trust contract](docs/trust_contract.md), and every clamp is logged to an
  append-only audit trail. Autonomy is something a surface *earns* and can lose.

## What it is not

- Not a feature-flag system (it uses yours).
- Not a metrics warehouse (it queries yours).
- Not a variant generator (it accepts variants from humans, LLMs, or external tools).
- Not a multi-armed bandit — DataTool does progressive delivery, not adaptive
  allocation.

## Quick start — five minutes to a running control plane

### The fastest path: Docker

Brings up Postgres plus the control plane (decision loop + read-only HTTP API)
with one command:

```bash
docker-compose up
```

DataTool is **terminal-native** — there is no browser dashboard. Run `datatool` in
your project to open the live console (an event feed over the audit log) and a
conversational assistant that answers "what's running?" / "why did it hold?" and
carries out actions on your confirmation. The daemon's HTTP surface is for ops:

- **http://localhost:8080/healthz** — liveness.
- **http://localhost:8080/metrics** — Prometheus metrics.
- **http://localhost:8080/api/experiments** — read-only JSON.

### The local path: uv

Prerequisites: Python 3.11+, [uv](https://docs.astral.sh/uv/), and a Postgres you
can reach (the line below starts one in Docker).

```bash
uv sync                                  # install deps
docker-compose up -d postgres            # or point at your own Postgres
uv run datatool init                     # create the schema — no manual SQL
uv run datatool doctor                   # confirm everything is wired up
```

`doctor` should report settings, database connectivity, the config directory, and
the bundled adapters all green.

## Your first experiment

Register the bundled pricing-page example (a clearer value-prop headline). This
resolves its [trust contract](docs/trust_contract.md), materializes the variants,
and stores it as `proposed`:

```bash
uv run datatool register examples/pricing_page.yaml
# registered pricing-headline-clarity (state: proposed)

uv run datatool show pricing-headline-clarity     # full state + effective contract
uv run datatool list                              # everything registered
```

From here you have two ways to see the controller actually drive it:

- **Rehearse on historical data** with the simulator — no live services needed.
  Replay a CSV of events through the controller and watch it ramp, hold, promote,
  or revert, cycle by cycle:

  The repo ships a ready-made run — a CSV-driven demo experiment and three days of
  synthetic events where the treatment genuinely wins:

  ```bash
  uv run datatool register examples/simulation_demo.yaml
  uv run datatool simulate checkout-button-color --data examples/synthetic_events.csv
  uv run datatool why checkout-button-color         # the reasoning behind each step
  ```

  Swap in your own event CSV to replay real history.

  Full walkthrough (including the CSV format): [replaying historical
  data](docs/recipes/replaying-historical-data.md).

- **Go live** by starting the daemon and connecting your metrics source:

  ```bash
  uv run datatool daemon
  ```

  Full walkthrough: [a landing-page test with PostHog](docs/recipes/landing-page-with-posthog.md).

Whatever the controller does, ask it why — straight from the append-only audit log:

```bash
uv run datatool why pricing-headline-clarity
```

## The terminal console

Run `datatool` with no arguments (in a project, like activating Claude Code) to open
the interactive terminal:

```bash
uv run datatool
```

You get a live event feed tailing the controller's audit log, a panel of experiments
and their state, and a conversational **assistant**. Ask it things in plain English —
"what's ramping?", "why is the pricing test holding?", "pause it" — and it uses a fixed
tool set over the same operations the CLI exposes. It **never decides** ramp/promote/
revert (the deterministic statistics and trust contract do); it only narrates and relays
your intent, and every action that changes an experiment waits for your explicit `/yes`
before it runs. The assistant uses your own LLM key (`ANTHROPIC_API_KEY`); with no key
set, the console still runs — just without the chat.

Type `/show <experiment>` for a detail view: the latest stat readout (allocation,
confidence-sequence bounds on the goal lift, alpha, sample sizes, last decision) above
an in-terminal chart of that confidence sequence over every evaluated cycle. `Esc` goes back.

The brain runs headless (`datatool daemon`) and survives closing the terminal; reopening
`datatool` re-attaches to the same live state over Postgres.

## How it works

A scheduler wakes each non-terminal experiment on a tick. For each, the decision
engine runs hard pre-checks (max-runtime, novelty buffer, **sample-ratio-mismatch**),
then the fast guardrail loop, then the goal-metric confidence sequence. Every
resulting action is clamped to the trust contract and executed through an adapter,
and a structured row is written to the audit log. See [`docs/architecture.md`](docs/architecture.md).

```
scheduler → decision engine → clamp to trust contract → adapter → audit log
                  │
        SRM · guardrails · confidence sequence
```

## Documentation

| Doc | What it covers |
|-----|----------------|
| [statistics](docs/statistics.md) | The statistics engine: papers, calibration tests, choices, failure modes. The credential. |
| [trust contract](docs/trust_contract.md) | The contract schema, field by field, with rationale. |
| [adapters](docs/adapters.md) | The four adapter protocols, the registry, and skeletons. |
| [CLI](docs/cli.md) | The `datatool` command reference. |
| [recipes](docs/recipes/) | End-to-end how-tos (PostHog, Postgres flag rollout, simulator replay). |
| [architecture](docs/architecture.md) | Reading guide into [`ARCHITECTURE.md`](ARCHITECTURE.md). |

## Development

```bash
uv sync                          # install deps (Python 3.11+)
uv run pytest                    # unit, stats, integration, e2e
uv run pytest tests/stats        # the calibration gate (must pass before merge)
uv run pytest -m slow            # the slow statistical regression guards
uv run ruff check . && uv run ruff format --check .
```

Contributions are welcome — see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Apache-2.0. See [`LICENSE`](LICENSE).
