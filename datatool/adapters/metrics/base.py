"""MetricsSource protocol (ARCHITECTURE.md §11.2).

A metrics source answers the only question the decision engine asks of the data
warehouse: for this metric, this experiment, and this time window, what are the
per-variant aggregated statistics? It returns :class:`~datatool.core.models.Sample`
objects (n, sum, sum_sq) — exactly the running statistics the confidence sequence
and guardrails consume — so the controller never touches raw events.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol, runtime_checkable
from uuid import UUID

from datatool.core.models import CupedData, Sample


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


@runtime_checkable
class CupedMetricsSource(MetricsSource, Protocol):
    """A metrics source that can also serve CUPED covariates (ARCHITECTURE.md §8.5).

    Optional: the controller checks for it when ``statistics.enable_cuped`` is on and
    records why it fell back to the plain confidence sequence when a source lacks it.
    """

    def query_cuped(
        self,
        metric: str,
        experiment_id: UUID,
        variant_split_by: str,
        window_start: datetime,
        window_end: datetime,
        pre_period: timedelta,
    ) -> CupedData:
        """Cross-moments over ``[window_start, window_end)`` with covariates from
        ``[window_start - pre_period, window_start)``, plus pre-period pairs from
        ``[window_start - 2*pre_period, window_start - pre_period)`` (earlier) and
        ``[window_start - pre_period, window_start)`` (later) for estimating theta."""
        ...
