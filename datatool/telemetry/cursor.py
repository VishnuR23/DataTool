"""A tiny on-disk watermark store for the reporter (spec §"Open decisions").

DECISION: the cursor is a local JSON file, NOT a table in the customer's database
— the customer's schema must stay unchanged (spec invariant 3). Per source table we
persist the ISO timestamp of the last row delivered; the reporter re-queries from
there and the panel dedups, giving at-least-once delivery that survives restarts.
"""

from __future__ import annotations

import json
from pathlib import Path


class FileCursor:
    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text())

    def save(self, watermarks: dict[str, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(watermarks))
