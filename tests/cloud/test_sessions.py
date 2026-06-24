from datetime import UTC, datetime, timedelta

from cloud.auth.sessions import create_session, destroy_session, resolve_session
from cloud.persistence.db import session_scope
from cloud.persistence.repositories import OrgRepository, UserRepository


def _user(s):
    org = OrgRepository(s).add("acme")
    return UserRepository(s).add(org.id, "a@acme.test", "h")


def test_resolve_returns_user_for_live_session(cloud_session_factory):
    now = datetime(2026, 6, 22, tzinfo=UTC)
    with session_scope(cloud_session_factory) as s:
        uid = _user(s).id
        token = create_session(s, uid, now=now, ttl_hours=24)
    with session_scope(cloud_session_factory) as s:
        user = resolve_session(s, token, now=now + timedelta(hours=1))
        assert user.id == uid


def test_resolve_returns_none_for_expired_session(cloud_session_factory):
    now = datetime(2026, 6, 22, tzinfo=UTC)
    with session_scope(cloud_session_factory) as s:
        token = create_session(s, _user(s).id, now=now, ttl_hours=1)
    with session_scope(cloud_session_factory) as s:
        assert resolve_session(s, token, now=now + timedelta(hours=2)) is None


def test_destroyed_session_no_longer_resolves(cloud_session_factory):
    now = datetime(2026, 6, 22, tzinfo=UTC)
    with session_scope(cloud_session_factory) as s:
        token = create_session(s, _user(s).id, now=now, ttl_hours=24)
    with session_scope(cloud_session_factory) as s:
        destroy_session(s, token)
    with session_scope(cloud_session_factory) as s:
        assert resolve_session(s, token, now=now) is None
