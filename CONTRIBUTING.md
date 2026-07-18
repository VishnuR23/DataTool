# Contributing to DataTool

Thanks for your interest in DataTool. It is an autonomous experimentation
controller, and the thing that makes it worth trusting — statistically valid
sequential inference clamped to a typed safety contract — is also the thing that is
easy to erode one well-meaning shortcut at a time. So this guide leans hard on a
small set of invariants. Inside them, contributions are very welcome.

By contributing you agree your work is licensed under the project's
[Apache-2.0](LICENSE) license. There is no CLA at this time.

## Start here

1. Read [`ARCHITECTURE.md`](ARCHITECTURE.md) — the full spec and the source of
   truth. If a change conflicts with it, raise that first; the architecture is the
   contract.
2. Read the [docs](docs/) for the area you're touching — especially
   [`docs/statistics.md`](docs/statistics.md) before any change under `stats/`.
3. For anything non-trivial, open an issue describing the change before you write
   code, so we can confirm it fits the spec and the anti-scope (`ARCHITECTURE.md §20`).

## Development setup

DataTool uses [uv](https://docs.astral.sh/uv/) and targets Python 3.11+. Postgres
is the only persistent store.

```bash
uv sync                          # install deps
docker-compose up -d postgres    # start Postgres
uv run datatool init             # apply the schema
```

Run the app:

```bash
uv run datatool                  # the terminal console + assistant (in a project)
uv run datatool daemon           # the headless control plane (loop + read-only API + metrics)
```

## Tests, lint, and the gate

```bash
uv run pytest                    # unit, stats, integration, e2e
uv run pytest tests/stats        # the calibration gate — must pass before any merge
uv run pytest -m slow            # the slow statistical regression guards
uv run ruff check . && uv run ruff format --check .
```

`tests/stats/` is a hard gate. A failing calibration test blocks the build, full
stop — it is the proof that the controller's core safety property holds. Never
weaken a calibration test to make a change pass; if a test is wrong, fix the test
with its own justification, in its own PR.

Test names describe the property being verified, not the function being called:
`test_cs_type_i_error_at_or_below_alpha`, not `test_confidence_sequence`.

## The invariants (do not violate)

These come from [`CLAUDE.md`](CLAUDE.md) and `ARCHITECTURE.md`. They are not
preferences — the project's value depends on them.

- **No silent statistics shortcuts.** Implement what `ARCHITECTURE.md §8` specifies,
  cite the paper in the docstring (author, year, equation), and make the calibration
  tests pass. New statistical code ships with a calibration test, not just unit
  tests.
- **The trust contract is the spine.** Every action that mutates experiment state
  goes through `core/contract.py` and is clamped to the resolved contract. Every
  clamp is logged. Don't add a side door.
- **The audit log is append-only.** `decisions`, `actions`, `guardrail_evaluations`,
  `trust_events`, and `audit_log` are insert-only — never `UPDATE` or `DELETE`.
  Repositories expose no mutation methods for them, and they should stay that way.
- **Adapters are protocols, not inheritance.** External integrations are PEP 544
  Protocols in `adapters/*/base.py`; no business logic depends on a specific vendor,
  and `core/`/`control/` never import a vendor module. See [`docs/adapters.md`](docs/adapters.md).
- **Respect the anti-scope.** No variant generation in `core/`, no bandits in
  `stats/`, no recommendation engine, no second persistence backend, no more than
  two variants per experiment. See `ARCHITECTURE.md §20`. If you want one of these,
  open an issue — the answer for the MVP is "not yet", on purpose.
- **The interface is the terminal.** DataTool is terminal-native: a Textual console
  + a conversational assistant, launched by `datatool`. No browser dashboard. The
  console watches the deterministic brain over the audit log; it never drives it.
- **The assistant is interface + variant generation only.** The LLM never decides
  ramp/promote/revert; it relays operator intent through a fixed tool set over
  `control/operations.py`, and every mutating action is confirmation-gated and audited.
- **Apache-2.0-compatible dependencies only.** No GPL/AGPL/SSPL. Check new
  `pyproject.toml` entries against this.

## Conventions

- **Type-hint everything.** Pydantic models for domain objects, Protocols for
  adapter interfaces.
- **Comments explain *why*, not *what*** — especially in `stats/`, where the *why*
  is in a paper. Don't restate the code.
- **Every decision and action writes a structured row.** The `datatool why` command
  depends on it.
- **Sentence-case** in docs and CLI output. No Title Case.
- If a design choice isn't covered by the spec and isn't an invariant above, make
  the simplest reasonable choice and mark it with a `# DECISION:` comment giving a
  one-line rationale.

## Submitting a change

1. Branch from `main`.
2. Keep the change focused — one logical change per PR. Surgical diffs review faster
   and break less.
3. Make sure `uv run pytest` and `uv run ruff check .` are green, and that you added
   tests for new behavior (a calibration test for new stats).
4. Write a clear PR description: what changed, why, and any deviation from
   `ARCHITECTURE.md` (flagged explicitly, with the `# DECISION:` rationale in code).
5. Be ready to discuss. The invariants above are non-negotiable; most everything
   else is open to a good argument.

## Reporting bugs and security issues

- **Bugs:** open an issue with steps to reproduce, what you expected, and what
  happened. For a controller bug, the output of `datatool why <experiment>` is
  invaluable.
- **Security:** please do not open a public issue for a vulnerability. See
  [`SECURITY.md`](SECURITY.md) if present, or contact the maintainers privately.

## Code of conduct

Participation in this project is governed by our
[Code of Conduct](CODE_OF_CONDUCT.md). By taking part, you agree to uphold it.
