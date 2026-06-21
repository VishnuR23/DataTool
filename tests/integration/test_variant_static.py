"""Static variant source (ARCHITECTURE.md §11.3)."""

from __future__ import annotations

import hashlib

import pytest

from datatool.adapters import base as registry
from datatool.adapters.variant.base import VariantSource
from datatool.adapters.variant.static import StaticVariantSource
from datatool.adapters.variant.static import register as register_static
from datatool.core.exceptions import AdapterError
from datatool.core.models import VariantSpec


def _spec(source, payload, *, is_control=False, name="treatment"):
    return VariantSpec(name=name, is_control=is_control, source=source, payload=payload)


def test_source_satisfies_variant_source_protocol():
    assert isinstance(StaticVariantSource(), VariantSource)


def test_existing_variant_materializes_to_empty_payload():
    out = StaticVariantSource().materialize(_spec("existing", {}, is_control=True, name="control"))
    assert out == {}


def test_static_ref_is_normalized_and_passed_through_without_base_dir():
    out = StaticVariantSource().materialize(
        _spec("static", {"type": "react_component", "ref": "variants/v2.tsx"})
    )
    assert out == {"source": "static", "type": "react_component", "ref": "variants/v2.tsx"}


def test_static_inline_records_a_content_hash():
    out = StaticVariantSource().materialize(_spec("static", {"inline": "<Button big/>"}))
    assert out["inline"] == "<Button big/>"
    assert out["sha256"] == hashlib.sha256(b"<Button big/>").hexdigest()


def test_static_ref_with_base_dir_reads_file_and_hashes_it(tmp_path):
    (tmp_path / "variants").mkdir()
    file = tmp_path / "variants" / "v2.tsx"
    file.write_bytes(b"export const V2 = 1;")
    out = StaticVariantSource(base_dir=tmp_path).materialize(
        _spec("static", {"ref": "variants/v2.tsx"})
    )
    assert out["sha256"] == hashlib.sha256(b"export const V2 = 1;").hexdigest()
    assert out["bytes"] == len(b"export const V2 = 1;")


def test_static_ref_with_base_dir_raises_when_file_missing(tmp_path):
    with pytest.raises(AdapterError):
        StaticVariantSource(base_dir=tmp_path).materialize(
            _spec("static", {"ref": "variants/missing.tsx"})
        )


def test_static_without_ref_or_inline_raises():
    with pytest.raises(AdapterError):
        StaticVariantSource().materialize(_spec("static", {"type": "react_component"}))


def test_cannot_materialize_a_non_static_source():
    with pytest.raises(AdapterError):
        StaticVariantSource().materialize(_spec("llm", {"surface_description": "x"}))


def test_inline_hash_is_deterministic():
    a = StaticVariantSource().materialize(_spec("static", {"inline": "same"}))
    b = StaticVariantSource().materialize(_spec("static", {"inline": "same"}))
    assert a["sha256"] == b["sha256"]


def test_register_adds_factory():
    registry.clear_registry()
    try:
        register_static()
        assert registry.get_adapter_factory("variant.static") is StaticVariantSource
    finally:
        registry.clear_registry()
