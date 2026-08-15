"""The architectural kill criterion.

Adding a topology, an information partition, a payoff, a fault or a partner
population must be a manifest or plug-in change. If any of them required a new
harness or new kernel scheduling logic, the architecture has failed and this
test is where that shows up.

Everything below is defined *in this file* and injected through the ordinary
composition path -- no kernel source is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from testbed_cli.composition import Registry, Workspace, compose
from testbed_cli.session import run_experiment
from testbed_contracts.enums import FaultKind
from testbed_contracts.manifest import (
    FaultSpec,
    InformationPartition,
    PayoffSpec,
    TeamSpec,
)
from testbed_kernel import RunController
from testbed_pack_sdk import Proposal, RoutingDecision, WorldPorts


class RoundRobinTopology:
    """A brand-new topology, written here, added without touching the kernel."""

    name = "round_robin_test_only"

    def route(self, proposal: Proposal, world: WorldPorts) -> RoutingDecision:
        order = list(world.agents())
        index = order.index(proposal.actor_id)
        return RoutingDecision(recipients=(order[(index + 1) % len(order)],))

    def opening_instruction(self, agent_id: str, world: WorldPorts) -> str | None:
        order = list(world.agents())
        return "start the round" if order and agent_id == order[0] else None


def test_a_new_topology_needs_no_kernel_change(coop_manifest, workspace: Path):
    registry = Registry.discover()
    registry.topologies["round_robin_test_only"] = RoundRobinTopology()

    manifest = coop_manifest.model_copy(
        update={
            "experiment_id": "kill_criterion_topology",
            "world": coop_manifest.world.model_copy(
                update={"topology": "round_robin_test_only"}
            ),
        }
    )
    composition, _, store, _ = compose(manifest, Workspace(workspace), registry)
    assert composition.topology.name == "round_robin_test_only"
    assert len(RunController(composition).plan(manifest)) == 3
    store.close()


def test_a_new_payoff_and_information_partition_are_manifest_only(coop_manifest, workspace):
    """Changing incentives and who knows what is a manifest edit."""
    manifest = coop_manifest.model_copy(
        update={
            "experiment_id": "kill_criterion_payoff",
            "payoff": PayoffSpec(
                mode="mixed_motive",
                team_weight=0.3,
                individual_weight=0.7,
                welfare="min",
            ),
            "world": coop_manifest.world.model_copy(
                update={
                    "information": InformationPartition(
                        public_facts={"round": 1},
                        private_facts={"researcher_1": {"hidden_role": "auditor"}},
                    )
                }
            ),
        }
    )
    results = run_experiment(manifest, workspace)
    assert results
    # The new partition reached the agent that owns it, and nobody else.
    from testbed_kernel import playback

    store, _ = Workspace(workspace).open()
    seen = playback(store, results[0].run.run_id, view="researcher_2").render()
    assert "auditor" not in seen
    store.close()


def test_a_new_fault_is_manifest_only(coop_manifest, workspace):
    """Dropping the hand-off message makes the cooperative task fail, without a
    line of kernel or pack code changing."""
    manifest = coop_manifest.model_copy(
        update={
            "experiment_id": "kill_criterion_fault",
            "faults": (
                FaultSpec(
                    kind=FaultKind.DROP_MESSAGE,
                    targets=("researcher_2",),
                    on_occurrences=(1,),
                ),
            ),
            "seeds": (0,),
        }
    )
    results = run_experiment(manifest, workspace)
    assert len(results) == 1
    assert results[0].state == "task_failed", "dropping the handoff should fail the task"
    assert results[0].measures["faults_injected"] >= 1


def test_a_new_partner_population_is_manifest_only(coop_manifest, workspace):
    manifest = coop_manifest.model_copy(
        update={
            "experiment_id": "kill_criterion_partners",
            "team": TeamSpec(
                team_config_id="held_out_v1",
                partner_population_id="pool_a",
                partner_sampling="held_out",
            ),
            "seeds": (0,),
        }
    )
    results = run_experiment(manifest, workspace)
    assert results[0].run.partner_population_id == "pool_a"
    assert results[0].run.team_config_id == "held_out_v1"


@pytest.mark.parametrize("topology", ["solo", "supervisor", "mesh", "pipeline"])
def test_every_shipped_topology_loads_through_the_registry(topology: str):
    registry = Registry.discover()
    assert topology in registry.topologies
    assert hasattr(registry.topologies[topology], "route")
