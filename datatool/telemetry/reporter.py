"""The telemetry reporter (spec §"Components", invariants 1–2).

``report_once`` tails the append-only tables, batches new events, and POSTs them
upward over an outbound-only HTTPS connection. It advances its cursor only after a
successful push, so a failed/offline push simply leaves the rows to be retried next
cycle — the append-only tables themselves are the durable offline buffer, and the
panel's dedup makes re-delivery idempotent.

``ReporterThread`` runs this on an interval in a daemon thread, fully decoupled from
the control loop: a slow or hung panel can never block a daemon tick (invariant:
"never blocks the loop"). All errors are swallowed and logged; the reporter is
strictly best-effort and read-only.
"""

from __future__ import annotations

import threading

import httpx
from sqlalchemy.orm import Session, sessionmaker

from datatool.observability.logging import get_logger
from datatool.persistence.db import session_scope
from datatool.telemetry.collector import collect_new
from datatool.telemetry.cursor import FileCursor
from datatool.telemetry.events import TelemetryBatch

_log = get_logger("telemetry.reporter")

_TIMEOUT = httpx.Timeout(5.0)  # tight: the reporter must never hang


class TelemetryReporter:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        cursor: FileCursor,
        *,
        client: httpx.Client,
        token: str,
        ingest_path: str = "/ingest",
    ):
        self._session_factory = session_factory
        self._cursor = cursor
        self._client = client
        self._token = token
        self._ingest_path = ingest_path

    def report_once(self) -> int:
        watermarks = self._cursor.load()
        with session_scope(self._session_factory) as session:
            events, advanced = collect_new(session, watermarks)
        if not events:
            return 0
        batch = TelemetryBatch(events=events)
        try:
            response = self._client.post(
                self._ingest_path,
                content=batch.model_dump_json(),
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
        except Exception:  # noqa: BLE001 — best-effort; never propagate to the loop
            _log.warning("telemetry.push_failed", count=len(events))
            return 0
        self._cursor.save(advanced)
        return len(events)


class ReporterThread:
    """Runs ``report_once`` on an interval in a background daemon thread."""

    def __init__(self, reporter: TelemetryReporter, *, interval_seconds: float):
        self._reporter = reporter
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="datatool-telemetry-reporter", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._reporter.report_once()
            except Exception:  # noqa: BLE001 — defensive; report_once already guards
                _log.exception("telemetry.reporter_tick_failed")
            self._stop.wait(self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
