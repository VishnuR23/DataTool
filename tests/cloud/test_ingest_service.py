from datetime import UTC, datetime

import pytest

from cloud.ingest.service import UnsupportedSchemaError, ingest_batch
from cloud.persistence.db import session_scope
from cloud.persistence.repositories import EventRepository, OrgRepository
from datatool.telemetry.events import TelemetryBatch, TelemetryEvent


def _batch(*source_ids, version=1):
    events = [
        TelemetryEvent(source="action", source_id=sid, kind="promote",
                       summary="s", occurred_at=datetime.now(UTC))
        for sid in source_ids
    ]
    return TelemetryBatch(schema_version=version, events=events)


def test_ingest_stores_new_events_and_dedups_replays(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        new = ingest_batch(s, org.id, _batch("a", "b"))
        assert len(new) == 2
    with session_scope(cloud_session_factory) as s:
        new = ingest_batch(s, org.id, _batch("b", "c"))
        assert [e.source_id for e in new] == ["c"]
        assert len(EventRepository(s).list_recent(org.id)) == 3


def test_unknown_schema_version_is_rejected(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        with pytest.raises(UnsupportedSchemaError):
            ingest_batch(s, org.id, _batch("a", version=999))


def test_events_are_isolated_between_orgs(cloud_session_factory):
    # Hard gate: org B must never see org A's ingested events.
    with session_scope(cloud_session_factory) as s:
        a = OrgRepository(s).add("a")
        b = OrgRepository(s).add("b")
        ingest_batch(s, a.id, _batch("secret-a"))
        b_events = EventRepository(s).list_recent(b.id)
        assert b_events == []
