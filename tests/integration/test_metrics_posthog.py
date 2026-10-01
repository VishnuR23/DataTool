"""PostHog metrics source (ARCHITECTURE.md §11.2).

The PostHog Query API is mocked with httpx.MockTransport, so the tests assert the
request (URL, auth, HogQL) and the parsing of a canned response into per-variant
Samples, without a real API call.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest

from datatool.adapters import base as registry
from datatool.adapters.metrics.base import MetricsSource
from datatool.adapters.metrics.posthog import PostHogMetricsSource
from datatool.adapters.metrics.posthog import register as register_posthog
from datatool.core.exceptions import AdapterError

CONTROL_ID = uuid4()
TREATMENT_ID = uuid4()
EXPERIMENT_ID = uuid4()
START = datetime(2026, 6, 1, tzinfo=UTC)
END = datetime(2026, 6, 1, 1, tzinfo=UTC)

_RESPONSE = {
    "results": [
        ["control", 1000, 100.0, 100.0],
        ["treatment", 1000, 140.0, 140.0],
        ["bot", 50, 5.0, 5.0],  # not a registered variant
    ],
    "columns": ["variant", "n", "s", "s_sq"],
}


def _source(handler, **overrides) -> PostHogMetricsSource:
    kwargs = dict(
        host="https://eu.posthog.com",
        project_id="42",
        api_key="phx_secret",
        variant_ids={"control": CONTROL_ID, "treatment": TREATMENT_ID},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    kwargs.update(overrides)
    return PostHogMetricsSource(**kwargs)


def _ok(_request):
    return httpx.Response(200, json=_RESPONSE)


def test_source_satisfies_metrics_source_protocol():
    assert isinstance(_source(_ok), MetricsSource)


def test_query_parses_response_into_per_variant_samples():
    samples = _source(_ok).query("conversion", EXPERIMENT_ID, "user", START, END)
    by_id = {s.variant_id: s for s in samples}
    assert (by_id[CONTROL_ID].n, by_id[CONTROL_ID].sum, by_id[CONTROL_ID].sum_sq) == (
        1000,
        100.0,
        100.0,
    )
    assert (by_id[TREATMENT_ID].n, by_id[TREATMENT_ID].sum) == (1000, 140.0)
    assert all(s.experiment_id == EXPERIMENT_ID and s.metric == "conversion" for s in samples)


def test_query_ignores_variants_not_in_the_mapping():
    samples = _source(_ok).query("conversion", EXPERIMENT_ID, "user", START, END)
    assert {s.variant_id for s in samples} == {CONTROL_ID, TREATMENT_ID}  # 'bot' dropped


def test_query_sends_auth_and_hogql():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["query"] = json.loads(request.content)["query"]["query"]
        return httpx.Response(200, json=_RESPONSE)

    _source(handler).query("conversion", EXPERIMENT_ID, "user", START, END)
    assert seen["url"] == "https://eu.posthog.com/api/projects/42/query/"
    assert seen["auth"] == "Bearer phx_secret"
    assert "event = 'conversion'" in seen["query"]
    assert str(EXPERIMENT_ID) in seen["query"]


def test_query_raises_on_error_status():
    with pytest.raises(AdapterError):
        _source(lambda r: httpx.Response(403, text="forbidden")).query(
            "conversion", EXPERIMENT_ID, "user", START, END
        )


def test_query_raises_on_unexpected_response_shape():
    with pytest.raises(AdapterError):
        _source(lambda r: httpx.Response(200, json={"nope": []})).query(
            "conversion", EXPERIMENT_ID, "user", START, END
        )


def test_missing_credentials_raise():
    with pytest.raises(AdapterError):
        PostHogMetricsSource(host="", project_id="42", api_key="k", variant_ids={})


def test_register_adds_factory():
    registry.clear_registry()
    try:
        register_posthog()
        assert registry.get_adapter_factory("metrics.posthog") is PostHogMetricsSource
    finally:
        registry.clear_registry()


def test_metric_names_are_escaped_inside_hogql_string_literals():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["query"] = json.loads(request.content)["query"]["query"]
        return httpx.Response(200, json=_RESPONSE)

    _source(handler).query("it's' OR 1=1 --", EXPERIMENT_ID, "user", START, END)
    assert "event = 'it\\'s\\' OR 1=1 --'" in seen["query"]


def test_property_names_must_be_plain_identifiers():
    with pytest.raises(AdapterError):
        _source(_ok, value_property="value) OR 1=1 --")


# --------------------------------------------------------------------------- #
# CUPED covariates (§8.5): pre-period events are paired to units by person_id
# --------------------------------------------------------------------------- #

from datetime import timedelta  # noqa: E402

_ARMS = {
    "results": [
        ["control", 1000, 100.0, 100.0, 80.0, 30.0, 40.0],
        ["treatment", 1000, 140.0, 140.0, 82.0, 31.0, 55.0],
        ["bot", 5, 1.0, 1.0, 0.0, 0.0, 0.0],
    ]
}
_PAIRS = {"results": [[1500, 120.0, 118.0, 40.0, 33.0]]}


def _cuped_handler(seen):
    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]["query"]
        seen.append(query)
        return httpx.Response(200, json=_PAIRS if "AS early" in query else _ARMS)

    return handler


def test_posthog_serves_cuped_cross_moments_and_pre_period_pairs():
    from datatool.adapters.metrics.base import CupedMetricsSource

    seen = []
    source = _source(_cuped_handler(seen))
    assert isinstance(source, CupedMetricsSource)
    data = source.query_cuped("conversion", EXPERIMENT_ID, "user", START, END, timedelta(days=7))

    arms = {a.variant_id: a for a in data.arms}
    assert set(arms) == {CONTROL_ID, TREATMENT_ID}  # 'bot' dropped
    c = arms[CONTROL_ID]
    assert (c.n, c.sum_y, c.sum_yy, c.sum_x, c.sum_xx, c.sum_xy) == (
        1000,
        100.0,
        100.0,
        80.0,
        30.0,
        40.0,
    )
    assert (data.pre_n, data.pre_sum_a, data.pre_sum_b, data.pre_sum_aa, data.pre_sum_ab) == (
        1500,
        120.0,
        118.0,
        40.0,
        33.0,
    )

    arms_query, pairs_query = sorted(seen, key=lambda q: "AS early" in q)
    # Outcomes are this experiment's events; covariates are the same person's mean
    # of the metric over the week before the start, with no experiment filter.
    assert "person_id" in arms_query and "LEFT JOIN" in arms_query
    assert str(EXPERIMENT_ID) in arms_query
    assert "'2026-05-25T00:00:00+00:00'" in arms_query  # START - 7d
    # theta pairs: the two pre-period weeks, joined per person.
    assert "'2026-05-18T00:00:00+00:00'" in pairs_query  # START - 14d
    assert "INNER JOIN" in pairs_query and str(EXPERIMENT_ID) not in pairs_query


def test_posthog_cuped_with_no_pre_period_history_has_no_pairs():
    def handler(request):
        query = json.loads(request.content)["query"]["query"]
        if "AS early" in query:
            return httpx.Response(200, json={"results": [[0, None, None, None, None]]})
        return httpx.Response(200, json=_ARMS)

    data = _source(handler).query_cuped(
        "conversion", EXPERIMENT_ID, "user", START, END, timedelta(days=7)
    )
    assert (data.pre_n, data.pre_sum_a, data.pre_sum_ab) == (0, 0.0, 0.0)


def test_posthog_cuped_needs_person_level_units():
    from datatool.core.exceptions import CovariatesUnavailable

    calls = []
    source = _source(lambda r: calls.append(r) or httpx.Response(200, json=_ARMS))
    with pytest.raises(CovariatesUnavailable, match="session"):
        source.query_cuped("conversion", EXPERIMENT_ID, "session", START, END, timedelta(days=7))
    assert calls == []  # refused before querying
