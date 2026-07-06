"""The conversational assistant is a gated interface over the operations spine.

Read tools run freely; every tool that mutates a live experiment is executed only
after the operator confirms it. The assistant never decides — it only relays the
operator's intent to the deterministic control plane and narrates the result.
"""

from __future__ import annotations

from datatool.console.assistant import (
    Assistant,
    AssistantTurn,
    ToolCall,
    build_tools,
)
from datatool.core.models import State
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import AuditLogRepository, ExperimentRepository


class FakeChatClient:
    """A scripted chat client: each reply() returns the next queued assistant turn.

    Records the transcript it was handed on each call so tests can assert that tool
    results were fed back into the conversation.
    """

    def __init__(self, turns: list[AssistantTurn]) -> None:
        self._turns = list(turns)
        self.transcripts: list[list] = []

    def reply(self, *, system, transcript, tools) -> AssistantTurn:
        self.transcripts.append(list(transcript))
        return self._turns.pop(0)


def _seed(session_factory, *, name="checkout-cta", state="ramping"):
    with session_scope(session_factory) as s:
        ExperimentRepository(s).add(
            name=name, surface="checkout", owner="o", contract={}, spec={}, state=state
        )


def _state(session_factory, name) -> str:
    with session_scope(session_factory) as s:
        return ExperimentRepository(s).get_by_name(name).state


def test_read_tool_runs_without_confirmation(session_factory):
    _seed(session_factory)
    client = FakeChatClient(
        [
            AssistantTurn(
                text="", tool_calls=[ToolCall(id="t1", name="list_experiments", input={})]
            ),
            AssistantTurn(text="you have one experiment ramping.", tool_calls=[]),
        ]
    )
    # A deny-all confirm proves reads never consult the gate.
    assistant = Assistant(client, build_tools(session_factory), confirm=lambda call: False)

    reply = assistant.send("what's running?")

    assert reply == "you have one experiment ramping."
    fed_back = assistant.transcript[-2]  # the tool-result turn before the final text
    assert "checkout-cta" in fed_back.results[0].content


def test_mutating_tool_is_gated_and_declined_leaves_state_untouched(session_factory):
    _seed(session_factory)
    client = FakeChatClient(
        [
            AssistantTurn(
                text="",
                tool_calls=[ToolCall(id="t1", name="pause", input={"name_or_id": "checkout-cta"})],
            ),
            AssistantTurn(text="okay, I left it running.", tool_calls=[]),
        ]
    )
    assistant = Assistant(client, build_tools(session_factory), confirm=lambda call: False)

    assistant.send("pause checkout-cta")

    assert _state(session_factory, "checkout-cta") == State.RAMPING.value  # untouched
    result = assistant.transcript[-2].results[0]
    assert "declined" in result.content.lower()


def test_mutating_tool_executes_when_operator_confirms(session_factory):
    _seed(session_factory)
    seen: list[ToolCall] = []

    def confirm(call: ToolCall) -> bool:
        seen.append(call)
        return True

    client = FakeChatClient(
        [
            AssistantTurn(
                text="",
                tool_calls=[ToolCall(id="t1", name="pause", input={"name_or_id": "checkout-cta"})],
            ),
            AssistantTurn(text="done, it's holding now.", tool_calls=[]),
        ]
    )
    assistant = Assistant(client, build_tools(session_factory), confirm=confirm)

    assistant.send("pause checkout-cta")

    assert seen and seen[0].name == "pause"  # the gate saw the mutating call
    assert _state(session_factory, "checkout-cta") == State.HOLDING.value
    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).get_by_name("checkout-cta")
        kinds = [e.kind for e in AuditLogRepository(s).list_for(exp.id)]
    assert "experiment.paused" in kinds  # the action was audited


def test_unknown_tool_surfaces_an_error_and_the_loop_continues(session_factory):
    client = FakeChatClient(
        [
            AssistantTurn(text="", tool_calls=[ToolCall(id="t1", name="nope", input={})]),
            AssistantTurn(text="sorry, I can't do that.", tool_calls=[]),
        ]
    )
    assistant = Assistant(client, build_tools(session_factory), confirm=lambda call: True)

    reply = assistant.send("do something impossible")

    assert reply == "sorry, I can't do that."
    result = assistant.transcript[-2].results[0]
    assert result.is_error and "unknown tool" in result.content.lower()


def test_status_and_why_narrate_from_the_audit_log(session_factory):
    _seed(session_factory)
    client = FakeChatClient(
        [
            AssistantTurn(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="status", input={"name_or_id": "checkout-cta"}),
                    ToolCall(id="t2", name="why", input={"name_or_id": "checkout-cta"}),
                ],
            ),
            AssistantTurn(text="it's ramping with no decisions yet.", tool_calls=[]),
        ]
    )
    assistant = Assistant(client, build_tools(session_factory), confirm=lambda call: False)

    assistant.send("status and why for checkout-cta")

    results = {r.tool_use_id: r.content for r in assistant.transcript[-2].results}
    assert "ramping" in results["t1"]
    assert "checkout-cta" in results["t2"]


def test_runaway_tool_loop_is_bounded(session_factory):
    # A client that always asks for a read tool would loop forever without a guard.
    always = AssistantTurn(
        text="", tool_calls=[ToolCall(id="t", name="list_experiments", input={})]
    )
    client = FakeChatClient([always] * 100)
    assistant = Assistant(
        client, build_tools(session_factory), confirm=lambda call: False, max_steps=3
    )

    reply = assistant.send("loop forever")

    assert "step limit" in reply.lower()
