"""Read/inspection commands: list, show, status, why, ledger, doctor (§12)."""

from __future__ import annotations


def _register(cli_env):
    assert cli_env.invoke("register", cli_env.example()).exit_code == 0


def test_list_shows_registered_experiments_and_filters(cli_env):
    _register(cli_env)
    listing = cli_env.invoke("list")
    assert listing.exit_code == 0
    assert "pricing-headline-clarity" in listing.output
    assert "proposed" in listing.output

    # Filter by a non-matching state -> not shown.
    assert "pricing-headline-clarity" not in cli_env.invoke("list", "--state", "promoted").output
    # Filter by surface -> shown.
    assert "pricing-headline-clarity" in cli_env.invoke("list", "--surface", "pricing-page").output


def test_list_empty_is_handled(cli_env):
    result = cli_env.invoke("list")
    assert result.exit_code == 0
    assert "no experiments" in result.output


def test_show_renders_detail_and_effective_contract(cli_env):
    _register(cli_env)
    result = cli_env.invoke("show", "pricing-headline-clarity")
    assert result.exit_code == 0
    assert "pricing-headline-clarity" in result.output
    assert "effective contract" in result.output
    assert "error_rate" in result.output  # a guardrail name


def test_show_reflects_graduation_in_effective_ceiling(cli_env):
    _register(cli_env)
    # Base ceiling is 5%. Granting +5% must show as 10% effective.
    cli_env.invoke("graduate", "pricing-page", "--by", "5")
    result = cli_env.invoke("show", "pricing-headline-clarity")
    assert "autonomous ceiling   10%" in result.output


def test_status_is_one_line(cli_env):
    _register(cli_env)
    result = cli_env.invoke("status", "pricing-headline-clarity")
    assert result.exit_code == 0
    assert "pricing-headline-clarity" in result.output
    assert "proposed" in result.output


def test_why_includes_registration_event(cli_env):
    _register(cli_env)
    result = cli_env.invoke("why", "pricing-headline-clarity")
    assert result.exit_code == 0
    assert "experiment.registered" in result.output


def test_unknown_experiment_fails(cli_env):
    result = cli_env.invoke("show", "does-not-exist")
    assert result.exit_code == 1


def test_ledger_shows_graduation_and_net_delta(cli_env):
    cli_env.invoke("graduate", "pricing-page", "--by", "5")
    cli_env.invoke("graduate", "pricing-page", "--by", "3")
    result = cli_env.invoke("ledger", "pricing-page")
    assert result.exit_code == 0
    assert "graduate" in result.output
    assert "+8" in result.output  # net 5 + 3


def test_doctor_passes_against_healthy_environment(cli_env):
    result = cli_env.invoke("doctor")
    assert result.exit_code == 0
    assert "database connectivity" in result.output
    assert "fail" not in result.output
