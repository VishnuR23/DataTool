"""In-terminal charts for the console (plotext, MIT).

``goal_chart`` draws the goal metric's confidence sequence per evaluated cycle: the
point estimate of the lift between its lower and upper bounds, against a zero line.
The controller ships once the whole band clears zero in the goal's direction, so the
band crossing that line is the thing an operator is watching for.
"""

from __future__ import annotations

import threading

import plotext as plt

from datatool.console.live import GoalPoint

# plotext draws on one module-level figure; serialize builds across threads.
_LOCK = threading.Lock()


def goal_chart(points: list[GoalPoint], *, metric: str, width: int = 72, height: int = 16) -> str:
    """Render the confidence sequence as a text chart (ANSI resets only, no colour)."""
    if not points:
        return f"{metric}: no goal statistics yet — the controller has not evaluated a cycle."
    cycles = list(range(1, len(points) + 1))
    with _LOCK:
        plt.clear_figure()
        plt.theme("clear")
        plt.plotsize(width, height)
        plt.plot(cycles, [p.upper for p in points], marker="dot", label="cs upper")
        plt.plot(cycles, [p.estimate for p in points], marker="braille", label="estimate")
        plt.plot(cycles, [p.lower for p in points], marker="dot", label="cs lower")
        plt.hline(0)
        plt.title(f"{metric} lift (treatment − control)")
        plt.xlabel("evaluated cycle")
        return plt.build()
