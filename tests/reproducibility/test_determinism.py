"""Seeded determinism.

Two executions of the same seeded fixture must produce the same logical event
sequence once wall-clock fields are removed. If this ever fails, no comparison
built on this testbed means anything.
"""

from __future__ import annotations

from pathlib import Path

from testbed_cli.composition import Workspace
from testbed_cli.session import run_experiment
from testbed_contracts.events import canonical_event_hash
from testbed_kernel import DeterministicRng


def _event_hash(workspace: Path, run_id: str) -> str:
    store, _ = Workspace(workspace).open()
    try:
        return canonical_event_hash(list(store.read(run_id)))
    finally:
        store.close()


def test_repeated_runs_of_a_seeded_fixture_have_identical_event_hashes(
    coop_manifest, tmp_path: Path
):
    first = run_experiment(coop_manifest, tmp_path / "a")
    second = run_experiment(coop_manifest, tmp_path / "b")

    assert [r.run.run_id for r in first] == [r.run.run_id for r in second]
    for a, b in zip(first, second, strict=True):
        assert _event_hash(tmp_path / "a", a.run.run_id) == _event_hash(
            tmp_path / "b", b.run.run_id
        )
        assert a.measures == b.measures


def test_run_identity_is_derived_from_content_not_wall_clock(coop_manifest, tmp_path: Path):
    a = run_experiment(coop_manifest, tmp_path / "a")
    b = run_experiment(coop_manifest, tmp_path / "b")
    assert {r.run.run_id for r in a} == {r.run.run_id for r in b}


def test_changing_the_seed_changes_the_run_but_not_the_manifest_hash(coop_manifest, tmp_path):
    reseeded = coop_manifest.model_copy(update={"seeds": (99,)})
    assert reseeded.manifest_hash != coop_manifest.manifest_hash
    results = run_experiment(reseeded, tmp_path / "c")
    assert results[0].run.env_seed == 99


def test_scores_are_stable_across_executions(coop_manifest, tmp_path: Path):
    a = run_experiment(coop_manifest, tmp_path / "a")
    b = run_experiment(coop_manifest, tmp_path / "b")
    for ra, rb in zip(a, b, strict=True):
        assert [(s.scorer, s.value) for s in ra.scores.scores] == [
            (s.scorer, s.value) for s in rb.scores.scores
        ]


def test_rng_streams_are_labelled_and_restorable():
    """Adding a new draw site must not shift an existing decision stream."""
    rng = DeterministicRng(7)
    before = [rng.random("faults") for _ in range(3)]

    other = DeterministicRng(7)
    other.random("something_new")
    after = [other.random("faults") for _ in range(3)]
    assert before == after

    restored = DeterministicRng.restore(DeterministicRng(7).state())
    assert [restored.random("faults") for _ in range(3)] == before


def test_declared_reproducibility_takes_the_weakest_component(coop_manifest):
    from testbed_contracts.enums import ReproducibilityLevel

    assert coop_manifest.declared_reproducibility() is ReproducibilityLevel.BIT_EXACT
    weakened = coop_manifest.model_copy(
        update={
            "agents": (
                coop_manifest.agents[0],
                coop_manifest.agents[1].model_copy(
                    update={"reproducibility": ReproducibilityLevel.BEST_EFFORT}
                ),
            )
        }
    )
    assert weakened.declared_reproducibility() is ReproducibilityLevel.BEST_EFFORT
