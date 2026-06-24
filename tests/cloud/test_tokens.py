from datetime import UTC, datetime

from cloud.accounts.tokens import issue_token, resolve_token, revoke_token
from cloud.persistence.db import session_scope
from cloud.persistence.repositories import OrgRepository


def test_issued_token_resolves_to_its_org(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        plaintext, _ = issue_token(s, org_id=org.id, label="prod")
    with session_scope(cloud_session_factory) as s:
        resolved = resolve_token(s, plaintext)
        assert resolved is not None and resolved.name == "acme"


def test_bad_token_does_not_resolve(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        assert resolve_token(s, "not-a-real-token") is None


def test_revoked_token_stops_resolving(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        plaintext, row = issue_token(s, org_id=org.id)
        token_id = row.id
    with session_scope(cloud_session_factory) as s:
        revoke_token(s, token_id, now=datetime.now(UTC))
    with session_scope(cloud_session_factory) as s:
        assert resolve_token(s, plaintext) is None


def test_plaintext_is_not_stored(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        org = OrgRepository(s).add("acme")
        plaintext, row = issue_token(s, org_id=org.id)
        assert row.token_hash != plaintext
