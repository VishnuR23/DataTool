import httpx
from pathlib import Path

from datatool.cli.commands import connect_agent


def test_connect_writes_env_on_valid_token(tmp_path, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok"
        return httpx.Response(200, json={"org": "acme"})

    # Patch the client factory the command uses so no real network call happens.
    import datatool.cli.commands as commands
    monkeypatch.setattr(
        commands, "_connect_client",
        lambda url: httpx.Client(transport=httpx.MockTransport(handler), base_url=url),
    )

    env_path = tmp_path / ".env"
    org = connect_agent(url="http://panel", token="tok", env_path=env_path)
    assert org == "acme"
    body = env_path.read_text()
    assert "DATATOOL_CLOUD_URL=http://panel" in body
    assert "DATATOOL_CLOUD_TOKEN=tok" in body


def test_connect_rejects_bad_token(tmp_path, monkeypatch):
    import typer
    import pytest
    import datatool.cli.commands as commands
    monkeypatch.setattr(
        commands, "_connect_client",
        lambda url: httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(401)), base_url=url),
    )
    with pytest.raises(typer.Exit):
        connect_agent(url="http://panel", token="bad", env_path=tmp_path / ".env")
