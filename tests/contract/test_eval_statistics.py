"""Statistics behave the way the plan requires reporting to behave."""

from __future__ import annotations

import pytest

from testbed_contracts.enums import EvalSetKind, RunState
from testbed_contracts.results import RunRecord
from testbed_eval import comparable, compare_paired, required_repetitions
from testbed_eval.compare import compare_experiments
from testbed_eval.stats import bootstrap_ci


def test_paired_comparison_pairs_on_the_key_and_counts_the_rest():
    a = {"t1|0": 1.0, "t2|0": 1.0, "only_in_a": 1.0}
    b = {"t1|0": 0.0, "t2|0": 0.0, "only_in_b": 0.0}
    stats = compare_paired(a, b, iterations=200)
    assert stats.n_pairs == 2
    assert stats.mean_difference == 1.0
    assert stats.unpaired_a == 1 and stats.unpaired_b == 1


def test_an_unresolved_difference_says_so():
    a = {f"t{i}": float(i % 2) for i in range(6)}
    b = {f"t{i}": float((i + 1) % 2) for i in range(6)}
    stats = compare_paired(a, b, iterations=400)
    assert "not resolved" in stats.summary() or stats.ci_excludes_zero


def test_bootstrap_interval_is_seeded_and_reproducible():
    diffs = [0.2, -0.1, 0.4, 0.3, -0.2, 0.1]
    assert bootstrap_ci(diffs, seed=3, iterations=500) == bootstrap_ci(
        diffs, seed=3, iterations=500
    )
    assert bootstrap_ci(diffs, seed=3, iterations=500) != bootstrap_ci(
        diffs, seed=4, iterations=500
    )


def test_a_single_pair_cannot_manufacture_an_interval():
    stats = compare_paired({"t": 1.0}, {"t": 0.0}, iterations=100)
    assert stats.n_pairs == 1
    assert stats.ci_low == stats.ci_high == 1.0


def test_required_repetitions_grows_with_variance():
    quiet = required_repetitions([0.01, -0.01, 0.0], target_effect=0.1)
    noisy = required_repetitions([0.9, -0.8, 0.7], target_effect=0.1)
    assert noisy > quiet


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (EvalSetKind.FROZEN_EVAL, True),
        (EvalSetKind.REGRESSION, True),
        (EvalSetKind.QUARANTINE, False),
        (EvalSetKind.OPTIMIZATION, False),
    ],
)
def test_quarantine_and_optimisation_cases_are_excluded_by_construction(kind, expected):
    record = RunRecord(
        run_id="run_x",
        experiment_id="e",
        manifest_hash="sha256:0",
        task_id="t",
        env_seed=0,
        team_config_id="default",
        eval_set_kind=kind,
    )
    assert comparable(record) is expected


def _result(
    run_id: str,
    *,
    kind: EvalSetKind,
    success: float,
    calls: float,
    state=RunState.COMPLETE,
):
    return {
        "run": RunRecord(
            run_id=run_id,
            experiment_id="e",
            manifest_hash="sha256:0",
            task_id="t",
            env_seed=0,
            team_config_id="default",
            eval_set_kind=kind,
        ).model_dump(mode="json"),
        "state": state,
        "measures": {"model_calls": calls},
        "verifier": {"success": bool(success), "reward": success, "per_agent_payoff": {},
                     "constraints_satisfied": True, "detail": {}},
    }


def test_comparison_excludes_quarantine_and_reports_attrition():
    a = [
        _result("r1", kind=EvalSetKind.FROZEN_EVAL, success=1.0, calls=3),
        _result("r2", kind=EvalSetKind.QUARANTINE, success=1.0, calls=3),
        _result("r3", kind=EvalSetKind.FROZEN_EVAL, success=0.0, calls=3,
                state=RunState.INFRA_FAILED),
    ]
    b = [_result("r1", kind=EvalSetKind.FROZEN_EVAL, success=0.0, calls=3)]
    report = compare_experiments(a, b, experiment_a="a", experiment_b="b", metric="success")
    assert report.excluded_non_comparable == 1
    assert report.arm_a.n_attrition == 1
    assert report.stats.n_pairs == 1
    assert "quarantine" in report.summary()


def test_compute_mismatch_is_reported_not_hidden():
    a = [_result("r1", kind=EvalSetKind.FROZEN_EVAL, success=1.0, calls=30)]
    b = [_result("r1", kind=EvalSetKind.FROZEN_EVAL, success=0.0, calls=3)]
    report = compare_experiments(a, b, experiment_a="a", experiment_b="b", metric="success")
    assert not report.compute_matched
    assert "NOT compute-matched" in report.summary()
