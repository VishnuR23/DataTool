"""LLM variant source (ARCHITECTURE.md §11.3).

A fake client verifies prompt construction, response parsing, scope-violation
detection, the failure modes, and the deterministic cache — without a real API call.
A live test (skipped unless DATATOOL_RUN_LIVE_LLM_TESTS=1) exercises the real client.
"""

from __future__ import annotations

import json
import os

import pytest

from datatool.adapters import base as registry
from datatool.adapters.variant.base import VariantSource
from datatool.adapters.variant.llm import (
    LLMVariantCache,
    LLMVariantSource,
    llm_extras_available,
    register,
)
from datatool.core.exceptions import AdapterError
from datatool.core.models import VariantSpec

PAYLOAD = {
    "surface_description": "Pricing hero: clearer value props.",
    "component_path": "src/Pricing/Hero.tsx",
    "seed": 17,
}


def _spec(payload=None, source="llm") -> VariantSpec:
    return VariantSpec(name="treatment", source=source, payload=payload or PAYLOAD)


def _good_response(code: str = "export const Hero = () => <h1>Clarity</h1>;") -> str:
    return json.dumps({"summary": "clearer headline", "rationale": "value props", "code": code})


class FakeClient:
    def __init__(self, response: str | Exception):
        self.response = response
        self.calls = 0
        self.prompts: list[str] = []

    def generate(self, *, model: str, prompt: str, seed) -> str:
        self.calls += 1
        self.prompts.append(prompt)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def test_source_satisfies_variant_source_protocol():
    assert isinstance(LLMVariantSource(client=FakeClient(_good_response())), VariantSource)


def test_materialize_parses_response_into_payload():
    src = LLMVariantSource(client=FakeClient(_good_response()))
    out = src.materialize(_spec())
    assert out["source"] == "llm"
    assert out["summary"] == "clearer headline"
    assert out["code"].startswith("export const Hero")
    assert "cache_key" in out


def test_prompt_includes_forbidden_components():
    client = FakeClient(_good_response())
    src = LLMVariantSource(client=client, forbidden_components=["PaymentForm", "TaxCalculator"])
    src.materialize(_spec())
    assert "PaymentForm" in client.prompts[0]
    assert "TaxCalculator" in client.prompts[0]
    assert PAYLOAD["surface_description"] in client.prompts[0]


def test_forbidden_component_in_output_is_rejected():
    client = FakeClient(
        _good_response(code="import PaymentForm; export const X = () => <PaymentForm/>;")
    )
    src = LLMVariantSource(client=client, forbidden_components=["PaymentForm"])
    with pytest.raises(AdapterError, match="forbidden"):
        src.materialize(_spec())


def test_invalid_json_raises_with_raw_output():
    src = LLMVariantSource(client=FakeClient("not json at all"))
    with pytest.raises(AdapterError, match="invalid JSON"):
        src.materialize(_spec())


def test_missing_output_fields_raise():
    src = LLMVariantSource(client=FakeClient(json.dumps({"summary": "x"})))
    with pytest.raises(AdapterError, match="missing fields"):
        src.materialize(_spec())


def test_api_error_raises_with_retry_hint():
    src = LLMVariantSource(client=FakeClient(RuntimeError("503 overloaded")))
    with pytest.raises(AdapterError, match="retry"):
        src.materialize(_spec())


def test_non_llm_source_cannot_be_materialized():
    src = LLMVariantSource(client=FakeClient(_good_response()))
    with pytest.raises(AdapterError):
        src.materialize(_spec(source="static", payload={"ref": "x"}))


def test_missing_surface_description_raises():
    src = LLMVariantSource(client=FakeClient(_good_response()))
    with pytest.raises(AdapterError):
        src.materialize(_spec(payload={"component_path": "x"}))


def test_identical_inputs_hit_the_cache(session_factory):
    client = FakeClient(_good_response())
    src = LLMVariantSource(client=client, cache=LLMVariantCache(session_factory))
    first = src.materialize(_spec())
    second = src.materialize(_spec())  # same inputs
    assert client.calls == 1  # second call served from cache
    assert first == second


def test_different_seed_misses_the_cache(session_factory):
    client = FakeClient(_good_response())
    src = LLMVariantSource(client=client, cache=LLMVariantCache(session_factory))
    src.materialize(_spec({**PAYLOAD, "seed": 1}))
    src.materialize(_spec({**PAYLOAD, "seed": 2}))
    assert client.calls == 2  # different cache key


def test_register_is_skipped_without_extras_and_key():
    registry.clear_registry()
    try:
        # In CI/dev neither an API key nor the extras are present.
        assert llm_extras_available() is False
        assert register() is False
        assert not registry.is_registered("variant.llm")
    finally:
        registry.clear_registry()


@pytest.mark.skipif(
    os.environ.get("DATATOOL_RUN_LIVE_LLM_TESTS") != "1",
    reason="live LLM test; set DATATOOL_RUN_LIVE_LLM_TESTS=1 and provide an API key",
)
def test_live_generation_against_real_api():
    from datatool.adapters.variant.llm import default_client_from_env

    src = LLMVariantSource(client=default_client_from_env(), forbidden_components=["PaymentForm"])
    out = src.materialize(_spec())
    assert out["code"] and out["summary"]
