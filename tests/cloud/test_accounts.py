import pytest

from cloud.accounts.service import EmailTakenError, authenticate, sign_up
from cloud.persistence.db import session_scope


def test_sign_up_creates_org_and_admin_then_authenticates(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        user = sign_up(s, org_name="acme", email="a@acme.test", password="pw12345678")
        assert user.org_id is not None
    with session_scope(cloud_session_factory) as s:
        assert authenticate(s, email="a@acme.test", password="pw12345678") is not None
        assert authenticate(s, email="a@acme.test", password="wrong") is None


def test_duplicate_email_is_rejected(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        sign_up(s, org_name="acme", email="a@acme.test", password="pw12345678")
    with session_scope(cloud_session_factory) as s:
        with pytest.raises(EmailTakenError):
            sign_up(s, org_name="other", email="a@acme.test", password="pw12345678")


def test_authenticate_unknown_email_returns_none(cloud_session_factory):
    with session_scope(cloud_session_factory) as s:
        assert authenticate(s, email="nobody@acme.test", password="x") is None
