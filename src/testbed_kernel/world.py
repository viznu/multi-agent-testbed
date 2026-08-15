"""World: the single owner of authoritative state, logical time, ordering,
visibility, delivery, faults, snapshots and payoff accounting.

Every execution path goes through this object. A runner provisions dependencies
and translates calls; a topology plugin proposes routing; neither may deliver a
message, advance the clock, resolve a timeout or commit an action.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from testbed_contracts.enums import EventKind, FaultKind, VisibilityPolicy
from testbed_contracts.events import ResourceDelta
from testbed_contracts.manifest import ExperimentManifest
from testbed_kernel.journal import EventJournal
from testbed_kernel.rng import DeterministicRng
from testbed_kernel.scheduler import DeliveryQueue, ScheduledDelivery
from testbed_pack_sdk.hooks import TaskCase
from testbed_pack_sdk.world_ports import (
    Delivery,
    Proposal,
    RoutingDecision,
    StateChange,
    WorldAction,
    WorldSnapshotView,
)

WORLD_ACTOR = "world"


class World:
    """Authoritative state plus the primitives plugins are allowed to use."""

    def __init__(
        self,
        *,
        manifest: ExperimentManifest,
        case: TaskCase,
        journal: EventJournal,
        rng: DeterministicRng,
    ) -> None:
        self.manifest = manifest
        self.case = case
        self.journal = journal
        self.rng = rng

        self.logical_time = 0
        self.finished = False
        self.finish_reason: str | None = None
        self.state: dict[str, Any] = dict(case.initial_state)
        self.queue = DeliveryQueue()
        self.payoffs: dict[str, float] = {a.agent_id: 0.0 for a in manifest.agents}
        self.roles: dict[str, str] = {a.agent_id: a.role for a in manifest.agents}
        self.dropped_agents: set[str] = set()

        # Public facts come from the pack and the manifest; private facts are the
        # information partition that makes complementary-information tasks possible.
        self.public_facts: dict[str, Any] = {
            **case.public_facts,
            **manifest.world.information.public_facts,
        }
        self.private_facts: dict[str, dict[str, Any]] = {
            agent.agent_id: {
                **case.private_facts.get(agent.agent_id, {}),
                **manifest.world.information.private_facts.get(agent.agent_id, {}),
            }
            for agent in manifest.agents
        }

        self.counters: dict[str, int] = {
            "messages": 0,
            "spawns": 0,
            "model_calls": 0,
            "tool_calls": 0,
            "duplicate_actions": 0,
        }
        self.fault_occurrences: dict[int, int] = {}
        self.resources = ResourceDelta()

    # -- WorldPorts --------------------------------------------------------

    def snapshot_view(self) -> WorldSnapshotView:
        return WorldSnapshotView(
            logical_time=self.logical_time,
            state=dict(self.state),
            agent_ids=tuple(self.roles),
            roles=dict(self.roles),
            private_facts={k: dict(v) for k, v in self.private_facts.items()},
            payoffs=dict(self.payoffs),
        )

    def run_id_short(self) -> str:
        return self.journal.run_id.removeprefix("run_")[:8]

    def agents(self) -> Sequence[str]:
        return tuple(a for a in self.roles if a not in self.dropped_agents)

    def role_of(self, agent_id: str) -> str:
        return self.roles.get(agent_id, "worker")

    def visible_state(self, agent_id: str) -> Mapping[str, Any]:
        """What an agent may observe: public state and its own private facts."""
        return {
            **{k: v for k, v in self.state.items() if not k.startswith("_")},
            **self.public_facts,
            "_private": dict(self.private_facts.get(agent_id, {})),
        }

    def rng_choice(self, options: Sequence[Any], *, label: str) -> Any:
        return self.rng.choice(f"plugin:{label}", options)

    # -- clock -------------------------------------------------------------

    def advance_to(self, time: int) -> None:
        if time <= self.logical_time:
            return
        self.logical_time = time
        self.journal.commit(
            kind=EventKind.WORLD_CLOCK_ADVANCED,
            actor_id=WORLD_ACTOR,
            logical_time=self.logical_time,
            payload={"logical_time": self.logical_time},
        )

    # -- messaging ---------------------------------------------------------

    def enqueue(
        self,
        delivery: Delivery,
        *,
        order_hint: int = 0,
        cause_sequence: int | None = None,
    ) -> None:
        """Schedule a delivery, applying communication policy and faults.

        Faults are applied here, by World, so no runner or plugin can bypass or
        double-apply them.
        """
        policy = self.manifest.world.communication
        if delivery.recipient_id not in self.roles:
            self.policy_event("unknown_recipient", False, delivery.recipient_id)
            return
        if delivery.recipient_id in self.dropped_agents:
            self.policy_event("recipient_dropped_out", False, delivery.recipient_id)
            return
        size = len(delivery.content.encode("utf-8"))
        if size > policy.max_message_bytes:
            self.policy_event("max_message_bytes", False, f"{size} bytes")
            return

        due = self.logical_time + policy.delivery_latency + delivery.extra_delay
        item = ScheduledDelivery(
            due_time=due,
            order_hint=order_hint,
            delivery=delivery,
            cause_sequence=cause_sequence,
        )
        for index, fault in enumerate(self.manifest.faults):
            item = self._apply_fault(index, fault, item)  # type: ignore[assignment]
            if item is None:
                return
        self.queue.push(item)
        self.counters["messages"] += 1
        self.resources = self.resources + ResourceDelta(message_bytes=size)

    def _fault_fires(self, index: int, fault: Any) -> bool:
        if fault.kind is FaultKind.AGENT_DROPOUT:
            return False  # handled by the driver at turn boundaries
        occurrence = self.fault_occurrences.get(index, 0) + 1
        self.fault_occurrences[index] = occurrence
        if fault.on_occurrences:
            return occurrence in fault.on_occurrences
        return self.rng.random(f"fault:{index}") < fault.probability

    def _applies_to(self, fault: Any, item: ScheduledDelivery) -> bool:
        """Whether a fault matches this delivery.

        All declared filters must match. A fault with no filters matches every
        delivery.
        """
        if fault.senders and item.delivery.sender_id not in fault.senders:
            return False
        if fault.recipients and item.delivery.recipient_id not in fault.recipients:
            return False
        return not (
            fault.targets
            and item.delivery.recipient_id not in fault.targets
            and item.delivery.sender_id not in fault.targets
        )

    def _apply_fault(
        self, index: int, fault: Any, item: ScheduledDelivery
    ) -> ScheduledDelivery | None:
        if not self._applies_to(fault, item) or not self._fault_fires(index, fault):
            return item
        self.journal.commit(
            kind=EventKind.FAULT_INJECTED,
            actor_id=WORLD_ACTOR,
            logical_time=self.logical_time,
            payload={
                "fault": str(fault.kind),
                "sender": item.delivery.sender_id,
                "recipient": item.delivery.recipient_id,
            },
            visibility=VisibilityPolicy.OMNISCIENT_ONLY,
        )
        if fault.kind is FaultKind.DROP_MESSAGE:
            return None
        if fault.kind is FaultKind.DELAY_MESSAGE:
            return item.model_copy(update={"due_time": item.due_time + fault.delay_ticks})
        if fault.kind is FaultKind.DUPLICATE_MESSAGE:
            self.queue.push(item.model_copy(update={"duplicate_index": 1}))
            self.counters["duplicate_actions"] += 1
            return item
        if fault.kind is FaultKind.CORRUPT_MESSAGE:
            corrupted = item.delivery.model_copy(
                update={"content": item.delivery.content[::-1], "payload": {}}
            )
            return item.model_copy(update={"delivery": corrupted})
        return item

    def drop_out(self, agent_id: str) -> None:
        """Remove an agent from the run (dropout fault or policy decision)."""
        self.dropped_agents.add(agent_id)
        removed = self.queue.drop_for(agent_id)
        self.policy_event("agent_dropout", False, f"{agent_id}: {removed} deliveries dropped")

    # -- routing and actions ----------------------------------------------

    def route(
        self, topology: Any, proposal: Proposal, requested: Sequence[str]
    ) -> RoutingDecision:
        """Ask the topology plugin to propose routing, then validate it.

        World keeps final authority: unknown recipients are dropped and
        broadcasts are checked against the communication policy.
        """
        decision = topology.route(proposal, self)
        recipients = tuple(
            r for r in decision.recipients if r in self.roles and r not in self.dropped_agents
        )
        policy = self.manifest.world.communication
        if not policy.broadcast_allowed and len(recipients) > 1:
            self.policy_event("broadcast_not_allowed", False, proposal.actor_id)
            recipients = recipients[:1]
        return decision.model_copy(update={"recipients": recipients})

    def apply_change(self, action: WorldAction, change: StateChange) -> None:
        """Commit a pack-declared state change. Only World may call this."""
        if change.rejected_reason:
            self.policy_event("action_rejected", False, change.rejected_reason)
            return
        self.state.update(change.updates)
        for agent_id, facts in change.facts_to.items():
            self.private_facts.setdefault(agent_id, {}).update(facts)
        for message in change.messages:
            self.enqueue(message)
        if change.finished:
            self.finished = True
            self.finish_reason = change.note or f"action:{action.kind}"
        self.journal.commit(
            kind=EventKind.WORLD_STATE_CHANGED,
            actor_id=WORLD_ACTOR,
            logical_time=self.logical_time,
            payload={
                "action": action.kind,
                "updates": change.updates,
                "finished": change.finished,
                "note": change.note,
            },
        )

    def assign_payoffs(self, per_agent: Mapping[str, float]) -> None:
        for agent_id, value in per_agent.items():
            self.payoffs[agent_id] = self.payoffs.get(agent_id, 0.0) + float(value)
        self.journal.commit(
            kind=EventKind.PAYOFF_ASSIGNED,
            actor_id=WORLD_ACTOR,
            logical_time=self.logical_time,
            payload={"payoffs": dict(self.payoffs), "mode": str(self.manifest.payoff.mode)},
        )

    def welfare(self) -> float:
        values = list(self.payoffs.values())
        if not values:
            return 0.0
        mode = self.manifest.payoff.welfare
        if mode == "min":
            return min(values)
        if mode == "nash":
            product = 1.0
            for value in values:
                product *= max(value, 0.0)
            return product ** (1 / len(values))
        return sum(values)

    def policy_event(self, rule: str, allowed: bool, reason: str) -> None:
        from testbed_contracts.events import PolicyDecision

        self.journal.commit(
            kind=EventKind.POLICY_DECISION,
            actor_id=WORLD_ACTOR,
            logical_time=self.logical_time,
            payload={"rule": rule, "reason": reason},
            policy_decision=PolicyDecision(rule=rule, allowed=allowed, reason=reason),
        )

    # -- snapshots ---------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        return {
            "logical_time": self.logical_time,
            "finished": self.finished,
            "finish_reason": self.finish_reason,
            "state": dict(self.state),
            "payoffs": dict(self.payoffs),
            "private_facts": {k: dict(v) for k, v in self.private_facts.items()},
            "public_facts": dict(self.public_facts),
            "counters": dict(self.counters),
            "fault_occurrences": {str(k): v for k, v in self.fault_occurrences.items()},
            "dropped_agents": sorted(self.dropped_agents),
            "resources": self.resources.model_dump(mode="json"),
            "queue": list(self.queue.dump()),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.logical_time = int(snapshot["logical_time"])
        self.finished = bool(snapshot["finished"])
        self.finish_reason = snapshot.get("finish_reason")
        self.state = dict(snapshot["state"])
        self.payoffs = dict(snapshot["payoffs"])
        self.private_facts = {k: dict(v) for k, v in snapshot["private_facts"].items()}
        self.public_facts = dict(snapshot["public_facts"])
        self.counters = dict(snapshot["counters"])
        self.fault_occurrences = {int(k): v for k, v in snapshot["fault_occurrences"].items()}
        self.dropped_agents = set(snapshot.get("dropped_agents", []))
        self.resources = ResourceDelta.model_validate(snapshot["resources"])
        self.queue = DeliveryQueue.load(snapshot["queue"])
