"""The canonical event envelope and the read-only views derived from it.

The omniscient ground-truth event is stored exactly once. Every per-agent view
is a *projection*; projecting never mutates or re-writes the stored event.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from datetime import UTC, datetime
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field

from testbed_contracts.enums import EventKind, VisibilityPolicy
from testbed_contracts.ids import ContentHash, content_hash, normalize_for_hash

EVENT_SCHEMA_VERSION = "1.0.0"

#: Fields that legitimately differ between two identical seeded executions.
#: They are removed before determinism hashes are compared.
VOLATILE_EVENT_FIELDS = frozenset({"recorded_at", "event_id", "attempt_id"})

OMNISCIENT_VIEW = "omniscient"
PUBLIC_VIEW = "public"


class ResourceDelta(BaseModel):
    """Resources consumed by the action that produced this event."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    cost_usd: float = 0.0
    wall_seconds: float = 0.0
    message_bytes: int = 0

    def __add__(self, other: ResourceDelta) -> ResourceDelta:
        return ResourceDelta(
            model_calls=self.model_calls + other.model_calls,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            tool_calls=self.tool_calls + other.tool_calls,
            cost_usd=self.cost_usd + other.cost_usd,
            wall_seconds=self.wall_seconds + other.wall_seconds,
            message_bytes=self.message_bytes + other.message_bytes,
        )


class PolicyDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    rule: str
    allowed: bool
    reason: str = ""


class Event(BaseModel):
    """One append-only domain event.

    Snapshots deliberately do not live on ordinary events; only checkpoint
    records and `world.snapshot.created` carry snapshot references, so state
    sized payloads are never duplicated across the log.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = EVENT_SCHEMA_VERSION
    event_id: str
    run_id: str
    attempt_id: str
    sequence: int

    logical_time: int
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    event_kind: EventKind

    actor_id: str
    target_ids: tuple[str, ...] = ()
    causation_id: str | None = None
    correlation_id: str | None = None
    idempotency_key: str | None = None

    visibility_policy: VisibilityPolicy = VisibilityPolicy.PUBLIC
    authorized_view_ids: tuple[str, ...] = ()

    payload: dict[str, Any] = Field(default_factory=dict)
    payload_ref: str | None = None
    payload_media_type: str = "application/json"
    payload_hash: ContentHash | None = None

    resource_delta: ResourceDelta = Field(default_factory=ResourceDelta)
    policy_decision: PolicyDecision | None = None
    redaction_metadata: dict[str, Any] = Field(default_factory=dict)

    def with_payload_hash(self) -> Self:
        return self.model_copy(update={"payload_hash": content_hash(self.payload)})

    def visible_to(self, view: str) -> bool:
        """Whether this event may appear in the given view.

        `omniscient` sees everything. `public` sees only public events. An agent
        view sees public events plus events it is explicitly authorised for.
        """
        if view == OMNISCIENT_VIEW:
            return True
        if self.visibility_policy is VisibilityPolicy.OMNISCIENT_ONLY:
            return False
        if self.visibility_policy is VisibilityPolicy.PUBLIC:
            return True
        if view == PUBLIC_VIEW:
            return False
        return view in self.authorized_view_ids

    def project(self, view: str) -> Event | None:
        """Project into a view, returning None when the view is not authorised.

        The projection is a copy: the stored omniscient event is never mutated.
        """
        if not self.visible_to(view):
            return None
        if view in (OMNISCIENT_VIEW,) or self.visibility_policy is VisibilityPolicy.PUBLIC:
            return self
        # A partitioned event reaches an authorised viewer intact, but the list of
        # *other* authorised viewers is itself private information.
        return self.model_copy(update={"authorized_view_ids": (view,)})


class EventView(Sequence[Event]):
    """An immutable, explicitly-scoped sequence of events.

    Judge scorers and playback must construct one of these, which forces the
    caller to name the view and prevents accidental omniscient leakage.
    """

    __slots__ = ("_events", "view")

    def __init__(self, events: Sequence[Event], view: str) -> None:
        self.view = view
        self._events = tuple(e for e in (ev.project(view) for ev in events) if e is not None)

    def __len__(self) -> int:
        return len(self._events)

    def __getitem__(self, index: int) -> Event:  # type: ignore[override]
        return self._events[index]

    def __iter__(self) -> Iterator[Event]:
        return iter(self._events)

    def of_kind(self, *kinds: EventKind) -> tuple[Event, ...]:
        wanted = set(kinds)
        return tuple(e for e in self._events if e.event_kind in wanted)

    def by_actor(self, actor_id: str) -> tuple[Event, ...]:
        return tuple(e for e in self._events if e.actor_id == actor_id)

    def total_resources(self) -> ResourceDelta:
        total = ResourceDelta()
        for event in self._events:
            total = total + event.resource_delta
        return total

    def __repr__(self) -> str:
        return f"EventView(view={self.view!r}, events={len(self._events)})"


def canonical_event_hash(events: Sequence[Event]) -> ContentHash:
    """Hash of a logical event sequence with volatile fields removed.

    Two seeded executions of the same deterministic fixture must produce the
    same value; this is what the reproducibility tests assert on.
    """
    payload = [
        normalize_for_hash(e.model_dump(mode="json"), drop_keys=VOLATILE_EVENT_FIELDS)
        for e in events
    ]
    return content_hash(payload)
