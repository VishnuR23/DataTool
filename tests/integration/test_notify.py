"""Notification sinks: webhook and Slack (ARCHITECTURE.md §11.4).

HTTP is mocked with httpx.MockTransport so the tests assert exactly what would be
sent and how failures are surfaced, without a real network call.
"""

from __future__ import annotations

import json

import httpx
import pytest

from datatool.adapters import base as registry
from datatool.adapters.notify.base import NotificationSink
from datatool.adapters.notify.slack import SlackNotificationSink
from datatool.adapters.notify.slack import register as register_slack
from datatool.adapters.notify.webhook import WebhookNotificationSink
from datatool.adapters.notify.webhook import register as register_webhook
from datatool.core.exceptions import AdapterError

_REVERT_PAYLOAD = {
    "experiment": "pricing-headline",
    "surface": "pricing-page",
    "reason": "goal.lost",
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_webhook_and_slack_satisfy_the_protocol():
    assert isinstance(
        WebhookNotificationSink("https://x", client=_client(lambda r: httpx.Response(200))),
        NotificationSink,
    )
    assert isinstance(
        SlackNotificationSink("https://x", client=_client(lambda r: httpx.Response(200))),
        NotificationSink,
    )


def test_webhook_posts_event_and_payload_as_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["method"] = request.method
        seen["body"] = json.loads(request.content)
        return httpx.Response(200)

    sink = WebhookNotificationSink("https://hooks.example.com/exp", client=_client(handler))
    sink.send("revert", _REVERT_PAYLOAD)

    assert seen["url"] == "https://hooks.example.com/exp"
    assert seen["method"] == "POST"
    assert seen["body"]["event"] == "revert"
    assert seen["body"]["experiment"] == "pricing-headline"


def test_webhook_raises_on_error_status():
    sink = WebhookNotificationSink("https://x", client=_client(lambda r: httpx.Response(500)))
    with pytest.raises(AdapterError):
        sink.send("revert", _REVERT_PAYLOAD)


def test_webhook_raises_on_transport_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    sink = WebhookNotificationSink("https://x", client=_client(handler))
    with pytest.raises(AdapterError):
        sink.send("revert", _REVERT_PAYLOAD)


def test_webhook_requires_url():
    with pytest.raises(AdapterError):
        WebhookNotificationSink("")


def test_slack_formats_message_blocks():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200)

    sink = SlackNotificationSink("https://hooks.slack.com/x", client=_client(handler))
    sink.send("promote", {"experiment": "exp-1", "surface": "home", "reason": "decisive win"})

    body = seen["body"]
    assert "blocks" in body
    assert "promote" in body["text"]
    assert "exp-1" in body["text"]
    assert ":rocket:" in body["text"]


def test_slack_raises_on_error_status():
    sink = SlackNotificationSink("https://x", client=_client(lambda r: httpx.Response(404)))
    with pytest.raises(AdapterError):
        sink.send("promote", {"experiment": "e", "surface": "s", "reason": "r"})


def test_register_adds_factories():
    registry.clear_registry()
    try:
        register_webhook()
        register_slack()
        assert registry.get_adapter_factory("notify.webhook") is WebhookNotificationSink
        assert registry.get_adapter_factory("notify.slack") is SlackNotificationSink
    finally:
        registry.clear_registry()
