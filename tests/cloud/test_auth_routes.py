from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels


def _client(f: sessionmaker[Session]) -> TestClient:
    app = create_app(f, channels=LiveChannels(),
                     settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:",
                                           cookie_secure=False))
    return TestClient(app)


def test_signup_sets_session_cookie(cloud_session_factory: sessionmaker[Session]) -> None:
    c = _client(cloud_session_factory)
    r = c.post("/signup", data={"org_name": "acme", "email": "a@acme.test",
                                "password": "pw12345678"}, follow_redirects=False)
    assert r.status_code == 303
    assert "datatool_session" in r.cookies


def test_login_with_bad_password_is_401(cloud_session_factory: sessionmaker[Session]) -> None:
    c = _client(cloud_session_factory)
    c.post("/signup", data={"org_name": "acme", "email": "a@acme.test",
                            "password": "pw12345678"})
    r = c.post("/login", data={"email": "a@acme.test", "password": "wrong"},
               follow_redirects=False)
    assert r.status_code == 401


def test_duplicate_signup_is_409(cloud_session_factory: sessionmaker[Session]) -> None:
    c = _client(cloud_session_factory)
    data = {"org_name": "acme", "email": "a@acme.test", "password": "pw12345678"}
    c.post("/signup", data=data)
    assert c.post("/signup", data=data, follow_redirects=False).status_code == 409
