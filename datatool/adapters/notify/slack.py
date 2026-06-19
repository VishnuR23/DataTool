"""Slack notification sink (ARCHITECTURE.md §11.4).

Posts a formatted Slack message to an incoming-webhook URL for the events a team
wants to see — promotions, reverts, SRM failures, guardrail breaches, approval
requests. The webhook URL comes from ``SLACK_WEBHOOK_URL`` in production.
"""

from __future__ import annotations

import httpx

from datatool.adapters import base as registry
from datatool.core.exceptions import AdapterError

ADAPTER_ID = "notify.slack"

# A leading emoji per event kind, for at-a-glance triage in the channel.
_EMOJI = {
    "promote": ":rocket:",
    "revert": ":rotating_light:",
    "srm": ":warning:",
    "guardrail_breach": ":warning:",
    "approval_requested": ":raising_hand:",
}


def _format_message(event_kind: str, payload: dict) -> dict:
    emoji = _EMOJI.get(event_kind, ":information_source:")
    experiment = payload.get("experiment", "?")
    surface = payload.get("surface", "?")
    reason = payload.get("reason", "")
    text = f"{emoji} *{event_kind}* — {experiment} (surface: {surface}): {reason}"
    return {
        "text": text,
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}],
    }


class SlackNotificationSink:
    """NotificationSink that posts formatted messages to a Slack incoming webhook."""

    adapter_id = ADAPTER_ID

    def __init__(
        self, webhook_url: str, *, client: httpx.Client | None = None, timeout: float = 10.0
    ):
        if not webhook_url:
            raise AdapterError("Slack webhook URL is required")
        self._url = webhook_url
        self._client = client or httpx.Client(timeout=timeout)

    def send(self, event_kind: str, payload: dict) -> None:
        try:
            response = self._client.post(self._url, json=_format_message(event_kind, payload))
        except httpx.HTTPError as exc:
            raise AdapterError(f"Slack POST failed: {exc}") from exc
        if response.status_code >= 400:
            raise AdapterError(f"Slack POST returned {response.status_code}: {response.text[:200]}")


def register() -> None:
    """Register the Slack sink factory under ``notify.slack``."""
    registry.register(ADAPTER_ID, SlackNotificationSink)
