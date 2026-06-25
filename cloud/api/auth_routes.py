"""Sign-up, login, logout, and the ``current_user`` dependency (spec §"Accounts").

Auth is session-cookie based. The cookie is HttpOnly and SameSite=Lax; ``Secure``
is driven by ``settings.cookie_secure`` so it is set in production over HTTPS but
left off for local HTTP tests. ``now`` for expiry comes from the request time.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from cloud.accounts.service import EmailTakenError, authenticate, sign_up
from cloud.auth.sessions import create_session, destroy_session, resolve_session
from cloud.persistence import models as m
from cloud.persistence.db import session_scope

SESSION_COOKIE = "datatool_session"


def _set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", secure=secure, path="/"
    )


def current_user(request: Request) -> m.User:
    """Resolve the signed-in user from the session cookie, or raise 401."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "not signed in")
    factory = request.app.state.session_factory
    with session_scope(factory) as session:
        user = resolve_session(session, token, now=datetime.now(UTC))
        if user is None:
            raise HTTPException(401, "session expired")
        # detach a lightweight copy: id + org_id are all downstream needs
        session.expunge(user)
        return user


def register_auth_routes(app: FastAPI) -> None:
    settings = app.state.settings
    factory = app.state.session_factory

    @app.post("/signup")
    def signup(
        org_name: str = Form(...), email: str = Form(...), password: str = Form(...)
    ) -> Response:
        with session_scope(factory) as session:
            try:
                user = sign_up(session, org_name=org_name, email=email, password=password)
            except EmailTakenError as exc:
                raise HTTPException(409, "email already registered") from exc
            token = create_session(
                session,
                user.id,
                now=datetime.now(UTC),
                ttl_hours=settings.session_ttl_hours,
            )
        response = RedirectResponse("/", status_code=303)
        _set_session_cookie(response, token, secure=settings.cookie_secure)
        return response

    @app.post("/login")
    def login(email: str = Form(...), password: str = Form(...)) -> Response:
        with session_scope(factory) as session:
            user = authenticate(session, email=email, password=password)
            if user is None:
                raise HTTPException(401, "invalid email or password")
            token = create_session(
                session,
                user.id,
                now=datetime.now(UTC),
                ttl_hours=settings.session_ttl_hours,
            )
        response = RedirectResponse("/", status_code=303)
        _set_session_cookie(response, token, secure=settings.cookie_secure)
        return response

    @app.post("/logout")
    def logout(request: Request) -> Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            with session_scope(factory) as session:
                destroy_session(session, token)
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response
