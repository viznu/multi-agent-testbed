"""Experiment comparison, including the compute-matched baseline check."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from testbed_contracts.enums import RunState
from testbed_eval.scoring import comparable
from testbed_eval.stats import PairedComparison, compare_paired


@dataclass(frozen=True)
class ArmSummary:
    experiment_id: str
    n_runs: int
    n_evaluable: int
    n_attrition: int
    mean_model_calls: float

    @property
    def attrition_rate(self) -> float:
        return self.n_attrition / self.n_runs if self.n_runs else 0.0


@dataclass(frozen=True)
class ComparisonReport:
    metric: str
    arm_a: ArmSummary
    arm_b: ArmSummary
    stats: PairedComparison
    compute_matched: bool
    excluded_non_comparable: int

    def summary(self) -> str:
        matched = "compute-matched" if self.compute_matched else "NOT compute-matched"
        return (
            f"{self.arm_a.experiment_id} vs {self.arm_b.experiment_id} on {self.metric}\n"
            f"  {self.stats.summary()}\n"
            f"  arms are {matched} "
            f"({self.arm_a.mean_model_calls:.1f} vs "
            f"{self.arm_b.mean_model_calls:.1f} model calls)\n"
            f"  attrition: {self.arm_a.n_attrition}/{self.arm_a.n_runs} vs "
            f"{self.arm_b.n_attrition}/{self.arm_b.n_runs}"
            + (
                f"\n  excluded {self.excluded_non_comparable} non-comparable "
                "(quarantine/optimization) runs"
                if self.excluded_non_comparable
                else ""
            )
        )


def _pairing_key(result: dict[str, Any]) -> str:
    run = result["run"]
    return f"{run['task_id']}|{run['env_seed']}|{run['perturbation_id']}|{run['repetition']}"


def _metric_value(result: dict[str, Any], metric: str) -> float | None:
    if metric in result.get("measures", {}):
        return float(result["measures"][metric])
    scores = (result.get("scores") or {}).get("scores") or []
    for score in scores:
        if score["scorer"] == metric:
            return float(score["value"])
    if metric == "success":
        verifier = result.get("verifier")
        return float(bool(verifier and verifier["success"]))
    return None


def _arm(results: Sequence[dict[str, Any]], experiment_id: str) -> ArmSummary:
    evaluable = [
        r for r in results if r["state"] in (RunState.COMPLETE, RunState.TASK_FAILED)
    ]
    calls = [float(r.get("measures", {}).get("model_calls", 0.0)) for r in evaluable]
    return ArmSummary(
        experiment_id=experiment_id,
        n_runs=len(results),
        n_evaluable=len(evaluable),
        n_attrition=len(results) - len(evaluable),
        mean_model_calls=sum(calls) / len(calls) if calls else 0.0,
    )


def compare_experiments(
    results_a: Sequence[dict[str, Any]],
    results_b: Sequence[dict[str, Any]],
    *,
    experiment_a: str,
    experiment_b: str,
    metric: str = "success",
    seed: int = 0,
    compute_tolerance: float = 0.25,
) -> ComparisonReport:
    """Compare two arms on paired task seeds.

    Runs that ended in an infrastructure or policy state are counted as
    attrition and excluded from the paired statistics rather than scored as
    failures.
    """
    excluded = 0
    paired: list[dict[str, float]] = []
    for results in (results_a, results_b):
        values: dict[str, float] = {}
        for result in results:
            record = _as_record(result)
            if not comparable(record):
                excluded += 1
                continue
            if result["state"] not in (RunState.COMPLETE, RunState.TASK_FAILED):
                continue
            value = _metric_value(result, metric)
            if value is not None:
                values[_pairing_key(result)] = value
        paired.append(values)

    stats = compare_paired(paired[0], paired[1], seed=seed)
    arm_a = _arm(results_a, experiment_a)
    arm_b = _arm(results_b, experiment_b)
    baseline = max(arm_a.mean_model_calls, arm_b.mean_model_calls)
    delta = abs(arm_a.mean_model_calls - arm_b.mean_model_calls)
    matched = baseline == 0 or (delta / baseline) <= compute_tolerance
    return ComparisonReport(
        metric=metric,
        arm_a=arm_a,
        arm_b=arm_b,
        stats=stats,
        compute_matched=matched,
        excluded_non_comparable=excluded,
    )


def _as_record(result: dict[str, Any]) -> Any:
    from testbed_contracts.results import RunRecord

    return RunRecord.model_validate(result["run"])
