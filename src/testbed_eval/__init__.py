"""Scoring, aggregation and statistics.

Scorers are pure functions over immutable event views; aggregation computes
paired statistics across runs without ever changing a sample score.
"""

from testbed_eval.builtin_scorers import SCORERS
from testbed_eval.compare import ArmSummary, ComparisonReport, compare_experiments
from testbed_eval.judges import HashRubricJudge, Judge
from testbed_eval.purity import no_external_calls
from testbed_eval.scoring import UnknownScorer, comparable, score_run
from testbed_eval.stats import (
    PairedComparison,
    bootstrap_ci,
    compare_paired,
    paired_cohens_d,
    required_repetitions,
)

__all__ = [
    "SCORERS",
    "ArmSummary",
    "ComparisonReport",
    "HashRubricJudge",
    "Judge",
    "PairedComparison",
    "UnknownScorer",
    "bootstrap_ci",
    "comparable",
    "compare_experiments",
    "compare_paired",
    "no_external_calls",
    "paired_cohens_d",
    "required_repetitions",
    "score_run",
]
