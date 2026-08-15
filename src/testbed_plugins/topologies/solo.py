"""One agent, no routing. The compute-matched single-agent baseline uses this."""

from __future__ import annotations

from testbed_pack_sdk import Proposal, RoutingDecision, WorldPorts


class SoloTopology:
    name = "solo"

    def route(self, proposal: Proposal, world: WorldPorts) -> RoutingDecision:
        # A solo agent has nobody to talk to; messages are recorded, not routed.
        return RoutingDecision(recipients=())

    def opening_instruction(self, agent_id: str, world: WorldPorts) -> str | None:
        return "Complete the task."


TOPOLOGY = SoloTopology()
