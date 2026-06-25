from fastapi.testclient import TestClient

from cloud.app import build_serving_app
from cloud.config import CloudSettings


def test_build_serving_app_creates_schema_and_serves(tmp_path):
    # Building from settings alone must create the panel's schema and wire the app,
    # so a fresh database can serve sign-up immediately (no separate init step).
    db = tmp_path / "panel.db"
    app = build_serving_app(CloudSettings(database_url=f"sqlite+pysqlite:///{db}"))
    c = TestClient(app)

    assert c.get("/healthz").json() == {"status": "ok"}
    r = c.post(
        "/signup",
        data={"org_name": "acme", "email": "a@acme.test", "password": "pw12345678"},
        follow_redirects=False,
    )
    assert r.status_code == 303  # schema exists + auth routes wired


def test_settings_default_to_a_distinct_panel_port():
    # The panel must not collide with the agent dashboard's 8080 by default.
    assert CloudSettings().port == 8090
