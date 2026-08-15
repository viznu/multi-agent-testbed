"""Ports: the narrow interfaces adapters implement.

The agent port is deliberately smaller than A2A or any framework API. Adapters
translate a framework's object model into these types at the boundary; framework
types never appear in kernel APIs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from testbed_contracts.enums import AgentEventKind, ReproducibilityLevel
from testbed_contracts.events import Event, ResourceDelta
from testbed_contracts.ids import ContentHash


class BlobRef(BaseModel):
    """A content-addressed reference into the artifact store."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    hash: ContentHash
    media_type: str = "application/octet-stream"
    size_bytes: int = 0


class AgentDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agent_id: str
    adapter: str
    model: str
    role: str = "worker"
    #: False for adapters that cannot restore state. Such adapters are marked
    #: `playback_only`: their transcripts can be replayed, but a fresh
    #: generation must not be described as reproducible.
    can_snapshot: bool = True
    reproducibility: ReproducibilityLevel = ReproducibilityLevel.BIT_EXACT


class Health(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    ok: bool = True
    detail: str = ""


class AgentRequest(BaseModel):
    """What World hands an agent for one invocation.

    `view` is the agent's *authorised* projection of history. The kernel builds
    it; an adapter cannot widen it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    invocation_id: str
    run_id: str
    agent_id: str
    logical_time: int
    instruction: str
    inbox: tuple[dict[str, Any], ...] = ()
    observation: dict[str, Any] = Field(default_factory=dict)
    private_facts: dict[str, Any] = Field(default_factory=dict)
    history: tuple[Event, ...] = ()
    seed: int = 0
    deadline_ticks: int | None = None


class AgentEvent(BaseModel):
    """What an agent adapter emits. The kernel normalises these into canonical
    events; an adapter never writes to the event store itself."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: AgentEventKind
    content: str = ""
    recipients: tuple[str, ...] = ()
    action: dict[str, Any] = Field(default_factory=dict)
    tool_name: str | None = None
    tool_args: dict[str, Any] = Field(default_factory=dict)
    resource_delta: ResourceDelta = Field(default_factory=ResourceDelta)
    private: bool = False


@runtime_checkable
class AgentHandle(Protocol):
    """The whole agent port. Anything richer belongs in an adapter."""

    async def describe(self) -> AgentDescriptor: ...

    def invoke(self, request: AgentRequest) -> AsyncIterator[AgentEvent]: ...

    async def cancel(self, invocation_id: str) -> None: ...

    async def health(self) -> Health: ...

    async def snapshot(self) -> BlobRef | None: ...

    async def restore(self, snapshot: BlobRef) -> None: ...


@runtime_checkable
class Sandbox(Protocol):
    """Where an agent or tool executes."""

    name: str

    async def start(self) -> None: ...

    async def exec(self, command: Sequence[str], *, timeout: float) -> tuple[int, str, str]: ...

    async def stop(self) -> None: ...


@runtime_checkable
class EventStore(Protocol):
    """Append-only event storage.

    `append` must reject a duplicate idempotency key rather than committing the
    same logical action twice.
    """

    def append(self, event: Event) -> bool: ...

    def read(self, run_id: str, *, attempt_id: str | None = None) -> Sequence[Event]: ...

    def next_sequence(self, run_id: str) -> int: ...


@runtime_checkable
class ArtifactStore(Protocol):
    def put(self, data: bytes, *, media_type: str = "application/octet-stream") -> BlobRef: ...

    def get(self, ref: BlobRef) -> bytes: ...


@runtime_checkable
class Runner(Protocol):
    """Provisions dependencies and translates agent calls.

    A runner must not own routing, spawning, timeout resolution, visibility,
    faults or retries: those belong to World on every execution path.
    """

    name: str

    async def provision(self, manifest: Any) -> None: ...

    async def agent_handles(self, manifest: Any) -> dict[str, AgentHandle]: ...

    async def teardown(self) -> None: ...
