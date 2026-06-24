"""Org sign-up and login (spec §"Accounts + auth").

Milestone 1 is single-admin: a sign-up creates one org and one admin user.
Teammate invites and roles are Milestone 2.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from cloud.auth.passwords import hash_password, verify_password
from cloud.persistence import models as m
from cloud.persistence.repositories import OrgRepository, UserRepository


class EmailTakenError(Exception):
    """Raised when signing up with an email that already exists."""


def sign_up(session: Session, *, org_name: str, email: str, password: str) -> m.User:
    users = UserRepository(session)
    if users.get_by_email(email) is not None:
        raise EmailTakenError(email)
    org = OrgRepository(session).add(org_name)
    return users.add(org.id, email, hash_password(password))


def authenticate(session: Session, *, email: str, password: str) -> m.User | None:
    user = UserRepository(session).get_by_email(email)
    if user is None or not verify_password(user.password_hash, password):
        return None
    return user
