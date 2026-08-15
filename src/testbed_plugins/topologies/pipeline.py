"""A fixed chain: each agent's output is handed to the next one.

Ordering comes from the manifest's agent order, which World already fixes, so
the chain is deterministic without the plug-in tracking any state.
"""

from __future__ import annotations

from testbed_pack_sdk import Proposal, RoutingDecision, WorldPorts


class PipelineTopology:
    name = "pipeline"

    def route(self, proposal: Proposal, world: WorldPorts) -> RoutingDecision:
        order = list(world.agents())
        try:
            index = order.index(proposal.actor_id)
        except ValueError:
            return RoutingDecision(recipients=())
        if index + 1 >= len(order):
            return RoutingDecision(recipients=())  # end of the chain
        return RoutingDecision(recipients=(order[index + 1],))

    def opening_instruction(self, agent_id: str, world: WorldPorts) -> str | None:
        order = list(world.agents())
        if order and agent_id == order[0]:
            return "You are first in the pipeline. Start the work."
        return None


TOPOLOGY = PipelineTopology()
