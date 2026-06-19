"""Generic webhook notification sink (ARCHITECTURE.md §11.4).

POSTs each event as a JSON body to a configured URL — the lowest-common-denominator
integration for piping controller events into any system that accepts a webhook.
"""

from __future__ import annotations

import httpx

from datatool.adapters import base as registry
from datatool.core.exceptions import AdapterError

ADAPTER_ID = "notify.webhook"


class WebhookNotificationSink:
    """NotificationSink that POSTs a JSON body to a fixed URL."""

    adapter_id = ADAPTER_ID

    def __init__(self, url: str, *, client: httpx.Client | None = None, timeout: float = 10.0):
        if not url:
            raise AdapterError("webhook URL is required")
        self._url = url
        self._client = client or httpx.Client(timeout=timeout)

    def send(self, event_kind: str, payload: dict) -> None:
        body = {"event": event_kind, **payload}
        try:
            response = self._client.post(self._url, json=body)
        except httpx.HTTPError as exc:
            raise AdapterError(f"webhook POST to {self._url} failed: {exc}") from exc
        if response.status_code >= 400:
            raise AdapterError(
                f"webhook POST to {self._url} returned {response.status_code}: "
                f"{response.text[:200]}"
            )


def register() -> None:
    """Register the webhook sink factory under ``notify.webhook``."""
    registry.register(ADAPTER_ID, WebhookNotificationSink)
