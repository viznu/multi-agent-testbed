"""Faults, topologies and the environment driver."""

from __future__ import annotations

from pathlib import Path

import pytest

from testbed_cli.composition import Workspace
from testbed_cli.session import run_experiment
from testbed_contracts.enums import EnvMode, EventKind, FaultKind, RunState, WorldDriverKind
from testbed_contracts.manifest import FaultSpec


def _events(workspace: Path, run_id: str):
    store, _ = Workspace(workspace).open()
    try:
        return list(store.read(run_id))
    finally:
        store.close()


@pytest.mark.parametrize(
    ("kind", "expect_success"),
    [
        (FaultKind.DROP_MESSAGE, False),
        (FaultKind.DELAY_MESSAGE, True),
        (FaultKind.DUPLICATE_MESSAGE, True),
        (FaultKind.CORRUPT_MESSAGE, False),
    ],
)
def test_faults_are_applied_by_world_and_recorded(
    coop_manifest, workspace: Path, kind: FaultKind, expect_success: bool
):
    manifest = coop_manifest.model_copy(
        update={
            "experiment_id": f"fault_{kind}",
            "seeds": (0,),
            # Precisely the hand-off message, not "anything involving agent two".
            "faults": (
                FaultSpec(
                    kind=kind,
                    senders=("researcher_1",),
                    recipients=("researcher_2",),
                    on_occurrences=(1,),
                ),
            ),
        }
    )
    result = run_experiment(manifest, workspace)[0]
    assert result.measures["faults_injected"] >= 1
    assert bool(result.verifier.success) is expect_success

    injected = [
        e for e in _events(workspace, result.run.run_id)
        if e.event_kind is EventKind.FAULT_INJECTED
    ]
    assert injected, "an injected fault must leave an auditable event"
    # Fault records are ground truth, not something an agent gets to observe.
    assert all(e.visibility_policy.value == "omniscient_only" for e in injected)


def test_a_delayed_message_still_arrives(coop_manifest, workspace: Path):
    manifest = coop_manifest.model_copy(
        update={
            "experiment_id": "fault_delay_timing",
            "seeds": (0,),
            "faults": (
                FaultSpec(
                    kind=FaultKind.DELAY_MESSAGE,
                    senders=("researcher_1",),
                    recipients=("researcher_2",),
                    on_occurrences=(1,),
                    delay_ticks=3,
                ),
            ),
        }
    )
    baseline = run_experiment(
        coop_manifest.model_copy(update={"experiment_id": "fault_delay_base", "seeds": (0,)}),
        workspace,
    )[0]
    delayed = run_experiment(manifest, workspace)[0]
    assert delayed.verifier.success
    assert delayed.logical_time > baseline.logical_time


def test_agent_dropout_is_recorded_as_a_policy_decision(coop_manifest, workspace: Path):
    """Dropout is applied through World, so it leaves a policy trail."""
    from testbed_cli.composition import compose
    from testbed_kernel import RunController

    manifest = coop_manifest.model_copy(update={"experiment_id": "dropout", "seeds": (0,)})
    composition, _, store, _ = compose(manifest, Workspace(workspace))
    record = RunController(composition).plan(manifest)[0]
    store.close()

    import anyio

    from testbed_kernel.journal import EventJournal
    from testbed_kernel.rng import DeterministicRng
    from testbed_kernel.world import World

    store, _ = Workspace(workspace).open()
    journal = EventJournal(store, run_id=record.run_id, attempt_id="att_manual")
    world = World(
        manifest=manifest,
        case=composition.pack.case(record.task_id),
        journal=journal,
        rng=DeterministicRng(0),
    )
    world.drop_out("researcher_2")
    assert "researcher_2" not in world.agents()
    decisions = [
        e for e in store.read(record.run_id) if e.event_kind is EventKind.POLICY_DECISION
    ]
    assert any(e.policy_decision.rule == "agent_dropout" for e in decisions)
    store.close()
    del anyio


@pytest.mark.parametrize("mode", [EnvMode.AEC, EnvMode.PARALLEL])
def test_env_driver_runs_in_both_modes(mixed_manifest, workspace: Path, mode: EnvMode):
    manifest = mixed_manifest.model_copy(
        update={
            "experiment_id": f"env_{mode}",
            "world": mixed_manifest.world.model_copy(
                update={"driver": WorldDriverKind.ENV, "env_mode": mode}
            ),
        }
    )
    result = run_experiment(manifest, workspace)[0]
    assert result.state is RunState.COMPLETE
    assert result.verifier.success
    claims = result.verifier.detail["claims"]
    assert set(claims) == {"trader_1", "trader_2"}


def test_topology_choice_changes_routing_not_the_kernel(coop_manifest, workspace: Path):
    """The same agents and the same pack, routed differently.

    Only the manifest's `world.topology` changed. Success is deliberately not
    the assertion here: these fixture scripts assume the mesh message order, so
    the honest observation is that routing -- and therefore which agent hears
    from whom -- is what the topology controls.
    """

    def edges(result) -> set[tuple[str, str]]:
        return {
            (e.payload["sender"], e.payload["recipient"])
            for e in _events(workspace, result.run.run_id)
            if e.event_kind is EventKind.WORLD_MESSAGE_DELIVERED
        }

    mesh = run_experiment(
        coop_manifest.model_copy(update={"experiment_id": "topo_mesh", "seeds": (0,)}), workspace
    )[0]
    pipeline = run_experiment(
        coop_manifest.model_copy(
            update={
                "experiment_id": "topo_pipeline",
                "seeds": (0,),
                "world": coop_manifest.world.model_copy(update={"topology": "pipeline"}),
            }
        ),
        workspace,
    )[0]

    mesh_edges, pipeline_edges = edges(mesh), edges(pipeline)
    assert mesh_edges != pipeline_edges
    # Mesh opens every agent; the pipeline opens only the head of the chain.
    assert ("world", "researcher_2") in mesh_edges
    assert ("world", "researcher_2") not in pipeline_edges
    assert ("researcher_1", "researcher_2") in pipeline_edges
    assert mesh.verifier.success


def test_unknown_actions_are_recorded_not_crashed(mixed_manifest, workspace: Path):
    broken = mixed_manifest.model_dump(mode="json")
    broken["experiment_id"] = "unknown_action"
    broken["agents"][0]["config"]["steps"][0]["action"]["kind"] = "teleport"
    from testbed_contracts.manifest import ExperimentManifest

    result = run_experiment(ExperimentManifest.model_validate(broken), workspace)[0]
    assert result.state in (RunState.TASK_FAILED, RunState.COMPLETE)
    denials = [
        e
        for e in _events(workspace, result.run.run_id)
        if e.event_kind is EventKind.POLICY_DECISION and e.payload.get("rule") == "unknown_action"
    ]
    assert denials, "an unknown action must be denied and recorded, not raised as infra failure"
