"""DataTool CLI entry point (ARCHITECTURE.md §12).

The ``datatool`` command. Commands stay thin: they parse arguments, open a session,
and delegate to ``commands.py`` (logic) and ``formatters.py`` (Rich rendering). The
``simulate`` and ``daemon`` commands arrive in later build steps.
"""

from __future__ import annotations

from pathlib import Path

import typer

from datatool.cli import commands as cmd
from datatool.cli import formatters as fmt
from datatool.config import get_settings
from datatool.control.loader import net_trust_deltas
from datatool.core.models import State
from datatool.persistence.db import init_db, make_engine, session_scope
from datatool.persistence.repositories import (
    ExperimentRepository,
    TrustEventRepository,
    VariantRepository,
)

app = typer.Typer(help="DataTool — autonomous experimentation controller.", no_args_is_help=True)

__version__ = "0.1.0"


@app.callback()
def _root(
    ctx: typer.Context,
    database_url: str = typer.Option(None, "--database-url", envvar="DATATOOL_DATABASE_URL"),
    config_dir: str = typer.Option(None, "--config-dir", envvar="DATATOOL_CONFIG_DIR"),
) -> None:
    settings = get_settings()
    ctx.obj = cmd.AppCtx(
        database_url=database_url or settings.database_url,
        config_dir=config_dir or settings.config_dir,
    )


@app.command()
def version() -> None:
    """Print the DataTool version."""
    typer.echo(f"datatool {__version__}")


@app.command()
def init(
    ctx: typer.Context,
    database_url: str = typer.Option(None, help="Override the database URL."),
) -> None:
    """Create the database schema (no manual SQL required)."""
    url = database_url or ctx.obj.database_url
    init_db(make_engine(url))
    typer.echo(f"initialized datatool schema at {url}")


@app.command()
def register(
    ctx: typer.Context, file: Path = typer.Argument(..., help="Experiment YAML file.")
) -> None:
    """Register an experiment from a YAML file."""
    name = cmd.register_experiment(ctx.obj, file)
    typer.echo(f"registered {name} (state: proposed)")


@app.command("list")
def list_experiments(
    ctx: typer.Context,
    state: str = typer.Option(None, help="Filter by lifecycle state."),
    surface: str = typer.Option(None, help="Filter by surface."),
) -> None:
    """List experiments."""
    with session_scope(ctx.obj.session_factory()) as session:
        experiments = ExperimentRepository(session).list(state=state, surface=surface)
        rows = [(experiment, cmd.treatment_pct(session, experiment)) for experiment in experiments]
        fmt.render_experiment_list(rows)


@app.command()
def show(ctx: typer.Context, name_or_id: str = typer.Argument(...)) -> None:
    """Show the full state of an experiment."""
    with session_scope(ctx.obj.session_factory()) as session:
        experiment = cmd.resolve_experiment(session, name_or_id)
        variants = VariantRepository(session).list_for(experiment.id)
        contract = cmd.effective_contract(session, experiment)
        counts = {
            "clean_promotion": TrustEventRepository(session).count(
                experiment.surface, "clean_promotion"
            ),
            "false_positive_ship": TrustEventRepository(session).count(
                experiment.surface, "false_positive_ship"
            ),
        }
        fmt.render_experiment_detail(
            experiment, variants, contract, cmd.treatment_pct(session, experiment), counts
        )


@app.command()
def status(ctx: typer.Context, name_or_id: str = typer.Argument(...)) -> None:
    """Show a one-line status for an experiment."""
    with session_scope(ctx.obj.session_factory()) as session:
        experiment = cmd.resolve_experiment(session, name_or_id)
        fmt.render_status_line(experiment, cmd.treatment_pct(session, experiment))


@app.command()
def why(ctx: typer.Context, name_or_id: str = typer.Argument(...)) -> None:
    """Explain the decisions and actions taken for an experiment."""
    with session_scope(ctx.obj.session_factory()) as session:
        experiment = cmd.resolve_experiment(session, name_or_id)
        fmt.render_why(experiment, cmd.build_why_events(session, experiment))


@app.command()
def pause(ctx: typer.Context, name_or_id: str = typer.Argument(...)) -> None:
    """Pause an experiment (transition to holding; it will not ramp further)."""
    name = cmd.transition_state(ctx.obj, name_or_id, State.HOLDING, "experiment.paused")
    typer.echo(f"paused {name} (holding)")


@app.command()
def resume(ctx: typer.Context, name_or_id: str = typer.Argument(...)) -> None:
    """Resume a paused experiment (holding -> ramping)."""
    name = cmd.transition_state(ctx.obj, name_or_id, State.RAMPING, "experiment.resumed")
    typer.echo(f"resumed {name} (ramping)")


@app.command()
def revert(
    ctx: typer.Context,
    name_or_id: str = typer.Argument(...),
    reason: str = typer.Option(None, help="Why the experiment is being reverted."),
) -> None:
    """Manually revert an experiment (kill to control)."""
    name = cmd.revert_experiment(ctx.obj, name_or_id, reason)
    typer.echo(f"reverted {name}")


@app.command()
def promote(
    ctx: typer.Context,
    name_or_id: str = typer.Argument(...),
    force: bool = typer.Option(False, help="Promote even if not holding for approval."),
) -> None:
    """Approve full rollout for an experiment (ship at 100%)."""
    name = cmd.promote_experiment(ctx.obj, name_or_id, force)
    typer.echo(f"promoted {name} to full rollout")


@app.command()
def graduate(
    ctx: typer.Context,
    surface: str = typer.Argument(...),
    by: float = typer.Option(5.0, help="Percentage points of autonomy to grant."),
) -> None:
    """Manually grant additional autonomy to a surface."""
    net = cmd.graduate_surface(ctx.obj, surface, by)
    typer.echo(f"granted {by:+g}% autonomy on {surface}; net surface delta now {net:+g}%")


@app.command()
def ledger(ctx: typer.Context, surface: str = typer.Argument(...)) -> None:
    """Show the trust ledger history for a surface."""
    with session_scope(ctx.obj.session_factory()) as session:
        events = TrustEventRepository(session).list_for_surface(surface)
        net = net_trust_deltas(session, surface).get("max_autonomous_pct", 0.0)
        fmt.render_ledger(surface, events, net)


@app.command()
def simulate(
    ctx: typer.Context,
    name_or_id: str = typer.Argument(...),
    data: Path = typer.Option(..., "--data", help="CSV of historical events to replay."),
    speed: str = typer.Option(None, "--speed", help="Advisory replay speed (e.g. 1000x)."),
) -> None:
    """Replay historical data through the controller and show what it would do."""
    result = cmd.simulate_experiment(ctx.obj, name_or_id, data, speed)
    fmt.render_simulation(result)


@app.command()
def doctor(ctx: typer.Context) -> None:
    """Check configuration and connectivity."""
    checks = cmd.run_doctor_checks(ctx.obj)
    fmt.render_doctor(checks)
    if not all(ok for _, ok, _ in checks):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
