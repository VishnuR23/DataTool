from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from cloud.persistence.db import session_scope
from cloud.persistence import models as m


def test_org_and_user_persist_and_relate(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = m.Org(name="acme")
        s.add(org)
        s.flush()
        s.add(m.User(org_id=org.id, email="a@acme.test", password_hash="x"))

    with session_scope(cloud_session_factory) as s:
        user = s.scalar(select(m.User).where(m.User.email == "a@acme.test"))
        assert user is not None
        assert s.get(m.Org, user.org_id).name == "acme"
        assert s.get(m.Org, user.org_id).license_tier == "free"


def test_event_unique_constraint_is_per_org_source_sourceid(cloud_session_factory):
    import pytest
    from sqlalchemy.exc import IntegrityError

    with session_scope(cloud_session_factory) as s:
        org = m.Org(name="acme")
        s.add(org)
        s.flush()
        org_id = org.id
        s.add(m.Event(org_id=org_id, source="action", source_id="dup",
                      kind="promote", summary="x", occurred_at=datetime.now(UTC), detail={}))

    with pytest.raises(IntegrityError):
        with session_scope(cloud_session_factory) as s:
            s.add(m.Event(org_id=org_id, source="action", source_id="dup",
                          kind="promote", summary="y", occurred_at=datetime.now(UTC), detail={}))
