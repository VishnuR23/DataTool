"""The console's chat pane drives the assistant and gates mutating actions in the UI.

Reads flow straight to a reply; a mutating tool pauses for the operator to type /yes
or /no before it runs. With no LLM key the pane still opens and says so.
"""

from __future__ import annotations

from textual.widgets import Input

from datatool.console.app import ConsoleApp
from datatool.console.assistant import AssistantTurn, Tool, ToolCall


class FakeChatClient:
    def __init__(self, turns):
        self._turns = list(turns)

    def reply(self, *, system, transcript, tools):
        return self._turns.pop(0)


async def _pump(pilot, cond, tries=100):
    for _ in range(tries):
        await pilot.pause()
        if cond():
            return
    raise AssertionError("condition never became true")


async def _submit(pilot, app, text):
    inp = app.query_one("#chat_input", Input)
    inp.focus()
    inp.value = text
    await pilot.press("enter")


async def test_disabled_assistant_still_opens_and_says_so(session_factory):
    app = ConsoleApp(session_factory, poll_interval=1000, chat_client=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _submit(pilot, app, "what's running?")
        await pilot.pause()
        joined = "\n".join(app.chat_lines)
        assert "unavailable" in joined.lower()  # graceful degradation, no crash


async def test_read_reply_is_rendered(session_factory):
    client = FakeChatClient([AssistantTurn(text="one experiment is ramping.", tool_calls=[])])
    app = ConsoleApp(session_factory, poll_interval=1000, chat_client=client, tools={})
    async with app.run_test() as pilot:
        await pilot.pause()
        await _submit(pilot, app, "what's running?")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert any("one experiment is ramping." in line for line in app.chat_lines)


async def test_mutating_tool_waits_for_confirmation_then_runs(session_factory):
    ran: list[int] = []
    tools = {
        "pause": Tool(
            "pause",
            "pause it",
            {"type": "object", "properties": {}},
            lambda args: (ran.append(1), "paused checkout-cta.")[1],
            mutating=True,
        )
    }
    client = FakeChatClient(
        [
            AssistantTurn(text="", tool_calls=[ToolCall(id="t1", name="pause", input={})]),
            AssistantTurn(text="done, it's holding now.", tool_calls=[]),
        ]
    )
    app = ConsoleApp(session_factory, poll_interval=1000, chat_client=client, tools=tools)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _submit(pilot, app, "pause checkout-cta")
        await _pump(pilot, lambda: any("confirm" in line.lower() for line in app.chat_lines))
        assert not ran  # nothing ran before the operator approved

        await _submit(pilot, app, "/yes")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert ran == [1]  # the tool ran only after approval
        assert any("done, it's holding now." in line for line in app.chat_lines)


async def test_mutating_tool_declined_never_runs(session_factory):
    ran: list[int] = []
    tools = {
        "pause": Tool(
            "pause",
            "pause it",
            {"type": "object", "properties": {}},
            lambda args: (ran.append(1), "paused.")[1],
            mutating=True,
        )
    }
    client = FakeChatClient(
        [
            AssistantTurn(text="", tool_calls=[ToolCall(id="t1", name="pause", input={})]),
            AssistantTurn(text="okay, I left it running.", tool_calls=[]),
        ]
    )
    app = ConsoleApp(session_factory, poll_interval=1000, chat_client=client, tools=tools)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _submit(pilot, app, "pause checkout-cta")
        await _pump(pilot, lambda: any("confirm" in line.lower() for line in app.chat_lines))

        await _submit(pilot, app, "/no")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert ran == []  # declined → never ran
        assert any("okay, I left it running." in line for line in app.chat_lines)


async def test_show_during_a_pending_approval_leaves_it_pending(session_factory):
    ran: list[int] = []
    tools = {
        "pause": Tool(
            "pause",
            "pause it",
            {"type": "object", "properties": {}},
            lambda args: (ran.append(1), "paused.")[1],
            mutating=True,
        )
    }
    client = FakeChatClient(
        [
            AssistantTurn(text="", tool_calls=[ToolCall(id="t1", name="pause", input={})]),
            AssistantTurn(text="paused it.", tool_calls=[]),
        ]
    )
    app = ConsoleApp(session_factory, poll_interval=1000, chat_client=client, tools=tools)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _submit(pilot, app, "pause checkout-cta")
        await _pump(pilot, lambda: any("confirm" in line.lower() for line in app.chat_lines))

        await _submit(pilot, app, "/show missing")  # a console command, not an answer
        await pilot.pause()
        assert not any("declined" in line for line in app.chat_lines)

        await _submit(pilot, app, "/yes")
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert ran == [1]  # the original approval prompt was still live
