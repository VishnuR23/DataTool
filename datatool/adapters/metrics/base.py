"""MetricsSource protocol (ARCHITECTURE.md §11.2).

A metrics source answers the only question the decision engine asks of the data
warehouse: for this metric, this experiment, and this time window, what are the
per-variant aggregated statistics? It returns :class:`~datatool.core.models.Sample`
objects (n, sum, sum_sq) — exactly the running statistics the confidence sequence
and guardrails consume — so the controller never touches raw events.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable
from uuid import UUID

from datatool.core.models import Sample


@runtime_checkable
class MetricsSource(Protocol):
    """Query per-variant aggregated metric samples over a time window."""

    adapter_id: str

    def query(
        self,
        metric: str,
        experiment_id: UUID,
        variant_split_by: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Sample]:
        """Return per-variant aggregated samples for ``metric`` over the window."""
        ...

    def supports_metric(self, metric: str) -> bool:
        """Whether this source can serve the named metric."""
        ...
