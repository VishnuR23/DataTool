<!-- Thanks for contributing to DataTool. Please keep PRs focused. -->

## What and why

<!-- What does this change and why? Link any related issue: Closes #123 -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Adapter (flag / metrics / variant / notify)
- [ ] Docs
- [ ] Refactor / internal

## Checklist

- [ ] Reads `ARCHITECTURE.md` — this change respects the spec and the immutable rules in `CLAUDE.md`.
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass.
- [ ] `uv run pytest tests/stats` (the calibration gate) passes.
- [ ] `uv run pytest tests/unit tests/stats tests/integration tests/e2e` passes.
- [ ] New behavior has tests; test names describe the property verified.
- [ ] No new state-mutating path bypasses `core/contract.py`; audit tables stay append-only.
- [ ] Any new dependency is Apache-2.0-compatible (no GPL/AGPL/SSPL).

## Notes for reviewers

<!-- Anything non-obvious: design decisions, trade-offs, follow-ups. -->
