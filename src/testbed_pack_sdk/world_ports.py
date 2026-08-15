"""World primitives available to packs and topology plugins.

A plugin may *propose* recipients, ordering hints or an action. Only World
orders and commits them, so supervisor / debate / market behaviour is a plugin,
never kernel scheduling logic.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from testbed_contracts.enums import VisibilityPolicy


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class WorldAction(Frozen):
    """A domain action proposed by an agent (or a topology plugin)."""

    kind: str
    actor_id: str
    args: dict[str, Any] = Field(default_factory=dict)
    #: Optional plugin hint. World may reorder; it never blindly obeys.
    priority: int = 0


class StateChange(Frozen):
    """The effect of an action on authoritative world state.

    `updates` are shallow-merged into state; `facts_to` grants private facts to
    named agents; `finished` ends the episode.
    """

    updates: dict[str, Any] = Field(default_factory=dict)
    facts_to: dict[str, dict[str, Any]] = Field(default_factory=dict)
    messages: tuple[Delivery, ...] = ()
    finished: bool = False
    note: str = ""
    rejected_reason: str | None = None


class Delivery(Frozen):
    """A message World will schedule for delivery."""

    sender_id: str
    recipient_id: str
    content: str
    payload: dict[str, Any] = Field(default_factory=dict)
    visibility: VisibilityPolicy = VisibilityPolicy.PUBLIC
    #: Extra logical ticks on top of the communication policy's latency.
    extra_delay: int = 0


class Proposal(Frozen):
    """What a topology plugin sees for one agent emission."""

    actor_id: str
    content: str
    requested_recipients: tuple[str, ...] = ()
    action: WorldAction | None = None
    logical_time: int = 0


class RoutingDecision(Frozen):
    """What a topology plugin proposes in response.

    World validates every field: unknown recipients are dropped, broadcasts are
    checked against the communication policy, and ordering hints are only
    tie-breakers within a logical tick.
    """

    recipients: tuple[str, ...] = ()
    visibility: VisibilityPolicy = VisibilityPolicy.PUBLIC
    order_hint: int = 0
    extra_delay: int = 0
    blocked_reason: str | None = None


class WorldSnapshotView(Frozen):
    """Read-only view of authoritative state handed to plugins and verifiers."""

    logical_time: int
    state: dict[str, Any] = Field(default_factory=dict)
    agent_ids: tuple[str, ...] = ()
    roles: dict[str, str] = Field(default_factory=dict)
    private_facts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    payoffs: dict[str, float] = Field(default_factory=dict)


@runtime_checkable
class WorldPorts(Protocol):
    """The kernel implements this; packs and plugins only call it.

    Deliberately read-mostly: mutation happens by returning a `StateChange`, so
    a plugin can never commit an action out of the scheduler's ordering.
    """

    def snapshot_view(self) -> WorldSnapshotView: ...

    def agents(self) -> Sequence[str]: ...

    def role_of(self, agent_id: str) -> str: ...

    def visible_state(self, agent_id: str) -> Mapping[str, Any]: ...

    def logical_time(self) -> int: ...

    def rng_choice(self, options: Sequence[Any], *, label: str) -> Any:
        """Seeded, recorded randomness.

        Plugins must use this rather than `random`, otherwise the scheduler
        cannot reproduce their decisions.
        """
        ...
