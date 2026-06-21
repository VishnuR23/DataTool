"""Static variant source (ARCHITECTURE.md §11.3).

The default, no-generation path: a variant is either a file path in the user's repo
or inline content carried in the YAML. ``materialize`` validates and normalizes that
payload into what gets stored with the variant; it does not generate anything.

A control variant (``source: existing``) materializes to an empty payload — there is
nothing to ship, it is the current production experience.

If a ``base_dir`` is configured, a referenced file is read so its content hash and
size can be recorded (a useful integrity check for CI). Without one, the reference is
passed through unverified — the file lives in the user's repo, which may not be
present at registration time.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from datatool.adapters import base as registry
from datatool.core.exceptions import AdapterError
from datatool.core.models import VariantSpec

ADAPTER_ID = "variant.static"


class StaticVariantSource:
    """VariantSource for file-referenced or inline static variants."""

    adapter_id = ADAPTER_ID

    def __init__(self, base_dir: str | Path | None = None):
        self._base_dir = Path(base_dir) if base_dir is not None else None

    def materialize(self, variant_spec: VariantSpec) -> dict:
        if variant_spec.source == "existing":
            return {}  # control: the current production experience, nothing to store

        if variant_spec.source != "static":
            raise AdapterError(
                f"static variant source cannot materialize a {variant_spec.source!r} variant"
            )

        payload = variant_spec.payload or {}
        ref = payload.get("ref")
        inline = payload.get("inline")
        if not ref and inline is None:
            raise AdapterError(
                "static variant payload must include 'ref' (a file path) or 'inline' content"
            )

        result: dict = {"source": "static", "type": payload.get("type", "static")}
        if inline is not None:
            result["inline"] = inline
            result["sha256"] = hashlib.sha256(str(inline).encode()).hexdigest()
        if ref:
            result["ref"] = ref
            if self._base_dir is not None:
                path = self._base_dir / ref
                if not path.exists():
                    raise AdapterError(f"static variant file not found: {path}")
                content = path.read_bytes()
                result["sha256"] = hashlib.sha256(content).hexdigest()
                result["bytes"] = len(content)
        return result


def register() -> None:
    """Register the static variant source factory under ``variant.static``."""
    registry.register(ADAPTER_ID, StaticVariantSource)
