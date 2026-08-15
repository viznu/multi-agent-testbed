"""Paired statistics for run comparisons.

Comparisons are paired on task and seed, report an effect size and a confidence
interval, keep infrastructure attrition separate from evaluation outcomes, and
never headline a single stochastic run.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class PairedComparison:
    n_pairs: int
    mean_a: float
    mean_b: float
    mean_difference: float
    ci_low: float
    ci_high: float
    effect_size: float
    #: Pairs discarded because one side did not produce an evaluable result.
    unpaired_a: int = 0
    unpaired_b: int = 0

    @property
    def ci_excludes_zero(self) -> bool:
        return self.ci_low > 0.0 or self.ci_high < 0.0

    @property
    def effect_size_text(self) -> str:
        if math.isinf(self.effect_size):
            return "d=undefined (every pair moved by the same amount)"
        return f"d={self.effect_size:.3f}"

    def summary(self) -> str:
        direction = "higher" if self.mean_difference > 0 else "lower"
        verdict = (
            "interval excludes zero"
            if self.ci_excludes_zero
            else "interval includes zero; difference is not resolved at this sample size"
        )
        caveat = "" if self.n_pairs >= 5 else f"; only {self.n_pairs} pairs, treat as a pilot"
        return (
            f"n={self.n_pairs} pairs; A={self.mean_a:.4f} B={self.mean_b:.4f}; "
            f"A is {abs(self.mean_difference):.4f} {direction} "
            f"(95% CI [{self.ci_low:.4f}, {self.ci_high:.4f}], {self.effect_size_text}); "
            f"{verdict}{caveat}"
        )


def mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stdev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / (len(values) - 1))


def paired_cohens_d(differences: Sequence[float]) -> float:
    """Paired Cohen's d.

    With zero variance the statistic is undefined, not zero: every pair moving
    by exactly the same non-zero amount is the *strongest* separation, and
    reporting `d=0.000` for it would read as "no effect". Infinity is returned
    so the caller has to say something honest about it.
    """
    sd = stdev(differences)
    if sd:
        return mean(differences) / sd
    average = mean(differences)
    if average == 0:
        return 0.0
    return math.inf if average > 0 else -math.inf


def bootstrap_ci(
    differences: Sequence[float],
    *,
    seed: int = 0,
    iterations: int = 2000,
    alpha: float = 0.05,
) -> tuple[float, float]:
    """Percentile bootstrap over paired differences, seeded so the interval is
    reproducible."""
    if not differences:
        return (0.0, 0.0)
    if len(differences) == 1:
        return (differences[0], differences[0])
    rng = random.Random(seed)
    n = len(differences)
    means = []
    for _ in range(iterations):
        sample = [differences[rng.randrange(n)] for _ in range(n)]
        means.append(mean(sample))
    means.sort()
    low = means[int((alpha / 2) * len(means))]
    high = means[min(int((1 - alpha / 2) * len(means)), len(means) - 1)]
    return (low, high)


def compare_paired(
    a: dict[str, float],
    b: dict[str, float],
    *,
    seed: int = 0,
    iterations: int = 2000,
) -> PairedComparison:
    """Compare two arms keyed by an identical pairing key.

    Keys present on only one side are counted as unpaired rather than silently
    averaged in.
    """
    shared = sorted(set(a) & set(b))
    values_a = [a[k] for k in shared]
    values_b = [b[k] for k in shared]
    differences = [x - y for x, y in zip(values_a, values_b, strict=True)]
    low, high = bootstrap_ci(differences, seed=seed, iterations=iterations)
    return PairedComparison(
        n_pairs=len(shared),
        mean_a=mean(values_a),
        mean_b=mean(values_b),
        mean_difference=mean(differences),
        ci_low=low,
        ci_high=high,
        effect_size=paired_cohens_d(differences),
        unpaired_a=len(set(a) - set(b)),
        unpaired_b=len(set(b) - set(a)),
    )


def required_repetitions(pilot_differences: Sequence[float], *, target_effect: float) -> int:
    """A crude power rule for choosing repetitions from a pilot.

    Returns the paired-t sample size needed to resolve `target_effect` at
    roughly 80% power, 5% two-sided. It is a planning aid, not a guarantee.
    """
    sd = stdev(pilot_differences)
    if sd == 0 or target_effect == 0:
        return 1
    n = ((1.96 + 0.84) * sd / abs(target_effect)) ** 2
    return max(1, math.ceil(n))
