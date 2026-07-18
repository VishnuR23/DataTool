"""HTTP routes: health, metrics, the read-only JSON API, and admin actions (§13).

GET endpoints are open by default (lockable via ``require_auth``); the admin POST
endpoints require the API key. All reads go through the repositories; all writes go
through ``control/operations.py`` so the API and CLI behave identically.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from datatool.control import operations
from datatool.control.loader import load_effective_contract, net_trust_deltas
from datatool.core.exceptions import StateTransitionError
from datatool.observability.metrics import REGISTRY
from datatool.persistence import models as m
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    ActionRepository,
    DecisionRepository,
    ExperimentRepository,
    GuardrailEvaluationRepository,
    TrustEventRepository,
    VariantRepository,
)

# --------------------------------------------------------------------------- #
# Auth dependencies
# --------------------------------------------------------------------------- #


def _require_admin(request: Request) -> None:
    key = request.app.state.api_key
    if not key:
        raise HTTPException(503, "admin endpoints require DATATOOL_API_KEY to be set")
    if request.headers.get("x-api-key") != key:
        raise HTTPException(401, "invalid or missing API key")


def _require_read(request: Request) -> None:
    if request.app.state.require_auth:
        _require_admin(request)


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #


def _last_decision(session: Session, experiment: m.Experiment) -> dict | None:
    recent = DecisionRepository(session).list_for(experiment.id, limit=1)
    if not recent:
        return None
    d = recent[0]
    return {"kind": d.kind, "reason": d.reason, "created_at": d.created_at}


def _summary(session: Session, experiment: m.Experiment) -> dict:
    return {
        "id": str(experiment.id),
        "name": experiment.name,
        "surface": experiment.surface,
        "owner": experiment.owner,
        "state": experiment.state,
        "treatment_pct": operations.treatment_pct(session, experiment),
        "created_at": experiment.created_at,
        "updated_at": experiment.updated_at,
        "last_decision": _last_decision(session, experiment),
    }


def _detail(session: Session, experiment: m.Experiment) -> dict:
    contract = load_effective_contract(session, experiment)
    return {
        **_summary(session, experiment),
        "variants": [
            {"name": v.name, "is_control": v.is_control, "payload": v.payload}
            for v in VariantRepository(session).list_for(experiment.id)
        ],
        "contract": contract.model_dump(mode="json"),
        "trust": {
            "clean_promotions": TrustEventRepository(session).count(
                experiment.surface, "clean_promotion"
            ),
            "false_positive_ships": TrustEventRepository(session).count(
                experiment.surface, "false_positive_ship"
            ),
        },
    }


def _resolve_or_404(session: Session, ident: str) -> m.Experiment:
    experiment = operations.resolve_experiment(session, ident)
    if experiment is None:
        raise HTTPException(404, f"no experiment matching {ident!r}")
    return experiment


def _run_op(call):
    try:
        return call()
    except operations.ExperimentNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except (operations.OperationError, StateTransitionError) as exc:
        raise HTTPException(409, str(exc)) from exc


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


def register_api_routes(app: FastAPI) -> None:
    health = APIRouter()

    @health.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @health.get("/readyz")
    def readyz(request: Request) -> dict:
        try:
            with session_scope(request.app.state.session_factory) as session:
                session.execute(text("SELECT 1"))
        except Exception as exc:
            raise HTTPException(503, f"database not ready: {exc}") from exc
        return {"status": "ready"}

    @health.get("/metrics")
    def metrics() -> Response:
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    api = APIRouter(prefix="/api", dependencies=[Depends(_require_read)])

    @api.get("/experiments")
    def list_experiments(
        request: Request,
        state: str | None = None,
        surface: str | None = None,
    ) -> list[dict]:
        with session_scope(request.app.state.session_factory) as session:
            experiments = ExperimentRepository(session).list(state=state, surface=surface)
            return [_summary(session, e) for e in experiments]

    @api.get("/experiments/{ident}")
    def get_experiment(request: Request, ident: str) -> dict:
        with session_scope(request.app.state.session_factory) as session:
            return _detail(session, _resolve_or_404(session, ident))

    @api.get("/experiments/{ident}/decisions")
    def get_decisions(
        request: Request, ident: str, limit: int = Query(50, ge=1, le=500)
    ) -> list[dict]:
        with session_scope(request.app.state.session_factory) as session:
            experiment = _resolve_or_404(session, ident)
            return [
                {
                    "kind": d.kind,
                    "reason": d.reason,
                    "outputs": d.outputs,
                    "created_at": d.created_at,
                }
                for d in DecisionRepository(session).list_for(experiment.id, limit=limit)
            ]

    @api.get("/experiments/{ident}/actions")
    def get_actions(
        request: Request, ident: str, limit: int = Query(50, ge=1, le=500)
    ) -> list[dict]:
        with session_scope(request.app.state.session_factory) as session:
            experiment = _resolve_or_404(session, ident)
            return [
                {
                    "kind": a.kind,
                    "adapter": a.adapter,
                    "clamped": a.clamped,
                    "clamp_reason": a.clamp_reason,
                    "succeeded": a.succeeded,
                    "error": a.error,
                    "created_at": a.created_at,
                }
                for a in ActionRepository(session).list_for(experiment.id, limit=limit)
            ]

    @api.get("/experiments/{ident}/guardrails")
    def get_guardrails(
        request: Request, ident: str, limit: int = Query(50, ge=1, le=500)
    ) -> list[dict]:
        with session_scope(request.app.state.session_factory) as session:
            experiment = _resolve_or_404(session, ident)
            return [
                {
                    "guardrail": g.guardrail_name,
                    "breached": g.breached,
                    "value": g.value,
                    "threshold": g.threshold,
                    "severity": g.severity,
                    "consecutive": g.consecutive,
                    "created_at": g.created_at,
                }
                for g in GuardrailEvaluationRepository(session).list_for(experiment.id, limit=limit)
            ]

    @api.get("/surfaces/{surface}/ledger")
    def get_ledger(request: Request, surface: str) -> dict:
        with session_scope(request.app.state.session_factory) as session:
            events = TrustEventRepository(session).list_for_surface(surface)
            return {
                "surface": surface,
                "net_max_autonomous_pct_delta": net_trust_deltas(session, surface).get(
                    "max_autonomous_pct", 0.0
                ),
                "events": [
                    {
                        "kind": e.kind,
                        "delta": e.delta,
                        "reason": e.reason,
                        "created_at": e.created_at,
                    }
                    for e in events
                ],
            }

    admin = APIRouter(prefix="/api", dependencies=[Depends(_require_admin)])

    @admin.post("/experiments/{ident}/pause")
    def pause(request: Request, ident: str) -> dict:
        factory = request.app.state.session_factory
        name = _run_op(lambda: operations.pause(factory, ident, actor="api"))
        return {"experiment": name, "state": "holding"}

    @admin.post("/experiments/{ident}/promote")
    def promote(request: Request, ident: str, force: bool = False) -> dict:
        factory = request.app.state.session_factory
        name = _run_op(lambda: operations.promote(factory, ident, force=force, actor="api"))
        return {"experiment": name, "state": "promoted"}

    @admin.post("/experiments/{ident}/revert")
    def revert(request: Request, ident: str, reason: str | None = None) -> dict:
        factory = request.app.state.session_factory
        name = _run_op(lambda: operations.revert(factory, ident, reason, actor="api"))
        return {"experiment": name, "state": "reverted"}

    app.include_router(health)
    app.include_router(api)
    app.include_router(admin)
