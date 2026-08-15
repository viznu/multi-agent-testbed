"""Playback: reconstruct stored events, state and authorised views with zero
model, agent, environment or tool calls.

`playback` is not `resume` and not `rerun`:

* playback -- replay what happened, no external calls at all.
* resume   -- continue an interrupted attempt from a checkpoint.
* rerun    -- execute a new run from the same manifest; output may differ.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from testbed_contracts.enums import EventKind
from testbed_contracts.events import OMNISCIENT_VIEW, Event, EventView


#: Views that reveal nothing an agent did not legitimately observe.
def _line(event: Event) -> str:
    kind = event.event_kind
    if kind is EventKind.WORLD_MESSAGE_DELIVERED:
        p = event.payload
        return (
            f"[{event.logical_time}] {p.get('sender')} -> "
            f"{p.get('recipient')}: {p.get('content')}"
        )
    if kind is EventKind.AGENT_MESSAGE:
        targets = ", ".join(event.target_ids) or "-"
        return (
            f"[{event.logical_time}] {event.actor_id} says to {targets}: "
            f"{event.payload.get('content')}"
        )
    if kind is EventKind.WORLD_ACTION:
        return (
            f"[{event.logical_time}] {event.actor_id} acts: "
            f"{event.payload.get('action')} {event.payload.get('args')}"
        )
    if kind is EventKind.TOOL_CALLED:
        return f"[{event.logical_time}] {event.actor_id} calls tool {event.payload.get('tool')}"
    if kind is EventKind.AGENT_FINAL:
        return (
            f"[{event.logical_time}] {event.actor_id} final: {event.payload.get('content')}"
        )
    if kind is EventKind.VERIFIER_RESULT:
        return (
            f"[{event.logical_time}] verifier: success={event.payload.get('success')} "
            f"reward={event.payload.get('reward')}"
        )
    return f"[{event.logical_time}] {kind}: {event.actor_id}"


@dataclass
class Playback:
    run_id: str
    view: str
    events: EventView
    transcript: tuple[str, ...]
    final_state: dict[str, Any]

    def render(self) -> str:
        return "\n".join(self.transcript)


def playback(store: Any, run_id: str, *, view: str = OMNISCIENT_VIEW) -> Playback:
    """Rebuild a run from stored events for an explicitly named view.

    The view argument is required by construction, which is what prevents an
    omniscient transcript from being handed to a judge or a viewer by accident.
    """
    stored: Sequence[Event] = store.read(run_id)
    projected = EventView(stored, view=view)
    state: dict[str, Any] = {}
    for event in projected:
        if event.event_kind is EventKind.WORLD_STATE_CHANGED:
            state.update(event.payload.get("updates", {}))
    return Playback(
        run_id=run_id,
        view=view,
        events=projected,
        transcript=tuple(_line(e) for e in projected),
        final_state=state,
    )


def unauthorized_payloads(store: Any, run_id: str, view: str) -> list[str]:
    """Every payload hash in a view that the view was not authorised to see.

    A conformant kernel returns an empty list for every agent view; the
    visibility tests assert exactly that.
    """
    stored = list(store.read(run_id))
    projected = EventView(stored, view=view)
    allowed = {e.payload_hash for e in stored if e.visible_to(view)}
    return [
        e.payload_hash
        for e in projected
        if e.payload_hash is not None and e.payload_hash not in allowed
    ]
