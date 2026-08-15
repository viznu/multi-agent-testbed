"""Every agent may address every other agent directly."""

from __future__ import annotations

from testbed_pack_sdk import Proposal, RoutingDecision, WorldPorts


class MeshTopology:
    name = "mesh"

    def route(self, proposal: Proposal, world: WorldPorts) -> RoutingDecision:
        if proposal.requested_recipients:
            return RoutingDecision(recipients=proposal.requested_recipients)
        peers = tuple(a for a in world.agents() if a != proposal.actor_id)
        return RoutingDecision(recipients=peers)

    def opening_instruction(self, agent_id: str, world: WorldPorts) -> str | None:
        return "Work with your peers to complete the task."


TOPOLOGY = MeshTopology()
