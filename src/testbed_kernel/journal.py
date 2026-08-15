"""Event commitment.

The journal is the only place that assigns sequence numbers and idempotency
keys, so no adapter, plugin or pack can write to the log directly.
"""

from __future__ import annotations

from typing import Any

from testbed_contracts.enums import EventKind, VisibilityPolicy
from testbed_contracts.events import Event, PolicyDecision, ResourceDelta
from testbed_contracts.ids import content_hash, idempotency_key, short_hash
from testbed_contracts.ports import EventStore


class EventJournal:
    """Assigns identity and ordering to events and appends them to the store.

    `logical_action_index` counts committed actions for this run across all
    attempts. Two attempts that reach the same logical action derive the same
    idempotency key, which is what makes a retry harmless.
    """

    def __init__(
        self,
        store: EventStore,
        *,
        run_id: str,
        attempt_id: str,
        sequence: int = 0,
        logical_action_index: int = 0,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.attempt_id = attempt_id
        self.sequence = sequence
        self.logical_action_index = logical_action_index
        self.committed: list[Event] = []
        self.rejected_duplicates: list[str] = []

    def commit(
        self,
        *,
        kind: EventKind,
        actor_id: str,
        logical_time: int,
        payload: dict[str, Any] | None = None,
        target_ids: tuple[str, ...] = (),
        visibility: VisibilityPolicy = VisibilityPolicy.PUBLIC,
        authorized_view_ids: tuple[str, ...] = (),
        resource_delta: ResourceDelta | None = None,
        policy_decision: PolicyDecision | None = None,
        causation_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Event | None:
        """Commit one event. Returns None when the store rejected it as a
        duplicate of an already-committed logical action."""
        payload = dict(payload or {})
        index = self.logical_action_index
        key = idempotency_key(
            run_id=self.run_id,
            logical_action={"n": index, "kind": str(kind), "actor": actor_id},
        )
        event = Event(
            event_id=f"evt_{short_hash({'run': self.run_id, 'seq': self.sequence}, 20)}",
            run_id=self.run_id,
            attempt_id=self.attempt_id,
            sequence=self.sequence,
            logical_time=logical_time,
            event_kind=kind,
            actor_id=actor_id,
            target_ids=tuple(target_ids),
            causation_id=causation_id,
            correlation_id=correlation_id,
            idempotency_key=key,
            visibility_policy=visibility,
            authorized_view_ids=tuple(authorized_view_ids),
            payload=payload,
            payload_hash=content_hash(payload),
            resource_delta=resource_delta or ResourceDelta(),
            policy_decision=policy_decision,
        )
        if not self.store.append(event):
            self.rejected_duplicates.append(key)
            self.logical_action_index += 1
            return None
        self.sequence += 1
        self.logical_action_index += 1
        self.committed.append(event)
        return event

    def state(self) -> dict[str, int]:
        return {
            "sequence": self.sequence,
            "logical_action_index": self.logical_action_index,
        }
