"""A supervisor routes everything through the agent holding the `supervisor` role.

Only the supervisor is activated at the start; workers speak when spoken to.
"""

from __future__ import annotations

from testbed_pack_sdk import Proposal, RoutingDecision, WorldPorts


class SupervisorTopology:
    name = "supervisor"

    def _supervisor(self, world: WorldPorts) -> str | None:
        for agent_id in world.agents():
            if world.role_of(agent_id) == "supervisor":
                return agent_id
        return None

    def route(self, proposal: Proposal, world: WorldPorts) -> RoutingDecision:
        supervisor = self._supervisor(world)
        if supervisor is None:
            return RoutingDecision(recipients=proposal.requested_recipients)
        if proposal.actor_id == supervisor:
            # The supervisor may address any worker it named.
            return RoutingDecision(recipients=proposal.requested_recipients)
        # A worker's output always goes back to the supervisor, whoever it named.
        return RoutingDecision(recipients=(supervisor,))

    def opening_instruction(self, agent_id: str, world: WorldPorts) -> str | None:
        if world.role_of(agent_id) == "supervisor":
            return "You lead this team. Delegate, then report the result."
        return None


TOPOLOGY = SupervisorTopology()
