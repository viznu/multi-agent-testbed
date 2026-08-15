"""Property tests for the scheduler and the visibility projector."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from testbed_contracts.enums import VisibilityPolicy
from testbed_contracts.events import Event, EventKind, EventView
from testbed_kernel.scheduler import DeliveryQueue, ScheduledDelivery
from testbed_pack_sdk import Delivery

agent_ids = st.sampled_from(["a", "b", "c", "d"])


@st.composite
def scheduled(draw) -> ScheduledDelivery:
    return ScheduledDelivery(
        due_time=draw(st.integers(min_value=0, max_value=5)),
        order_hint=draw(st.integers(min_value=-2, max_value=2)),
        delivery=Delivery(
            sender_id=draw(agent_ids),
            recipient_id=draw(agent_ids),
            content=draw(st.text(min_size=0, max_size=12)),
        ),
    )


@settings(max_examples=200, deadline=None)
@given(items=st.lists(scheduled(), min_size=0, max_size=8), permutation=st.randoms())
def test_reordering_physically_concurrent_inputs_does_not_change_the_schedule(items, permutation):
    """The plan's central scheduling property.

    Two controllers that receive the same set of deliveries in different
    physical orders must produce the same logical order.
    """
    shuffled = list(items)
    permutation.shuffle(shuffled)

    a = DeliveryQueue(items).pop_due(5)
    b = DeliveryQueue(shuffled).pop_due(5)
    assert [i.sort_key for i in a] == [i.sort_key for i in b]


@settings(max_examples=100, deadline=None)
@given(items=st.lists(scheduled(), min_size=1, max_size=8))
def test_due_deliveries_are_never_dropped_or_duplicated(items):
    queue = DeliveryQueue(items)
    due = queue.pop_due(2)
    remaining = queue.peek_all()
    assert len(due) + len(remaining) == len(items)
    assert all(i.due_time <= 2 for i in due)
    assert all(i.due_time > 2 for i in remaining)


@settings(max_examples=100, deadline=None)
@given(now=st.integers(min_value=0, max_value=6), items=st.lists(scheduled(), max_size=6))
def test_next_due_time_is_the_earliest_pending(now, items):
    queue = DeliveryQueue(items)
    queue.pop_due(now)
    pending = queue.peek_all()
    expected = min((i.due_time for i in pending), default=None)
    assert queue.next_due_time() == expected


def _event(seq: int, visibility: VisibilityPolicy, authorized: tuple[str, ...]) -> Event:
    return Event(
        event_id=f"evt_{seq}",
        run_id="run_x",
        attempt_id="att_x",
        sequence=seq,
        logical_time=seq,
        event_kind=EventKind.AGENT_MESSAGE,
        actor_id="a",
        visibility_policy=visibility,
        authorized_view_ids=authorized,
        payload={"content": f"c{seq}"},
    )


@settings(max_examples=100, deadline=None)
@given(
    visibilities=st.lists(
        st.sampled_from(list(VisibilityPolicy)), min_size=1, max_size=8
    ),
    viewer=agent_ids,
)
def test_a_view_never_widens_beyond_what_it_is_authorised_for(visibilities, viewer):
    events = [
        _event(i, visibility, ("a",)) for i, visibility in enumerate(visibilities)
    ]
    view = EventView(events, view=viewer)
    for event in view:
        assert event.visible_to(viewer)
        if event.visibility_policy is VisibilityPolicy.OMNISCIENT_ONLY:
            raise AssertionError("omniscient-only events must never reach an agent view")


@settings(max_examples=100, deadline=None)
@given(visibilities=st.lists(st.sampled_from(list(VisibilityPolicy)), min_size=1, max_size=8))
def test_omniscient_sees_everything_and_public_sees_only_public(visibilities):
    events = [_event(i, v, ("a",)) for i, v in enumerate(visibilities)]
    assert len(EventView(events, view="omniscient")) == len(events)
    public = EventView(events, view="public")
    assert all(e.visibility_policy is VisibilityPolicy.PUBLIC for e in public)


@settings(max_examples=100, deadline=None)
@given(tamper=st.text(min_size=1, max_size=10))
def test_changing_an_unauthorised_private_event_cannot_change_a_view(tamper):
    """Noninterference: what an agent sees depends only on what it may see."""
    events = [
        _event(0, VisibilityPolicy.PUBLIC, ()),
        _event(1, VisibilityPolicy.PRIVATE, ("a",)),
        _event(2, VisibilityPolicy.PUBLIC, ()),
    ]
    before = [e.payload for e in EventView(events, view="b")]
    events[1] = events[1].model_copy(update={"payload": {"content": tamper}})
    after = [e.payload for e in EventView(events, view="b")]
    assert before == after
