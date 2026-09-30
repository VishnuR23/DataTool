"""The goal chart renders the confidence sequence as plain terminal text."""

from __future__ import annotations

import re
from datetime import UTC, datetime

from datatool.console.charts import goal_chart
from datatool.console.live import GoalPoint

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _points(n: int) -> list[GoalPoint]:
    now = datetime.now(UTC)
    return [GoalPoint(at=now, lower=-0.05 + i * 0.01, estimate=0.01, upper=0.07) for i in range(n)]


def test_chart_fits_the_requested_box_and_names_the_metric():
    text = _ANSI.sub("", goal_chart(_points(8), metric="conversion", width=60, height=14))
    lines = text.splitlines()
    assert len(lines) <= 14
    assert all(len(line) <= 60 for line in lines)
    assert "conversion" in text


def test_chart_without_evaluated_cycles_says_so_instead_of_drawing():
    assert "no goal statistics yet" in goal_chart([], metric="conversion")


def test_a_single_cycle_still_renders():
    assert "conversion" in goal_chart(_points(1), metric="conversion")
