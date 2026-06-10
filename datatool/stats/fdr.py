"""Online false discovery rate control via LORD++.

Reference
---------
Javanmard, A. & Montanari, A. (2018). *Online rules for control of false discovery
rate and false discovery exceedance.* Annals of Statistics, 46(2), 526-554.
LORD++ update: Ramdas, Yang, Wainwright & Jordan (2017), *Online control of the
false discovery rate with decaying memory* (NeurIPS).

Why this exists
---------------
Controlling each experiment's type-I error at ``alpha`` is not enough when an org
runs many experiments over time: by chance alone, a steady stream of true nulls
will occasionally cross any fixed threshold, so the *fraction of promotions that
are false* (the false discovery rate) creeps up. LORD spends a finite "alpha
wealth" budget across the infinite sequence of tests, so the FDR stays at or below
``alpha`` no matter how many experiments run.

The rule (LORD++)
-----------------
Choose ``alpha`` and an initial wealth ``W0`` with ``0 <= W0 <= alpha``, plus a
non-increasing sequence ``gamma_j`` summing to 1. With rejection (discovery) times
``tau_1 < tau_2 < ...``, the level for test ``t`` is

    alpha_t = gamma_t * W0
              + (alpha - W0) * gamma_{t - tau_1}        # the first discovery
              + alpha * sum_{j >= 2} gamma_{t - tau_j}   # later discoveries

Each discovery "earns" alpha wealth that is paid out over later tests via the
decaying ``gamma`` weights; between discoveries the level decays, which is what
makes the infinite-horizon FDR bound hold (for independent or positively
dependent p-values).

In DataTool, the per-experiment level handed to the confidence sequence is
``contract.statistics.fdr_budget_share * next_alpha()`` (ARCHITECTURE.md §8.2);
this controller owns the org-level ``next_alpha()`` and the wealth accounting,
and the decision engine applies the budget share.

Note on the spec
----------------
ARCHITECTURE.md §8.2 sketched a simpler scalar "wealth += alpha - w0" recursion.
We implement the full LORD++ alpha-investing update instead, because it is the
form with a *proven* FDR guarantee; the scalar sketch does not control FDR on its
own. This is a deliberate, documented deviation.
"""

from __future__ import annotations

from scipy.special import zeta

from datatool.core.exceptions import StatisticsError

# Default discount sequence gamma_j proportional to j^{-1.6}, normalised to sum to
# 1 by the Riemann zeta value zeta(1.6). Any non-increasing, summable-to-one
# sequence is valid (Javanmard & Montanari 2018); this polynomial choice is the
# one the architecture brief calls out and is simple to reason about.
_DEFAULT_GAMMA_EXPONENT = 1.6


class LORDController:
    """Sequential FDR controller implementing the LORD++ rule.

    Usage per test::

        level = controller.next_alpha()      # the alpha to test against now
        rejected = p_value <= level
        controller.record_outcome(rejected)  # advance the controller

    The controller is intentionally tiny and deterministic: its entire state is the
    list of rejection (discovery) times and the count of tests seen.
    """

    def __init__(
        self,
        alpha: float = 0.05,
        w0: float | None = None,
        gamma_exponent: float = _DEFAULT_GAMMA_EXPONENT,
    ) -> None:
        if not (0 < alpha < 1):
            raise StatisticsError(f"alpha must be in (0, 1), got {alpha}")
        if w0 is None:
            w0 = alpha / 2  # default initial wealth
        # FDR control requires the initial wealth not exceed the target level.
        if not (0 <= w0 <= alpha):
            raise StatisticsError(f"initial wealth w0 must be in [0, alpha], got {w0}")
        if gamma_exponent <= 1:
            raise StatisticsError(
                f"gamma_exponent must be > 1 so the series converges, got {gamma_exponent}"
            )

        self.alpha = alpha
        self.w0 = w0
        self._gamma_exponent = gamma_exponent
        self._gamma_norm = float(zeta(gamma_exponent))  # sum_{j>=1} j^{-exponent}
        self.tests_seen = 0
        self.rejections: list[int] = []  # 1-indexed test numbers at which we rejected

    def _gamma(self, k: int) -> float:
        """Discount weight gamma_k = k^{-exponent} / zeta(exponent) for k >= 1."""
        if k < 1:
            return 0.0
        return k ** (-self._gamma_exponent) / self._gamma_norm

    def next_alpha(self) -> float:
        """Significance level for the next (not-yet-recorded) test.

        Implements the LORD++ update for test index ``t = tests_seen + 1``.
        """
        t = self.tests_seen + 1
        # Base term: initial wealth paid out over time.
        level = self._gamma(t) * self.w0
        # Earned wealth from each prior discovery. The first discovery earns
        # (alpha - W0); every later discovery earns the full alpha.
        for j, tau in enumerate(self.rejections):
            coefficient = (self.alpha - self.w0) if j == 0 else self.alpha
            level += coefficient * self._gamma(t - tau)
        return level

    def record_outcome(self, rejected: bool) -> None:
        """Advance the controller by one test, recording whether it rejected."""
        self.tests_seen += 1
        if rejected:
            self.rejections.append(self.tests_seen)
