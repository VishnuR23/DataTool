"""Manual lifecycle operations — the shared service layer (ARCHITECTURE.md §12, §13).

The CLI, the read-only HTTP API, and the terminal assistant all need the same
lifecycle actions: register, pause, resume, revert, promote, graduate, and the read
queries behind ``show``/``why`` and the console. Those live here, keyed off a session
factory and raising domain errors, so there is exactly one implementation. The CLI
translates these errors to friendly exits; the API to HTTP status codes; the assistant
surfaces them as tool results.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from datatool.adapters.flag.postgres import PostgresFlagProvider
from datatool.control.decision_engine import ControlDecision
from datatool.control.loader import load_effective_contract, load_runtime, net_trust_deltas
from datatool.control.orchestrator import execute
from datatool.control.persist import persist_outcome
from datatool.core.exceptions import DataToolError
from datatool.core.models import DecisionKind, State
from datatool.core.state_machine import transition
from datatool.persistence import models as m
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    ActionRepository,
    AuditLogRepository,
    DecisionRepository,
    ExperimentRepository,
    FlagAllocationRepository,
    TrustEventRepository,
)


class OperationError(DataToolError):
    """A requested manual operation could not be performed."""


class ExperimentNotFound(OperationError):
    """No experiment matched the given name or id."""


def resolve_experiment(session: Session, identifier: str) -> m.Experiment | None:
    """Resolve an experiment by UUID or by name; return None if absent."""
    repo = ExperimentRepository(session)
    try:
        return repo.get(uuid.UUID(str(identifier)))
    except ValueError:
        return repo.get_by_name(identifier)


def _require(session: Session, identifier: str) -> m.Experiment:
    experiment = resolve_experiment(session, identifier)
    if experiment is None:
        raise ExperimentNotFound(f"no experiment matching {identifier!r}")
    return experiment


def treatment_pct(session: Session, experiment: m.Experiment) -> float:
    row = FlagAllocationRepository(session).get(experiment.id)
    if row is None or row.killed:
        return 0.0
    return float(row.allocations.get("treatment", 0.0))


def _compact(data: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in data.items()) if data else ""


def decision_log(session: Session, experiment: m.Experiment) -> list[tuple]:
    """Chronological (timestamp, kind, line) narrative from decisions/actions/audit."""
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


# --------------------------------------------------------------------------- #
# Mutating operations
# --------------------------------------------------------------------------- #


def _transition(
    session_factory: sessionmaker[Session],
    identifier: str,
    target: State,
    audit_kind: str,
    actor: str,
) -> str:
    with session_scope(session_factory) as session:
        experiment = _require(session, identifier)
        current = State(experiment.state)
        new_state = transition(current, target, audit_kind)  # raises StateTransitionError
        ExperimentRepository(session).set_state(experiment.id, new_state.value)
        AuditLogRepository(session).add(
            kind=audit_kind,
            actor=actor,
            experiment_id=experiment.id,
            payload={"from": current.value, "to": new_state.value},
        )
        return experiment.name


def pause(session_factory: sessionmaker[Session], identifier: str, *, actor: str = "cli") -> str:
    return _transition(session_factory, identifier, State.HOLDING, "experiment.paused", actor)


def resume(session_factory: sessionmaker[Session], identifier: str, *, actor: str = "cli") -> str:
    return _transition(session_factory, identifier, State.RAMPING, "experiment.resumed", actor)


def _execute_manual(
    session_factory: sessionmaker[Session], identifier: str, decision: ControlDecision, actor: str
) -> str:
    flag = PostgresFlagProvider(session_factory)
    with session_scope(session_factory) as session:
        experiment = _require(session, identifier)
        runtime = load_runtime(session, experiment)
        contract = load_effective_contract(session, experiment)
        result = execute(
            decision, runtime, contract, flag_provider=flag, notifier=None, now=datetime.now(UTC)
        )
        persist_outcome(
            session, experiment.id, decision, result, adapter_id=flag.adapter_id, actor=actor
        )
        if result.halt_related and result.exclusivity_group:
            AuditLogRepository(session).add(
                kind="revert.halt_related",
                actor=actor,
                experiment_id=experiment.id,
                payload={"exclusivity_group": result.exclusivity_group},
            )
        return experiment.name


def revert(
    session_factory: sessionmaker[Session],
    identifier: str,
    reason: str | None = None,
    *,
    actor: str = "cli",
) -> str:
    decision = ControlDecision(
        kind=DecisionKind.REVERT,
        target_state=State.REVERTED,
        reason=reason or "manual revert",
        structured_reason={"actor": actor, "manual": True},
    )
    return _execute_manual(session_factory, identifier, decision, actor)


def promote(
    session_factory: sessionmaker[Session],
    identifier: str,
    *,
    force: bool = False,
    actor: str = "cli",
) -> str:
    with session_scope(session_factory) as session:
        experiment = _require(session, identifier)
        if experiment.state != State.HOLDING.value and not force:
            raise OperationError(
                f"{experiment.name} is {experiment.state}, not holding for approval; "
                f"force to promote anyway"
            )
    decision = ControlDecision(
        kind=DecisionKind.PROMOTE,
        target_state=State.PROMOTING,
        reason="manual promotion",
        structured_reason={"actor": actor, "manual": True},
        target_allocation_pct=100.0,
    )
    return _execute_manual(session_factory, identifier, decision, actor)


def _load_yaml_optional(path) -> dict | None:
    from pathlib import Path

    import yaml

    p = Path(path)
    return yaml.safe_load(p.read_text()) if p.exists() else None


def _materialize_variant(variant_spec, *, contract, factory) -> dict:
    """Materialize a variant's payload via the source that owns it (§11.3).

    Static/existing go through the static source; ``llm`` variants are generated (and
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


def register(
    session_factory: sessionmaker[Session],
    *,
    data: dict,
    config_dir: str,
    actor: str = "cli",
) -> str:
    """Register an experiment from a parsed definition dict (§7, §12).

    The single implementation behind the CLI ``register`` command and the assistant's
    ``register`` tool: it layers org/surface defaults under the definition's contract,
    materializes each variant (which may call an LLM and enforce scope), then writes the
    experiment, its variants, and an audit row. Raises :class:`OperationError` on any
    problem so the caller (CLI or assistant) can render it — never leaving a
    half-registered experiment (variants are materialized before any write).
    """
    from pathlib import Path

    from datatool.core.contract import resolve_contract
    from datatool.core.models import ExperimentSpec
    from datatool.persistence.repositories import VariantRepository

    try:
        surface = data["surface"]
        org = _load_yaml_optional(Path(config_dir) / "org_defaults.yaml")
        surface_layer = _load_yaml_optional(Path(config_dir) / "surfaces" / f"{surface}.yaml")
        contract = resolve_contract(org, surface_layer, data.get("contract"))
        spec = ExperimentSpec(
            name=data["experiment"],
            surface=surface,
            owner=data["owner"],
            description=data.get("description"),
            variants=data["variants"],
            contract=contract,
        )
    except KeyError as exc:
        raise OperationError(f"experiment definition missing required field {exc}") from exc
    except Exception as exc:  # contract/spec validation
        raise OperationError(f"invalid experiment definition: {exc}") from exc

    materialized: list[tuple] = []
    for variant in spec.variants:
        try:
            materialized.append(
                (variant, _materialize_variant(variant, contract=contract, factory=session_factory))
            )
        except Exception as exc:  # AdapterError from materialization
            raise OperationError(f"could not materialize variant {variant.name!r}: {exc}") from exc

    with session_scope(session_factory) as session:
        repo = ExperimentRepository(session)
        if repo.get_by_name(spec.name) is not None:
            raise OperationError(f"experiment {spec.name!r} is already registered")
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
            actor=actor,
            experiment_id=experiment.id,
            payload={"surface": surface, "owner": spec.owner},
        )
        experiment_id = experiment.id

    # Initial allocation: everyone on control until the experiment is started.
    PostgresFlagProvider(session_factory).set_allocation(
        experiment_id, {"control": 100.0, "treatment": 0.0}
    )
    return spec.name


def graduate(
    session_factory: sessionmaker[Session], surface: str, by: float, *, actor: str = "cli"
) -> float:
    with session_scope(session_factory) as session:
        TrustEventRepository(session).add(
            surface=surface,
            kind="graduate",
            experiment_id=None,
            delta={"max_autonomous_pct": float(by)},
            new_state={"manual": True},
            reason=f"manual graduation {by:+g}%",
        )
        AuditLogRepository(session).add(
            kind="trust.graduated",
            actor=actor,
            experiment_id=None,
            payload={"surface": surface, "by": by},
        )
        return net_trust_deltas(session, surface).get("max_autonomous_pct", 0.0)
