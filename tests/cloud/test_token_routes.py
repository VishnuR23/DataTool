from fastapi.testclient import TestClient

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels


def _signed_in_client(f, email="a@acme.test"):
    app = create_app(
        f,
        channels=LiveChannels(),
        settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"),
    )
    c = TestClient(app)
    c.post("/signup", data={"org_name": "acme", "email": email, "password": "pw12345678"})
    return c


def test_token_creation_returns_plaintext_once_then_lists_it(cloud_session_factory):
    c = _signed_in_client(cloud_session_factory)
    created = c.post("/tokens", data={"label": "prod"})
    assert created.status_code == 201
    assert created.json()["token"]  # plaintext present
    listed = c.get("/tokens").json()["tokens"]
    assert len(listed) == 1 and listed[0]["label"] == "prod"
    assert "token" not in listed[0]  # plaintext never returned again


def test_tokens_require_sign_in(cloud_session_factory):
    app = create_app(
        cloud_session_factory,
        channels=LiveChannels(),
        settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"),
    )
    anon = TestClient(app)
    assert anon.get("/tokens").status_code == 401


def test_revoking_marks_token_revoked(cloud_session_factory):
    c = _signed_in_client(cloud_session_factory)
    tok_id = c.post("/tokens", data={"label": "x"}).json()["id"]
    assert c.post(f"/tokens/{tok_id}/revoke").json() == {"revoked": True}
    assert c.get("/tokens").json()["tokens"][0]["revoked"] is True


def test_revoking_a_malformed_token_id_is_404_not_500(cloud_session_factory):
    c = _signed_in_client(cloud_session_factory)
    assert c.post("/tokens/not-a-uuid/revoke").status_code == 404
