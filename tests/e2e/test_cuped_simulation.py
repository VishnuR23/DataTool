"""CUPED end to end: the same replay, with and without variance reduction (§8.5, §14).

Units have persistent, heterogeneous conversion rates, and the goal is each unit's
in-experiment conversion rate (over 10 sessions), so pre-period behaviour strongly
predicts it (rho^2 ~ 0.58) — the low-variance, high-correlation regime the spec says
CUPED is for. The CSV carries two pre-period days as unassigned rows (empty variant).
With ``enable_cuped`` the controller should become decisive earlier than without it,
on identical data.

(With a single Bernoulli outcome per unit, rho^2 ~ 0.27, CUPED roughly breaks even at
a few thousand units: the variance reduction is offset by the wider support bound
c(1 + |theta|) in the boundary's lower-order term. docs/statistics.md covers this.)
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import yaml

from datatool.persistence.db import session_scope
from datatool.persistence.repositories import DecisionRepository, ExperimentRepository
from datatool.simulator.replay import simulate

START = datetime(2026, 1, 1, tzinfo=UTC)
UNITS_PER_ARM = 3_000
PRE_EVENTS_PER_DAY = 10
SESSIONS = 10  # in-experiment sessions behind each unit's conversion rate
LIFT = 0.05


def _write_csv(path: Path, seed: int = 11) -> Path:
    rng = np.random.default_rng(seed)
    rows = ["timestamp,unit_id,variant,metric,value"]
    for arm, lift in (("control", 0.0), ("treatment", LIFT)):
        p = rng.beta(0.5, 2.0, UNITS_PER_ARM)
        for u in range(UNITS_PER_ARM):
            unit = f"{arm[0]}{u}"
            for day in (2, 1):  # two pre-period days, unassigned
                for k in range(PRE_EVENTS_PER_DAY):
                    ts = START - timedelta(days=day) + timedelta(minutes=10 * k + u % 60)
                    rows.append(f"{ts.isoformat()},{unit},,conversion,{int(rng.random() < p[u])}")
            # One in-experiment outcome per unit, spread over two days.
            ts = START + timedelta(minutes=int(u * 2880 / UNITS_PER_ARM))
            y = rng.binomial(SESSIONS, min(1.0, p[u] + lift)) / SESSIONS
            rows.append(f"{ts.isoformat()},{unit},{arm},conversion,{y}")
            rows.append(f"{ts.isoformat()},{unit},{arm},error_rate,0")
    path.write_text("\n".join(rows) + "\n")
    return path


def _register(cli_env, tmp_path, name: str, cuped: bool) -> None:
    spec = yaml.safe_load(Path(cli_env.example("simulation_demo.yaml")).read_text())
    spec["experiment"] = name
    spec["contract"]["statistics"]["enable_cuped"] = cuped
    if cuped:
        spec["contract"]["statistics"]["cuped_pre_period"] = "P1D"
    path = tmp_path / f"{name}.yaml"
    path.write_text(yaml.safe_dump(spec))
    result = cli_env.invoke("register", str(path))
    assert result.exit_code == 0, result.output


def _first_decisive(result) -> datetime | None:
    return next((s.at for s in result.steps if s.decision_kind in ("ramp", "promote")), None)


def test_cuped_reaches_the_decision_earlier_on_the_same_data(cli_env, tmp_path):
    csv = str(_write_csv(tmp_path / "events.csv"))
    _register(cli_env, tmp_path, "plain", cuped=False)
    _register(cli_env, tmp_path, "cuped", cuped=True)
    factory = cli_env.factory()

    plain = simulate(factory, "plain", csv)
    cuped = simulate(factory, "cuped", csv)

    with session_scope(factory) as s:
        exp = ExperimentRepository(s).get_by_name("cuped")
        outputs = [d.outputs for d in DecisionRepository(s).list_for(exp.id, limit=500)]
    assert outputs and all(o.get("cuped_applied") is True for o in outputs)
    assert len({o["cuped_theta"] for o in outputs}) == 1  # frozen for the whole run
    assert 0 < outputs[0]["cuped_theta"] <= 1

    # Both ship the real winner; CUPED gets there first (~4h earlier at seed 11).
    assert plain.final_state.value == cuped.final_state.value == "promoted"
    assert _first_decisive(cuped) < _first_decisive(plain)
