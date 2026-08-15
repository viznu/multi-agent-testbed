"""Per-run measures computable directly from canonical events.

Everything here is *directly observed*. Per-agent contribution, ablation and
specialisation are deliberately absent: the plan requires them to be optional
scorers or separate counterfactual experiments, never fields populated as if
they had been measured.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from testbed_contracts.enums import EventKind
from testbed_contracts.events import Event, ResourceDelta


def _gini(values: Sequence[float]) -> float:
    """Payoff inequality. 0.0 when everyone gets the same amount."""
    if not values:
        return 0.0
    shifted = [v - min(values) for v in values] if min(values) < 0 else list(values)
    total = sum(shifted)
    if total == 0:
        return 0.0
    ordered = sorted(shifted)
    n = len(ordered)
    weighted = sum((i + 1) * v for i, v in enumerate(ordered))
    return (2 * weighted) / (n * total) - (n + 1) / n


def compute_measures(events: Sequence[Event], world: Any) -> dict[str, float]:
    kinds = [e.event_kind for e in events]
    total = ResourceDelta()
    for event in events:
        total = total + event.resource_delta

    messages = [e for e in events if e.event_kind is EventKind.AGENT_MESSAGE]
    deliveries = [e for e in events if e.event_kind is EventKind.WORLD_MESSAGE_DELIVERED]
    handoff_latencies = []
    for delivered in deliveries:
        cause = delivered.payload.get("sender")
        if cause and cause != "world":
            source = next(
                (
                    m
                    for m in messages
                    if m.actor_id == cause and m.logical_time <= delivered.logical_time
                ),
                None,
            )
            if source is not None:
                handoff_latencies.append(delivered.logical_time - source.logical_time)

    policy_violations = [
        e
        for e in events
        if e.event_kind is EventKind.POLICY_DECISION
        and e.policy_decision is not None
        and not e.policy_decision.allowed
    ]

    measures: dict[str, float] = {
        "events": float(len(events)),
        "logical_time": float(world.logical_time),
        "model_calls": float(total.model_calls),
        "input_tokens": float(total.input_tokens),
        "output_tokens": float(total.output_tokens),
        "cost_usd": float(total.cost_usd),
        "tool_calls": float(kinds.count(EventKind.TOOL_CALLED)),
        "messages_sent": float(len(messages)),
        "messages_delivered": float(len(deliveries)),
        "message_bytes": float(total.message_bytes),
        "duplicate_actions": float(world.counters.get("duplicate_actions", 0)),
        "spawned_agents": float(kinds.count(EventKind.AGENT_SPAWNED)),
        "faults_injected": float(kinds.count(EventKind.FAULT_INJECTED)),
        "policy_violations": float(len(policy_violations)),
        "agent_errors": float(kinds.count(EventKind.AGENT_ERROR)),
        "checkpoints": float(kinds.count(EventKind.WORLD_SNAPSHOT_CREATED)),
        "welfare": float(world.welfare()),
        "payoff_gini": float(_gini(list(world.payoffs.values()))),
        "mean_handoff_latency": (
            sum(handoff_latencies) / len(handoff_latencies) if handoff_latencies else 0.0
        ),
    }
    first_action = next(
        (e.logical_time for e in events if e.event_kind is EventKind.WORLD_ACTION), None
    )
    measures["time_to_first_action"] = float(first_action) if first_action is not None else -1.0
    #: Ticks in which no agent acted at all.
    active_ticks = {e.logical_time for e in events if e.event_kind in
                    (EventKind.AGENT_MESSAGE, EventKind.WORLD_ACTION, EventKind.TOOL_CALLED)}
    measures["idle_ticks"] = float(max(world.logical_time - len(active_ticks), 0))
    return measures
