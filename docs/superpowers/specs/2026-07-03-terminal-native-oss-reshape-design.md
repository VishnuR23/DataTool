# Terminal-native OSS reshape — design spec

_Date: 2026-07-03 · Supersedes the hybrid-SaaS-console direction
(`2026-06-22-hybrid-saas-console-design.md`)._

## Goal

Reshape DataTool from a hybrid-SaaS product back into a **self-contained,
open-source, terminal-native tool**. Instead of shipping telemetry up to a
hosted web console behind a sign-in, the operator runs `datatool` in their own
project and gets an interactive terminal: a conversational assistant (powered by
the operator's *own* LLM key) to set up and reason about experiments, and a live
terminal console that shows the deterministic controller's activity in real time.

The deterministic core is unchanged and remains the decision authority. The LLM
is an *interface and a variant generator*, never a decision-maker.

## Context & why now

The SaaS pivot (June 2026) added a hosted panel (`cloud/`) and an upward
telemetry reporter (`datatool/telemetry/`). The project is returning to its
open-source roots. `CLAUDE.md` and `ARCHITECTURE.md` were never actually flipped
to proprietary — they still say Apache-2.0, open-source — so this is largely a
*removal* plus a new terminal front-end, not a core rewrite.

## Non-goals / explicitly removed

- **No hosted web console, no sign-in, no orgs/tokens, no upward reporting.**
- **No local web dashboard.** The Jinja2/FastAPI read-only dashboard is replaced
  by the terminal console. (This retires the `CLAUDE.md` rule *"No JS frameworks
  in the dashboard… Plain Jinja2 templates"* — there is no browser surface.)
- **The LLM does not make ramp/promote/revert decisions.** Anti-scope's "no
  recommendation engine in the decision path" stays in force. The assistant only
  *proposes* setup and *narrates*; the stats + trust contract decide.
- No new persistent store; Postgres stays the single source of truth.

## Architecture

Three cleanly separated parts, all communicating through Postgres (the DB is the
bus — no new network protocol):

1. **The brain (unchanged).** The deterministic controller — decision loop,
   `stats/`, trust contract, orchestrator — runs headless via `datatool daemon`.
   It manages experiments continuously and survives the terminal closing. Every
   decision/action/guardrail/trust/audit event already lands in the append-only
   audit tables; **those tables are the live event stream.**

2. **The live terminal console (new).** A [Textual](https://textual.textualize.io/)
   TUI that tails the audit tables and renders the controller's activity live:
   a status/experiments panel, a color-coded event feed (reusing the semantic
   glyph/color design from the retired web console — ramp ↑, hold ⏸, promote ✓,
   revert ⟲, guardrail ⚠), and lightweight stat readouts. Detailed stat charts
   render in-terminal via `plotext` (unicode/braille plots) — "good enough,"
   terminal-native, no browser.

3. **The conversational assistant (new).** Launched by `datatool` in a project,
   powered by the operator's own LLM key (`ANTHROPIC_API_KEY` / OpenAI, read from
   the environment exactly as the existing variant adapter does). It:
   - helps define experiments + trust contracts in natural language, producing a
     draft experiment YAML the operator reviews and approves;
   - narrates and answers "why did it hold/revert this?" by reading the audit log
     and stats (this is `datatool why` surfaced conversationally);
   - generates variants via the existing `adapters/variant/llm.py`.
   It drives DataTool **only through the existing operations** (`register`,
   `pause`, `resume`, `promote`, `revert`), so every action it takes is
   trust-contract-clamped and audited — the interface, not the authority.

### How the pieces connect

```
you ──chat──▶ assistant ──proposes spec/action──▶ [approval gate] ──▶ existing
                   │                                                    operations
                   │ reads audit + stats to narrate                        │
                   ▼                                                       ▼
             live TUI console ◀────tails append-only audit tables──── Postgres ◀── brain
```

The TUI tails the audit tables for the live feed; the assistant issues actions
through the contract-clamped operations. Identical whether the brain is local
(the TUI starts one if none is running) or on a server (the TUI attaches to the
same DB).

## Components in detail

### C1. Removed surface (Phase 0)

Delete and unwire:
- `cloud/` (entire hosted panel: auth, sessions, orgs, tokens, ingest, SSE web
  console, app factory, `__main__`, `datatool-cloud` script).
- `datatool/telemetry/reporter.py` (the upward push) and `cursor.py` (the
  file watermark). From `events.py`, drop the cloud-wire bits (`TelemetryBatch`,
  `SCHEMA_VERSION`); **keep the `TelemetryEvent` model** — it becomes the console
  feed's normalized event type (see C2).
- Reporter/connect wiring in `datatool/cli/commands.py` (`connect_agent`,
  `_connect_client`, `_upsert_env`, the `ReporterThread` block in `run_daemon`)
  and the `connect` command in `datatool/cli/main.py`.
- Cloud fields in `datatool/config.py` (`cloud_url`, `cloud_token`,
  `cloud_report_interval_seconds`).
- Deps no longer needed: `argon2-cffi`, `python-multipart` (drop from
  `pyproject.toml` + relock). `packages = ["datatool"]` (drop `cloud`).
- `docker-compose.yml` `cloud` service + `docker/initdb/`; `Dockerfile` `COPY
  cloud`; the hybrid-SaaS spec/plan docs are marked superseded (kept for history).
- Tests: `tests/cloud/`, and the SaaS-specific telemetry tests.

### C2. Repurposed collector → local live feed (Phase 1)

`datatool/telemetry/collector.py` already does "read new audit rows since a
watermark → normalized `TelemetryEvent`s." **Keep this logic and the
`TelemetryEvent` model**, move them under the console (e.g. `datatool/console/`),
and point them at the TUI instead of a reporter. This is the read side of the
live feed; the TUI holds its watermark in memory (no `FileCursor`). Live updates
use Postgres `LISTEN/NOTIFY` if available, falling back to short-interval polling
of the audit tables by watermark (portable, works on SQLite in tests).

### C3. The TUI (Phase 1)

- **Framework:** Textual (MIT — Apache-compatible), from the Rich authors already
  in use. `plotext` for in-terminal charts.
- **Layout:** a header (connection/brain status + a live lamp), a left panel
  (experiments + their state/trust level), a main live event feed (the glyph
  rail), and a footer/command line. A detail view per experiment shows its stat
  readout + a `plotext` chart of the guarded metric over time.
- **Data:** read-only over the brain's state via the existing repositories +
  the C2 feed. No writes except through C4's operations.

### C4. The conversational assistant (Phase 2)

- **LLM client:** reuse the patterns in `adapters/variant/llm.py` (lazy optional
  import of `anthropic`/`openai`, env-key, deterministic caching where sensible).
  Follow the `claude-api` skill for the Anthropic SDK + tool-use wiring. Default
  model from `Settings.llm_default_model`.
- **Tool-calling:** a small, fixed tool set mapped 1:1 to existing operations and
  reads — `propose_experiment(spec)`, `register`, `status`, `why(id)`, `pause`,
  `resume`, `promote`, `revert`, `generate_variant`. No tool bypasses the trust
  contract; every mutating tool routes through `control/operations.py`.
- **Approval gates:** any tool that *mutates a live experiment or production*
  (register/promote/revert/pause/resume) requires explicit operator confirmation
  in the TUI before it executes. Read/narrate tools run freely. This mirrors
  DataTool's "every action clamped and logged" ethos.
- **Graceful degradation:** with no LLM key, the TUI still runs fully — setup via
  a plain form/`register` and the live console — just without the conversational
  layer. The assistant is an enhancement, not a hard dependency.

### C5. Entry points

- `datatool` (bare, or an explicit `datatool start`/`datatool tui`) → the
  interactive terminal (assistant + live console).
- `datatool daemon` → the headless brain, unchanged (prod/CI/servers).
- **Local-dev fuse:** if the TUI finds no running brain against the configured
  DB, it offers to start one (in a background thread/subprocess) so a single
  command gives the full experience.

## Data flow (happy path)

1. Operator runs `datatool` in their project → TUI opens, assistant greets.
2. Operator describes an experiment in natural language → assistant drafts a spec
   + trust contract YAML and shows it.
3. Operator approves → assistant calls `register` (contract-clamped, audited).
4. The brain's decision loop ramps/evaluates/promotes/reverts per the stats and
   contract — autonomously, as today.
5. Every event lands in the audit tables → the TUI live feed shows it; the
   assistant can narrate "why" on demand.
6. Operator closes the terminal → the brain keeps running; reopening `datatool`
   re-attaches to the same live state.

## Invariants preserved

- Determinism of decisions (stats + trust contract); the LLM never decides.
- The trust contract remains the spine; every action goes through
  `core/contract.py` clamps and is audited (append-only).
- The **stats calibration gate stays untouched and green** (`tests/stats`,
  46 passed / 1 deselected). Nothing here touches `stats/`.
- Apache-2.0; permissive deps only (Textual MIT, plotext MIT).

## Docs to update

- `ARCHITECTURE.md`: replace the "read-only web dashboard" sections with the
  terminal console + assistant; add a "Terminal UX" section; keep the trust
  contract, stats, audit, adapter-protocol sections intact.
- `CLAUDE.md`: retire the "No JS frameworks in the dashboard / Jinja2" rule;
  restate the observability surface as the TUI; add the assistant's boundary
  (interface + variant gen only, never the decision path — reaffirming anti-scope);
  refresh the layout, build-order, and commands sections; keep OSS/Apache framing.
- `README.md` / packaging docs: reframe from the hybrid-SaaS walkthrough back to
  the terminal-native OSS story; drop the panel/compose-cloud instructions.

## Tech decisions (resolved)

- Textual for the TUI; `plotext` for terminal charts (both MIT).
- DB-as-the-bus (no new IPC); `LISTEN/NOTIFY` with polling fallback.
- Assistant is optional (`datatool[llm]` extra, already defined); TUI degrades
  gracefully without a key.
- Reuse (not rewrite) the collector's audit-tailing logic for the live feed.

## Testing strategy

- Reuse the existing pytest layout. `stats/` gate is sacred and untouched.
- Live feed: unit-test the audit-tailing against seeded audit rows (SQLite),
  as the collector tests already do.
- TUI: Textual's built-in test harness (`App.run_test()` / pilot) for
  interaction + snapshot-style assertions on rendered content.
- Assistant: test with a **fake LLM client** (the variant adapter's pattern),
  asserting tool-calls map to the right operations and that mutating tools are
  gated behind confirmation. Live LLM tests gated on an env var, as today.
- An end-to-end test: seed an experiment via the assistant's `register` tool →
  run a decision tick → assert the audit rows surface in the feed model.

## Phasing (each phase = its own implementation plan)

- **Phase 0 — OSS revert + remove SaaS surface.** Delete `cloud/`, the reporter,
  wiring, deps, compose/Docker cloud bits; update docs. Repo builds, full suite
  green (minus deleted SaaS tests), stats gate intact. Low risk, mostly deletion.
- **Phase 1 — Live terminal console.** Repurpose the collector into the feed;
  build the Textual TUI (status + event feed + charts) replacing the web
  dashboard; wire `datatool` to launch it (with the local-dev brain fuse).
- **Phase 2 — Conversational assistant.** LLM client + fixed tool set over the
  existing operations + approval gates + narration; integrate variant gen; wire
  into the TUI. Degrades gracefully without a key.

## Open decisions (made on the operator's behalf — flag any to change)

1. Textual + plotext as the TUI/plotting stack (vs. hand-rolled Rich).
2. Drop the local web dashboard entirely (terminal-only).
3. Assistant gates all mutating actions behind explicit confirmation (vs. letting
   it act autonomously within the contract).
4. Repurpose the collector rather than deleting all of `datatool/telemetry/`.
5. `datatool` bare launches the TUI; `datatool daemon` stays the headless brain.
