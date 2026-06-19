"""Rich rendering for the CLI (ARCHITECTURE.md §12).

All output is sentence-case (CLAUDE.md). A fresh ``Console`` is created per call so
output is captured correctly under test runners that redirect stdout.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from rich.console import Console
from rich.table import Table

from datatool.core.models import TrustContract

# Lifecycle-state colours for at-a-glance status.
_STATE_STYLE = {
    "proposed": "dim",
    "canary": "cyan",
    "ramping": "blue",
    "holding": "yellow",
    "promoting": "green",
    "promoted": "bold green",
    "reverted": "bold red",
    "concluded": "magenta",
}


def _console() -> Console:
    return Console(width=120, highlight=False)


def _state(state: str) -> str:
    return f"[{_STATE_STYLE.get(state, 'white')}]{state}[/]"


def _ts(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "—"


def render_experiment_list(rows: Iterable[tuple]) -> None:
    """rows: iterable of (experiment, treatment_pct)."""
    table = Table(title="experiments", title_justify="left")
    for column in ("name", "surface", "state", "treatment %", "owner", "created"):
        table.add_column(column)
    any_rows = False
    for experiment, treatment_pct in rows:
        any_rows = True
        table.add_row(
            experiment.name,
            experiment.surface,
            _state(experiment.state),
            f"{treatment_pct:g}",
            experiment.owner,
            _ts(experiment.created_at),
        )
    console = _console()
    if not any_rows:
        console.print("no experiments registered")
        return
    console.print(table)


def render_status_line(experiment, treatment_pct: float) -> None:
    _console().print(
        f"{experiment.name} — {_state(experiment.state)} @ {treatment_pct:g}% "
        f"(surface: {experiment.surface})"
    )


def render_experiment_detail(
    experiment,
    variants: list,
    contract: TrustContract,
    treatment_pct: float,
    trust_counts: dict[str, int],
) -> None:
    console = _console()
    console.print(f"[bold]{experiment.name}[/]  {_state(experiment.state)}")
    console.print(f"  id        {experiment.id}")
    console.print(f"  surface   {experiment.surface}")
    console.print(f"  owner     {experiment.owner}")
    console.print(
        f"  created   {_ts(experiment.created_at)}   updated {_ts(experiment.updated_at)}"
    )
    console.print(f"  allocation  treatment {treatment_pct:g}%")

    variants_table = Table(title="variants", title_justify="left")
    for column in ("name", "control?", "source"):
        variants_table.add_column(column)
    for variant in variants:
        source = (variant.payload or {}).get("type") or (variant.payload or {}).get("source") or "—"
        variants_table.add_row(variant.name, "yes" if variant.is_control else "no", str(source))
    console.print(variants_table)

    allocation = contract.allocation
    console.print("[bold]effective contract[/] (written + trust deltas)")
    console.print(f"  autonomous ceiling   {allocation.max_autonomous_pct:g}%")
    console.print(f"  ramp schedule        {allocation.ramp_schedule}")
    console.print(f"  full rollout         {allocation.full_rollout_requires}")
    console.print(
        f"  goal                 {contract.goal.metric} "
        f"({contract.goal.direction}, mde {contract.goal.minimum_detectable_effect:g})"
    )
    console.print(
        f"  statistics           alpha {contract.statistics.alpha:g}, "
        f"min_runtime {contract.statistics.min_runtime}, "
        f"max_runtime {contract.statistics.max_runtime}"
    )

    if contract.guardrails:
        guardrails_table = Table(title="guardrails", title_justify="left")
        for column in ("name", "severity", "metric", "threshold"):
            guardrails_table.add_column(column)
        for guardrail in contract.guardrails:
            guardrails_table.add_row(
                guardrail.name,
                guardrail.severity.value,
                guardrail.metric,
                f"{guardrail.threshold.type.value} {guardrail.threshold.value:g}",
            )
        console.print(guardrails_table)

    console.print(
        f"[bold]surface trust[/]  clean promotions {trust_counts.get('clean_promotion', 0)}, "
        f"false-positive ships {trust_counts.get('false_positive_ship', 0)}"
    )


def render_why(experiment, events: list[tuple]) -> None:
    """events: chronological list of (timestamp, kind, line) tuples."""
    console = _console()
    console.print(f"[bold]why[/] {experiment.name} ({_state(experiment.state)})")
    if not events:
        console.print("  no recorded decisions, actions, or events yet")
        return
    for timestamp, kind, line in events:
        console.print(f"  [dim]{_ts(timestamp)}[/]  [cyan]{kind}[/]  {line}")


def render_ledger(surface: str, events: list, net_ceiling_delta: float) -> None:
    console = _console()
    table = Table(title=f"trust ledger — {surface}", title_justify="left")
    for column in ("when", "kind", "delta", "reason"):
        table.add_column(column)
    for event in events:
        delta = "—" if not event.delta else ", ".join(f"{k} {v:+g}" for k, v in event.delta.items())
        table.add_row(_ts(event.created_at), event.kind, delta, event.reason)
    if not events:
        console.print(f"no trust events for surface {surface}")
        return
    console.print(table)
    console.print(f"net autonomy delta on this surface: {net_ceiling_delta:+g}%")


def render_simulation(result) -> None:
    console = _console()
    console.print(f"[bold]simulation[/] {result.experiment} — {result.n_cycles} cycles")
    if result.steps:
        table = Table(title="decisions", title_justify="left")
        for column in ("when", "decision", "state", "treatment %", "reason"):
            table.add_column(column)
        for step in result.steps:
            table.add_row(
                _ts(step.at),
                step.decision_kind,
                _state(step.state),
                f"{step.allocation_pct:g}",
                step.reason,
            )
        console.print(table)
    else:
        console.print("  no state-changing decisions (the controller would have continued)")
    console.print(f"final: {_state(result.final_state.value)} @ {result.final_allocation_pct:g}%")


def render_doctor(checks: list[tuple[str, bool, str]]) -> None:
    console = _console()
    table = Table(title="doctor", title_justify="left")
    for column in ("check", "status", "detail"):
        table.add_column(column)
    for name, ok, detail in checks:
        status = "[green]ok[/]" if ok else "[bold red]fail[/]"
        table.add_row(name, status, detail)
    console.print(table)
