"""`/show NAME` opens a per-experiment detail view: a stat readout and the goal chart."""

from __future__ import annotations

from textual.widgets import Input

from datatool.console.app import ConsoleApp, ExperimentDetail
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import DecisionRepository, ExperimentRepository


def _seed(session_factory):
    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).add(
            name="pricing",
            surface="checkout",
            owner="o",
            contract={"goal": {"metric": "conversion", "direction": "increase"}},
            spec={},
            state="ramping",
        )
        DecisionRepository(s).add(
            experiment_id=exp.id,
            kind="ramp",
            reason="goal cs clears zero [canary tier]; ramping",
            inputs={},
            outputs={
                "cs_lower": 0.002,
                "cs_point_estimate": 0.03,
                "cs_upper": 0.06,
                "goal_alpha": 0.05,
                "goal_direction": "increase",
                "n_control": 1200,
                "n_treatment": 1188,
                "current_allocation_pct": 25.0,
            },
        )


async def _submit(pilot, app, text):
    inp = app.query_one("#chat_input", Input)
    inp.focus()
    inp.value = text
    await pilot.press("enter")
    await pilot.pause()


async def test_show_opens_a_readout_and_chart_and_escape_returns(session_factory):
    _seed(session_factory)
    app = ConsoleApp(session_factory, poll_interval=1000, chat_client=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _submit(pilot, app, "/show pricing")

        screen = app.screen
        assert isinstance(screen, ExperimentDetail)
        assert "ramping" in screen.readout_text
        # Rendered as plain text: brackets in the readout are not eaten as markup.
        rendered = str(screen.query_one("#readout").render())
        assert "[canary tier]" in rendered
        assert "25.0%" in screen.readout_text
        assert "[0.0020, 0.0600]" in screen.readout_text
        assert "goal cs clears zero [canary tier]; ramping" in screen.readout_text
        assert "conversion" in screen.chart_text

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ExperimentDetail)


async def test_show_unknown_experiment_says_so_and_stays_put(session_factory):
    app = ConsoleApp(session_factory, poll_interval=1000, chat_client=None)
    async with app.run_test() as pilot:
        await pilot.pause()
        await _submit(pilot, app, "/show nope")
        assert not isinstance(app.screen, ExperimentDetail)
        assert any("no experiment named 'nope'" in line for line in app.chat_lines)
