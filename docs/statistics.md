# Statistics

This is the document to read if you are evaluating whether to trust DataTool to run experiments without a human in the loop. It explains every statistical method the controller uses, the paper each one comes from, the choices we made and why, how we prove the methods are calibrated, and — just as important — where they fall short.

The statistics module (`datatool/stats/`) is the project's moat, and we hold it to a higher bar than the rest of the codebase: every algorithm cites its paper in the docstring, and every guarantee is backed by a calibration test that gates the build. A failing test in `tests/stats/` blocks the merge. There are no silent shortcuts.

## The problem we are solving

An autonomous controller looks at the data on *every* decision cycle and acts the moment the evidence justifies it — ramp, hold, promote, or revert. That is exactly the workflow that breaks classical statistics.

A fixed-horizon confidence interval or t-test is valid only if you look **once**, at a sample size fixed in advance. If you peek repeatedly and stop as soon as the interval excludes zero, the true false-positive rate is far above the nominal α — the more often you peek, the worse it gets. This is the "peeking" problem, and it is the single most common way A/B testing programs ship losers as winners.

DataTool peeks continuously by design, so it cannot use fixed-horizon inference. Everything below follows from that one constraint:

- **Goal metrics** use *confidence sequences* — intervals valid under continuous monitoring (§ confidence sequences).
- **Many concurrent experiments** are kept honest by *online FDR control* (§ online FDR control).
- **Assignment integrity** is checked on every cycle before any inference runs (§ sample ratio mismatch).
- **Safety metrics** are watched on a fast, anti-flapping loop (§ guardrails).

## Confidence sequences (the primary method)

> Howard, S. R., Ramdas, A., McAuliffe, J., & Sekhon, J. (2021). *Time-uniform, nonparametric, nonasymptotic confidence sequences.* Annals of Statistics, 49(2), 1055–1080. [arXiv:1810.08240](https://arxiv.org/abs/1810.08240)

Implemented in `datatool/stats/confidence_sequence.py`.

A confidence sequence (CS) is a sequence of intervals `(L_t, U_t)` that covers the true parameter **simultaneously for all t** with probability at least `1 − α`:

```
P( there exists t ≥ 1 such that θ ∉ (L_t, U_t) )  ≤  α
```

The quantifier is the whole point. A classical CI promises coverage at one pre-chosen `t`; a CS promises coverage at *every* `t` at once. So you may stop at any data-dependent time — including "the first cycle the interval excludes zero" — and your type-I error is still bounded by α. Peeking is free.

### What we build, line by line

We construct a CS for each arm's mean from the **polynomial-stitching uniform boundary** (Howard et al. 2021, the stitched boundary of their §3.6, Theorem 1), instantiated for the *sub-gamma* process whose intrinsic time is the empirical variance process. That sub-gamma / empirical-variance instance is the **empirical-Bernstein** confidence sequence (their §3.7, Theorem 4). The brief's shorthand "polynomial mixture, ρ = 1.4" is this boundary with stitching exponent `s = 1.4`.

The boundary function `poly_stitching_bound(v, α, …)` returns `u(v)` such that, for a sub-gamma process `S_t` with variance process `V_t` and scale `c`,

```
P( there exists t such that S_t ≥ u(V_t) )  ≤  α
```

Every constant in the implementation is tied to an equation in the paper and matches the authors' reference [`confseq`](https://github.com/gostevehoward/confseq) library (consulted, never depended on — re-derived in pure Python with comments tying each line to the paper):

- `s = 1.4` — the polynomial stitching exponent; the boundary grows at the law-of-the-iterated-logarithm rate with this power. Valid for any `s > 1`.
- `η = 2.0` — geometric spacing of the stitched epochs. Valid for any `η > 1`.
- `v_min = 1.0` — the intrinsic time at which the boundary is tightest. Validity holds for *any* `v_min > 0` (Theorem 1); `v_min` only trades early-time tightness against late-time tightness. We keep it at 1.0 so the boundary stays very wide while an arm has near-zero observed variance, which suppresses spurious early stops.

The boundary bounds the centred sum process `S_t = Σ(X_i − μ) = n·(mean − μ)`, so the half-width on the *mean* is `u(V_t) / n`, with the variance process `V_t = Σ(X_i − mean)² = sum_sq − n·mean²`.

### From per-arm means to the difference

The quantity we actually care about is `mean(treatment) − mean(control)`. We form its CS by a **Bonferroni split** across the two independent per-arm CSs: the difference CS excludes its true value only if one of *four* one-sided boundary-crossing events occurs (control or treatment, low or high side), so each one-sided boundary runs at `α / 4`. The half-widths add:

```
half_width = u_control(V_control)/n_control + u_treatment(V_treatment)/n_treatment
```

This makes `P( there exists t : (mean_T − mean_C) ∉ (L_t, U_t) ) ≤ α`, time-uniformly.

### Decision: this construction is deliberately conservative

The Bonferroni split is not the tightest possible difference CS — a direct martingale on the paired difference would be narrower. We chose the split on purpose:

- It is **rigorously valid** with an elementary union-bound argument — no subtle cross-arm dependence to get wrong.
- Its only cost is **wider intervals**, which means experiments take *longer* to reach significance, never that they reach it *wrongly*. The error is always on the safe side for an autonomous controller.
- A tighter direct-difference martingale is tracked as future work; adopting it would only narrow the interval, never invalidate it.

This is why the realized type-I error in calibration is essentially **0.0000** rather than sitting just under 0.05 (see § calibration). The CS is conservative, and for a tool that ships product changes without asking, that is the correct direction to be wrong in.

### `max_value` is an a-priori bound, not a running max

The empirical-Bernstein construction needs an upper bound `c` on the support of the metric — the sub-gamma scale. `ArmStats.max_value` is that bound (e.g. `1.0` for a rate in `[0, 1]`), **set in advance**, not the largest value observed so far. Using a running max would peek at the data to set a parameter the guarantee depends on, quietly breaking coverage.

The goal's bound comes from the contract: `goal.max_value`, default `1.0`, which is correct for rates and anything in `[0, 1]` (conversion, click-through, retention). A continuous metric needs a true cap declared up front — e.g. revenue per visitor clipped at a known maximum order value, with the metrics pipeline enforcing the clip. Values must lie in `[0, max_value]`. Coverage at a wider bound is checked in the stats gate (`test_cs_type_i_error_holds_on_a_wider_declared_support`, `c = 100`). A larger `c` costs power: intervals widen with the scale, so declare the tightest cap that is actually true.

## Online FDR control (LORD++)

> Javanmard, A. & Montanari, A. (2018). *Online rules for control of false discovery rate and false discovery exceedance.* Annals of Statistics, 46(2), 526–554.
>
> LORD++ update: Ramdas, A., Yang, F., Wainwright, M. J., & Jordan, M. I. (2017). *Online control of the false discovery rate with decaying memory.* NeurIPS.

Implemented in `datatool/stats/fdr.py`.

Controlling each experiment's type-I error at α is not enough once an org runs many experiments over time. By chance alone, a steady stream of true nulls will occasionally cross any fixed threshold, so the **fraction of promotions that are false** — the false discovery rate (FDR) — creeps up as you scale. LORD spends a finite "alpha wealth" budget across the infinite sequence of tests so the FDR stays at or below α no matter how many experiments run.

### The rule

Choose α and an initial wealth `W0` with `0 ≤ W0 ≤ α`, plus a non-increasing sequence `γ_j` summing to 1. With rejection (discovery) times `τ_1 < τ_2 < …`, the level for test `t` is

```
α_t = γ_t · W0
      + (α − W0) · γ_{t − τ_1}          # the first discovery
      + α · Σ_{j ≥ 2} γ_{t − τ_j}        # later discoveries
```

Each discovery earns alpha wealth that is paid out over later tests via the decaying `γ` weights; between discoveries the level decays. That decay is what makes the infinite-horizon FDR bound hold for independent or positively dependent p-values.

We use `γ_j ∝ j^{−1.6}`, normalised to sum to 1 by the Riemann zeta value `ζ(1.6)`. Any non-increasing, summable-to-one sequence is valid; this polynomial choice is the one the brief calls out and is easy to reason about. Default initial wealth is `W0 = α / 2`.

In DataTool the org-level controller owns `next_alpha()` and the wealth accounting; the decision engine hands the confidence sequence a per-experiment level of `contract.statistics.fdr_budget_share × next_alpha()` (`ARCHITECTURE.md §8.2`). `fdr_budget_share` defaults to `0.10` — each experiment may consume at most a tenth of the current org-level allocation.

### Decision: we implement LORD++, not the brief's scalar sketch

`ARCHITECTURE.md §8.2` sketched a simpler scalar recursion (`wealth += α − w0` on rejection, `wealth −= next_alpha()` otherwise). We implemented the full LORD++ alpha-investing update instead, **because the scalar sketch does not actually control FDR on its own** — it has no proven guarantee, whereas LORD++ does. This is a deliberate, documented deviation from the brief; it is flagged in the module docstring and in `tests/stats/test_fdr_calibration.py`. The whole reason this module exists is the guarantee, so shipping the version with the proof was non-negotiable.

## Sample ratio mismatch (SRM)

Implemented in `datatool/stats/srm.py`.

SRM is when the observed split of units across variants does not match the configured allocation — a 50/50 experiment receiving a 52/48 split. It is one of the most corrosive A/B failures because it is silent: it signals broken randomization, bot traffic, cache pollution, or instrumentation loss, any of which biases *every* downstream comparison. An experiment with SRM cannot be trusted to promote, no matter how good its goal metric looks.

We detect it with **Pearson's chi-squared goodness-of-fit test** of the observed counts against the counts the configured allocation predicts, with `k − 1` degrees of freedom. The module is a pure function returning a p-value; the policy lives in the decision engine.

Per `ARCHITECTURE.md §8.3`, the controller treats **`p < 0.001`** as SRM and reverts with a distinct `srm_failed` reason. The threshold is deliberately strict: random noise should essentially never cross it, so a trip is strong evidence of broken randomization rather than chance. **This check runs on every decision cycle, before any goal-metric inference, and no experiment can promote without passing it.** It is the first gate, not an afterthought.

## Guardrails

Implemented in `datatool/stats/guardrails.py`.

Guardrails are the fast safety loop, evaluated independently of the goal-metric statistics. Each guardrail compares a metric against a threshold; to avoid reacting to a single noisy blip, it only **trips** after a configured number of *consecutive* breaches (anti-flapping). The module is a pure function — the consecutive-breach counter is passed in and returned, so the decision engine owns persistence and the evaluation has no hidden state, which is what makes it auditable.

### Threshold semantics

A breach is a **strict** exceedance, so a value sitting exactly on the threshold is within limits.

- `absolute` — upper limit on the treatment metric (latency, error count). Breaches when `treatment > threshold`.
- `relative_increase` — breaches when treatment rose above control by more than the threshold fraction: `(treatment − control) / control > threshold`. From a zero control baseline, any positive treatment is an unbounded increase (a breach).
- `relative_decrease` — breaches when treatment fell below control by more than the threshold fraction: `(control − treatment) / control > threshold`. Nothing can decrease below a zero baseline, so it never breaches there.

> Practical note: relative guardrails on rare events are mostly noise — a one-event blip off a tiny baseline reads as a huge relative swing. For rare-event safety metrics, prefer an `absolute` threshold. The simulator's regression demo uses an absolute guardrail for exactly this reason.

### Severity → decision, and anti-flapping

Below `min_samples_per_arm` in either arm, the guardrail is not evaluated; it returns `insufficient_data` and carries the prior consecutive count forward unchanged (a gap in data is neither a breach nor a recovery). Otherwise a breach increments the consecutive counter and a clean cycle resets it to zero. The guardrail trips only once the counter reaches `consecutive_breaches_to_trip`.

A tripped guardrail maps to a decision by severity (`ARCHITECTURE.md §8.4`):

| Severity | On trip |
|----------|---------|
| `critical` | `REVERT` immediately |
| `high` | `REVERT` |
| `medium` | `HOLD` + notification |

## CUPED — variance reduction from pre-period data

> Deng, A., Xu, Y., Kohavi, R., & Walker, T. (2013). *Improving the sensitivity of online controlled experiments by utilizing pre-experiment data.* WSDM '13.

Implemented in [`datatool/stats/cuped.py`](../datatool/stats/cuped.py); feature-flagged **off** by default (`statistics.enable_cuped`, with `cuped_pre_period`).

CUPED regresses out a pre-experiment covariate `X` — here, each unit's mean of the goal metric over the pre-period — that is correlated with the outcome but, measured before randomization, unaffected by the treatment:

```
Y_cv = Y − θ·X,    Var(Y_cv) = Var(Y)·(1 − ρ²)  at  θ = Cov(Y, X) / Var(X)
```

Randomization makes `E[X]` equal in both arms, so the difference of adjusted means is unbiased for the treatment effect for *any* θ that does not look at the outcomes; the usual `E[X]` centering cancels in the difference.

**θ is fixed before the experiment.** The confidence sequence's guarantee is time-uniform over one running sum. Re-fitting θ on in-experiment data at every look would make each look a different data-dependent transform and void that guarantee. So θ is estimated once from pre-period data only — regressing each unit's later pre-window mean on its earlier one (windows `[start − 2p, start − p)` and `[start − p, start)`) — clamped to `[−1, 1]`, recorded on the first CUPED decision (`cuped_theta`), and read back on every later cycle. Late-arriving pre-period data cannot move it.

**Support.** With `Y, X ∈ [0, c]`, `Y − θX` lies in an interval of width `c·(1 + |θ|)`; it is shifted into `[0, c·(1 + |θ|)]` (the shift cancels in the difference) and the CS runs with that wider bound. Everything is computed from per-arm cross-moments (`Σy, Σy², Σx, Σx², Σxy`), so the existing CS is reused unchanged.

**When it helps — and when it doesn't.** The empirical-Bernstein boundary is roughly `√(V·ℓ) + c·ℓ`. CUPED shrinks the first term by `√(1 − ρ²)` and grows the second by `1 + |θ|`, so the gain depends on regime:

- **Low-variance metrics with strong pre-period correlation** (e.g. a per-user conversion rate over many sessions, ρ² ≈ 0.6): a clear win. In the end-to-end test the same replay promotes the same winner about 4 hours sooner.
- **Single Bernoulli outcomes** (ρ² ≈ 0.25): roughly break-even at a few thousand units, where the wider support offsets the variance reduction; the benefit (≈12% narrower intervals at 50k units) appears only at large samples.
- **No pre-period history**: units without pre-period data get `X = 0` — still unbiased, no reduction for them. With no usable pairs, θ = 0 and the result is the plain CS.

**Data path.** CUPED needs a metrics source that implements `query_cuped` ([`CupedMetricsSource`](adapters.md#metricssource--the-data-warehouse-architecturemd-112)); both bundled sources do. The CSV source reads pre-period rows with an empty `variant`; the PostHog source pairs each in-experiment event with the same person's (`person_id`) mean of the metric over the pre-period, which needs `assignment_unit: user`. With `enable_cuped` on and a source or experiment that cannot provide covariates (a source without `query_cuped`, or PostHog with session/account units), the controller uses the plain CS and every decision records `cuped_applied: false` with a `cuped_reason` — it never silently pretends.

## Calibration: how we prove it

These are the tests that turn "we cite good papers" into "the implementation is demonstrably calibrated." They live in `tests/stats/` and are CI gates: a failure blocks the merge. Each uses an explicit, documented seed — a flaky calibration test is worse than none, because it trains the team to ignore the one signal that protects correctness.

Run them with:

```bash
uv run pytest tests/stats
```

As of this writing: **55 passed** (plus one slow power test deselected by default).

### Confidence-sequence type-I error — the most important test in the project

`tests/stats/test_cs_calibration.py` generates 10,000 sequences under H0 (zero true effect), runs the *real* boundary on every step up to n = 5,000, and counts the fraction of sequences whose CS ever excludes zero. Under H0 that fraction is exactly the type-I error, and it must satisfy `realized ≤ α + 0.005`.

- The Monte Carlo slack of `0.005` is ~2.3 standard errors at N = 10,000 (`√(α(1−α)/N) ≈ 0.0022`): wide enough to absorb noise, tight enough to catch a real miscalibration.
- The vectorised Monte Carlo path is pinned to the public scalar `confidence_sequence_diff` step-for-step (`test_vectorized_calibration_matches_scalar_confidence_sequence_diff`), so the test certifies the *shipped* function, not a re-derivation that could drift.
- Calibration is checked across three distributions to prove the guarantee is nonparametric, not an artifact of binary support: low-rate `Bernoulli(0.1)`, maximum-variance `Bernoulli(0.5)`, and continuous `Uniform(0, 1)`.

**Realized type-I error: 0.0000** under the fixed seeds — comfortably under the 0.055 gate. As explained above, the near-zero figure is the expected signature of the conservative Bonferroni-split difference CS, not a fluke. **Failure means the CS is too narrow and the controller would promote losers as winners** — the failure mode that destroys an autonomous controller's credibility.

### Confidence-sequence power (regression guard, not a gate)

`tests/stats/test_cs_power.py::test_cs_detects_two_x_mde_effect_at_least_eighty_percent` checks that the CS detects an effect of size 2× the MDE within a reasonable sample at least 80% of the time. This is a *regression guard*, not a correctness gate — it catches a CS that became uselessly wide. It is marked slow and deselected from the default run; execute it with `uv run pytest -m slow`. Correctness (type-I) is never traded for power: a conservative-but-valid CS is always preferred to a tight-but-wrong one.

### Online-FDR calibration

`tests/stats/test_fdr_calibration.py::test_lord_realized_fdr_at_or_below_target` runs 1,000 independent streams of 200 sequential experiments each, 80% true nulls and 20% real effects (one-sided p-values from `Z ~ N(μ, 1)`, `μ = 3.5` for alternatives), and averages the realized FDR. It must satisfy `realized ≤ target + 0.02`.

**Realized FDR: 0.037** against a 0.05 target. The rest of the file directly exercises the LORD++ update rule: the level decays between discoveries, a rejection raises subsequent levels (alpha-investing), the level is always strictly positive, the `γ` weights sum to 1, and invalid configuration raises rather than mis-controlling.

### SRM correctness

`tests/stats/test_srm.py` verifies the acceptance criterion (`§21.5`): a 60/40 imbalance on a configured 50/50 allocation is detected at `p < 0.001`, while an exact 50/50 split passes. It also covers asymmetric (90/10) and three-way allocations, and confirms structurally invalid input (single variant, zero total, mismatched variant sets, negative counts, non-positive shares) raises rather than returning a meaningless p-value.

### Guardrail anti-flapping

`tests/stats/test_guardrails.py` verifies the property that makes guardrails usable in production: a *single* breach does not trip when two consecutive are required, two consecutive *do* trip, and a clean cycle between breaches resets the counter. It also pins the threshold-boundary semantics (strict exceedance, zero-baseline behaviour), the severity→decision mapping, and the insufficient-data carry-forward. A hypothesis property test asserts the evaluation never raises and never produces an inconsistent state across randomised inputs.

## Known limitations and failure modes

We would rather you learn these from the docs than from a postmortem.

- **The difference CS is conservative.** Experiments take longer to reach significance than a tighter (but more delicate) direct-difference martingale would require. This is a deliberate latency-for-rigor trade; see the confidence-sequences section.
- **Goal metrics need a true, declared support bound.** The empirical-Bernstein scale is `goal.max_value` (default `1.0`); an uncapped metric (raw revenue, latency) has no valid bound and must be clipped upstream first. The decision engine guards the declared bound: when the aggregates prove a value outside `[0, max_value]` (any `x` in `[0, c]` satisfies `sum ≤ c·n` and `sum_sq ≤ c·sum`), it holds with a `support_violation` reason instead of computing bounds. The check is necessary, not sufficient — a few values just above the cap among many small ones can pass it — so the cap must be enforced where the metric is produced.
- **FDR control assumes independent or positively dependent p-values.** Strongly negatively dependent experiment outcomes are outside the proven regime. In practice experiments across an org are close enough to independent that this holds, but it is an assumption, not a theorem about your specific portfolio.
- **CUPED's gain is regime-dependent.** Its wider support bound `c·(1 + |θ|)` offsets part of the variance reduction, so single-Bernoulli metrics at modest sample sizes see little benefit; see the CUPED section. With PostHog, covariates need person-level assignment (`assignment_unit: user`).
- **Calibration is Monte Carlo, not a closed-form proof.** The tests demonstrate calibration to within documented slack at fixed seeds; they do not re-prove the theorems. The theorems are the papers' job, the tests are ours, and the two together are the credential.

## Reading list

The four papers, in priority order for a reviewer:

1. Howard, Ramdas, McAuliffe & Sekhon (2021) — the confidence-sequence guarantee everything else leans on.
2. Javanmard & Montanari (2018) and Ramdas, Yang, Wainwright & Jordan (2017) — online FDR / LORD++.
3. Deng, Xu, Kohavi & Walker (2013) — CUPED (`stats/cuped.py`).

If you read one thing, read Howard et al. (2021) §3.6–3.7 alongside `datatool/stats/confidence_sequence.py`. Every constant in the code points back to an equation in those sections.
