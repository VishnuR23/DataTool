# tests/cloud/test_console_routes.py
from fastapi.testclient import TestClient

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels


def _app(f):
    return create_app(f, channels=LiveChannels(),
                      settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"))


def test_login_page_is_public(cloud_session_factory):
    c = TestClient(_app(cloud_session_factory))
    r = c.get("/login")
    assert r.status_code == 200 and "sign in" in r.text.lower()


def test_console_redirects_anonymous_to_login(cloud_session_factory):
    c = TestClient(_app(cloud_session_factory))
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_console_renders_for_signed_in_user(cloud_session_factory):
    c = TestClient(_app(cloud_session_factory))
    c.post("/signup", data={"org_name": "acme", "email": "a@acme.test", "password": "pw12345678"})
    assert c.get("/").status_code == 200


def test_stream_requires_sign_in(cloud_session_factory):
    c = TestClient(_app(cloud_session_factory))
    assert c.get("/console/stream").status_code == 401


def test_stream_backfills_recent_events(cloud_session_factory):
    from datetime import UTC, datetime
    from cloud.persistence.db import session_scope
    from cloud.persistence.repositories import EventRepository, UserRepository
    from datatool.telemetry.events import TelemetryEvent

    c = TestClient(_app(cloud_session_factory))
    c.post("/signup", data={"org_name": "acme", "email": "a@acme.test", "password": "pw12345678"})
    with session_scope(cloud_session_factory) as s:
        org_id = UserRepository(s).get_by_email("a@acme.test").org_id
        EventRepository(s).add_batch(org_id, [TelemetryEvent(
            source="action", source_id="seed", kind="promote",
            summary="promoted checkout", occurred_at=datetime.now(UTC))])

    # Read one SSE frame from the backfill, then stop.
    with c.stream("GET", "/console/stream") as r:
        assert r.status_code == 200
        for line in r.iter_lines():
            if line.startswith("data:"):
                assert "promoted checkout" in line
                break
