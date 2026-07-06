"""The assistant's register and generate_variant tools, exercised through the loop.

register runs the real registration pipeline (gated); generate_variant runs the LLM
variant source (a fake client here) under the experiment's contract scope, without
shipping anything.
"""

from __future__ import annotations

import json
from pathlib import Path

from datatool.console.assistant import Assistant, AssistantTurn, ToolCall, build_tools
from datatool.core.models import State
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import (
    AuditLogRepository,
    ExperimentRepository,
    VariantRepository,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


class FakeChatClient:
    def __init__(self, turns):
        self._turns = list(turns)

    def reply(self, *, system, transcript, tools):
        return self._turns.pop(0)


class FakeVariantClient:
    """Returns a fixed, valid generation regardless of the prompt."""

    def generate(self, *, model, prompt, seed):
        return json.dumps(
            {
                "summary": "clearer headline",
                "rationale": "states the value prop up front",
                "code": "export const Headline = () => <h1>Save more</h1>",
            }
        )


def test_register_tool_creates_the_experiment_when_confirmed(session_factory):
    definition = (_REPO_ROOT / "examples" / "pricing_page.yaml").read_text()
    client = FakeChatClient(
        [
            AssistantTurn(
                text="",
                tool_calls=[ToolCall(id="t1", name="register", input={"definition": definition})],
            ),
            AssistantTurn(text="registered it.", tool_calls=[]),
        ]
    )
    tools = build_tools(session_factory, config_dir=str(_REPO_ROOT / "config"))
    assistant = Assistant(client, tools, confirm=lambda call: True)

    assistant.send("register this experiment")

    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).get_by_name("pricing-headline-clarity")
        assert exp is not None
        assert exp.state == State.PROPOSED.value
        kinds = [e.kind for e in AuditLogRepository(s).list_for(exp.id)]
    assert "experiment.registered" in kinds


def test_register_tool_is_gated_and_declined_registers_nothing(session_factory):
    definition = (_REPO_ROOT / "examples" / "pricing_page.yaml").read_text()
    client = FakeChatClient(
        [
            AssistantTurn(
                text="",
                tool_calls=[ToolCall(id="t1", name="register", input={"definition": definition})],
            ),
            AssistantTurn(text="left it unregistered.", tool_calls=[]),
        ]
    )
    tools = build_tools(session_factory, config_dir=str(_REPO_ROOT / "config"))
    assistant = Assistant(client, tools, confirm=lambda call: False)

    assistant.send("register this experiment")

    with session_scope(session_factory) as s:
        assert ExperimentRepository(s).get_by_name("pricing-headline-clarity") is None


def test_generate_variant_runs_freely_under_contract_scope(session_factory):
    with session_scope(session_factory) as s:
        ExperimentRepository(s).add(
            name="headline",
            surface="pricing",
            owner="o",
            contract={"scope": {"forbidden_components": ["PaymentForm"]}},
            spec={},
            state="proposed",
        )

    client = FakeChatClient(
        [
            AssistantTurn(
                text="",
                tool_calls=[
                    ToolCall(
                        id="t1",
                        name="generate_variant",
                        input={"name_or_id": "headline", "surface_description": "clearer headline"},
                    )
                ],
            ),
            AssistantTurn(text="here's a candidate.", tool_calls=[]),
        ]
    )
    tools = build_tools(session_factory, variant_client=FakeVariantClient())
    # A deny-all confirm proves generate_variant is a read — it must still run.
    assistant = Assistant(client, tools, confirm=lambda call: False)

    assistant.send("draft a variant")

    result = assistant.transcript[-2].results[0]
    assert not result.is_error
    assert "clearer headline" in result.content
    # It only proposes: no variant row was persisted to the experiment.
    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).get_by_name("headline")
        assert VariantRepository(s).list_for(exp.id) == []
