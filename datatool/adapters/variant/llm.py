"""LLM variant source (ARCHITECTURE.md §11.3).

Generation lives here, in an adapter — never in ``core/``. The controller does not
know or care how a variant came to exist; this is what keeps "Claude generates,
DataTool safely ships" a complement to generation tools rather than a generation
product.

``materialize`` turns a plain-English ``surface_description`` (plus the contract's
scope) into a candidate variant by prompting an LLM, with three properties the spec
demands:

- **Deterministic via caching.** ``cache_key = sha256(...)`` over the inputs; identical
  inputs always return the same stored output, which keeps results reproducible and
  API costs predictable.
- **Scope-enforced.** ``forbidden_components`` are passed to the model as a hard
  constraint *and* checked in the output; a violation is rejected, not shipped.
- **No silent fallbacks.** Invalid JSON, a forbidden-component reference, or an API
  error all raise with the offending detail.

The ``anthropic`` / ``openai`` packages are optional extras (``pip install
datatool[llm]``) imported lazily by the concrete clients, so this module imports and
its logic is testable with a fake client even when neither is installed.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session, sessionmaker

from datatool.adapters import base as registry
from datatool.core.exceptions import AdapterError
from datatool.core.models import VariantSpec
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import LLMVariantCacheRepository

ADAPTER_ID = "variant.llm"
DEFAULT_MODEL = "claude-opus-5-5"
_PROMPT_PATH = Path(__file__).parent / "prompts" / "generate_variant.md"
_REQUIRED_OUTPUT_FIELDS = ("summary", "rationale", "code")


@runtime_checkable
class LLMClient(Protocol):
    """The minimal LLM interface the adapter needs: prompt in, raw text out."""

    def generate(self, *, model: str, prompt: str, seed: int | None) -> str: ...


class AnthropicClient:
    """LLMClient backed by the Anthropic Messages API (lazy import)."""

    def __init__(self, *, api_key: str, max_tokens: int = 4096):
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise AdapterError(
                "the 'anthropic' package is required for the LLM variant adapter; "
                "install with: pip install datatool[llm]"
            ) from exc
        self._client = anthropic.Anthropic(api_key=api_key)
        self._max_tokens = max_tokens

    def generate(self, *, model: str, prompt: str, seed: int | None) -> str:
        # Anthropic has no seed parameter; determinism comes from the cache.
        message = self._client.messages.create(
            model=model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        # A refusal returns HTTP 200 with no usable output; surface it rather than
        # letting an empty string fail later as "invalid JSON".
        if message.stop_reason == "refusal":
            raise AdapterError("the model declined to generate this variant (refusal).")
        # Read by block type: current models can lead with thinking blocks.
        return "".join(block.text for block in message.content if block.type == "text")


class OpenAIClient:
    """LLMClient backed by the OpenAI Chat Completions API (lazy import)."""

    def __init__(self, *, api_key: str):
        try:
            import openai
        except ImportError as exc:  # pragma: no cover
            raise AdapterError(
                "the 'openai' package is required for the LLM variant adapter; "
                "install with: pip install datatool[llm]"
            ) from exc
        self._client = openai.OpenAI(api_key=api_key)

    def generate(self, *, model: str, prompt: str, seed: int | None) -> str:
        completion = self._client.chat.completions.create(
            model=model,
            seed=seed,
            messages=[{"role": "user", "content": prompt}],
        )
        return completion.choices[0].message.content


def default_client_from_env() -> LLMClient:
    """Build a client from the environment: Anthropic preferred, then OpenAI."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return AnthropicClient(api_key=os.environ["ANTHROPIC_API_KEY"])
    if os.environ.get("OPENAI_API_KEY"):
        return OpenAIClient(api_key=os.environ["OPENAI_API_KEY"])
    raise AdapterError("the LLM variant adapter requires ANTHROPIC_API_KEY or OPENAI_API_KEY")


class LLMVariantCache:
    """DB-backed cache (the ``llm_variant_cache`` table) keyed by ``cache_key``."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self._session_factory = session_factory

    def get(self, cache_key: str) -> dict | None:
        with session_scope(self._session_factory) as session:
            repo = LLMVariantCacheRepository(session)
            row = repo.get(cache_key)
            if row is None:
                return None
            repo.record_hit(cache_key)
            return dict(row.response)

    def put(self, cache_key: str, model: str, prompt: str, response: dict) -> None:
        with session_scope(self._session_factory) as session:
            LLMVariantCacheRepository(session).add(
                cache_key=cache_key, model=model, prompt=prompt, response=response
            )


class LLMVariantSource:
    """VariantSource that generates a candidate variant with an LLM."""

    adapter_id = ADAPTER_ID

    def __init__(
        self,
        *,
        client: LLMClient,
        forbidden_components: list[str] | tuple[str, ...] = (),
        cache: LLMVariantCache | None = None,
        default_model: str = DEFAULT_MODEL,
        base_dir: str | Path | None = None,
    ):
        self._client = client
        self._forbidden = list(forbidden_components)
        self._cache = cache
        self._default_model = default_model
        self._base_dir = Path(base_dir) if base_dir is not None else None
        self._template: str | None = None

    def materialize(self, variant_spec: VariantSpec) -> dict:
        if variant_spec.source != "llm":
            raise AdapterError(
                f"llm variant source cannot materialize a {variant_spec.source!r} variant"
            )
        payload = variant_spec.payload or {}
        surface_description = payload.get("surface_description")
        if not surface_description:
            raise AdapterError("llm variant payload requires 'surface_description'")

        component_path = payload.get("component_path", "")
        model = payload.get("model") or self._default_model
        seed = payload.get("seed")
        extra = payload.get("extra_instructions", "")
        current_impl, current_ref_hash = self._current_implementation(
            payload.get("current_implementation_ref")
        )

        cache_key = self._cache_key(
            surface_description, component_path, current_ref_hash, model, seed, extra
        )
        if self._cache is not None:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        prompt = self._build_prompt(surface_description, component_path, current_impl, extra)
        try:
            raw = self._client.generate(model=model, prompt=prompt, seed=seed)
        except AdapterError:
            raise
        except Exception as exc:
            raise AdapterError(
                f"LLM generation failed: {exc}. Check credentials/quota and retry."
            ) from exc

        result = self._parse_and_check(raw, model, cache_key)
        if self._cache is not None:
            self._cache.put(cache_key, model, prompt, result)
        return result

    # -- internals ---------------------------------------------------------- #

    def _current_implementation(self, ref: str | None) -> tuple[str, str]:
        if not ref:
            return "", ""
        if self._base_dir is not None and (self._base_dir / ref).exists():
            content = (self._base_dir / ref).read_text()
            return content, hashlib.sha256(content.encode()).hexdigest()
        # No readable file: hash the reference string so the cache key still varies.
        return "", hashlib.sha256(ref.encode()).hexdigest()

    def _cache_key(self, surface, path, ref_hash, model, seed, extra) -> str:
        material = "|".join(
            [surface, path, ref_hash, model, "" if seed is None else str(seed), extra]
        )
        return hashlib.sha256(material.encode()).hexdigest()

    def _build_prompt(self, surface, path, current_impl, extra) -> str:
        if self._template is None:
            self._template = _PROMPT_PATH.read_text()
        forbidden = "\n".join(f"- {c}" for c in self._forbidden) or "(none)"
        return (
            self._template.replace("{{SURFACE_DESCRIPTION}}", surface)
            .replace("{{COMPONENT_PATH}}", path or "(unspecified)")
            .replace("{{CURRENT_IMPLEMENTATION}}", current_impl or "(not provided)")
            .replace("{{FORBIDDEN_COMPONENTS}}", forbidden)
            .replace("{{EXTRA_INSTRUCTIONS}}", extra or "(none)")
        )

    def _parse_and_check(self, raw: str, model: str, cache_key: str) -> dict:
        try:
            parsed = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise AdapterError(
                f"LLM returned invalid JSON ({exc}); raw output: {raw[:500]}"
            ) from exc
        missing = [f for f in _REQUIRED_OUTPUT_FIELDS if f not in parsed]
        if missing:
            raise AdapterError(f"LLM output missing fields {missing}; raw output: {raw[:500]}")

        code = parsed["code"]
        violations = [c for c in self._forbidden if c in code]
        if violations:
            raise AdapterError(
                f"generated variant references forbidden components {violations}; rejected"
            )

        return {
            "source": "llm",
            "model": model,
            "cache_key": cache_key,
            "summary": parsed["summary"],
            "rationale": parsed["rationale"],
            "code": code,
        }


def llm_extras_available() -> bool:
    """Whether the LLM adapter can be constructed (an extra installed + a key set)."""
    import importlib.util

    has_pkg = bool(importlib.util.find_spec("anthropic") or importlib.util.find_spec("openai"))
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"))
    return has_pkg and has_key


def register() -> bool:
    """Register the LLM variant source — only if the extras + a key are available (§11.3).

    Returns True if registered. The factory is the class; callers construct it with a
    client (or :func:`default_client_from_env`), the contract's forbidden components,
    and a cache.
    """
    if not llm_extras_available():
        return False
    registry.register(ADAPTER_ID, LLMVariantSource)
    return True
