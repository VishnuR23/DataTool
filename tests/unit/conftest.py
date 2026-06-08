"""Shared fixtures and builders for the core unit tests."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from datatool.core.models import (
    Allocation,
    Authorization,
    Goal,
    Graduation,
    Guardrail,
    ReversionPolicy,
    Scope,
    Statistics,
    Threshold,
    ThresholdType,
    TrustContract,
    VariantSpec,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES_DIR = REPO_ROOT / "examples"
CONFIG_DIR = REPO_ROOT / "config"


def load_yaml(path: Path) -> Any:
    """Parse a YAML file into Python data."""
    return yaml.safe_load(path.read_text())


def make_guardrail(name: str = "error_rate") -> Guardrail:
    return Guardrail(
        name=name,
        source="metrics.csv",
        metric="error_rate",
        threshold=Threshold(type=ThresholdType.RELATIVE_INCREASE, value=0.2),
        window=timedelta(minutes=10),
    )


def make_contract(**overrides: Any) -> TrustContract:
    """Build a minimal valid TrustContract, with optional section overrides."""
    sections: dict[str, Any] = dict(
        scope=Scope(),
        allocation=Allocation(),
        guardrails=[make_guardrail()],
        goal=Goal(source="metrics.csv", metric="signup_rate", direction="increase"),
        statistics=Statistics(),
        reversion=ReversionPolicy(),
        graduation=Graduation(),
        authorization=Authorization(),
    )
    sections.update(overrides)
    return TrustContract(**sections)


def make_contract_dict() -> dict:
    """A minimal contract as a plain dict (for resolution tests)."""
    return {
        "scope": {"assignment_unit": "user", "holdout_pct": 0.0},
        "allocation": {"max_autonomous_pct": 5.0, "ramp_schedule": [1, 2.5, 5]},
        "guardrails": [
            {
                "name": "error_rate",
                "source": "metrics.csv",
                "metric": "error_rate",
                "threshold": {"type": "relative_increase", "value": 0.2},
                "window": "PT10M",
            }
        ],
        "goal": {"source": "metrics.csv", "metric": "signup_rate", "direction": "increase"},
        "statistics": {"alpha": 0.05},
        "reversion": {},
        "graduation": {},
        "authorization": {},
    }


@pytest.fixture
def guardrail() -> Guardrail:
    return make_guardrail()


@pytest.fixture
def contract() -> TrustContract:
    return make_contract()


@pytest.fixture
def contract_dict() -> dict:
    return make_contract_dict()


@pytest.fixture
def two_arm_variants() -> list[VariantSpec]:
    return [
        VariantSpec(name="control", is_control=True, source="existing"),
        VariantSpec(name="treatment", source="static", payload={"ref": "v2.tsx"}),
    ]
