"""`datatool daemon` binds its HTTP API to loopback unless told otherwise."""

from __future__ import annotations

import pytest
import uvicorn

import datatool.control.daemon as daemon_module
from datatool.config import get_settings


@pytest.fixture
def served(monkeypatch):
    calls: dict = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.update(kw))
    monkeypatch.setattr(daemon_module, "run", lambda *a, **kw: None)  # no control loop
    get_settings.cache_clear()
    yield calls
    get_settings.cache_clear()


def test_daemon_listens_on_loopback_by_default(cli_env, served, monkeypatch):
    monkeypatch.delenv("DATATOOL_API_HOST", raising=False)
    result = cli_env.invoke("daemon", "--port", "9999")
    assert result.exit_code == 0, result.output
    assert served["host"] == "127.0.0.1" and served["port"] == 9999
    assert "http://127.0.0.1:9999" in result.output


def test_daemon_host_can_be_widened_by_flag_or_env(cli_env, served, monkeypatch):
    assert cli_env.invoke("daemon", "--host", "0.0.0.0").exit_code == 0
    assert served["host"] == "0.0.0.0"

    monkeypatch.setenv("DATATOOL_API_HOST", "10.0.0.5")
    get_settings.cache_clear()
    assert cli_env.invoke("daemon").exit_code == 0
    assert served["host"] == "10.0.0.5"
