"""Playback, visibility and offline purity.

Three separate promises are checked here:

* playback reconstructs a run with zero model, agent or tool calls;
* an agent's view contains no payload it was not authorised to see;
* rescoring cannot write execution events or reach the network.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

from testbed_cli.composition import Registry, Workspace
from testbed_cli.session import run_experiment
from testbed_contracts.enums import EventKind
from testbed_contracts.errors import PlaybackViolation
from testbed_contracts.events import OMNISCIENT_VIEW, PUBLIC_VIEW, EventView
from testbed_eval import no_external_calls, score_run
from testbed_kernel import playback, unauthorized_payloads


@pytest.fixture
def coop_run(coop_manifest, tmp_path: Path):
    manifest = coop_manifest.model_copy(update={"seeds": (0,)})
    results = run_experiment(manifest, tmp_path / "w")
    return manifest, results[0], tmp_path / "w"


def test_playback_requires_an_explicit_view(coop_run):
    _, result, workspace = coop_run
    store, _ = Workspace(workspace).open()
    with pytest.raises(TypeError):
        playback(store, result.run.run_id, "researcher_1")  # positional view is refused
    store.close()


def test_playback_makes_no_external_calls(coop_run):
    _, result, workspace = coop_run
    store, _ = Workspace(workspace).open()
    with no_external_calls():
        replayed = playback(store, result.run.run_id, view=OMNISCIENT_VIEW)
    assert replayed.transcript
    assert replayed.final_state["submitted"] == "alphaomega"
    store.close()


def test_agent_views_contain_no_unauthorized_payloads(coop_run):
    _, result, workspace = coop_run
    store, _ = Workspace(workspace).open()
    for view in ("researcher_1", "researcher_2", PUBLIC_VIEW):
        assert unauthorized_payloads(store, result.run.run_id, view) == []
    store.close()


def test_a_private_instruction_does_not_reach_the_other_agent(coop_run):
    _, result, workspace = coop_run
    store, _ = Workspace(workspace).open()
    events = list(store.read(result.run.run_id))

    one = EventView(events, view="researcher_1")
    two = EventView(events, view="researcher_2")
    omniscient = EventView(events, view=OMNISCIENT_VIEW)

    assert len(omniscient) > len(one)
    invocations_seen_by_two = [
        e for e in two.of_kind(EventKind.AGENT_INVOKED) if e.actor_id == "researcher_1"
    ]
    assert invocations_seen_by_two == [], "one agent must not see another's invocation"
    store.close()


def test_visibility_is_noninterfering(coop_run):
    """Changing an unauthorised private event must not change an agent's view."""
    _, result, workspace = coop_run
    store, _ = Workspace(workspace).open()
    events = list(store.read(result.run.run_id))
    before = [e.payload_hash for e in EventView(events, view="researcher_2")]

    private_to_one = next(
        e
        for e in events
        if e.visibility_policy.value != "public" and "researcher_2" not in e.authorized_view_ids
    )
    mutated = [
        e.model_copy(update={"payload": {"content": "TAMPERED"}}) if e is private_to_one else e
        for e in events
    ]
    after = [e.payload_hash for e in EventView(mutated, view="researcher_2")]
    assert before == after
    store.close()


def test_rescoring_is_pure_and_writes_no_execution_events(coop_run):
    manifest, result, workspace = coop_run
    store, _ = Workspace(workspace).open()
    before = list(store.read(result.run.run_id))

    scores = score_run(
        record=result.run,
        events=before,
        specs=manifest.scorers,
        registry=Registry.discover().scorers,
        verifier=result.verifier,
        agent_ids=tuple(a.agent_id for a in manifest.agents),
    )
    after = list(store.read(result.run.run_id))
    assert len(after) == len(before), "rescoring must not append execution events"
    assert [(s.scorer, s.value) for s in scores.scores] == [
        (s.scorer, s.value) for s in result.scores.scores
    ]
    store.close()


def test_the_purity_guard_actually_blocks_the_network():
    with no_external_calls(), pytest.raises(PlaybackViolation):
        socket.socket()


def test_the_purity_guard_restores_the_network_afterwards():
    with no_external_calls():
        pass
    sock = socket.socket()
    sock.close()


def test_judge_scores_stay_separate_from_hard_success(solo_manifest, tmp_path: Path):
    results = run_experiment(solo_manifest, tmp_path / "w")
    scores = results[0].scores
    assert [s.scorer for s in scores.judged] == ["judge_quality"]
    assert "judge_quality" not in [s.scorer for s in scores.hard]
    judged = scores.judged[0]
    # A judge score is only attributable if it records what produced it.
    assert judged.detail["judge_model"] == "fake/hash-rubric"
    assert judged.detail["prompt_digest"]
    assert judged.detail["transcript_view"] == "omniscient"


def test_scoring_ground_truth_never_enters_an_agent_view(coop_run):
    """Verifier results and settled payoffs are ground truth, not observations.

    They are recorded in full for auditing, and they must not appear in any
    agent's projection: a judge or a viewer reading that projection would
    otherwise be handed the answer key.
    """
    _, result, workspace = coop_run
    store, _ = Workspace(workspace).open()
    events = list(store.read(result.run.run_id))

    omniscient = EventView(events, view=OMNISCIENT_VIEW)
    assert omniscient.of_kind(EventKind.VERIFIER_RESULT), "ground truth must still be recorded"

    for view in ("researcher_1", "researcher_2", PUBLIC_VIEW):
        projected = EventView(events, view=view)
        assert projected.of_kind(EventKind.VERIFIER_RESULT) == ()
        assert projected.of_kind(EventKind.PAYOFF_ASSIGNED) == ()
    store.close()
