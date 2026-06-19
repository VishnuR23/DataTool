"""CLI command logic and helpers (ARCHITECTURE.md §12).

The Typer commands in ``main.py`` stay thin; the real work — YAML resolution,
runtime reconstruction, orchestration, persistence — lives here and reuses the
control plane rather than reimplementing it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from sqlalchemy.orm import Session, sessionmaker

from datatool.adapters import base as registry
from datatool.adapters.flag.postgres import PostgresFlagProvider
from datatool.adapters.flag.postgres import register as register_flag
from datatool.adapters.metrics.csv import register as register_csv
from datatool.config import get_settings
from datatool.control.decision_engine import ControlDecision
from datatool.control.loader import load_effective_contract, load_runtime, net_trust_deltas
from datatool.control.orchestrator import execute
from datatool.control.persist import persist_outcome
from datatool.core.contract import resolve_contract
from datatool.core.models import DecisionKind, ExperimentSpec, State, TrustContract
from datatool.persistence import models as m
from datatool.persistence.db import make_engine, make_session_factory, session_scope
from datatool.persistence.repositories import (
    ActionRepository,
    AuditLogRepository,
    DecisionRepository,
    ExperimentRepository,
    FlagAllocationRepository,
    TrustEventRepository,
    VariantRepository,
)

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


def _now() -> datetime:
    return datetime.now(UTC)


def resolve_experiment(session: Session, identifier: str) -> m.Experiment:
    """Resolve an experiment by UUID or by name; fail clearly if absent."""
    repo = ExperimentRepository(session)
    experiment: m.Experiment | None
    try:
        experiment = repo.get(uuid.UUID(identifier))
    except ValueError:
        experiment = repo.get_by_name(identifier)
    if experiment is None:
        _fail(f"no experiment matching {identifier!r}")
    return experiment  # type: ignore[return-value]


def treatment_pct(session: Session, experiment: m.Experiment) -> float:
    row = FlagAllocationRepository(session).get(experiment.id)
    if row is None or row.killed:
        return 0.0
    return float(row.allocations.get("treatment", 0.0))


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        _fail(f"file not found: {path}")
    return yaml.safe_load(path.read_text())


def _load_yaml_optional(path: Path) -> dict | None:
    return yaml.safe_load(path.read_text()) if path.exists() else None


# --------------------------------------------------------------------------- #
# register
# --------------------------------------------------------------------------- #


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
        for variant in spec.variants:
            variants.add(
                experiment_id=experiment.id,
                name=variant.name,
                is_control=variant.is_control,
                payload=variant.payload,
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


def transition_state(ctx: AppCtx, identifier: str, target: State, audit_kind: str) -> str:
    """Apply a pure state transition (pause/resume) and audit it."""
    from datatool.core.state_machine import transition

    with session_scope(ctx.session_factory()) as session:
        experiment = resolve_experiment(session, identifier)
        current = State(experiment.state)
        try:
            new_state = transition(current, target, audit_kind)
        except Exception as exc:
            _fail(str(exc))
        ExperimentRepository(session).set_state(experiment.id, new_state.value)
        AuditLogRepository(session).add(
            kind=audit_kind,
            actor=ACTOR,
            experiment_id=experiment.id,
            payload={"from": current.value, "to": new_state.value},
        )
        return experiment.name


def _execute_manual(ctx: AppCtx, identifier: str, decision: ControlDecision) -> tuple[str, str]:
    """Reconstruct runtime, execute a manual decision, and persist the outcome."""
    factory = ctx.session_factory()
    flag = PostgresFlagProvider(factory)
    with session_scope(factory) as session:
        experiment = resolve_experiment(session, identifier)
        runtime = load_runtime(session, experiment)
        contract = load_effective_contract(session, experiment)
        try:
            result = execute(
                decision, runtime, contract, flag_provider=flag, notifier=None, now=_now()
            )
        except Exception as exc:  # illegal transition, etc.
            _fail(str(exc))
        persist_outcome(
            session, experiment.id, decision, result, adapter_id=flag.adapter_id, actor=ACTOR
        )
        if result.halt_related and result.exclusivity_group:
            AuditLogRepository(session).add(
                kind="revert.halt_related",
                actor=ACTOR,
                experiment_id=experiment.id,
                payload={"exclusivity_group": result.exclusivity_group},
            )
        return experiment.name, runtime.state.value


def revert_experiment(ctx: AppCtx, identifier: str, reason: str | None) -> str:
    decision = ControlDecision(
        kind=DecisionKind.REVERT,
        target_state=State.REVERTED,
        reason=reason or "manual revert via CLI",
        structured_reason={"actor": ACTOR, "manual": True},
    )
    name, _ = _execute_manual(ctx, identifier, decision)
    return name


def promote_experiment(ctx: AppCtx, identifier: str, force: bool) -> str:
    with session_scope(ctx.session_factory()) as session:
        experiment = resolve_experiment(session, identifier)
        if experiment.state != State.HOLDING.value and not force:
            _fail(
                f"{experiment.name} is {experiment.state}, not holding for approval; "
                f"pass --force to promote anyway"
            )
    decision = ControlDecision(
        kind=DecisionKind.PROMOTE,
        target_state=State.PROMOTING,
        reason="manual promotion via CLI",
        structured_reason={"actor": ACTOR, "manual": True},
        target_allocation_pct=100.0,
    )
    name, _ = _execute_manual(ctx, identifier, decision)
    return name


def graduate_surface(ctx: AppCtx, surface: str, by: float) -> float:
    with session_scope(ctx.session_factory()) as session:
        TrustEventRepository(session).add(
            surface=surface,
            kind="graduate",
            experiment_id=None,
            delta={"max_autonomous_pct": float(by)},
            new_state={"manual": True},
            reason=f"manual graduation {by:+g}% via CLI",
        )
        AuditLogRepository(session).add(
            kind="trust.graduated",
            actor=ACTOR,
            experiment_id=None,
            payload={"surface": surface, "by": by},
        )
        return net_trust_deltas(session, surface).get("max_autonomous_pct", 0.0)


# --------------------------------------------------------------------------- #
# why narrative + effective-contract helper
# --------------------------------------------------------------------------- #


def _compact(data: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in data.items()) if data else ""


def build_why_events(session: Session, experiment: m.Experiment) -> list[tuple]:
    events: list[tuple[Any, str, str]] = []
    for decision in DecisionRepository(session).list_for(experiment.id):
        events.append(
            (
                decision.created_at,
                f"decision:{decision.kind}",
                f"{decision.reason}. {_compact(decision.outputs)}",
            )
        )
    for action in ActionRepository(session).list_for(experiment.id):
        detail = action.adapter
        if action.clamped:
            detail += f" (clamped: {action.clamp_reason})"
        if not action.succeeded:
            detail += f" (failed: {action.error})"
        events.append((action.created_at, f"action:{action.kind}", detail))
    for entry in AuditLogRepository(session).list_for(experiment.id):
        if entry.kind == "state.transition":
            p = entry.payload
            line = f"{p.get('from')} → {p.get('to')}: {p.get('reason', '')}"
        else:
            line = _compact(entry.payload)
        events.append((entry.created_at, entry.kind, line))
    events.sort(key=lambda event: event[0])
    return events


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
