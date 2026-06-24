from datetime import UTC, datetime, timedelta

from cloud.persistence.db import session_scope
from cloud.persistence.repositories import (
    EnrollmentTokenRepository,
    EventRepository,
    OrgRepository,
    SessionRepository,
    UserRepository,
)
from datatool.telemetry.events import TelemetryEvent


def _event(source_id: str) -> TelemetryEvent:
    return TelemetryEvent(
        source="action", source_id=source_id, kind="promote",
        summary="s", occurred_at=datetime.now(UTC),
    )


def test_add_batch_skips_already_seen_source_ids(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        first = EventRepository(s).add_batch(org.id, [_event("a"), _event("b")])
        assert len(first) == 2

    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).get(org.id)
        second = EventRepository(s).add_batch(org.id, [_event("b"), _event("c")])
        assert [e.source_id for e in second] == ["c"]
        assert len(EventRepository(s).list_recent(org.id)) == 3


def test_list_recent_only_returns_one_orgs_events(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        a = OrgRepository(s).add("a")
        b = OrgRepository(s).add("b")
        EventRepository(s).add_batch(a.id, [_event("a1")])
        EventRepository(s).add_batch(b.id, [_event("b1")])
        a_ids = {e.source_id for e in EventRepository(s).list_recent(a.id)}
        assert a_ids == {"a1"}


def test_active_token_lookup_excludes_revoked(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        tok = EnrollmentTokenRepository(s).add(org.id, "hash1", "laptop")
        assert EnrollmentTokenRepository(s).get_active_by_hash("hash1").id == tok.id
        EnrollmentTokenRepository(s).revoke(tok.id, datetime.now(UTC))
    with session_scope(cloud_session_factory) as s:
        assert EnrollmentTokenRepository(s).get_active_by_hash("hash1") is None


def test_session_resolves_and_deletes(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        user = UserRepository(s).add(org.id, "a@acme.test", "h")
        SessionRepository(s).add("tok", user.id, datetime.now(UTC) + timedelta(hours=1))
    with session_scope(cloud_session_factory) as s:
        assert SessionRepository(s).get("tok").user_id == user.id
        SessionRepository(s).delete("tok")
    with session_scope(cloud_session_factory) as s:
        assert SessionRepository(s).get("tok") is None
