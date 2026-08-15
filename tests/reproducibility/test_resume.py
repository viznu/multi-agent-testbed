"""Controller death and resume.

The gate: terminating the controller at a checkpoint boundary and resuming must
neither duplicate finished work nor lose it, and must reach the same result an
uninterrupted run reaches.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from testbed_cli.composition import Workspace
from testbed_cli.session import resume_run, run_experiment
from testbed_contracts.enums import EventKind, RunState
from testbed_kernel import SimulatedControllerDeath

#: The events that represent actual work, as opposed to bookkeeping that a
#: second attempt legitimately adds (attempt markers, checkpoint records).
WORK_KINDS = (
    EventKind.WORLD_MESSAGE_DELIVERED,
    EventKind.AGENT_MESSAGE,
    EventKind.WORLD_ACTION,
    EventKind.TOOL_CALLED,
    EventKind.AGENT_FINAL,
    EventKind.WORLD_STATE_CHANGED,
)


def _work(store, run_id):
    return [
        (e.event_kind, e.actor_id, e.logical_time, e.payload_hash)
        for e in store.read(run_id)
        if e.event_kind in WORK_KINDS
    ]


def _single_seed(manifest, experiment_id: str):
    return manifest.model_copy(update={"seeds": (0,), "experiment_id": experiment_id})


def test_death_at_a_checkpoint_resumes_without_duplicating_work(coop_manifest, tmp_path: Path):
    reference = _single_seed(coop_manifest, "resume_reference")
    uninterrupted = run_experiment(reference, tmp_path / "clean")

    crashy = _single_seed(coop_manifest, "resume_reference")
    with pytest.raises(SimulatedControllerDeath):
        run_experiment(crashy, tmp_path / "crashed", crash_after_checkpoints=1)

    store, _ = Workspace(tmp_path / "crashed").open()
    run_id = store.list_runs()[0].run_id
    partial = store.get_run(run_id)
    assert partial.state is RunState.INFRA_FAILED, "a dead controller is infra, not task, failure"
    checkpoints = store.checkpoints(run_id)
    assert checkpoints, "the run must have written a checkpoint before dying"
    store.close()

    resumed = resume_run(crashy, tmp_path / "crashed", run_id)
    assert resumed.state is RunState.COMPLETE
    assert resumed.verifier.success

    store, _ = Workspace(tmp_path / "crashed").open()
    resumed_work = _work(store, run_id)
    # Every logical action was committed exactly once.
    assert len(resumed_work) == len(set(resumed_work))
    submissions = [w for w in resumed_work if w[0] is EventKind.WORLD_ACTION]
    assert len(submissions) == 1, "the submission must not be executed twice"

    keys = [e.idempotency_key for e in store.read(run_id) if e.idempotency_key]
    assert len(keys) == len(set(keys)), "an idempotency key may be committed at most once"
    assert store.superseded(run_id), "uncheckpointed work must be archived, not silently dropped"
    store.close()

    clean_store, _ = Workspace(tmp_path / "clean").open()
    assert _work(clean_store, uninterrupted[0].run.run_id) == resumed_work
    clean_store.close()


def test_resumed_run_reaches_the_same_scores(coop_manifest, tmp_path: Path):
    manifest = _single_seed(coop_manifest, "resume_scores")
    clean = run_experiment(manifest, tmp_path / "clean")

    with pytest.raises(SimulatedControllerDeath):
        run_experiment(manifest, tmp_path / "crashed", crash_after_checkpoints=1)
    store, _ = Workspace(tmp_path / "crashed").open()
    run_id = store.list_runs()[0].run_id
    store.close()
    resumed = resume_run(manifest, tmp_path / "crashed", run_id)

    assert [(s.scorer, s.value) for s in clean[0].scores.scores] == [
        (s.scorer, s.value) for s in resumed.scores.scores
    ]


def test_resume_creates_a_new_attempt_on_the_same_run(coop_manifest, tmp_path: Path):
    manifest = _single_seed(coop_manifest, "resume_attempts")
    with pytest.raises(SimulatedControllerDeath):
        run_experiment(manifest, tmp_path / "w", crash_after_checkpoints=1)
    store, _ = Workspace(tmp_path / "w").open()
    run_id = store.list_runs()[0].run_id
    store.close()

    resume_run(manifest, tmp_path / "w", run_id)

    store, _ = Workspace(tmp_path / "w").open()
    attempts = store.attempts(run_id)
    assert len(attempts) == 2, "a retry is a new attempt, never a new run"
    assert attempts[1].resumed_from_checkpoint is not None
    assert len(store.list_runs()) == 1, "retries must not inflate the number of runs"
    store.close()


def test_an_already_committed_idempotency_key_is_rejected(coop_manifest, tmp_path: Path):
    """The store, not the scheduler, is the last line of defence against a
    duplicated logical action."""
    run_experiment(_single_seed(coop_manifest, "resume_idem"), tmp_path / "w")
    store, _ = Workspace(tmp_path / "w").open()
    run_id = store.list_runs()[0].run_id
    original = [e for e in store.read(run_id) if e.idempotency_key][0]
    replayed = original.model_copy(
        update={"event_id": "evt_replayed", "sequence": 9_999}
    )
    assert store.append(replayed) is False
    store.close()


def test_resume_rejects_a_manifest_that_changed(coop_manifest, tmp_path: Path):
    from testbed_kernel import ResumeIncompatible

    manifest = _single_seed(coop_manifest, "resume_mismatch")
    with pytest.raises(SimulatedControllerDeath):
        run_experiment(manifest, tmp_path / "w", crash_after_checkpoints=1)
    store, _ = Workspace(tmp_path / "w").open()
    run_id = store.list_runs()[0].run_id
    store.close()

    tampered = manifest.model_copy(update={"seeds": (0, 1)})
    with pytest.raises(ResumeIncompatible):
        resume_run(tampered, tmp_path / "w", run_id)
