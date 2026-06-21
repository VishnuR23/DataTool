"""HTTP API: health, metrics, read endpoints, admin actions, auth (§13)."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from datatool.api.app import create_app
from datatool.core.models import (
    Allocation,
    Authorization,
    Goal,
    Graduation,
    ReversionPolicy,
    Scope,
    State,
    Statistics,
    TrustContract,
)
from datatool.persistence.db import init_db, make_engine, make_session_factory, session_scope
from datatool.persistence.repositories import (
    ActionRepository,
    DecisionRepository,
    ExperimentRepository,
    FlagAllocationRepository,
    GuardrailEvaluationRepository,
    TrustEventRepository,
    VariantRepository,
)


@pytest.fixture
def factory(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'api.db'}"
    init_db(make_engine(url))
    return make_session_factory(make_engine(url))


def _contract() -> TrustContract:
    return TrustContract(
        scope=Scope(assignment_unit="user"),
        allocation=Allocation(ramp_schedule=[1, 5, 25, 50], max_autonomous_pct=50.0),
        guardrails=[],
        goal=Goal(source="metrics.csv", metric="conversion", direction="increase"),
        statistics=Statistics(min_runtime=timedelta(hours=1)),
        reversion=ReversionPolicy(),
        graduation=Graduation(),
        authorization=Authorization(),
    )


def _seed(
    factory, *, name="exp", surface="checkout", state="ramping", treatment=5.0, history=False
) -> UUID:
    with session_scope(factory) as s:
        exp = ExperimentRepository(s).add(
            name=name,
            surface=surface,
            owner="growth",
            contract=_contract().model_dump(mode="json"),
            spec={},
            state=state,
        )
        vr = VariantRepository(s)
        vr.add(experiment_id=exp.id, name="control", is_control=True, payload={})
        vr.add(experiment_id=exp.id, name="treatment", is_control=False, payload={"ref": "v2"})
        eid = exp.id
    with session_scope(factory) as s:
        FlagAllocationRepository(s).upsert(
            eid, {"control": 100.0 - treatment, "treatment": treatment}
        )
        if history:
            for point in (0.01, 0.03):
                DecisionRepository(s).add(
                    experiment_id=eid,
                    kind="ramp",
                    reason="cs decisive",
                    inputs={},
                    outputs={"cs_lower": 0.0, "cs_point_estimate": point},
                )
            ActionRepository(s).add(
                experiment_id=eid,
                decision_id=None,
                kind="allocate",
                adapter="flag.postgres",
                payload={},
                clamped=True,
                clamp_reason="ceiling",
                succeeded=True,
                error=None,
            )
            GuardrailEvaluationRepository(s).add(
                experiment_id=eid,
                guardrail_name="error_rate",
                breached=False,
                value=0.04,
                threshold=0.2,
                severity="critical",
                consecutive=0,
            )
            TrustEventRepository(s).add(
                surface=surface,
                kind="clean_promotion",
                experiment_id=eid,
                delta=None,
                new_state={},
                reason="ok",
            )
    return eid


def _client(factory, **kwargs) -> TestClient:
    return TestClient(create_app(factory, **kwargs))


# --------------------------------------------------------------------------- #
# Health + metrics
# --------------------------------------------------------------------------- #


def test_healthz(factory):
    assert _client(factory).get("/healthz").json() == {"status": "ok"}


def test_readyz_reports_db_connectivity(factory):
    r = _client(factory).get("/readyz")
    assert r.status_code == 200 and r.json()["status"] == "ready"


def test_metrics_exposes_prometheus_text(factory):
    r = _client(factory).get("/metrics")
    assert r.status_code == 200
    assert b"datatool_" in r.content


# --------------------------------------------------------------------------- #
# Read API
# --------------------------------------------------------------------------- #


def test_list_experiments(factory):
    _seed(factory, name="exp-a")
    rows = _client(factory).get("/api/experiments").json()
    assert any(e["name"] == "exp-a" and e["state"] == "ramping" for e in rows)


def test_get_experiment_detail_by_name_and_id(factory):
    eid = _seed(factory, name="exp-d")
    client = _client(factory)
    by_name = client.get("/api/experiments/exp-d").json()
    assert by_name["surface"] == "checkout"
    assert {v["name"] for v in by_name["variants"]} == {"control", "treatment"}
    assert by_name["contract"]["allocation"]["max_autonomous_pct"] == 50.0
    by_id = client.get(f"/api/experiments/{eid}").json()
    assert by_id["id"] == str(eid)


def test_unknown_experiment_returns_404(factory):
    assert _client(factory).get("/api/experiments/nope").status_code == 404


def test_decisions_actions_guardrails_endpoints(factory):
    _seed(factory, name="exp-h", history=True)
    client = _client(factory)
    assert client.get("/api/experiments/exp-h/decisions").json()[0]["kind"] == "ramp"
    assert client.get("/api/experiments/exp-h/actions").json()[0]["clamped"] is True
    assert client.get("/api/experiments/exp-h/guardrails").json()[0]["guardrail"] == "error_rate"


def test_surface_ledger_endpoint(factory):
    _seed(factory, name="exp-l", surface="pricing", history=True)
    body = _client(factory).get("/api/surfaces/pricing/ledger").json()
    assert body["surface"] == "pricing"
    assert any(e["kind"] == "clean_promotion" for e in body["events"])


# --------------------------------------------------------------------------- #
# Admin actions + auth
# --------------------------------------------------------------------------- #


def test_admin_pause_requires_api_key_to_be_configured(factory):
    _seed(factory, name="exp-p", state="ramping")
    # No api_key configured -> admin endpoints are 503.
    assert _client(factory).post("/api/experiments/exp-p/pause").status_code == 503


def test_admin_pause_rejects_wrong_key(factory):
    _seed(factory, name="exp-p", state="ramping")
    client = _client(factory, api_key="secret")
    assert (
        client.post("/api/experiments/exp-p/pause", headers={"x-api-key": "wrong"}).status_code
        == 401
    )


def test_admin_pause_promote_revert_with_key(factory):
    _seed(factory, name="exp-x", state="holding", treatment=5.0)
    client = _client(factory, api_key="secret")
    headers = {"x-api-key": "secret"}

    promoted = client.post("/api/experiments/exp-x/promote", headers=headers)
    assert promoted.status_code == 200 and promoted.json()["state"] == "promoted"
    with session_scope(factory) as s:
        assert ExperimentRepository(s).get_by_name("exp-x").state == State.PROMOTED.value

    reverted = client.post(
        "/api/experiments/exp-x/revert", headers=headers, params={"reason": "rollback"}
    )
    assert reverted.status_code == 200 and reverted.json()["state"] == "reverted"


def test_admin_promote_off_holding_conflicts(factory):
    _seed(factory, name="exp-r", state="ramping")
    client = _client(factory, api_key="secret")
    r = client.post("/api/experiments/exp-r/promote", headers={"x-api-key": "secret"})
    assert r.status_code == 409  # not holding, no force


def test_read_endpoints_locked_when_require_auth(factory):
    _seed(factory, name="exp-a")
    client = _client(factory, api_key="secret", require_auth=True)
    assert client.get("/api/experiments").status_code == 401
    assert client.get("/api/experiments", headers={"x-api-key": "secret"}).status_code == 200


# --------------------------------------------------------------------------- #
# Dashboard (server-rendered HTML)
# --------------------------------------------------------------------------- #


def test_dashboard_index_lists_experiments(factory):
    _seed(factory, name="exp-dash")
    r = _client(factory).get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "exp-dash" in r.text


def test_dashboard_detail_renders_log_and_sparklines(factory):
    _seed(factory, name="exp-detail", history=True)
    r = _client(factory).get("/experiments/exp-detail")
    assert r.status_code == 200
    assert "exp-detail" in r.text
    assert "decision log" in r.text
    assert "<svg" in r.text  # goal sparkline from the seeded cs_point_estimate series


def test_dashboard_detail_unknown_returns_404(factory):
    assert _client(factory).get("/experiments/nope").status_code == 404
