"""End-to-end simulator scenarios (ARCHITECTURE.md §14, §21.2).

Register the demo experiment, then replay synthetic data with a known answer and
assert the controller does the right thing: ship a real winner, revert a regression,
conclude a null. This is the acceptance demo (§21.2) in test form.
"""

from __future__ import annotations

import pytest

from datatool.core.models import State
from datatool.simulator.replay import SimulationError, simulate
from datatool.simulator.synthetic import generate_synthetic_csv


def _register_demo(cli_env) -> None:
    result = cli_env.invoke("register", cli_env.example("simulation_demo.yaml"))
    assert result.exit_code == 0, result.output


def test_positive_scenario_ramps_and_promotes(cli_env, tmp_path):
    _register_demo(cli_env)
    csv = generate_synthetic_csv(tmp_path / "positive.csv", scenario="positive")
    result = simulate(cli_env.factory(), "checkout-button-color", str(csv))

    assert result.final_state is State.PROMOTED
    assert result.final_allocation_pct == 100.0
    kinds = [s.decision_kind for s in result.steps]
    assert "ramp" in kinds and "promote" in kinds


def test_regression_scenario_reverts_on_guardrail(cli_env, tmp_path):
    _register_demo(cli_env)
    csv = generate_synthetic_csv(tmp_path / "regression.csv", scenario="regression")
    result = simulate(cli_env.factory(), "checkout-button-color", str(csv))

    assert result.final_state is State.REVERTED
    revert = next(s for s in result.steps if s.decision_kind == "revert")
    assert "guardrail" in revert.reason


def test_null_scenario_concludes_without_shipping(cli_env, tmp_path):
    _register_demo(cli_env)
    csv = generate_synthetic_csv(tmp_path / "null.csv", scenario="null")
    result = simulate(cli_env.factory(), "checkout-button-color", str(csv))

    assert result.final_state is State.CONCLUDED
    kinds = {s.decision_kind for s in result.steps}
    assert "promote" not in kinds and "revert" not in kinds


def test_simulate_cli_renders_a_decision_log(cli_env, tmp_path):
    _register_demo(cli_env)
    csv = generate_synthetic_csv(tmp_path / "positive.csv", scenario="positive")
    result = cli_env.invoke("simulate", "checkout-button-color", "--data", str(csv))
    assert result.exit_code == 0
    assert "promoted" in result.output


def test_simulate_requires_a_proposed_experiment(cli_env, tmp_path):
    """Replaying an already-run experiment fails rather than double-starting it."""
    _register_demo(cli_env)
    csv = generate_synthetic_csv(tmp_path / "positive.csv", scenario="positive")
    factory = cli_env.factory()
    simulate(factory, "checkout-button-color", str(csv))  # now promoted, not proposed
    with pytest.raises(SimulationError):
        simulate(factory, "checkout-button-color", str(csv))
