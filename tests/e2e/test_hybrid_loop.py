"""End-to-end: agent telemetry → ingest → org-isolated storage. (spec §"Milestones")"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from cloud.app import create_app
from cloud.config import CloudSettings
from cloud.ingest.channel import LiveChannels
from cloud.persistence.db import (
    init_db as cloud_init_db,
)
from cloud.persistence.db import (
    make_engine as cloud_make_engine,
)
from cloud.persistence.db import (
    make_session_factory as cloud_make_session_factory,
)
from cloud.persistence.db import (
    session_scope as cloud_scope,
)
from cloud.persistence.repositories import EventRepository, UserRepository
from datatool.persistence.db import (
    init_db,
    make_engine,
    make_session_factory,
    session_scope,
)
from datatool.persistence.repositories import ActionRepository, ExperimentRepository
from datatool.telemetry.cursor import FileCursor
from datatool.telemetry.reporter import TelemetryReporter


@pytest.fixture
def cloud_factory(tmp_path) -> sessionmaker:
    # File-backed SQLite: the panel is driven through FastAPI's TestClient, which
    # opens connections on a separate thread; an in-memory database would be
    # isolated per-connection and the panel's tables would be invisible to it.
    engine = cloud_make_engine(f"sqlite+pysqlite:///{tmp_path / 'cloud.db'}")
    cloud_init_db(engine)
    return cloud_make_session_factory(engine)


@pytest.fixture
def agent_factory() -> sessionmaker:
    engine = make_engine("sqlite+pysqlite:///:memory:")
    init_db(engine)
    return make_session_factory(engine)


def test_agent_events_reach_the_panel_scoped_to_the_signing_org(
    cloud_factory, agent_factory, tmp_path
):
    app = create_app(
        cloud_factory,
        channels=LiveChannels(),
        settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"),
    )
    panel = TestClient(app, base_url="http://panel")

    # Two orgs sign up; only org A's token is given to the agent.
    panel.post("/signup", data={"org_name": "a", "email": "a@a.test", "password": "pw12345678"})
    token_a = panel.post("/tokens", data={"label": "prod"}).json()["token"]
    panel.post("/logout")
    panel.post("/signup", data={"org_name": "b", "email": "b@b.test", "password": "pw12345678"})

    # Agent produces an action row.
    with session_scope(agent_factory) as s:
        exp = ExperimentRepository(s).add(
            name="exp", surface="checkout", owner="o", contract={}, spec={}, state="proposed"
        )
        ActionRepository(s).add(
            experiment_id=exp.id,
            decision_id=None,
            kind="promote",
            adapter="postgres",
            payload={},
            clamped=False,
            clamp_reason=None,
            succeeded=True,
            error=None,
        )

    # Reporter pushes to the panel using the TestClient as its HTTP client.
    reporter = TelemetryReporter(
        agent_factory, FileCursor(tmp_path / "cur.json"), client=panel, token=token_a
    )
    assert reporter.report_once() >= 1

    # Org A sees the event; org B sees nothing (hard gate).
    with cloud_scope(cloud_factory) as s:
        org_a = UserRepository(s).get_by_email("a@a.test").org_id
        org_b = UserRepository(s).get_by_email("b@b.test").org_id
        a_events = EventRepository(s).list_recent(org_a)
        b_events = EventRepository(s).list_recent(org_b)
    assert any(e.kind == "promote" for e in a_events)
    assert b_events == []


def test_replay_is_idempotent_on_the_panel(cloud_factory, agent_factory, tmp_path):
    app = create_app(
        cloud_factory,
        channels=LiveChannels(),
        settings=CloudSettings(database_url="sqlite+pysqlite:///:memory:"),
    )
    panel = TestClient(app, base_url="http://panel")
    panel.post("/signup", data={"org_name": "a", "email": "a@a.test", "password": "pw12345678"})
    token = panel.post("/tokens", data={"label": "x"}).json()["token"]

    with session_scope(agent_factory) as s:
        exp = ExperimentRepository(s).add(
            name="exp", surface="checkout", owner="o", contract={}, spec={}, state="proposed"
        )
        ActionRepository(s).add(
            experiment_id=exp.id,
            decision_id=None,
            kind="promote",
            adapter="postgres",
            payload={},
            clamped=False,
            clamp_reason=None,
            succeeded=True,
            error=None,
        )

    cursor = FileCursor(tmp_path / "cur.json")
    r1 = TelemetryReporter(agent_factory, cursor, client=panel, token=token)
    r1.report_once()
    # Force a re-send from scratch by resetting the cursor; panel must dedup.
    cursor.save({})
    TelemetryReporter(agent_factory, cursor, client=panel, token=token).report_once()

    with cloud_scope(cloud_factory) as s:
        org = UserRepository(s).get_by_email("a@a.test").org_id
        assert len(EventRepository(s).list_recent(org)) == 1  # no duplicate
