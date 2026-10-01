"""CSV metrics source (ARCHITECTURE.md §11.2).

Reads a flat event CSV with columns ``unit_id, variant, metric, value, timestamp``
and returns the per-variant aggregated :class:`~datatool.core.models.Sample`
statistics (n, sum, sum_sq) the confidence sequence and guardrails consume. This is
the replay/testing path: point it at a quarter of historical events and let the
simulator drive the controller as if they were arriving live.

Because :class:`Sample` is keyed by ``variant_id`` (a UUID) but the CSV carries
variant *names*, the adapter is constructed with a name->id mapping (the
experiment's registered variants). Variants in the CSV that are not in the mapping
are ignored; malformed rows raise rather than being silently dropped.

CUPED (§8.5): rows with an *empty* ``variant`` are pre-experiment observations —
units are not assigned yet. They never count toward an arm or the replay window;
:meth:`CsvMetricsSource.query_cuped` uses them as each unit's covariate.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from datatool.adapters import base as registry
from datatool.core.exceptions import AdapterError
from datatool.core.models import CovariateSample, CupedData, Sample

ADAPTER_ID = "metrics.csv"
_REQUIRED_COLUMNS = {"unit_id", "variant", "metric", "value", "timestamp"}


def _to_utc(dt: datetime) -> datetime:
    """Normalize to timezone-aware UTC (naive timestamps are assumed to be UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class _Event:
    __slots__ = ("unit_id", "variant", "metric", "value", "timestamp")

    def __init__(self, unit_id: str, variant: str, metric: str, value: float, timestamp: datetime):
        self.unit_id = unit_id
        self.variant = variant
        self.metric = metric
        self.value = value
        self.timestamp = timestamp


class CsvMetricsSource:
    """MetricsSource that aggregates a static event CSV."""

    adapter_id = ADAPTER_ID

    def __init__(self, csv_path: str | Path, variant_ids: dict[str, UUID]):
        self._variant_ids = dict(variant_ids)
        self._events = self._load(Path(csv_path))

    def _load(self, path: Path) -> list[_Event]:
        if not path.exists():
            raise AdapterError(f"metrics CSV not found: {path}")
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            missing = _REQUIRED_COLUMNS - set(reader.fieldnames or [])
            if missing:
                raise AdapterError(f"metrics CSV {path} missing columns: {sorted(missing)}")
            events: list[_Event] = []
            for line_no, row in enumerate(reader, start=2):  # header is line 1
                try:
                    value = float(row["value"])
                    timestamp = _to_utc(datetime.fromisoformat(row["timestamp"]))
                except (ValueError, TypeError) as exc:
                    raise AdapterError(f"malformed row {line_no} in {path}: {exc}") from exc
                events.append(
                    _Event(row["unit_id"], row["variant"], row["metric"], value, timestamp)
                )
        return events

    def supports_metric(self, metric: str) -> bool:
        return any(event.metric == metric for event in self._events)

    def time_span(self) -> tuple[datetime, datetime] | None:
        """The (earliest, latest) event timestamp, or None if the file is empty.

        The simulator uses this to drive its clock across the data (not part of the
        MetricsSource protocol — a replay convenience specific to a static file).
        """
        # Only assigned events: unassigned pre-period rows (CUPED covariates) sit
        # before the experiment and must not pull its start earlier.
        timestamps = [e.timestamp for e in self._events if e.variant]
        if not timestamps:
            return None
        return min(timestamps), max(timestamps)

    def query(
        self,
        metric: str,
        experiment_id: UUID,
        variant_split_by: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Sample]:
        """Per-variant aggregated samples for ``metric`` over ``[window_start, window_end)``.

        ``variant_split_by`` is unused here — the CSV carries an explicit ``variant``
        column — but is part of the protocol for warehouse sources that split by an
        assignment-unit column.
        """
        start, end = _to_utc(window_start), _to_utc(window_end)

        # Accumulate n, sum, sum_sq per variant name in one pass.
        acc: dict[str, list[float]] = {}  # variant -> [n, sum, sum_sq]
        for event in self._events:
            if event.metric != metric or not (start <= event.timestamp < end):
                continue
            if event.variant not in self._variant_ids:
                continue  # not a registered variant for this experiment
            stats = acc.setdefault(event.variant, [0.0, 0.0, 0.0])
            stats[0] += 1
            stats[1] += event.value
            stats[2] += event.value * event.value

        samples: list[Sample] = []
        for variant_name, (n, total, total_sq) in acc.items():
            samples.append(
                Sample(
                    experiment_id=experiment_id,
                    variant_id=self._variant_ids[variant_name],
                    metric=metric,
                    window_start=start,
                    window_end=end,
                    n=int(n),
                    sum=total,
                    sum_sq=total_sq,
                )
            )
        return samples

    def _unit_means(self, metric: str, start: datetime, end: datetime) -> dict[str, float]:
        totals: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])  # unit -> [n, sum]
        for event in self._events:
            if event.metric == metric and start <= event.timestamp < end:
                totals[event.unit_id][0] += 1
                totals[event.unit_id][1] += event.value
        return {unit: total / n for unit, (n, total) in totals.items()}

    def query_cuped(
        self,
        metric: str,
        experiment_id: UUID,
        variant_split_by: str,
        window_start: datetime,
        window_end: datetime,
        pre_period: timedelta,
    ) -> CupedData:
        """Cross-moments of each in-window outcome with its unit's pre-period mean.

        The covariate window is ``[start - pre_period, start)``; theta's pairs come
        from that window (later, ``b``) and the one before it (earlier, ``a``).
        """
        start, end = _to_utc(window_start), _to_utc(window_end)
        later = self._unit_means(metric, start - pre_period, start)
        earlier = self._unit_means(metric, start - 2 * pre_period, start - pre_period)

        paired = [(earlier[u], later[u]) for u in earlier.keys() & later.keys()]
        # DECISION: a unit with no pre-period data gets x = 0. Any fixed function of
        # pre-treatment data keeps the adjusted difference unbiased; it just gets no
        # variance reduction for that unit.
        acc: dict[str, list[float]] = {}  # variant -> [n, Σy, Σy², Σx, Σx², Σxy]
        for event in self._events:
            if event.metric != metric or not (start <= event.timestamp < end):
                continue
            if event.variant not in self._variant_ids:
                continue
            y, x = event.value, later.get(event.unit_id, 0.0)
            m = acc.setdefault(event.variant, [0.0] * 6)
            for i, v in enumerate((1.0, y, y * y, x, x * x, x * y)):
                m[i] += v

        return CupedData(
            arms=[
                CovariateSample(
                    variant_id=self._variant_ids[name],
                    n=int(n),
                    sum_y=sy,
                    sum_yy=syy,
                    sum_x=sx,
                    sum_xx=sxx,
                    sum_xy=sxy,
                )
                for name, (n, sy, syy, sx, sxx, sxy) in acc.items()
            ],
            pre_n=len(paired),
            pre_sum_a=sum(a for a, _ in paired),
            pre_sum_b=sum(b for _, b in paired),
            pre_sum_aa=sum(a * a for a, _ in paired),
            pre_sum_ab=sum(a * b for a, b in paired),
        )


def register() -> None:
    """Register the CSV metrics source factory under ``metrics.csv``."""
    registry.register(ADAPTER_ID, CsvMetricsSource)
