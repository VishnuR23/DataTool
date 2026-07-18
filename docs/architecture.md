# Architecture

The full architecture specification lives in [`../ARCHITECTURE.md`](../ARCHITECTURE.md) at the repository root. It is the source of truth for the system's design and the contract every contribution is held to. This page is a reading guide into it, plus pointers to the focused reference docs that expand on individual subsystems.

> Why this is a pointer and not a copy: `ARCHITECTURE.md` is long and actively maintained. Duplicating it here would create two documents that drift apart, and a stale architecture doc is worse than none. So this file orients you and links out; the spec stays single-sourced.

## What DataTool is

DataTool is an autonomous experimentation controller. It sits on top of existing feature-flag, metrics, and variant-generation tools and drives experiments through their full lifecycle — ramp, evaluate, promote, or revert — inside a declarative **trust contract** that bounds everything it is allowed to do. It does progressive delivery with statistically valid sequential inference, not adaptive allocation (no bandits) and not variant generation (that lives in an adapter).

The design separates a **control plane** (the "brain": contract, statistics, decision engine, state machine) from an **execution plane** (the adapters that touch real flags, metrics, and notifications). An operator should be able to swap every adapter and keep the brain intact.

## The shape of the system

```
datatool/
├── core/         # domain models, the trust contract, the state machine
├── stats/        # the moat: confidence sequences, online FDR, SRM, guardrails
├── control/      # scheduler, decision engine, orchestrator, ledger, daemon
├── adapters/     # flag/, metrics/, variant/, notify/ — PEP 544 protocols
├── persistence/  # SQLAlchemy models, repositories, Alembic migrations
├── api/          # FastAPI read-only HTTP API + Prometheus metrics (no browser dashboard)
├── console/      # live Textual TUI + conversational assistant (the primary UI)
├── cli/          # the `datatool` Typer CLI
├── simulator/    # replay + synthetic data
└── observability/# structlog logging, prometheus metrics
```

## How a decision flows

1. The **scheduler** (`control/scheduler.py`) wakes experiments that are due for a decision cycle.
2. The **decision engine** (`control/decision_engine.py`) runs hard pre-checks (max-runtime, novelty buffer, **SRM** — see [statistics](statistics.md)), then the fast **guardrail** loop, then the **goal-metric** sequential inference (a confidence sequence).
3. Every proposed action is **clamped to the trust contract** in `core/contract.py`. Every clamp is logged. No code path mutates experiment state without going through the contract — this is the spine.
4. The chosen action is executed through the relevant **adapter**, and a structured row is written to the append-only audit log. `datatool why` reads those rows back.

## Reference docs

| Doc | What it covers |
|-----|----------------|
| [`statistics.md`](statistics.md) | The statistics engine: the papers, the calibration tests, the choices and the failure modes. The credential. |
| [`trust_contract.md`](trust_contract.md) | The trust contract schema, field by field, with the rationale for each — the typed, versioned asset. |
| [`adapters.md`](adapters.md) | How to write an adapter: the four protocols, the registry, copy-paste skeletons. |
| [`cli.md`](cli.md) | The `datatool` command reference. |
| [`recipes/`](recipes/) | End-to-end how-tos: a landing-page test with PostHog, a feature rollout with the Postgres flag adapter, and a historical replay through the simulator. |

## Invariants you should know before contributing

These come from `ARCHITECTURE.md` and `CLAUDE.md`, and they are not negotiable:

- **No silent statistics shortcuts.** The calibration tests in `tests/stats/` gate the build.
- **The trust contract is the spine.** Every state change clamps through `core/contract.py`, and every clamp is logged.
- **The audit log is append-only.** Rows in `decisions`, `actions`, `guardrail_evaluations`, `trust_events`, and `audit_log` are only ever inserted, never updated or deleted.
- **Adapters are protocols, not inheritance.** No business logic depends on a specific vendor.
- **Two arms only, Postgres only, no bandits, no variant generation in core.** See `ARCHITECTURE.md §20` (anti-scope) for the full list of what DataTool deliberately is not.
