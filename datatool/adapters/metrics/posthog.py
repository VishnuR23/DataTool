"""PostHog metrics source (ARCHITECTURE.md §11.2).

Queries the PostHog Query API (HogQL) and aggregates events into per-variant
:class:`~datatool.core.models.Sample` statistics. Auth is a personal API key from the
environment (``POSTHOG_API_KEY``).

Assumed event schema (documented so it can be matched in the user's instrumentation):
each metric is an *event name*; events carry properties ``experiment_id`` (the
experiment's UUID as a string), a variant property (default ``variant``), and a
numeric ``value`` property (0/1 for a rate metric, a number for a continuous one).
``n`` is the event count, ``sum`` the sum of ``value``, ``sum_sq`` the sum of squares.

CUPED (§8.5): pre-experiment events carry no ``experiment_id`` or variant, so each
in-experiment event is paired with the *same person's* (``person_id``) mean of the
metric over the pre-period. That pairing only exists for person-level assignment
(``assignment_unit: user``); other units raise :class:`CovariatesUnavailable`, which
the controller records as the reason it used the plain confidence sequence.

Like the CSV source, this is constructed with a variant name->id mapping because
:class:`Sample` is keyed by ``variant_id`` while the events carry variant names.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from uuid import UUID

import httpx

from datatool.adapters import base as registry
from datatool.core.exceptions import AdapterError, CovariatesUnavailable
from datatool.core.models import CovariateSample, CupedData, Sample

ADAPTER_ID = "metrics.posthog"
_IDENTIFIER = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*$")


def _literal(value: object) -> str:
    """A HogQL string literal: backslashes and quotes escaped, so a metric name can
    never close the literal and inject query text."""
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


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
        for name in (variant_property, value_property):
            # Interpolated as identifiers (properties.<name>), so they must be plain.
            if not _IDENTIFIER.match(name):
                raise AdapterError(f"PostHog property name {name!r} is not a plain identifier")
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

    @property
    def _value(self) -> str:
        return f"coalesce(toFloat64OrNull(properties.{self._value_property}), 0)"

    def _hogql(self, metric: str, experiment_id: UUID, start: datetime, end: datetime) -> str:
        v = self._variant_property
        value = f"toFloat64OrNull(properties.{self._value_property})"
        return (
            f"SELECT properties.{v} AS variant, "
            f"count() AS n, "
            f"sum(coalesce({value}, 0)) AS s, "
            f"sum(coalesce({value}, 0) * coalesce({value}, 0)) AS s_sq "
            f"FROM events "
            f"WHERE event = {_literal(metric)} "
            f"AND properties.experiment_id = {_literal(experiment_id)} "
            f"AND timestamp >= {_literal(start.isoformat())} "
            f"AND timestamp < {_literal(end.isoformat())} "
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
        results = self._run(self._hogql(metric, experiment_id, window_start, window_end))
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

    def _unit_means(self, metric: str, start: datetime, end: datetime, column: str) -> str:
        """Per-person mean of the metric over a window, any experiment or none."""
        return (
            f"(SELECT person_id AS unit, avg({self._value}) AS {column} "
            f"FROM events WHERE event = {_literal(metric)} "
            f"AND timestamp >= {_literal(start.isoformat())} "
            f"AND timestamp < {_literal(end.isoformat())} "
            f"GROUP BY unit)"
        )

    def query_cuped(
        self,
        metric: str,
        experiment_id: UUID,
        variant_split_by: str,
        window_start: datetime,
        window_end: datetime,
        pre_period: timedelta,
    ) -> CupedData:
        """Outcome/covariate cross-moments per variant, plus pre-period pairs for theta."""
        if variant_split_by != "user":
            raise CovariatesUnavailable(
                "PostHog pairs pre-period events by person, which needs assignment_unit "
                f"'user', not {variant_split_by!r}"
            )
        start = window_start
        x = "coalesce(pre.x, 0)"  # DECISION: no pre-period history -> x = 0 (still unbiased)
        arms_hogql = (
            f"SELECT e.variant, count() AS n, sum(e.y), sum(e.y * e.y), "
            f"sum({x}), sum({x} * {x}), sum({x} * e.y) "
            f"FROM (SELECT person_id AS unit, properties.{self._variant_property} AS variant, "
            f"{self._value} AS y FROM events WHERE event = {_literal(metric)} "
            f"AND properties.experiment_id = {_literal(experiment_id)} "
            f"AND timestamp >= {_literal(start.isoformat())} "
            f"AND timestamp < {_literal(window_end.isoformat())}) AS e "
            f"LEFT JOIN {self._unit_means(metric, start - pre_period, start, 'x')} AS pre "
            f"ON e.unit = pre.unit "
            f"GROUP BY e.variant"
        )
        pairs_hogql = (
            "SELECT count(), sum(early.a), sum(late.b), sum(early.a * early.a), "
            "sum(early.a * late.b) "
            f"FROM {self._unit_means(metric, start - 2 * pre_period, start - pre_period, 'a')}"
            " AS early "
            f"INNER JOIN {self._unit_means(metric, start - pre_period, start, 'b')} AS late "
            "ON early.unit = late.unit"
        )

        arms = [
            CovariateSample(
                variant_id=self._variant_ids[row[0]],
                n=int(row[1]),
                sum_y=float(row[2] or 0.0),
                sum_yy=float(row[3] or 0.0),
                sum_x=float(row[4] or 0.0),
                sum_xx=float(row[5] or 0.0),
                sum_xy=float(row[6] or 0.0),
            )
            for row in self._run(arms_hogql)
            if row[0] in self._variant_ids
        ]
        pairs = self._run(pairs_hogql)
        n, sa, sb, saa, sab = pairs[0] if pairs else (0, 0, 0, 0, 0)
        return CupedData(
            arms=arms,
            pre_n=int(n or 0),
            pre_sum_a=float(sa or 0.0),
            pre_sum_b=float(sb or 0.0),
            pre_sum_aa=float(saa or 0.0),
            pre_sum_ab=float(sab or 0.0),
        )

    def _run(self, hogql: str) -> list:
        """POST one HogQL query; return its result rows (raises AdapterError)."""
        url = f"{self._host}/api/projects/{self._project_id}/query/"
        body = {"query": {"kind": "HogQLQuery", "query": hogql}}
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
            return response.json()["results"]
        except (ValueError, KeyError) as exc:
            raise AdapterError(f"unexpected PostHog response shape: {exc}") from exc


def register() -> None:
    """Register the PostHog metrics source factory under ``metrics.posthog``."""
    registry.register(ADAPTER_ID, PostHogMetricsSource)
