from fastapi.testclient import TestClient

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels


def _client(cloud_session_factory):
    app = create_app(
        cloud_session_factory,
        channels=LiveChannels(),
        settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"),
    )
    return TestClient(app)


def test_healthz_reports_ok(cloud_session_factory):
    assert _client(cloud_session_factory).get("/healthz").json() == {"status": "ok"}
