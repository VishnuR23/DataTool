"""The live terminal console (Textual) — the mission-control readout + assistant.

Tails the running controller's audit log and renders its activity as a scannable
instrument trace: a left panel of experiments + their state, and a live event feed
with a semantic glyph per kind (ramp ↑, hold ⏸, promote ✓, revert ⟲, guardrail ⚠).
The feed is read-only: it watches the deterministic brain, it does not drive it.

A chat pane hosts the conversational assistant. The assistant proposes actions via
tools; reads run freely, but any mutating action pauses for the operator to approve
it in the UI (reply ``/yes`` or ``/no``) before the trust-contract-clamped operation
runs. With no LLM key the pane still opens — it just says the assistant is disabled.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, Input, RichLog, Static

from datatool.console.assistant import (
    Assistant,
    ToolCall,
    build_tools,
    default_chat_client_from_env,
)
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

_FROM_ENV = object()  # sentinel: build the chat client from the environment


@dataclass
class _Pending:
    """A mutating tool call blocked in the assistant worker, awaiting operator approval."""

    call: ToolCall
    event: threading.Event
    decision: dict


class ConsoleApp(App):
    CSS = """
    #experiments { width: 32; border-right: solid $panel; padding: 1; }
    #feed { height: 1fr; padding: 0 1; }
    #chat { height: 12; border-top: solid $panel; padding: 0 1; }
    #chat_input { border: none; }
    """
    TITLE = "datatool"
    SUB_TITLE = "live console"

    def __init__(
        self,
        session_factory,
        *,
        poll_interval: float = 1.0,
        chat_client=_FROM_ENV,
        tools=None,
    ) -> None:
        super().__init__()
        self._factory = session_factory
        self._poller = FeedPoller(session_factory)
        self._poll_interval = poll_interval
        # Rendered content mirrored on the app so tests can assert the data
        # pipeline without depending on Textual's internal widget rendering.
        self.rendered_lines: list[str] = []
        self.experiments_text: str = ""
        self.chat_lines: list[str] = []

        client = default_chat_client_from_env() if chat_client is _FROM_ENV else chat_client
        self._assistant = (
            Assistant(client, tools or build_tools(session_factory), confirm=self._confirm)
            if client is not None
            else None
        )
        self._pending: _Pending | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield Static(id="experiments")
            with Vertical():
                yield RichLog(id="feed", wrap=True, markup=True, highlight=False)
                yield RichLog(id="chat", wrap=True, markup=True, highlight=False)
                yield Input(
                    id="chat_input", placeholder="ask the assistant… (approve actions with /yes)"
                )
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_experiments()
        self._drain_feed()
        greeting = (
            "assistant ready. describe an action; I'll ask before changing anything."
            if self._assistant is not None
            else "assistant unavailable — set ANTHROPIC_API_KEY to enable it. the feed is live."
        )
        self._chat_write(f"[cyan]assistant[/] {greeting}")
        self.set_interval(self._poll_interval, self._tick)

    def _tick(self) -> None:
        self._drain_feed()
        self._refresh_experiments()

    # -- feed ---------------------------------------------------------------- #

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

    # -- chat / assistant ---------------------------------------------------- #

    def _chat_write(self, line: str) -> None:
        self.chat_lines.append(line)
        self.query_one("#chat", RichLog).write(line)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        self.query_one("#chat_input", Input).value = ""
        if not text:
            return
        self._chat_write(f"[b]you[/] {text}")

        # An approval prompt takes priority: resolve the blocked mutating call.
        pending = self._pending
        if pending is not None:
            approved = text.lower() in ("y", "yes", "/yes", "ok")
            pending.decision["ok"] = approved
            self._pending = None
            self._chat_write("[dim]— approved[/]" if approved else "[dim]— declined[/]")
            pending.event.set()
            return

        if self._assistant is None:
            self._chat_write(
                "[yellow]assistant unavailable — set ANTHROPIC_API_KEY to enable it.[/]"
            )
            return
        self._send(text)

    @work(thread=True)
    def _send(self, text: str) -> None:
        try:
            reply = self._assistant.send(text)
        except Exception as exc:  # never let the assistant crash the console
            reply = f"error: {exc}"
        self.call_from_thread(self._chat_write, f"[cyan]assistant[/] {reply}")

    def _confirm(self, call: ToolCall) -> bool:
        """Block the assistant worker until the operator approves in the UI (thread-safe)."""
        pending = _Pending(call=call, event=threading.Event(), decision={"ok": False})
        self._pending = pending
        self.call_from_thread(self._render_confirm_prompt, call)
        pending.event.wait()
        return pending.decision["ok"]

    def _render_confirm_prompt(self, call: ToolCall) -> None:
        args = ", ".join(f"{k}={v}" for k, v in call.input.items())
        self._chat_write(
            f"[yellow]⚠ confirm[/] {call.name}({args}) — reply [b]/yes[/] or [b]/no[/]"
        )
