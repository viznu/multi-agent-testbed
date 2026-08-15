"""The lm-evaluation-harness task pack.

Everything here runs offline against synthetic fixtures. The dataset source is
the only part that needs the upstream dependency, and it is isolated so the
prompt assembly, verification and metric code stay fully covered without it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from testbed_cli.composition import Workspace
from testbed_cli.loader import load_manifest
from testbed_cli.session import run_experiment
from testbed_contracts.enums import EvalSetKind, RunState
from testbed_contracts.events import EventView
from testbed_packs.lm_eval import PACK
from testbed_packs.lm_eval.metrics import METRICS, last_number, normalize, score_answer
from testbed_packs.lm_eval.pack import TARGET_KEY
from testbed_packs.lm_eval.source import LmEvalNotInstalled, load_items, load_jsonl

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


# -- metrics ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("submission", "target", "metric", "correct"),
    [
        ("19", "19", "exact_match", True),
        (" 19 ", "19", "exact_match", True),
        ("nineteen", "19", "exact_match", False),
        ("The Cat", "cat", "normalized_match", True),
        ("a cat.", "the cat", "normalized_match", True),
        ("So the answer is 19.", "19", "numeric", True),
        ("1,650 litres", "1650", "numeric", True),
        ("I think 20", "19", "numeric", False),
        ("no digits here", "19", "numeric", False),
        ("#### 42", "42", "numeric", True),
    ],
)
def test_metric_behaviour(submission, target, metric, correct):
    assert score_answer(submission, target, metric=metric).correct is correct


@pytest.mark.parametrize("answer", ["blue", "B", "b", "  Blue  "])
def test_multiple_choice_accepts_text_letter_or_index(answer):
    result = score_answer(
        answer, "blue", metric="multiple_choice", choices=("mauve", "blue", "beige")
    )
    assert result.correct


def test_multiple_choice_refuses_ambiguous_bare_digits():
    """"2" could mean the second or third option; guessing would shift scores."""
    result = score_answer(
        "2", "blue", metric="multiple_choice", choices=("mauve", "blue", "beige")
    )
    assert not result.correct


def test_a_digit_that_is_itself_a_choice_still_matches():
    result = score_answer("17", "17", metric="multiple_choice", choices=("21", "17", "27"))
    assert result.correct


def test_multiple_choice_rejects_an_out_of_range_letter():
    result = score_answer(
        "Z", "blue", metric="multiple_choice", choices=("mauve", "blue", "beige")
    )
    assert not result.correct


def test_an_unknown_metric_is_rejected():
    with pytest.raises(ValueError, match="unknown metric"):
        score_answer("1", "1", metric="vibes")
    assert "numeric" in METRICS


def test_number_extraction_prefers_the_explicit_marker():
    assert last_number("12 then 34 #### 56") == "56"
    assert last_number("no numbers") is None
    assert normalize("  The  QUICK, brown fox! ") == "quick brown fox"


# -- sources ---------------------------------------------------------------


def test_fixture_items_load_offline():
    items = load_items({"source": "fixture", "fixture": "synthetic_arithmetic"})
    assert len(items) == 5
    assert all(item.target for item in items)


def test_jsonl_round_trips(tmp_path: Path):
    path = tmp_path / "items.jsonl"
    path.write_text('{"id": "x1", "input": "2+2?", "target": "4"}\n', "utf-8")
    items = load_jsonl(path)
    assert items[0].item_id == "x1" and items[0].target == "4"


def test_a_missing_file_is_an_error_not_an_empty_task_set(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_items({"source": "jsonl", "path": str(tmp_path / "nope.jsonl")})


def test_an_unknown_source_is_rejected():
    with pytest.raises(ValueError, match="unknown source"):
        load_items({"source": "telepathy"})


def test_the_lm_eval_source_names_the_extra_when_absent():
    """The pack works without upstream; only this source needs it, and it says
    exactly how to get it."""
    pytest.importorskip  # noqa: B018 - documenting intent, not a skip
    try:
        import lm_eval  # noqa: F401
    except ImportError:
        with pytest.raises(LmEvalNotInstalled, match=r"multi-agent-testbed\[lm-eval\]"):
            load_items({"source": "lm_eval", "task": "gsm8k"})
    else:  # pragma: no cover - only when the extra is installed
        pytest.skip("lm_eval is installed; the missing-dependency path cannot be exercised")


# -- pack configuration ----------------------------------------------------


def test_configuration_selects_items_metric_and_fewshot():
    pack = PACK.configured({"source": "fixture", "metric": "numeric", "num_fewshot": 2})
    cases = pack.tasks.cases()
    assert len(cases) == 3, "few-shot examples must leave the evaluation set"
    assert cases[0].metadata["num_fewshot"] == 2
    assert "A: 19" in cases[0].instruction, "the prompt should carry the examples"
    assert pack.verifiers["default"].metric == "numeric"


def test_a_fewshot_item_is_never_also_scored():
    pack = PACK.configured({"source": "fixture", "num_fewshot": 2})
    assert {c.task_id for c in pack.tasks.cases()}.isdisjoint({"arith_0000", "arith_0001"})


def test_asking_for_more_shots_than_items_is_rejected():
    with pytest.raises(ValueError, match="leaves no evaluation items"):
        PACK.configured({"source": "fixture", "num_fewshot": 99})


def test_limit_applies_before_fewshot_is_taken():
    pack = PACK.configured({"source": "fixture", "limit": 3, "num_fewshot": 1})
    assert len(pack.tasks.cases()) == 2


def test_choice_items_render_options_in_the_prompt():
    pack = PACK.configured(
        {"source": "fixture", "fixture": "synthetic_choice", "metric": "multiple_choice"}
    )
    case = pack.tasks.cases()[0]
    assert "A. mauve" in case.instruction and "B. blue" in case.instruction
    assert case.metadata["choices"]


def test_eval_set_kind_is_configurable_for_quarantined_variants():
    pack = PACK.configured({"source": "fixture", "eval_set_kind": "quarantine"})
    assert pack.tasks.cases()[0].eval_set_kind is EvalSetKind.QUARANTINE


def test_an_unconfigured_pack_yields_nothing_rather_than_a_default_dataset():
    """Silently running some default task would be worse than running none."""
    assert PACK.tasks.cases() == []


# -- end to end ------------------------------------------------------------


def test_the_target_never_reaches_the_agent(workspace: Path):
    """The expected answer lives in world state the projector hides."""
    manifest = load_manifest(EXAMPLES / "lm_eval_floor_baseline.yaml")
    results = run_experiment(manifest, workspace)
    assert results

    store, _ = Workspace(workspace).open()
    run_id = results[0].run.run_id
    targets = {
        c.initial_state[TARGET_KEY]
        for c in PACK.configured(manifest.task_pack.config).tasks.cases()
    }
    view = EventView(store.read(run_id), view="solver")
    blob = " ".join(str(event.payload) for event in view)
    store.close()

    for target in targets:
        assert f"'{target}'" not in blob, "an answer key leaked into the agent's view"


def test_wiring_check_and_floor_baseline_separate(workspace: Path):
    from testbed_eval import compare_experiments

    solver = run_experiment(load_manifest(EXAMPLES / "lm_eval_wiring_check.yaml"), workspace)
    floor = run_experiment(load_manifest(EXAMPLES / "lm_eval_floor_baseline.yaml"), workspace)

    assert all(r.state is RunState.COMPLETE for r in solver)
    assert all(r.state is RunState.TASK_FAILED for r in floor)

    store, _ = Workspace(workspace).open()
    report = compare_experiments(
        store.results_for("lm_eval_wiring_check"),
        store.results_for("lm_eval_floor_baseline"),
        experiment_a="lm_eval_wiring_check",
        experiment_b="lm_eval_floor_baseline",
        metric="success",
    )
    store.close()
    assert report.stats.mean_difference == 1.0
    assert report.compute_matched
    assert "treat as a pilot" in report.stats.summary()


def test_wiring_check_cases_are_labelled_as_such():
    """A run that hands the agent the answer must be impossible to mistake for a
    measurement."""
    pack = PACK.configured({"source": "fixture", "answer_hint": True})
    for case in pack.tasks.cases():
        assert case.metadata["wiring_check_only"] is True
        assert case.private_facts["solver"]["answer"] == case.initial_state[TARGET_KEY]


def test_no_submission_is_a_task_failure_not_a_crash(workspace: Path):
    manifest = load_manifest(EXAMPLES / "lm_eval_floor_baseline.yaml")
    silent = manifest.model_dump(mode="json")
    silent["experiment_id"] = "lm_eval_silent"
    silent["agents"][0]["config"]["steps"] = [{"kind": "final", "content": "I decline."}]
    from testbed_contracts.manifest import ExperimentManifest

    results = run_experiment(ExperimentManifest.model_validate(silent), workspace)
    assert results[0].state is RunState.TASK_FAILED
    assert results[0].verifier.detail["reason"] == "no answer submitted"
