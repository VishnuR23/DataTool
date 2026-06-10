"""The CUPED module is a deferred stub (ARCHITECTURE.md §8.5).

It must import cleanly (so the package builds) and must fail loudly rather than
silently returning unadjusted data, which would quietly defeat the variance
reduction it claims to provide.
"""

from __future__ import annotations

import numpy as np
import pytest

from datatool.stats.cuped import apply_cuped


def test_cuped_is_deferred_and_raises_not_implemented():
    """apply_cuped raises NotImplementedError until CUPED is built and calibrated.

    Failure means: someone wired CUPED in without its calibration test, risking a
    silently mis-adjusted metric feeding the confidence sequence.
    """
    with pytest.raises(NotImplementedError):
        apply_cuped(np.array([1.0, 2.0, 3.0]), np.array([0.5, 1.0, 1.5]))
