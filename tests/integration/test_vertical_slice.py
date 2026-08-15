"""The M1 vertical slice: three packs, a baseline, and honest terminal states."""

from __future__ import annotations

from pathlib import Path

from testbed_cli.composition import Workspace
from testbed_cli.session import run_experiment
from testbed_contracts.enums import EventKind, RunState
from testbed_contracts.events import EventView
from testbed_eval import compare_experiments


def test_single_agent_deterministic_tool_task(solo_manifest, workspace: Path):
    results = run_experiment(solo_manifest, workspace)
    assert len(results) == 1
    result = results[0]
    assert result.state is RunState.COMPLETE
    assert result.verifier.success
    assert result.verifier.detail["submitted"] == "AC-40921"
    assert result.measures["tool_calls"] == 1


def test_cooperative_task_needs_the_handoff(coop_manifest, workspace: Path):
    """Success here depends on information actually moving between the agents."""
    results = run_experiment(coop_manifest, workspace)
    assert len(results) == 3  # three seeds
    for result in results:
        assert result.state is RunState.COMPLETE
        assert result.verifier.detail["submitted"] == "alphaomega"

    store, _ = Workspace(workspace).open()
    events = EventView(store.read(results[0].run.run_id), view="omniscient")
    delivered = [
        e
        for e in events.of_kind(EventKind.WORLD_MESSAGE_DELIVERED)
        if e.payload["sender"] == "researcher_1"
    ]
    assert delivered, "the cooperative task must involve a real hand-off"
    store.close()


def test_mixed_motive_keeps_individual_and_team_payoffs_separate(mixed_manifest, workspace: Path):
    results = run_experiment(mixed_manifest, workspace)
    result = results[0]
    payoffs = result.verifier.per_agent_payoff
    assert payoffs["trader_1"] != payoffs["trader_2"], "individual payoffs must be distinguishable"
    assert result.measures["welfare"] == sum(payoffs.values())
    assert result.measures["payoff_gini"] > 0.0


def test_overclaiming_fails_the_constraint_not_the_infrastructure(mixed_manifest, workspace: Path):
    greedy = mixed_manifest.model_dump(mode="json")
    greedy["experiment_id"] = "smoke_mixed_overclaim"
    for agent in greedy["agents"]:
        agent["config"]["steps"][0]["action"]["amount"] = 9
    from testbed_contracts.manifest import ExperimentManifest

    results = run_experiment(ExperimentManifest.model_validate(greedy), workspace)
    result = results[0]
    assert result.state is RunState.TASK_FAILED
    assert result.verifier.constraints_satisfied is False
    assert all(v == 0.0 for v in result.verifier.per_agent_payoff.values())
    assert result.is_evaluable, "a task failure is a result, not attrition"


def test_multi_agent_arm_is_compared_against_a_compute_matched_baseline(
    coop_manifest, baseline_manifest, workspace: Path
):
    run_experiment(coop_manifest, workspace)
    run_experiment(baseline_manifest, workspace)

    store, _ = Workspace(workspace).open()
    report = compare_experiments(
        store.results_for("smoke_coop_codeword"),
        store.results_for("smoke_coop_baseline"),
        experiment_a="smoke_coop_codeword",
        experiment_b="smoke_coop_baseline",
        metric="success",
    )
    store.close()

    assert report.stats.n_pairs == 3
    assert report.compute_matched, "the baseline must be compute-matched to be a fair control"
    assert report.arm_a.n_attrition == 0
    # Three paired runs cannot resolve a difference, and the report says so.
    assert "not resolved" in report.stats.summary() or report.stats.ci_excludes_zero


def test_budget_exhaustion_is_attrition_not_task_failure(coop_manifest, workspace: Path):
    starved = coop_manifest.model_copy(
        update={
            "experiment_id": "smoke_coop_starved",
            "limits": coop_manifest.limits.model_copy(update={"max_messages": 0}),
            "seeds": (0,),
        }
    )
    results = run_experiment(starved, workspace)
    result = results[0]
    assert result.state is RunState.POLICY_BLOCKED
    assert result.attrition_reason == "max_messages"
    assert not result.is_evaluable, "a starved run must not be scored as a task failure"
