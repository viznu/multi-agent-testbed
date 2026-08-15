"""The five hook families a pack or plugin may implement."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from testbed_contracts.enums import EvalSetKind
from testbed_contracts.events import EventView
from testbed_contracts.ids import ContentHash, content_hash
from testbed_contracts.results import Score, VerifierResult
from testbed_pack_sdk.world_ports import (
    Proposal,
    RoutingDecision,
    StateChange,
    WorldAction,
    WorldPorts,
    WorldSnapshotView,
)


class TaskCase(BaseModel):
    """One stable, content-addressed task case.

    `task_id` must be stable across revisions of the pack; changing what a
    `task_id` means requires a new pack version, never a silent edit.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_id: str
    instruction: str
    initial_state: dict[str, Any] = Field(default_factory=dict)
    public_facts: dict[str, Any] = Field(default_factory=dict)
    private_facts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    eval_set_kind: EvalSetKind = EvalSetKind.FROZEN_EVAL
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def digest(self) -> ContentHash:
        return content_hash(self.model_dump(mode="json"))


@runtime_checkable
class TaskProvider(Protocol):
    """Hook family 1: produce task cases."""

    name: str
    revision: str

    def cases(self) -> Sequence[TaskCase]: ...


@runtime_checkable
class ActionHandler(Protocol):
    """Hook family 3: interpret a domain action against authoritative state.

    The handler is pure: it reads a snapshot and returns a `StateChange`. World
    applies it, assigns ordering and writes the event.
    """

    #: Action kinds this handler claims. `"*"` means "any".
    handles: tuple[str, ...]

    def apply(self, action: WorldAction, state: WorldSnapshotView) -> StateChange: ...


@runtime_checkable
class TopologyPlugin(Protocol):
    """Hook family 2: propose routing.

    Built only from World primitives. A plugin cannot deliver a message, spawn
    an agent or advance the clock itself.
    """

    name: str

    def route(self, proposal: Proposal, world: WorldPorts) -> RoutingDecision: ...

    def opening_instruction(self, agent_id: str, world: WorldPorts) -> str | None:
        """Optional: who speaks first and what they are told. Returning None
        means the agent is not activated at the start of the episode."""
        ...


@runtime_checkable
class Verifier(Protocol):
    """Hook family 4: deterministically judge final state.

    A verifier must not call a model or a network service; it reads final state
    and the omniscient event view.
    """

    name: str
    version: str

    def verify(self, state: WorldSnapshotView, events: EventView) -> VerifierResult: ...


class ScorerContext(BaseModel):
    """Everything a scorer is allowed to know besides the event view."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    task_id: str
    agent_ids: tuple[str, ...] = ()
    verifier: VerifierResult | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    #: Judge scorers receive their pinned model handle here; deterministic
    #: scorers receive None and cannot obtain one.
    judge: Any = None


@runtime_checkable
class Scorer(Protocol):
    """Hook family 5: read an immutable event view and produce scores.

    Scorers run offline. `testbed_eval.purity` blocks network access while they
    execute, so rescoring provably performs no model or tool calls.
    """

    name: str
    version: str
    kind: str

    def score(self, events: EventView, context: ScorerContext) -> Sequence[Score]: ...


class Pack(BaseModel):
    """A bundle of hooks published under one name and revision."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    name: str
    revision: str
    tasks: Any
    action_handlers: tuple[Any, ...] = ()
    verifiers: Mapping[str, Any] = Field(default_factory=dict)
    scorers: tuple[Any, ...] = ()
    description: str = ""

    #: Optional `(pack, config) -> Pack` callable applied by the composition
    #: root before a run, using `manifest.task_pack.config`.
    #:
    #: This refines hook family 1 rather than adding a sixth one: a pack that
    #: needs no configuration leaves it unset and `configured()` returns itself.
    #: Configuration must be pure -- it selects and shapes task cases, and must
    #: not execute anything.
    configurator: Any = None

    def configured(self, config: Mapping[str, Any] | None) -> Pack:
        """Return the pack this experiment should actually run."""
        if self.configurator is None or not config:
            return self
        configured = self.configurator(self, dict(config))
        if not isinstance(configured, Pack):
            raise TypeError(
                f"pack {self.name!r}: configurator returned {type(configured).__name__}, "
                "expected a Pack"
            )
        return configured

    def case(self, task_id: str) -> TaskCase:
        for c in self.tasks.cases():
            if c.task_id == task_id:
                return c
        raise KeyError(task_id)

    def handler_for(self, kind: str) -> Any | None:
        for handler in self.action_handlers:
            if kind in handler.handles or "*" in handler.handles:
                return handler
        return None
