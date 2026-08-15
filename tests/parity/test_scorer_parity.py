"""Scorer parity against lm-evaluation-harness.

This job only runs where `lm_eval` is installed, so it is skipped by default and
belongs on a scheduled CI run rather than on every pull request.

Until it has run for a given task, the catalog record for that task stays
`experimental` and no result from it should be compared with a published number.
Two differences are known in advance and are not bugs:

* upstream scores multiple-choice tasks by log-likelihood over the choices;
  this testbed observes a generated answer. The two are not comparable, so only
  generative tasks are checked here.
* upstream applies per-task output filters. Where those differ from the
  normalisation in `metrics`, this test is the place the difference surfaces.
"""

from __future__ import annotations

import pytest

lm_eval = pytest.importorskip("lm_eval", reason="needs the lm-eval extra")

from testbed_packs.lm_eval.metrics import score_answer  # noqa: E402
from testbed_packs.lm_eval.source import load_lm_eval  # noqa: E402

#: Generative tasks only. Add a task here once its items have been inspected.
PARITY_TASKS = ["gsm8k"]


@pytest.mark.parametrize("task", PARITY_TASKS)
def test_items_materialise_with_targets(task: str):
    items = load_lm_eval(task, limit=5)
    assert items, f"{task} produced no items"
    assert all(item.input and item.target for item in items)


@pytest.mark.parametrize("task", PARITY_TASKS)
def test_the_reference_answer_scores_as_correct(task: str):
    """The weakest parity check that still means something.

    If submitting the dataset's own target does not score as correct, the
    normalisation here disagrees with the data's own format, and any accuracy
    measured against that task is meaningless.
    """
    for item in load_lm_eval(task, limit=10):
        result = score_answer(item.target, item.target, metric="numeric")
        assert result.correct, f"{task}/{item.item_id}: target {item.target!r} does not self-match"


@pytest.mark.parametrize("task", PARITY_TASKS)
def test_a_wrong_answer_is_not_scored_correct(task: str):
    for item in load_lm_eval(task, limit=10):
        assert not score_answer("-999999", item.target, metric="numeric").correct
