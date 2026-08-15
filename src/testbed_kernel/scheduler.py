"""The deterministic delivery queue.

The ordering key is derived entirely from content and the seed, never from the
order in which deliveries physically arrived. That is what makes the property
"reordering physically concurrent inputs does not alter the logical schedule"
true rather than merely likely.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from testbed_contracts.ids import short_hash
from testbed_pack_sdk.world_ports import Delivery


class ScheduledDelivery(BaseModel):
    """A delivery with its scheduling decision attached."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    due_time: int
    order_hint: int
    delivery: Delivery
    #: Distinguishes intentional duplicates of an otherwise identical message.
    duplicate_index: int = 0
    cause_sequence: int | None = None
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def sort_key(self) -> tuple[int, int, str, int]:
        content_key = short_hash(
            {
                "sender": self.delivery.sender_id,
                "recipient": self.delivery.recipient_id,
                "content": self.delivery.content,
                "payload": self.delivery.payload,
                "visibility": str(self.delivery.visibility),
            },
            16,
        )
        return (self.due_time, self.order_hint, content_key, self.duplicate_index)


class DeliveryQueue:
    """A content-ordered queue of pending deliveries."""

    def __init__(self, items: Iterable[ScheduledDelivery] = ()) -> None:
        self._items: list[ScheduledDelivery] = list(items)

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def push(self, item: ScheduledDelivery) -> None:
        self._items.append(item)

    def pop_due(self, now: int) -> list[ScheduledDelivery]:
        """Remove and return every delivery due at or before `now`, in canonical
        order."""
        due = sorted((i for i in self._items if i.due_time <= now), key=lambda i: i.sort_key)
        remaining = [i for i in self._items if i.due_time > now]
        self._items = remaining
        return due

    def next_due_time(self) -> int | None:
        return min((i.due_time for i in self._items), default=None)

    def peek_all(self) -> list[ScheduledDelivery]:
        return sorted(self._items, key=lambda i: i.sort_key)

    def drop_for(self, agent_id: str) -> int:
        """Drop pending deliveries addressed to an agent (used by dropout faults)."""
        before = len(self._items)
        self._items = [i for i in self._items if i.delivery.recipient_id != agent_id]
        return before - len(self._items)

    # -- persistence -------------------------------------------------------

    def dump(self) -> tuple[dict[str, Any], ...]:
        return tuple(i.model_dump(mode="json") for i in self.peek_all())

    @classmethod
    def load(cls, rows: Iterable[dict[str, Any]]) -> DeliveryQueue:
        return cls(ScheduledDelivery.model_validate(r) for r in rows)
