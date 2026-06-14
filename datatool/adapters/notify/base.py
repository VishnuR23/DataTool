"""NotificationSink protocol (ARCHITECTURE.md §11.4).

A notification sink delivers a structured event — a promotion, a revert, an SRM
failure, a guardrail breach, an approval request — to wherever a team watches
(Slack, a generic webhook). The orchestrator emits events by kind and payload and
does not care how or where they are rendered.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class NotificationSink(Protocol):
    """Deliver a structured controller event to an external channel."""

    adapter_id: str

    def send(self, event_kind: str, payload: dict) -> None:
        """Deliver ``payload`` for an event of kind ``event_kind`` (e.g. 'revert')."""
        ...
