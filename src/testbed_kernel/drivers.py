"""Execution drivers.

One Gym-style interface is not enough, so there are two drivers over one World:

* `SessionDriver` -- turns, messages, handoffs, asynchronous delivery, gates.
* `EnvDriver`     -- reset/step, in AEC (turn-taking) and parallel (simultaneous
  action) modes.

Both delegate every ordering, visibility and fault decision to World.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from testbed_contracts.enums import AgentEventKind, EnvMode, EventKind, VisibilityPolicy
from testbed_contracts.events import Event, EventView
from testbed_contracts.ports import AgentHandle, AgentRequest
from testbed_kernel.errors import LimitExceeded
from testbed_kernel.world import WORLD_ACTOR, World
from testbed_pack_sdk.hooks import Pack
from testbed_pack_sdk.world_ports import Delivery, Proposal, WorldAction

CheckpointHook = Callable[["DriverState"], Awaitable[None]]


@dataclass
class DriverState:
    """Driver-owned state that must survive a checkpoint."""

    invocations: int = 0
    activated: list[str] = field(default_factory=list)
    turn: int = 0

    def dump(self) -> dict[str, Any]:
        return {
            "invocations": self.invocations,
            "activated": list(self.activated),
            "turn": self.turn,
        }

    @classmethod
    def load(cls, raw: Mapping[str, Any]) -> DriverState:
        return cls(
            invocations=int(raw.get("invocations", 0)),
            activated=list(raw.get("activated", [])),
            turn=int(raw.get("turn", 0)),
        )


@dataclass
class DriverOutcome:
    finished: bool
    reason: str
    limit_hit: str | None = None


class SessionDriver:
    """Turn-based sessions with asynchronous message delivery."""

    kind = "session"

    def __init__(
        self,
        *,
        world: World,
        agents: Mapping[str, AgentHandle],
        topology: Any,
        pack: Pack,
        state: DriverState | None = None,
        checkpoint_hook: CheckpointHook | None = None,
        checkpoint_every: int | None = None,
    ) -> None:
        self.world = world
        self.agents = dict(agents)
        self.topology = topology
        self.pack = pack
        self.state = state or DriverState()
        self.checkpoint_hook = checkpoint_hook
        limits = world.manifest.limits
        self.checkpoint_every = checkpoint_every or limits.checkpoint_every_events
        self._events_since_checkpoint = 0

    # -- activation --------------------------------------------------------

    def activate(self) -> None:
        """Ask the topology which agents open the episode.

        The plugin only *proposes* an opening instruction; World turns it into a
        normal scheduled delivery, so the first turn is ordered like any other.
        """
        if self.state.activated:
            return
        for agent_id in self.world.agents():
            instruction = self.topology.opening_instruction(agent_id, self.world)
            if instruction is None:
                continue
            self.world.enqueue(
                Delivery(
                    sender_id=WORLD_ACTOR,
                    recipient_id=agent_id,
                    content=instruction,
                    payload={"kind": "opening"},
                    visibility=VisibilityPolicy.PRIVATE,
                )
            )
            self.state.activated.append(agent_id)

    # -- main loop ---------------------------------------------------------

    async def run(self) -> DriverOutcome:
        limits = self.world.manifest.limits
        self.activate()

        while not self.world.finished:
            limit = self._limit_hit()
            if limit:
                return DriverOutcome(finished=False, reason=f"limit:{limit}", limit_hit=limit)

            due = self.world.queue.pop_due(self.world.logical_time)
            if not due:
                next_time = self.world.queue.next_due_time()
                if next_time is None:
                    return DriverOutcome(finished=False, reason="quiescent")
                if next_time > limits.max_logical_time:
                    return DriverOutcome(finished=False, reason="limit:max_logical_time",
                                         limit_hit="max_logical_time")
                self.world.advance_to(next_time)
                continue

            for item in due:
                await self._deliver_and_invoke(item)
                if self.world.finished:
                    break
            self.state.turn += 1
            self.world.advance_to(self.world.logical_time + self.world.manifest.world.clock_step)

        return DriverOutcome(finished=True, reason=self.world.finish_reason or "task_finished")

    def _limit_hit(self) -> str | None:
        limits = self.world.manifest.limits
        world = self.world
        if world.logical_time > limits.max_logical_time:
            return "max_logical_time"
        if world.journal.sequence >= limits.max_events:
            return "max_events"
        if world.counters["messages"] > limits.max_messages:
            return "max_messages"
        if world.counters["model_calls"] > limits.max_model_calls:
            return "max_model_calls"
        if world.resources.cost_usd > limits.max_cost_usd:
            return "max_cost_usd"
        return None

    async def _deliver_and_invoke(self, item: Any) -> None:
        world = self.world
        delivery = item.delivery
        recipient = delivery.recipient_id
        if recipient in world.dropped_agents:
            return

        authorized = (
            (delivery.sender_id, recipient)
            if delivery.visibility is not VisibilityPolicy.PUBLIC
            else ()
        )
        delivered = world.journal.commit(
            kind=EventKind.WORLD_MESSAGE_DELIVERED,
            actor_id=WORLD_ACTOR,
            logical_time=world.logical_time,
            target_ids=(recipient,),
            payload={
                "sender": delivery.sender_id,
                "recipient": recipient,
                "content": delivery.content,
                "payload": delivery.payload,
            },
            visibility=delivery.visibility,
            authorized_view_ids=authorized,
        )
        await self._maybe_checkpoint()

        handle = self.agents.get(recipient)
        if handle is None:
            return
        await self._invoke(recipient, handle, delivery, cause=delivered)

    async def _invoke(
        self, agent_id: str, handle: AgentHandle, delivery: Any, *, cause: Event | None
    ) -> None:
        world = self.world
        self.state.invocations += 1
        invocation_id = f"inv_{world.run_id_short()}_{self.state.invocations}"

        history = self._agent_history(agent_id)
        request = AgentRequest(
            invocation_id=invocation_id,
            run_id=world.journal.run_id,
            agent_id=agent_id,
            logical_time=world.logical_time,
            instruction=delivery.content,
            inbox=({"sender": delivery.sender_id, "content": delivery.content,
                    "payload": delivery.payload},),
            observation=dict(world.visible_state(agent_id)),
            private_facts=dict(world.private_facts.get(agent_id, {})),
            history=history,
            seed=world.rng.seed,
        )
        world.journal.commit(
            kind=EventKind.AGENT_INVOKED,
            actor_id=agent_id,
            logical_time=world.logical_time,
            payload={"invocation_id": invocation_id, "instruction": delivery.content},
            visibility=VisibilityPolicy.PRIVATE,
            authorized_view_ids=(agent_id,),
            causation_id=cause.event_id if cause else None,
        )

        async for emitted in handle.invoke(request):
            world.counters["model_calls"] += emitted.resource_delta.model_calls
            world.resources = world.resources + emitted.resource_delta
            await self._handle_agent_event(agent_id, emitted, invocation_id)
            await self._maybe_checkpoint()
            if world.finished:
                break

    async def _handle_agent_event(self, agent_id: str, emitted: Any, invocation_id: str) -> None:
        world = self.world
        if emitted.kind is AgentEventKind.MESSAGE:
            proposal = Proposal(
                actor_id=agent_id,
                content=emitted.content,
                requested_recipients=emitted.recipients,
                logical_time=world.logical_time,
            )
            decision = world.route(self.topology, proposal, emitted.recipients)
            visibility = (
                VisibilityPolicy.PRIVATE if emitted.private else decision.visibility
            )
            authorized = (
                (agent_id, *decision.recipients)
                if visibility is not VisibilityPolicy.PUBLIC
                else ()
            )
            world.journal.commit(
                kind=EventKind.AGENT_MESSAGE,
                actor_id=agent_id,
                logical_time=world.logical_time,
                target_ids=decision.recipients,
                payload={"content": emitted.content, "invocation_id": invocation_id},
                visibility=visibility,
                authorized_view_ids=authorized,
                resource_delta=emitted.resource_delta,
            )
            if decision.blocked_reason:
                return
            for recipient in decision.recipients:
                world.enqueue(
                    Delivery(
                        sender_id=agent_id,
                        recipient_id=recipient,
                        content=emitted.content,
                        payload=emitted.action,
                        visibility=visibility,
                        extra_delay=decision.extra_delay,
                    ),
                    order_hint=decision.order_hint,
                )
            return

        if emitted.kind is AgentEventKind.WORLD_ACTION:
            action = WorldAction(
                kind=str(emitted.action.get("kind", "noop")),
                actor_id=agent_id,
                args={k: v for k, v in emitted.action.items() if k != "kind"},
            )
            world.journal.commit(
                kind=EventKind.WORLD_ACTION,
                actor_id=agent_id,
                logical_time=world.logical_time,
                payload={"action": action.kind, "args": action.args},
                resource_delta=emitted.resource_delta,
            )
            handler = self.pack.handler_for(action.kind)
            if handler is None:
                world.policy_event("unknown_action", False, action.kind)
                return
            change = handler.apply(action, world.snapshot_view())
            world.apply_change(action, change)
            return

        if emitted.kind is AgentEventKind.TOOL_CALL:
            world.counters["tool_calls"] += 1
            world.journal.commit(
                kind=EventKind.TOOL_CALLED,
                actor_id=agent_id,
                logical_time=world.logical_time,
                payload={"tool": emitted.tool_name, "args": emitted.tool_args},
                visibility=VisibilityPolicy.PRIVATE,
                authorized_view_ids=(agent_id,),
                resource_delta=emitted.resource_delta,
            )
            return

        if emitted.kind is AgentEventKind.FINAL:
            world.journal.commit(
                kind=EventKind.AGENT_FINAL,
                actor_id=agent_id,
                logical_time=world.logical_time,
                payload={"content": emitted.content, "invocation_id": invocation_id},
                resource_delta=emitted.resource_delta,
            )
            return

        if emitted.kind is AgentEventKind.ERROR:
            world.journal.commit(
                kind=EventKind.AGENT_ERROR,
                actor_id=agent_id,
                logical_time=world.logical_time,
                payload={"error": emitted.content},
            )

    def _agent_history(self, agent_id: str) -> tuple[Event, ...]:
        """The agent's authorised projection of everything so far.

        Built by the kernel from stored events; an adapter cannot widen it.
        """
        stored = self.world.journal.store.read(self.world.journal.run_id)
        return tuple(EventView(stored, view=agent_id))

    async def _maybe_checkpoint(self) -> None:
        self._events_since_checkpoint += 1
        if self.checkpoint_hook is None:
            return
        if self._events_since_checkpoint >= self.checkpoint_every:
            self._events_since_checkpoint = 0
            await self.checkpoint_hook(self.state)


class EnvDriver:
    """reset/step over a World, in AEC or parallel mode.

    Parallel mode collects every agent's action for a tick before World commits
    any of them, which is what simultaneous-action environments require.
    """

    kind = "env"

    def __init__(
        self,
        *,
        world: World,
        agents: Mapping[str, AgentHandle],
        topology: Any,
        pack: Pack,
        mode: EnvMode = EnvMode.AEC,
        state: DriverState | None = None,
        checkpoint_hook: CheckpointHook | None = None,
        checkpoint_every: int | None = None,
    ) -> None:
        self.world = world
        self.agents = dict(agents)
        self.topology = topology
        self.pack = pack
        self.mode = mode
        self.state = state or DriverState()
        self.checkpoint_hook = checkpoint_hook
        self.checkpoint_every = checkpoint_every or world.manifest.limits.checkpoint_every_events
        self._events_since_checkpoint = 0

    def observation(self, agent_id: str) -> dict[str, Any]:
        return dict(self.world.visible_state(agent_id))

    async def run(self) -> DriverOutcome:
        limits = self.world.manifest.limits
        while not self.world.finished:
            if self.world.logical_time > limits.max_logical_time:
                return DriverOutcome(False, "limit:max_logical_time", "max_logical_time")
            if self.world.journal.sequence >= limits.max_events:
                return DriverOutcome(False, "limit:max_events", "max_events")
            await self._tick()
            self.state.turn += 1
            self.world.advance_to(self.world.logical_time + self.world.manifest.world.clock_step)
        return DriverOutcome(True, self.world.finish_reason or "task_finished")

    async def _tick(self) -> None:
        world = self.world
        agent_ids = list(world.agents())
        collected: list[tuple[str, WorldAction]] = []

        for agent_id in agent_ids:
            handle = self.agents[agent_id]
            self.state.invocations += 1
            request = AgentRequest(
                invocation_id=f"inv_{world.run_id_short()}_{self.state.invocations}",
                run_id=world.journal.run_id,
                agent_id=agent_id,
                logical_time=world.logical_time,
                instruction=world.case.instruction,
                observation=self.observation(agent_id),
                private_facts=dict(world.private_facts.get(agent_id, {})),
                seed=world.rng.seed,
            )
            async for emitted in handle.invoke(request):
                world.resources = world.resources + emitted.resource_delta
                if emitted.kind is not AgentEventKind.WORLD_ACTION:
                    continue
                action = WorldAction(
                    kind=str(emitted.action.get("kind", "noop")),
                    actor_id=agent_id,
                    args={k: v for k, v in emitted.action.items() if k != "kind"},
                )
                if self.mode is EnvMode.AEC:
                    self._commit(action)
                    await self._maybe_checkpoint()
                    if world.finished:
                        return
                else:
                    collected.append((agent_id, action))

        if self.mode is EnvMode.PARALLEL:
            # Simultaneous actions are ordered by World, not by iteration order.
            for _, action in sorted(collected, key=lambda pair: (pair[1].kind, pair[0])):
                self._commit(action)
                await self._maybe_checkpoint()
                if world.finished:
                    return

    def _commit(self, action: WorldAction) -> None:
        world = self.world
        world.journal.commit(
            kind=EventKind.WORLD_ACTION,
            actor_id=action.actor_id,
            logical_time=world.logical_time,
            payload={"action": action.kind, "args": action.args},
        )
        handler = self.pack.handler_for(action.kind)
        if handler is None:
            world.policy_event("unknown_action", False, action.kind)
            return
        world.apply_change(action, handler.apply(action, world.snapshot_view()))

    async def _maybe_checkpoint(self) -> None:
        self._events_since_checkpoint += 1
        if self.checkpoint_hook is None:
            return
        if self._events_since_checkpoint >= self.checkpoint_every:
            self._events_since_checkpoint = 0
            await self.checkpoint_hook(self.state)


__all__ = ["DriverOutcome", "DriverState", "EnvDriver", "SessionDriver", "LimitExceeded"]
