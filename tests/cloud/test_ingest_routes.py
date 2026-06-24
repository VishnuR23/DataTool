from datetime import UTC, datetime

from fastapi.testclient import TestClient

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels
from datatool.telemetry.events import TelemetryBatch, TelemetryEvent


def _client_and_token(cloud_session_factory):
    app = create_app(
        cloud_session_factory,
        channels=LiveChannels(),
        settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"),
    )
    c = TestClient(app)
    c.post("/signup", data={"org_name": "acme", "email": "a@acme.test", "password": "pw12345678"})
    token = c.post("/tokens", data={"label": "prod"}).json()["token"]
    return c, token


def _batch_json():
    ev = TelemetryEvent(
        source="action",
        source_id="x1",
        kind="promote",
        summary="promoted",
        occurred_at=datetime.now(UTC),
    )
    return TelemetryBatch(events=[ev]).model_dump(mode="json")


def test_ingest_accepts_events_with_valid_token(cloud_session_factory):
    c, token = _client_and_token(cloud_session_factory)
    r = c.post("/ingest", json=_batch_json(), headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json() == {"accepted": 1}


def test_ingest_rejects_missing_or_bad_token(cloud_session_factory):
    c, _ = _client_and_token(cloud_session_factory)
    assert c.post("/ingest", json=_batch_json()).status_code == 401
    assert (
        c.post("/ingest", json=_batch_json(), headers={"Authorization": "Bearer nope"}).status_code
        == 401
    )


def test_agent_connect_returns_org_for_valid_token(cloud_session_factory):
    c, token = _client_and_token(cloud_session_factory)
    r = c.post("/agent/connect", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json() == {"org": "acme"}
