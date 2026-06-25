"""In-process per-org pub/sub for the live console (spec §"Live streaming").

Each browser SSE connection subscribes and gets its own ``asyncio.Queue``; ingest
publishes a serialized event to every queue for that org. Isolation is structural:
a publish only ever touches the publishing org's queue set.

DECISION: single-process in-memory fan-out for Milestone 1. A multi-process panel
would need a shared broker (e.g. Redis pub/sub); that is deferred. ``publish`` is
non-blocking — a full queue drops the event for that one slow subscriber rather
than blocking ingest (the durable store remains the source of truth on reconnect).
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict

_MAX_QUEUE = 1000


class LiveChannels:
    def __init__(self) -> None:
        self._subscribers: dict[uuid.UUID, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, org_id: uuid.UUID) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_QUEUE)
        self._subscribers[org_id].add(queue)
        return queue

    def unsubscribe(self, org_id: uuid.UUID, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(org_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                del self._subscribers[org_id]

    def publish(self, org_id: uuid.UUID, payload: dict) -> None:
        for queue in self._subscribers.get(org_id, set()):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # slow subscriber; it will backfill from storage on reconnect
