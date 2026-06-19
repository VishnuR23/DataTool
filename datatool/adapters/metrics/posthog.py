"""PostHog metrics source (ARCHITECTURE.md §11.2).

Queries the PostHog Query API (HogQL) and aggregates events into per-variant
:class:`~datatool.core.models.Sample` statistics. Auth is a personal API key from the
environment (``POSTHOG_API_KEY``).

Assumed event schema (documented so it can be matched in the user's instrumentation):
each metric is an *event name*; events carry properties ``experiment_id`` (the
experiment's UUID as a string), a variant property (default ``variant``), and a
numeric ``value`` property (0/1 for a rate metric, a number for a continuous one).
``n`` is the event count, ``sum`` the sum of ``value``, ``sum_sq`` the sum of squares.

Like the CSV source, this is constructed with a variant name->id mapping because
:class:`Sample` is keyed by ``variant_id`` while the events carry variant names.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

import httpx

from datatool.adapters import base as registry
from datatool.core.exceptions import AdapterError
from datatool.core.models import Sample

ADAPTER_ID = "metrics.posthog"


class PostHogMetricsSource:
    """MetricsSource backed by the PostHog Query API."""

    adapter_id = ADAPTER_ID

    def __init__(
        self,
        *,
        host: str,
        project_id: str,
        api_key: str,
        variant_ids: dict[str, UUID],
        variant_property: str = "variant",
        value_property: str = "value",
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ):
        if not (host and project_id and api_key):
            raise AdapterError("PostHog host, project_id, and api_key are required")
        self._host = host.rstrip("/")
        self._project_id = project_id
        self._api_key = api_key
        self._variant_ids = dict(variant_ids)
        self._variant_property = variant_property
        self._value_property = value_property
        self._client = client or httpx.Client(timeout=timeout)

    def supports_metric(self, metric: str) -> bool:
        # PostHog can serve any event by name; the controller validates the metric
        # exists at query time (an unknown event simply returns no rows).
        return True

    def _hogql(self, metric: str, experiment_id: UUID, start: datetime, end: datetime) -> str:
        v = self._variant_property
        value = f"toFloat64OrNull(properties.{self._value_property})"
        return (
            f"SELECT properties.{v} AS variant, "
            f"count() AS n, "
            f"sum(coalesce({value}, 0)) AS s, "
            f"sum(coalesce({value}, 0) * coalesce({value}, 0)) AS s_sq "
            f"FROM events "
            f"WHERE event = '{metric}' "
            f"AND properties.experiment_id = '{experiment_id}' "
            f"AND timestamp >= '{start.isoformat()}' "
            f"AND timestamp < '{end.isoformat()}' "
            f"GROUP BY variant"
        )

    def query(
        self,
        metric: str,
        experiment_id: UUID,
        variant_split_by: str,
        window_start: datetime,
        window_end: datetime,
    ) -> list[Sample]:
        url = f"{self._host}/api/projects/{self._project_id}/query/"
        body = {
            "query": {
                "kind": "HogQLQuery",
                "query": self._hogql(metric, experiment_id, window_start, window_end),
            }
        }
        try:
            response = self._client.post(
                url, headers={"Authorization": f"Bearer {self._api_key}"}, json=body
            )
        except httpx.HTTPError as exc:
            raise AdapterError(f"PostHog query failed: {exc}") from exc
        if response.status_code >= 400:
            raise AdapterError(
                f"PostHog query returned {response.status_code}: {response.text[:300]}"
            )

        try:
            results = response.json()["results"]
        except (ValueError, KeyError) as exc:
            raise AdapterError(f"unexpected PostHog response shape: {exc}") from exc

        samples: list[Sample] = []
        for row in results:
            variant_name, n, total, total_sq = row[0], row[1], row[2], row[3]
            if variant_name not in self._variant_ids:
                continue  # not a registered variant for this experiment
            samples.append(
                Sample(
                    experiment_id=experiment_id,
                    variant_id=self._variant_ids[variant_name],
                    metric=metric,
                    window_start=window_start,
                    window_end=window_end,
                    n=int(n),
                    sum=float(total or 0.0),
                    sum_sq=float(total_sq or 0.0),
                )
            )
        return samples


def register() -> None:
    """Register the PostHog metrics source factory under ``metrics.posthog``."""
    registry.register(ADAPTER_ID, PostHogMetricsSource)
