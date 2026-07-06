# DataTool — Claude Code Project Instructions

## What this project is

DataTool is an open-source autonomous experimentation controller written in Python. It sits on top of existing feature-flag, metrics, and variant-generation tools and drives experiments through their full lifecycle — ramp, evaluate, promote, or revert — within a declarative trust contract.

**This project is being built from scratch.** When you start working in this repo, treat `ARCHITECTURE.md` as the source of truth and execute against it.

## Read this first, every session

1. `ARCHITECTURE.md` — the full spec. Read it before making any non-trivial decision.
2. `docs/statistics.md` (once it exists) — paper citations and algorithm choices. Don't re-derive; follow the brief.

If `ARCHITECTURE.md` and a request in chat conflict, ask before deviating. The architecture is the contract.

## Immutable rules (do not violate)

These are not preferences. They are project invariants.

- **No silent stats shortcuts.** The statistics module is the project's moat. Implement what `ARCHITECTURE.md §8` specifies, cite the papers in docstrings, and make the calibration tests in `tests/stats/` pass before any merge. A failing calibration test blocks the build.
- **No scope creep.** Anything listed in `ARCHITECTURE.md §20 (Anti-scope)` is out. If you find yourself wanting to add variant generation logic to `core/`, bandits to `stats/`, or a recommendation engine anywhere, stop and ask.
- **Adapter protocols, not inheritance.** All external integrations are PEP 544 Protocols in `adapters/*/base.py`. No business logic depends on a specific vendor.
- **The trust contract is the spine.** Every action the orchestrator takes is clamped to it. Every clamp is logged. Never add a code path that mutates state without going through `core/contract.py`.
- **Audit log is append-only.** Never update or delete rows in `audit_log`, `decisions`, `actions`, `guardrail_evaluations`, or `trust_events`. New rows only.
- **The interface is the terminal.** DataTool ships as a self-contained, terminal-native OSS tool: a live Textual console + a conversational assistant, launched by running `datatool` in a project (like activating Claude Code). No hosted panel, no browser SPA. The console is glanceable observability over the append-only audit log — it watches the deterministic brain, it never drives it.
- **The assistant is interface + variant generation only.** The LLM narrates and relays operator intent through a small, fixed tool set; it never decides ramp/promote/revert (deterministic stats + the trust contract do). Every mutating tool routes through `control/operations.py`, and requires explicit operator confirmation in the TUI before it runs. Reads run freely. This reaffirms the anti-scope rule: no recommendation engine in the decision path.
- **Apache-2.0 license.** Don't introduce dependencies with incompatible licenses (GPL, AGPL, SSPL). Check `pyproject.toml` additions against this.

## Tech stack (decided — do not relitigate)

- Python 3.11+
- Pydantic v2 for schemas
- SQLAlchemy 2.0 + Alembic for persistence
- Textual + plotext for the terminal console (the primary UI; both MIT)
- FastAPI for the daemon's read-only HTTP API + Prometheus metrics (no browser dashboard)
- anthropic / openai (optional `datatool[llm]` extra) for the assistant + variant generation
- Typer for the CLI
- pytest + hypothesis for tests
- structlog for logging
- prometheus_client for metrics
- uv for package management
- PostgreSQL 15+ as the only persistent store

If you think a different choice is better, write your reasoning and ask. Do not swap silently.

## Layout

```
datatool/         # package
├── core/        # domain models, contract, state machine
├── stats/       # the moat: CS, FDR, SRM, guardrails
├── control/     # scheduler, decision engine, orchestrator, ledger
├── adapters/    # flag/, metrics/, variant/, notify/
├── persistence/ # SQLAlchemy models, migrations
├── console/     # live Textual TUI + conversational assistant (the primary UI)
├── api/         # FastAPI read-only HTTP API + Prometheus metrics for the daemon
├── cli/         # Typer commands
├── simulator/   # replay + synthetic data
└── observability/
tests/           # unit, stats, integration, e2e
examples/        # YAML experiment definitions
benchmarks/      # slow Monte Carlo correctness checks
```

## Build order (when starting from scratch)

Follow `ARCHITECTURE.md` final section's priority order:

1. `core/models.py` and `core/contract.py` — the public API
2. `stats/` with passing calibration tests in `tests/stats/`
3. `control/` with the state machine
4. `adapters/flag/postgres.py` + `adapters/metrics/csv.py` — minimum to demo end-to-end
5. `cli/`
6. `simulator/`
7. `adapters/metrics/posthog.py` + `adapters/notify/{slack,webhook}.py`
8. `adapters/variant/static.py`
9. `adapters/variant/llm.py` (fake-client tests in CI; live tests gated on env var)
10. `console/` — the live Textual TUI + conversational assistant (the primary UI); `api/` exposes the daemon's read-only HTTP API + metrics
11. The rest as community contributions

Do not start on step N+1 before step N has tests passing.

## Conventions

- **Type-hint everything.** Pydantic models for domain objects, Protocols for adapter interfaces.
- **Every statistical algorithm cites its paper** in the docstring, with author, year, and equation reference where relevant.
- **Every adapter is registered** via a `register()` function consumed by `adapters/base.py`. No global imports of adapter modules from `core/` or `control/`.
- **Every decision and every action writes a row** with structured reasoning. The `datatool why` CLI command depends on this.
- **Sentence-case in docs and CLI output.** No Title Case.
- **No comments that restate the code.** Comments explain *why*, especially for stats code where the why is in a paper.
- **Test names describe the property being verified**, not the function being called. `test_cs_type_i_error_at_or_below_alpha` not `test_confidence_sequence`.

## Commands

```bash
# Setup
uv sync                          # install deps
docker-compose up -d postgres    # start Postgres
uv run datatool init                  # apply migrations

# Run
uv run datatool                       # launch the terminal console + assistant (in a project)
uv run datatool console               # same, explicit
uv run datatool daemon                # start the headless control plane (brain; survives terminal close)

# Test
uv run pytest tests/unit tests/stats tests/integration tests/e2e
uv run pytest tests/stats        # the gate — must pass before any commit
uv run pytest benchmarks/        # slow, nightly

# Lint / format
uv run ruff check .
uv run ruff format .

# Demo
uv run datatool register examples/pricing_page.yaml
uv run datatool simulate pricing-headline-clarity --data examples/synthetic_events.csv
```

## When in doubt

- If a design choice isn't covered by `ARCHITECTURE.md` and isn't an immutable rule above, make the simplest reasonable choice and note it in a `# DECISION:` comment with a one-line rationale.
- If a design choice contradicts `ARCHITECTURE.md`, stop and ask.
- If a request in chat asks you to violate an immutable rule, push back. The rules exist because the project's value depends on them.

## Acquisition context (keep in mind)

This is open-source first, but the design optimizes for being acquirable by experimentation infra vendors (LaunchDarkly, Statsig, PostHog, Optimizely, GrowthBook). Practical implications:

- Clean separation between control plane and execution plane (an acquirer should be able to swap all adapters and keep the brain).
- Typed, versioned trust contract schema (this is the asset).
- Statistics module with citations and calibration tests (this is the credential).
- Structured, complete audit log (compliance/SRE reviewers will read this).
- Apache-2.0, no CLA for now.

Keep these in mind on every PR. See `ARCHITECTURE.md §22` for the full list.
