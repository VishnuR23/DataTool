"""End-to-end: an operator-approved assistant action surfaces in the live feed.

Closes the terminal-console loop the design describes — the assistant issues a tool
call, the operator approves it, the trust-contract-clamped operation runs and writes
an append-only audit row, and the console's feed poller picks that row up. The LLM is
faked; the control plane and the feed are real.
"""

from __future__ import annotations

from datatool.console.assistant import Assistant, AssistantTurn, ToolCall, build_tools
from datatool.console.live import FeedPoller
from datatool.core.models import State
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import ExperimentRepository


class _ScriptedClient:
    def __init__(self, turns):
        self._turns = list(turns)

    def reply(self, *, system, transcript, tools):
        return self._turns.pop(0)


def test_assistant_action_lands_in_the_audit_feed(session_factory):
    with session_scope(session_factory) as s:
        ExperimentRepository(s).add(
            name="pricing-headline",
            surface="pricing",
            owner="o",
            contract={},
            spec={},
            state="ramping",
        )

    client = _ScriptedClient(
        [
            AssistantTurn(
                text="",
                tool_calls=[
                    ToolCall(id="t1", name="pause", input={"name_or_id": "pricing-headline"})
                ],
            ),
            AssistantTurn(text="paused it.", tool_calls=[]),
        ]
    )
    assistant = Assistant(client, build_tools(session_factory), confirm=lambda call: True)

    assistant.send("pause the pricing headline experiment")

    # The clamped operation actually transitioned the experiment...
    with session_scope(session_factory) as s:
        assert ExperimentRepository(s).get_by_name("pricing-headline").state == State.HOLDING.value

    # ...and the audit row it wrote surfaces through the console's feed.
    events = FeedPoller(session_factory).poll()
    paused = [e for e in events if e.kind == "experiment.paused"]
    assert paused, "the assistant-driven pause did not reach the feed"
    assert "assistant" in paused[0].summary  # attributed to the assistant actor
