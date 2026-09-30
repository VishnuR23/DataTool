# CLI reference

The `datatool` command is the operator's entry point: it registers experiments, drives them by hand when needed, runs the control plane, and — crucially — explains every decision the controller made. It is a thin Typer wrapper; each command parses arguments, opens a database session, and delegates to the service layer, so the CLI and the HTTP API behave identically.

Every invocation in this doc uses the `uv run datatool …` form, which is how the project is run during development (see [`CLAUDE.md`](../CLAUDE.md)). Once installed (`pip install datatool`), drop the `uv run` prefix.

## Global options

These are accepted before any subcommand and fall back to environment variables, then to the defaults in [`datatool/config.py`](../datatool/config.py):

| Option | Env var | Default | Purpose |
|--------|---------|---------|---------|
| `--database-url` | `DATATOOL_DATABASE_URL` | `postgresql+psycopg://datatool:datatool@localhost:5432/datatool` | The Postgres connection string. |
| `--config-dir` | `DATATOOL_CONFIG_DIR` | `./config` | Directory holding `org_defaults.yaml` and `surfaces/`. |

Running `datatool` with no command opens the live console (see [`console`](#console)); `datatool --help` lists the commands.

## The console

### `console`

```bash
uv run datatool            # bare form
uv run datatool console    # explicit form
```

Opens the live terminal console: an event feed tailing the audit log, a panel of experiments and their state, and the conversational assistant. The console only watches; the headless `daemon` makes the decisions, so it can run in another terminal or container and the console re-attaches over Postgres. The assistant uses `ANTHROPIC_API_KEY` (model: `DATATOOL_LLM_DEFAULT_MODEL`); without a key the console runs without the chat. On models that support it, a refused request is retried server-side on Anthropic's recommended fallback model; set `DATATOOL_LLM_REFUSAL_FALLBACK=false` to pin the configured model. Any action that changes an experiment waits for you to reply `/yes` before it runs. Type `/show NAME` for a per-experiment detail view — the latest stat readout and a chart of the goal lift's confidence sequence over time (`Esc` returns).

## Setup

### `version`

```bash
uv run datatool version
```

Prints the installed version (`datatool 0.1.0`).

### `init`

```bash
uv run datatool init [--database-url URL]
```

Creates the database schema — no manual SQL. Run once against a fresh Postgres before anything else. `--database-url` overrides the global option for this command. Prints `initialized datatool schema at <url>`.

### `doctor`

```bash
uv run datatool doctor
```

Checks the controller is wired up correctly and exits non-zero if anything fails — use it as a pre-flight and in CI. It verifies, in order: settings load, database connectivity (and reports the dialect), the config directory and `org_defaults.yaml` are present, and that the `flag.postgres` and `metrics.csv` adapters register. Sample output:

```
settings load            ok        ok
database connectivity    ok        postgresql
config directory         ok        ./config/org_defaults.yaml
adapter flag.postgres    ok        flag.postgres
adapter metrics.csv      ok        metrics.csv
```

## Lifecycle and inspection

### `register FILE`

```bash
uv run datatool register examples/pricing_page.yaml
```

Registers an experiment from a YAML file. It resolves the layered trust contract (org → surface → experiment), materializes each variant through its variant adapter (a forbidden-component reference is rejected here with a structured error), and persists the experiment in the `proposed` state. Prints `registered <name> (state: proposed)`.

### `list`

```bash
uv run datatool list [--state STATE] [--surface SURFACE]
```

Lists experiments as a table (name, surface, state, current treatment %). Filter with `--state` (e.g. `ramping`, `holding`) or `--surface`.

### `show NAME_OR_ID`

```bash
uv run datatool show pricing-headline-clarity
```

The full state of one experiment: identity, variants, the current treatment allocation, the **effective (resolved) contract** — ceiling, ramp, goal, statistics, guardrails — and the surface's trust tally (clean promotions vs. false-positive ships). Accepts either the experiment name or its id.

### `status NAME_OR_ID`

```bash
uv run datatool status pricing-headline-clarity
```

A single-line status: the state, the current treatment percentage, and the surface. Useful in scripts and watch loops.

### `why NAME_OR_ID`

```bash
uv run datatool why pricing-headline-clarity
```

**The accountability command.** It prints the chronological decisions, actions, and trust events for an experiment, each with its structured reasoning, read straight from the append-only audit log. This is where you see *why* the controller ramped, held, reverted, or concluded — the confidence-sequence bounds it acted on, the guardrail that tripped, the clamp it applied. Sample output:

```
why pricing-headline-clarity

  2026-06-18 09:00  ramp     proposed → canary    started at 1% (canary)
  2026-06-18 13:05  ramp     canary → ramping     cs lower bound 0.018 > 0; ramped to 2.5%
  2026-06-18 21:30  hold     ramping → holding    reached autonomous ceiling 5%; awaiting approval
```

The completeness of this log is a project invariant: the audit tables are append-only, so the history is never rewritten. See [`statistics.md`](statistics.md) for what the bounds in the reasons mean.

## Manual control

These commands let a human override the controller. Every one of them clamps to the trust contract and writes to the audit log, exactly as an autonomous action would — there is no privileged side door around the contract.

### `pause NAME_OR_ID`

```bash
uv run datatool pause pricing-headline-clarity
```

Transitions the experiment to `holding`: it stays at its current allocation and will not ramp further. Prints `paused <name> (holding)`.

### `resume NAME_OR_ID`

```bash
uv run datatool resume pricing-headline-clarity
```

Resumes a paused experiment (`holding → ramping`). Prints `resumed <name> (ramping)`.

### `revert NAME_OR_ID`

```bash
uv run datatool revert pricing-headline-clarity --reason "manual kill: bad headline"
```

Kills the experiment back to control. `--reason` records why in the audit log. Prints `reverted <name>`.

### `promote NAME_OR_ID`

```bash
uv run datatool promote pricing-headline-clarity [--force]
```

Approves full rollout (ship at 100%). This is the human approval that `full_rollout_requires: human_approval` waits for. `--force` promotes even when the experiment is not holding for approval. Prints `promoted <name> to full rollout`.

### `graduate SURFACE`

```bash
uv run datatool graduate pricing-page --by 5
```

Manually grants additional autonomy to a surface — raises its `max_autonomous_pct` ceiling by `--by` percentage points (default `5.0`). This writes a trust event to the ledger. Prints the grant and the net surface delta. Contract graduation rules also run automatically after every promote and revert (see [trust contract](trust_contract.md#graduation--autonomy-that-earns-itself)); this command is the manual override.

## Operations

### `daemon`

```bash
uv run datatool daemon [--port 8080] [--tick SECONDS]
```

Starts the headless control plane: the decision loop runs on a background thread while uvicorn serves the read-only JSON API and `/metrics` on the foreground (there is no browser dashboard — run `datatool` for the terminal console). `--port` sets the HTTP port (default `8080`); `--tick` overrides the loop interval in seconds (default from config, `60`). Runs until interrupted. The live daemon uses the PostHog metrics adapter and the Postgres flag adapter, and runs the SRM check on every cycle. On start it prints:

```
datatool daemon started: control loop (tick 60s) + read-only HTTP API + metrics on http://0.0.0.0:8080. Run `datatool` in another terminal for the live console. Press Ctrl-C to stop.
```

### `ledger SURFACE`

```bash
uv run datatool ledger pricing-page
```

Shows the trust ledger history for a surface — every trust event (clean promotions, false-positive ships, manual grants) and the net autonomy delta they sum to. This is the audit trail behind how a surface's `max_autonomous_pct` got to where it is.

### `simulate NAME_OR_ID --data CSV`

```bash
uv run datatool simulate checkout-button-color --data events.csv
```

Replays a CSV of historical events through the controller and shows what it would do, cycle by cycle — without touching any live flag. The experiment must be freshly registered (`proposed`). `--speed` is advisory (replay is deterministic and runs instantly). The CSV columns are `unit_id, variant, metric, value, timestamp`. Note that SRM is **off** during replay — the historical split is fixed, so comparing it to a ramping allocation would be spurious. See the [replay recipe](recipes/replaying-historical-data.md) for the full flow.

## See also

- [`trust_contract.md`](trust_contract.md) — the contract schema every command resolves and clamps to.
- [`adapters.md`](adapters.md) — the flag, metrics, variant, and notification adapters the commands drive.
- [`statistics.md`](statistics.md) — what the bounds and reasons in `datatool why` mean.
- [`recipes/`](recipes/) — end-to-end how-tos.
