"""The Textual console renders the controller's activity from the audit log."""

from datatool.console.app import ConsoleApp
from datatool.persistence.db import session_scope
from datatool.persistence.repositories import ActionRepository, ExperimentRepository


def _seed(session_factory):
    with session_scope(session_factory) as s:
        exp = ExperimentRepository(s).add(
            name="checkout-cta",
            surface="checkout",
            owner="o",
            contract={},
            spec={},
            state="ramping",
        )
        ActionRepository(s).add(
            experiment_id=exp.id,
            decision_id=None,
            kind="promote",
            adapter="postgres",
            payload={},
            clamped=False,
            clamp_reason=None,
            succeeded=True,
            error=None,
        )


async def test_console_renders_a_seeded_action_and_experiment(session_factory):
    _seed(session_factory)
    app = ConsoleApp(session_factory, poll_interval=1000)  # manual drain; no timer races
    async with app.run_test() as pilot:
        await pilot.pause()  # let on_mount run the initial drain + snapshot
        feed_text = "\n".join(app.rendered_lines)
        assert "action promote" in feed_text  # the seeded action surfaced in the feed
        assert "✓" in feed_text  # promote glyph

        assert "checkout-cta" in app.experiments_text  # the experiment shows in the left panel


async def test_console_opens_clean_with_no_data(session_factory):
    app = ConsoleApp(session_factory, poll_interval=1000)
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.rendered_lines == []  # nothing to show, and it does not crash
