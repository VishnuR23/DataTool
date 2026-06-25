"""Event intake (spec §"Live streaming", §"Security and trust").

Validates the batch schema version, then stores the events scoped to exactly one
org. Returns the newly-inserted events so the caller can fan them out to the live
channel. Org isolation is enforced here and in the repository: every write carries
an explicit ``org_id`` resolved from the presented enrollment token.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from cloud.persistence.repositories import EventRepository
from datatool.telemetry.events import SCHEMA_VERSION, TelemetryBatch, TelemetryEvent


class UnsupportedSchemaError(Exception):
    """Raised when a batch's schema version is not supported by this panel."""


def ingest_batch(
    session: Session, org_id: uuid.UUID, batch: TelemetryBatch
) -> list[TelemetryEvent]:
    if batch.schema_version != SCHEMA_VERSION:
        raise UnsupportedSchemaError(
            f"unsupported schema version {batch.schema_version}; expected {SCHEMA_VERSION}"
        )
    return EventRepository(session).add_batch(org_id, batch.events)
