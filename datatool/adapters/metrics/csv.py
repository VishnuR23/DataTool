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
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from datatool.adapters import base as registry
from datatool.core.exceptions import AdapterError
from datatool.core.models import Sample

ADAPTER_ID = "metrics.csv"
_REQUIRED_COLUMNS = {"unit_id", "variant", "metric", "value", "timestamp"}


def _to_utc(dt: datetime) -> datetime:
    """Normalize to timezone-aware UTC (naive timestamps are assumed to be UTC)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class _Event:
    __slots__ = ("variant", "metric", "value", "timestamp")

    def __init__(self, variant: str, metric: str, value: float, timestamp: datetime):
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
                events.append(_Event(row["variant"], row["metric"], value, timestamp))
        return events

    def supports_metric(self, metric: str) -> bool:
        return any(event.metric == metric for event in self._events)

    def time_span(self) -> tuple[datetime, datetime] | None:
        """The (earliest, latest) event timestamp, or None if the file is empty.

        The simulator uses this to drive its clock across the data (not part of the
        MetricsSource protocol — a replay convenience specific to a static file).
        """
        if not self._events:
            return None
        timestamps = [event.timestamp for event in self._events]
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


def register() -> None:
    """Register the CSV metrics source factory under ``metrics.csv``."""
    registry.register(ADAPTER_ID, CsvMetricsSource)
