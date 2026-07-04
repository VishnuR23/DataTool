# Phase 0 — OSS revert + remove SaaS surface — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the hybrid-SaaS surface (hosted `cloud/` panel + upward telemetry reporter + their wiring, deps, and Docker bits) so DataTool is a self-contained open-source tool again, with the deterministic core and the full test suite (minus the deleted SaaS tests) still green.

**Architecture:** Pure removal + unwiring. Nothing in `core/`, `stats/`, `control/`, `adapters/`, `persistence/`, or `api/` depends on the SaaS surface — the only couplings are the reporter block inside `run_daemon`, the `connect` CLI command + its `connect_agent` helpers, three `Settings` fields, packaging/deps, and Docker. Keep `datatool/telemetry/collector.py` and `datatool/telemetry/events.py` (the `TelemetryEvent` model) — Phase 1 repurposes them for the local console; delete only `reporter.py` and `cursor.py`.

**Tech Stack:** Python 3.11+, uv, pytest, ruff. No new dependencies (this phase only removes).

## Global Constraints

- uv only: `export PATH="$HOME/.local/bin:$PATH"` then `uv run …`. macOS has no `timeout`.
- **The stats calibration gate stays untouched and green.** Nothing here touches `stats/`. `uv run pytest tests/stats` must still show **46 passed / 1 deselected** at the end.
- **Do not touch** `core/`, `stats/`, `control/`, `adapters/`, `persistence/`, or `api/` logic. Phase 0 is removal + unwiring only.
- **Keep** `datatool/telemetry/collector.py`, `datatool/telemetry/events.py`, `tests/telemetry/test_collector.py`, `tests/telemetry/test_events.py` — Phase 1 repurposes them. Delete only the reporter/cursor/cloud/connect surface named below.
- Sentence-case in CLI output and docs. Comments explain *why*, not *what*.
- After every task, `uv run ruff check .` must pass (removing code must not leave unused imports).
- Commit message trailer on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```

## File structure (what changes)

| File | Change |
|---|---|
| `datatool/cli/commands.py` | Remove the reporter block in `run_daemon` (revert to a plain `uvicorn.run`); remove `connect_agent`, `_connect_client`, `_upsert_env`, and the now-unused `httpx`/`TYPE_CHECKING` imports. |
| `datatool/cli/main.py` | Remove the `connect` command. |
| `datatool/config.py` | Remove `cloud_url`, `cloud_token`, `cloud_report_interval_seconds`. |
| `datatool/telemetry/reporter.py`, `cursor.py` | Delete. |
| `cloud/` (whole tree) | Delete. |
| `tests/cloud/`, `tests/telemetry/test_reporter.py`, `tests/telemetry/test_cursor.py`, `tests/e2e/test_hybrid_loop.py`, `tests/e2e/test_connect_cli.py` | Delete. |
| `pyproject.toml` | Remove `argon2-cffi`, `python-multipart`, the `datatool-cloud` script, and `cloud` from wheel packages; relock. |
| `Dockerfile` | Remove `COPY cloud ./cloud`. |
| `docker-compose.yml` | Remove the `cloud` service and the initdb mount on `postgres`. |
| `docker/initdb/` | Delete. |
| `README.md` / packaging docs | Scrub references to the panel / `datatool-cloud` / `datatool connect`. |

---

## Task 1: Unwire the daemon, CLI, and config from the SaaS surface

**Files:**
- Modify: `datatool/cli/commands.py`
- Modify: `datatool/cli/main.py`
- Modify: `datatool/config.py`

**Interfaces:**
- Produces: a `run_daemon` with no telemetry/reporter references; a CLI with no `connect` command; a `Settings` with no `cloud_*` fields.

- [ ] **Step 1: Revert `run_daemon` to a plain server start.**

In `datatool/cli/commands.py`, in `run_daemon`, DELETE the block that starts after `loop_thread.start()` — the `import httpx as _httpx`, the `from datatool.telemetry...` imports, the `reporter_thread = None` / `if settings.cloud_url and settings.cloud_token:` block, and the `try/finally` around `uvicorn.run`. The region from `loop_thread.start()` through the end of `run_daemon` must read exactly:

```python
    loop_thread.start()

    app = create_app(factory, api_key=settings.api_key, require_auth=settings.require_auth)
    Console().print(
        f"datatool daemon started: control loop (tick {interval}s) + "
        f"HTTP API/dashboard on http://0.0.0.0:{port}. Press Ctrl-C to stop."
    )
    uvicorn.run(app, host="0.0.0.0", port=port, log_level=settings.log_level)
```

- [ ] **Step 2: Remove the connect helpers.**

In `datatool/cli/commands.py`, DELETE the three functions `_connect_client`, `_upsert_env`, and `connect_agent` in their entirety (and the comment banner directly above `_connect_client` if it only introduces them). Then DELETE the now-unused imports at the top: the `from typing import TYPE_CHECKING` line and the `if TYPE_CHECKING:` / `import httpx` block added for the connect helper. (Leave every other import.)

- [ ] **Step 3: Remove the `connect` command.**

In `datatool/cli/main.py`, DELETE the entire `@app.command()` `def connect(...)` function (the one whose docstring is "Connect this agent to the hosted console…").

- [ ] **Step 4: Remove the cloud settings.**

In `datatool/config.py`, DELETE the three fields:
```python
    cloud_url: str | None = None
    cloud_token: str | None = None
    cloud_report_interval_seconds: int = 10
```

- [ ] **Step 5: Verify no danglers + lint.**

Run:
```bash
export PATH="$HOME/.local/bin:$PATH"
grep -rn "connect_agent\|cloud_url\|cloud_token\|cloud_report_interval\|ReporterThread\|TelemetryReporter\|_connect_client\|_upsert_env" datatool/
uv run ruff check datatool/cli/ datatool/config.py
uv run python -c "import datatool.cli.main, datatool.cli.commands, datatool.config; print('imports OK')"
uv run datatool --help
```
Expected: grep returns nothing; ruff passes (no unused imports); imports succeed; `--help` lists no `connect` command.

- [ ] **Step 6: Commit.**

```bash
git add datatool/cli/commands.py datatool/cli/main.py datatool/config.py
git commit -m "refactor(cli): unwire daemon/CLI/config from the SaaS reporter and connect"
```

---

## Task 2: Delete the SaaS code + tests

**Files:**
- Delete: `cloud/` (entire tree)
- Delete: `datatool/telemetry/reporter.py`, `datatool/telemetry/cursor.py`
- Delete: `tests/cloud/` (entire tree)
- Delete: `tests/telemetry/test_reporter.py`, `tests/telemetry/test_cursor.py`
- Delete: `tests/e2e/test_hybrid_loop.py`, `tests/e2e/test_connect_cli.py`

**Interfaces:**
- Consumes: Task 1 (nothing in `datatool/` still imports the reporter/cursor once Task 1 lands).
- Produces: a repo with no `cloud/` package and no upward-reporter code; `collector.py` + `events.py` remain.

- [ ] **Step 1: Delete the trees and files.**

```bash
git rm -r cloud/ tests/cloud/
git rm datatool/telemetry/reporter.py datatool/telemetry/cursor.py
git rm tests/telemetry/test_reporter.py tests/telemetry/test_cursor.py
git rm tests/e2e/test_hybrid_loop.py tests/e2e/test_connect_cli.py
```

- [ ] **Step 2: Verify nothing references the removed modules.**

Run:
```bash
export PATH="$HOME/.local/bin:$PATH"
grep -rn "from cloud\|import cloud\|telemetry.reporter\|telemetry.cursor\|FileCursor" datatool/ tests/ || echo "no references — clean"
uv run python -c "import datatool.telemetry.collector, datatool.telemetry.events; print('collector+events still import OK')"
```
Expected: grep prints "no references — clean"; the collector + events still import (they do not depend on the deleted files).

- [ ] **Step 3: Run the full suite + the stats gate.**

Run:
```bash
uv run pytest tests/unit tests/stats tests/integration tests/e2e tests/telemetry -p no:cacheprovider
uv run pytest tests/stats -p no:cacheprovider
```
Expected: all green; `tests/stats` shows **46 passed / 1 deselected**. (The collector and events tests still pass; the deleted SaaS tests are gone.)

- [ ] **Step 4: Commit.**

```bash
git add -A
git commit -m "feat: remove the hosted cloud panel and upward telemetry reporter"
```

---

## Task 3: Remove SaaS dependencies + Docker packaging

**Files:**
- Modify: `pyproject.toml`
- Modify: `Dockerfile`
- Modify: `docker-compose.yml`
- Delete: `docker/initdb/`

**Interfaces:**
- Produces: a `pyproject.toml` with no argon2/multipart/`datatool-cloud`/`cloud` package, a relocked `uv.lock`, and Docker files with no cloud service.

- [ ] **Step 1: Trim `pyproject.toml`.**

In `pyproject.toml`:
- In `[project].dependencies`, DELETE the lines `"argon2-cffi>=23.1",` and `"python-multipart>=0.0.32",`.
- In `[project.scripts]`, DELETE the line `datatool-cloud = "cloud.__main__:main"`.
- In `[tool.hatch.build.targets.wheel]`, change `packages = ["datatool", "cloud"]` back to `packages = ["datatool"]` (and drop the `cloud/` mention from that comment).
- In `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls`, DELETE the `fastapi.Form`, `fastapi.Query`, `fastapi.Path`, `fastapi.Header`, `fastapi.Cookie`, `fastapi.Body` entries added for the panel. **Keep** `typer.Argument`, `typer.Option`, and `fastapi.Depends` (the agent's read-only dashboard API in `datatool/api/` still uses FastAPI `Depends`).

- [ ] **Step 2: Relock and sync.**

```bash
export PATH="$HOME/.local/bin:$PATH"
uv lock
uv sync
```
Expected: resolves without argon2-cffi / python-multipart; `uv lock --check` passes.

- [ ] **Step 3: Remove the Docker cloud bits.**

- In `Dockerfile`, DELETE the `COPY cloud ./cloud` line (and de-pluralize the adjacent comment if it says "agent + hosted panel").
- In `docker-compose.yml`, DELETE the entire `cloud:` service block, and DELETE the `- ./docker/initdb:/docker-entrypoint-initdb.d:ro` volume line under `postgres`.
- Delete the initdb dir: `git rm -r docker/initdb/`.

- [ ] **Step 4: Verify.**

```bash
export PATH="$HOME/.local/bin:$PATH"
uv run ruff check .
uv run python -c "import datatool.cli.main; print('datatool imports OK')"
grep -rn "cloud\|datatool_cloud\|argon2\|multipart" pyproject.toml Dockerfile docker-compose.yml || echo "no SaaS refs in packaging — clean"
(docker compose config >/dev/null 2>&1 && echo "compose valid") || uv run python -c "import yaml; d=yaml.safe_load(open('docker-compose.yml')); print('services:', list(d['services']))"
```
Expected: ruff clean; import OK; no SaaS refs remain; compose parses with services `postgres`, `datatool` only.

- [ ] **Step 5: Commit.**

```bash
git add -A
git commit -m "chore: drop SaaS deps (argon2, multipart), the cloud package, and its docker service"
```

---

## Task 4: Scrub SaaS references from the docs

**Files:**
- Modify: `README.md` (and any packaging doc under `docs/` that references the panel)

**Interfaces:**
- Produces: docs that describe only the self-contained OSS tool (no panel / `datatool-cloud` / `datatool connect`).

- [ ] **Step 1: Find the references.**

```bash
grep -rln "datatool-cloud\|datatool connect\|hosted console\|hosted panel\|enrollment token\|sign in to your console\|:8090" README.md docs/ | grep -v "docs/superpowers/"
```
This lists the docs to fix. (Do NOT edit anything under `docs/superpowers/` — those are historical specs/plans; the new spec already marks the hybrid one superseded.)

- [ ] **Step 2: Remove the SaaS-onboarding sections.**

In each file the grep found (excluding `docs/superpowers/`), DELETE any section, code block, or bullet that instructs the reader to run `datatool-cloud`, `datatool connect`, sign in to a hosted console, or bring up the compose `cloud` service. Leave the rest of the walkthrough (init, register, daemon, simulate) intact. Do not add new content — this task only removes; the terminal-native docs come in later phases.

- [ ] **Step 3: Verify.**

```bash
grep -rn "datatool-cloud\|datatool connect\|hosted console\|hosted panel\|enrollment token" README.md docs/ | grep -v "docs/superpowers/" || echo "docs clean of SaaS onboarding"
```
Expected: prints "docs clean of SaaS onboarding".

- [ ] **Step 4: Commit.**

```bash
git add -A
git commit -m "docs: remove hosted-panel onboarding; back to the self-contained tool"
```

---

## Task 5: Final Phase-0 verification

**Files:** none (verification + gate).

- [ ] **Step 1: Full suite + stats gate + lint + format + CLI.**

Run:
```bash
export PATH="$HOME/.local/bin:$PATH"
uv run pytest tests/unit tests/stats tests/integration tests/e2e tests/telemetry -p no:cacheprovider
uv run pytest tests/stats -p no:cacheprovider
uv run ruff check . && uv run ruff format --check .
uv run datatool --help
```
Expected: all tests green; `tests/stats` = **46 passed / 1 deselected**; ruff check + format clean; `datatool --help` shows the normal commands (init, register, daemon, doctor, simulate, why, …) and **no** `connect`.

- [ ] **Step 2: Confirm the SaaS surface is fully gone.**

```bash
test ! -d cloud && test ! -d tests/cloud && test ! -d docker/initdb && test ! -f datatool/telemetry/reporter.py && echo "SaaS surface removed"
grep -rn "from cloud\|import cloud\|TelemetryReporter\|datatool-cloud" . --include=*.py --include=*.toml --include=Dockerfile --include=*.yml | grep -v "docs/superpowers/" || echo "no code/config references remain"
```
Expected: "SaaS surface removed" and "no code/config references remain".

- [ ] **Step 3: Commit (if `ruff format` changed anything).**

```bash
git add -A && git commit -m "chore: phase 0 complete — SaaS surface removed, suite + stats gate green" || echo "nothing to commit"
```

---

## Self-review

**Spec coverage** (Phase 0 items from the design spec → task):

| Spec §"Removed surface" item | Task |
|---|---|
| Delete `cloud/` (panel, `__main__`, `datatool-cloud` script) | 2, 3 |
| Delete `reporter.py` + `cursor.py`; keep `collector.py` + `TelemetryEvent` | 2 |
| Unwire reporter/connect from commands.py + main.py | 1 |
| Remove `cloud_*` fields from config | 1 |
| Drop `argon2-cffi`, `python-multipart`; `packages=["datatool"]`; relock | 3 |
| Remove docker `cloud` service, initdb, Dockerfile `COPY cloud` | 3 |
| Scrub README/packaging references | 4 |
| Stats gate stays green; suite green | 2, 5 |

Docs note: the forward-looking `ARCHITECTURE.md`/`CLAUDE.md` rewrites (retiring the "no JS dashboard" rule, adding the TUI + assistant) are deliberately **deferred to the phases that build those features** — Phase 0 only removes, so `ARCHITECTURE.md` (which never described the SaaS panel) stays accurate.

**Placeholder scan:** none — every task lists exact files, exact edits, and exact verification commands.

**Type/name consistency:** `run_daemon`'s reverted tail matches the real surrounding names (`factory`, `settings.api_key`, `settings.require_auth`, `create_app`, `Console`, `uvicorn`). The deletion lists match the files discovered in the repo (`reporter.py`, `cursor.py`, `tests/cloud/` (16), `test_reporter.py`, `test_cursor.py`, `test_hybrid_loop.py`, `test_connect_cli.py`).
