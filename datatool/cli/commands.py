"""CLI command logic and helpers (ARCHITECTURE.md §12).

The Typer commands in ``main.py`` stay thin; the real work — YAML resolution,
runtime reconstruction, orchestration, persistence — lives here and reuses the
control plane rather than reimplementing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import typer
import yaml
from rich.console import Console
from sqlalchemy.orm import Session, sessionmaker

from datatool.adapters import base as registry
from datatool.adapters.flag.postgres import PostgresFlagProvider
from datatool.adapters.flag.postgres import register as register_flag
from datatool.adapters.metrics.csv import register as register_csv
from datatool.config import get_settings
from datatool.control import operations
from datatool.control.loader import load_effective_contract
from datatool.control.operations import ExperimentNotFound, OperationError
from datatool.core.contract import resolve_contract
from datatool.core.exceptions import StateTransitionError
from datatool.core.models import ExperimentSpec, State, TrustContract
from datatool.persistence import models as m
from datatool.persistence.db import make_engine, make_session_factory, session_scope
from datatool.persistence.repositories import (
    AuditLogRepository,
    ExperimentRepository,
    VariantRepository,
)

# Shared read helpers (single implementation in control/operations.py).
treatment_pct = operations.treatment_pct
build_why_events = operations.decision_log

ACTOR = "cli"


@dataclass
class AppCtx:
    """Per-invocation context carried on the Typer context object."""

    database_url: str
    config_dir: str
    _factory: sessionmaker[Session] | None = None

    def session_factory(self) -> sessionmaker[Session]:
        if self._factory is None:
            self._factory = make_session_factory(make_engine(self.database_url))
        return self._factory


def _fail(message: str) -> None:
    """Print an error to stderr and exit non-zero."""
    Console(stderr=True).print(f"[bold red]error[/] {message}")
    raise typer.Exit(code=1)


def resolve_experiment(session: Session, identifier: str) -> m.Experiment:
    """Resolve an experiment by UUID or by name; fail clearly if absent."""
    experiment = operations.resolve_experiment(session, identifier)
    if experiment is None:
        _fail(f"no experiment matching {identifier!r}")
    return experiment  # type: ignore[return-value]


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        _fail(f"file not found: {path}")
    return yaml.safe_load(path.read_text())


def _load_yaml_optional(path: Path) -> dict | None:
    return yaml.safe_load(path.read_text()) if path.exists() else None


# --------------------------------------------------------------------------- #
# register
# --------------------------------------------------------------------------- #


def _materialize_variant(variant_spec, *, contract, factory) -> dict:
    """Materialize a variant's payload via the source that owns it.

    Static/existing go through the static source; llm variants are generated (and
    scope-checked) via the LLM source with a client from the environment and the
    DB-backed cache; external variants are stored as-is.
    """
    from datatool.adapters.variant.static import StaticVariantSource

    if variant_spec.source in ("static", "existing"):
        return StaticVariantSource().materialize(variant_spec)
    if variant_spec.source == "llm":
        from datatool.adapters.variant.llm import (
            LLMVariantCache,
            LLMVariantSource,
            default_client_from_env,
        )

        source = LLMVariantSource(
            client=default_client_from_env(),
            forbidden_components=contract.scope.forbidden_components,
            cache=LLMVariantCache(factory),
        )
        return source.materialize(variant_spec)
    return variant_spec.payload


def register_experiment(ctx: AppCtx, file: Path) -> str:
    data = _load_yaml(Path(file))
    surface = data["surface"]
    org = _load_yaml_optional(Path(ctx.config_dir) / "org_defaults.yaml")
    surface_layer = _load_yaml_optional(Path(ctx.config_dir) / "surfaces" / f"{surface}.yaml")

    try:
        contract = resolve_contract(org, surface_layer, data.get("contract"))
        spec = ExperimentSpec(
            name=data["experiment"],
            surface=surface,
            owner=data["owner"],
            description=data.get("description"),
            variants=data["variants"],
            contract=contract,
        )
    except Exception as exc:  # contract/spec validation
        _fail(f"invalid experiment definition: {exc}")

    factory = ctx.session_factory()

    # Materialize variants first (this may call an LLM and enforce scope) so a
    # generation failure never leaves a half-registered experiment.
    materialized: list[tuple] = []
    for variant in spec.variants:
        try:
            materialized.append(
                (variant, _materialize_variant(variant, contract=contract, factory=factory))
            )
        except Exception as exc:  # AdapterError from materialization
            _fail(f"could not materialize variant {variant.name!r}: {exc}")

    with session_scope(factory) as session:
        repo = ExperimentRepository(session)
        if repo.get_by_name(spec.name) is not None:
            _fail(f"experiment {spec.name!r} is already registered")
        experiment = repo.add(
            name=spec.name,
            surface=spec.surface,
            owner=spec.owner,
            contract=contract.model_dump(mode="json"),
            spec={
                "description": spec.description,
                "variants": [v.model_dump() for v in spec.variants],
            },
            state=State.PROPOSED.value,
        )
        variants = VariantRepository(session)
        for variant, payload in materialized:
            variants.add(
                experiment_id=experiment.id,
                name=variant.name,
                is_control=variant.is_control,
                payload=payload,
            )
        AuditLogRepository(session).add(
            kind="experiment.registered",
            actor=ACTOR,
            experiment_id=experiment.id,
            payload={"surface": surface, "owner": spec.owner},
        )
        experiment_id = experiment.id

    # Initial allocation: everyone on control until the experiment is started.
    PostgresFlagProvider(factory).set_allocation(
        experiment_id, {"control": 100.0, "treatment": 0.0}
    )
    return spec.name


# --------------------------------------------------------------------------- #
# manual lifecycle
# --------------------------------------------------------------------------- #


def _op(call):
    """Run a shared operation, translating domain errors to a friendly CLI exit."""
    try:
        return call()
    except (OperationError, ExperimentNotFound, StateTransitionError) as exc:
        _fail(str(exc))


def pause(ctx: AppCtx, identifier: str) -> str:
    return _op(lambda: operations.pause(ctx.session_factory(), identifier, actor=ACTOR))


def resume(ctx: AppCtx, identifier: str) -> str:
    return _op(lambda: operations.resume(ctx.session_factory(), identifier, actor=ACTOR))


def revert_experiment(ctx: AppCtx, identifier: str, reason: str | None) -> str:
    return _op(lambda: operations.revert(ctx.session_factory(), identifier, reason, actor=ACTOR))


def promote_experiment(ctx: AppCtx, identifier: str, force: bool) -> str:
    return _op(
        lambda: operations.promote(ctx.session_factory(), identifier, force=force, actor=ACTOR)
    )


def graduate_surface(ctx: AppCtx, surface: str, by: float) -> float:
    return operations.graduate(ctx.session_factory(), surface, by, actor=ACTOR)


def effective_contract(session: Session, experiment: m.Experiment) -> TrustContract:
    return load_effective_contract(session, experiment)


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def simulate_experiment(ctx: AppCtx, identifier: str, data: Path, speed: str | None = None):
    from datatool.simulator.replay import SimulationError, simulate

    factory = ctx.session_factory()
    with session_scope(factory) as session:
        name = resolve_experiment(session, identifier).name
    try:
        return simulate(factory, name, str(data), speed=speed)
    except SimulationError as exc:
        _fail(str(exc))


def build_notifier():
    """Construct a notifier from the environment (Slack if configured, else none)."""
    import os

    from datatool.adapters.notify.slack import SlackNotificationSink

    url = os.environ.get("SLACK_WEBHOOK_URL")
    return SlackNotificationSink(url) if url else None


def build_metrics_resolver():
    """Return metrics_for(experiment, variant_ids) -> MetricsSource for the live daemon.

    Uses each experiment's goal source. Only PostHog is supported for a live daemon
    (the CSV source is replay-only); an unsupported source raises so the daemon skips
    that experiment loudly rather than silently.
    """
    import os

    from datatool.adapters.metrics.posthog import PostHogMetricsSource
    from datatool.core.exceptions import AdapterError

    def metrics_for(experiment, variant_ids):
        source = (experiment.contract.get("goal") or {}).get("source", "")
        if source == "metrics.posthog":
            return PostHogMetricsSource(
                host=os.environ["POSTHOG_HOST"],
                project_id=os.environ["POSTHOG_PROJECT_ID"],
                api_key=os.environ["POSTHOG_API_KEY"],
                variant_ids=variant_ids,
            )
        raise AdapterError(f"live daemon has no metrics adapter for goal source {source!r}")

    return metrics_for


def run_daemon(ctx: AppCtx, *, port: int, tick: int | None) -> None:
    """Configure and run the control-plane daemon (blocking)."""
    from datatool.control.daemon import run
    from datatool.observability.logging import configure_logging

    configure_logging(get_settings().log_level)
    factory = ctx.session_factory()
    flag = PostgresFlagProvider(factory)
    interval = tick or get_settings().tick_interval_seconds
    Console().print(
        f"datatool daemon started (tick {interval}s); the HTTP API on port {port} "
        f"lands in a later build step. Press Ctrl-C to stop."
    )
    run(
        factory,
        metrics_for=build_metrics_resolver(),
        flag=flag,
        notifier=build_notifier(),
        tick_interval_seconds=interval,
    )


def run_doctor_checks(ctx: AppCtx) -> list[tuple[str, bool, str]]:
    checks: list[tuple[str, bool, str]] = []

    try:
        get_settings()
        checks.append(("settings load", True, "ok"))
    except Exception as exc:
        checks.append(("settings load", False, str(exc)))

    try:
        engine = make_engine(ctx.database_url)
        with engine.connect() as connection:
            connection.exec_driver_sql("SELECT 1")
        checks.append(("database connectivity", True, engine.dialect.name))
    except Exception as exc:
        checks.append(("database connectivity", False, str(exc)))

    config_dir = Path(ctx.config_dir)
    org_defaults = config_dir / "org_defaults.yaml"
    checks.append(
        (
            "config directory",
            config_dir.exists() and org_defaults.exists(),
            str(org_defaults) if org_defaults.exists() else f"missing {org_defaults}",
        )
    )

    for adapter_id, register_fn in (
        ("flag.postgres", register_flag),
        ("metrics.csv", register_csv),
    ):
        try:
            if not registry.is_registered(adapter_id):
                register_fn()
            checks.append((f"adapter {adapter_id}", registry.is_registered(adapter_id), adapter_id))
        except Exception as exc:
            checks.append((f"adapter {adapter_id}", False, str(exc)))

    return checks
