"""The live terminal console (Textual) — the mission-control readout.

Tails the running controller's audit log and renders its activity as a scannable
instrument trace: a left panel of experiments + their state, and a live event feed
with a semantic glyph per kind (ramp ↑, hold ⏸, promote ✓, revert ⟲, guardrail ⚠).
Read-only: it watches the deterministic brain, it does not drive it.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Footer, Header, RichLog, Static

from datatool.console.events import TelemetryEvent
from datatool.console.live import FeedPoller, experiment_summaries

# Each kind owns a hue; only a guardrail breach alarms (red).
_GLYPH = {"ramp": "↑", "hold": "⏸", "promote": "✓", "revert": "⟲", "guardrail": "⚠"}
_COLOR = {
    "ramp": "cyan",
    "hold": "yellow",
    "promote": "green",
    "revert": "blue",
    "guardrail": "red",
}
_STATE_COLOR = {
    "proposed": "dim",
    "ramping": "cyan",
    "holding": "yellow",
    "promoted": "green",
    "reverted": "blue",
}


class ConsoleApp(App):
    CSS = """
    #experiments { width: 32; border-right: solid $panel; padding: 1; }
    #feed { padding: 0 1; }
    """
    TITLE = "datatool"
    SUB_TITLE = "live console"

    def __init__(self, session_factory, *, poll_interval: float = 1.0) -> None:
        super().__init__()
        self._factory = session_factory
        self._poller = FeedPoller(session_factory)
        self._poll_interval = poll_interval
        # Rendered content mirrored on the app so tests can assert the data
        # pipeline without depending on Textual's internal widget rendering.
        self.rendered_lines: list[str] = []
        self.experiments_text: str = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield Static(id="experiments")
            yield RichLog(id="feed", wrap=True, markup=True, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_experiments()
        self._drain_feed()
        self.set_interval(self._poll_interval, self._tick)

    def _tick(self) -> None:
        self._drain_feed()
        self._refresh_experiments()

    def _drain_feed(self) -> None:
        log = self.query_one("#feed", RichLog)
        for event in self._poller.poll():
            line = self._format(event)
            self.rendered_lines.append(line)
            log.write(line)

    def _format(self, event: TelemetryEvent) -> str:
        glyph = _GLYPH.get(event.kind, "•")
        color = _COLOR.get(event.kind, "white")
        ts = event.occurred_at.strftime("%H:%M:%S")
        exp = rf"[cyan]\[{event.experiment_id}][/] " if event.experiment_id else ""
        return f"[dim]{ts}[/] [{color}]{glyph}[/] {exp}{event.summary}"

    def _refresh_experiments(self) -> None:
        panel = self.query_one("#experiments", Static)
        rows = experiment_summaries(self._factory)
        if not rows:
            text = "[b]experiments[/]\n\n[dim]none yet[/]"
        else:
            lines = ["[b]experiments[/]", ""]
            for r in rows:
                dot_color = _STATE_COLOR.get(r.state, "white")
                lines.append(f"[{dot_color}]●[/] {r.name} [dim]{r.state}[/]")
            text = "\n".join(lines)
        self.experiments_text = text
        panel.update(text)
