"""Clamping semantics for the autonomous-authority ceiling (ARCHITECTURE.md §9.3).

The clamp is the safety primitive: it is the single place a desired allocation is
bounded to what the contract permits autonomously. These tests pin that it is
total over [0, 100], reports whether it acted, and refuses nonsense inputs.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from datatool.core.contract import ClampResult, clamp_to_ceiling
from datatool.core.exceptions import ClampError

from .conftest import make_contract


def _contract(ceiling: float):
    from datatool.core.models import Allocation

    return make_contract(allocation=Allocation(max_autonomous_pct=ceiling))


def test_value_below_ceiling_passes_through_unclamped():
    result = clamp_to_ceiling(3.0, _contract(5.0))
    assert result == ClampResult(value=3.0, clamped=False, reason=None)


def test_value_equal_to_ceiling_is_not_clamped():
    result = clamp_to_ceiling(5.0, _contract(5.0))
    assert result.value == 5.0
    assert result.clamped is False
    assert result.reason is None


def test_value_above_ceiling_is_clamped_to_ceiling():
    result = clamp_to_ceiling(25.0, _contract(5.0))
    assert result.value == 5.0
    assert result.clamped is True
    assert result.reason is not None
    assert "5" in result.reason  # mentions the ceiling it clamped to


def test_zero_and_hundred_are_valid_inputs():
    assert clamp_to_ceiling(0.0, _contract(5.0)).value == 0.0
    # 100 desired against a 100 ceiling: legal, unclamped.
    assert clamp_to_ceiling(100.0, _contract(100.0)).clamped is False


@pytest.mark.parametrize("bad", [-0.1, -1, 100.1, 150])
def test_out_of_range_desired_raises_clamp_error(bad):
    with pytest.raises(ClampError):
        clamp_to_ceiling(bad, _contract(5.0))


def test_clamp_result_is_immutable():
    result = clamp_to_ceiling(3.0, _contract(5.0))
    with pytest.raises(FrozenInstanceError):
        result.value = 99.0  # frozen dataclass
