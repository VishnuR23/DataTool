import httpx

from datatool.persistence.db import session_scope
from datatool.persistence.repositories import ActionRepository, ExperimentRepository
from datatool.telemetry.cursor import FileCursor
from datatool.telemetry.reporter import TelemetryReporter


def _seed_action(session_factory):
    with session_scope(session_factory) as s:
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


def test_report_once_posts_events_and_advances_cursor(session_factory, tmp_path):
    _seed_action(session_factory)
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = request.read().decode()
        return httpx.Response(200, json={"accepted": 1})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://panel")
    cursor = FileCursor(tmp_path / "cur.json")
    reporter = TelemetryReporter(session_factory, cursor, client=client, token="tok")

    assert reporter.report_once() >= 1
    assert seen["auth"] == "Bearer tok"
    assert "promote" in seen["body"]
    assert cursor.load() != {}  # advanced


def test_report_once_does_not_advance_cursor_on_server_error(session_factory, tmp_path):
    _seed_action(session_factory)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://panel")
    cursor = FileCursor(tmp_path / "cur.json")
    reporter = TelemetryReporter(session_factory, cursor, client=client, token="tok")

    assert reporter.report_once() == 0
    assert cursor.load() == {}  # not advanced — rows remain for retry


def test_report_once_swallows_transport_errors(session_factory, tmp_path):
    _seed_action(session_factory)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://panel")
    reporter = TelemetryReporter(
        session_factory, FileCursor(tmp_path / "c.json"), client=client, token="tok"
    )
    assert reporter.report_once() == 0  # no raise


def test_report_once_with_no_new_events_returns_zero(session_factory, tmp_path):
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200)), base_url="http://panel"
    )
    reporter = TelemetryReporter(
        session_factory, FileCursor(tmp_path / "c.json"), client=client, token="tok"
    )
    assert reporter.report_once() == 0
