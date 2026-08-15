"""Project canonical events into OpenTelemetry-shaped spans.

The projection direction matters: canonical events are the source of truth and
spans are an export. Span conventions for multi-agent systems are still moving,
and this testbed's semantics must not depend on them.

No OTLP dependency ships here; the exporter emits the JSON structure an OTLP
exporter would send, which any collector-side tool can ingest.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from testbed_contracts.events import Event

#: Nanoseconds per logical tick in the exported timeline. Logical time is not
#: wall time; the constant only keeps spans ordered in a trace viewer.
TICK_NS = 1_000_000


def event_to_span(event: Event) -> dict[str, Any]:
    start = event.logical_time * TICK_NS
    return {
        "name": str(event.event_kind),
        "trace_id": event.run_id,
        "span_id": event.event_id,
        "parent_span_id": event.causation_id,
        "start_time_unix_nano": start,
        "end_time_unix_nano": start + TICK_NS,
        "attributes": {
            "testbed.run_id": event.run_id,
            "testbed.attempt_id": event.attempt_id,
            "testbed.sequence": event.sequence,
            "testbed.logical_time": event.logical_time,
            "testbed.actor_id": event.actor_id,
            "testbed.target_ids": list(event.target_ids),
            "testbed.visibility": str(event.visibility_policy),
            "gen_ai.usage.input_tokens": event.resource_delta.input_tokens,
            "gen_ai.usage.output_tokens": event.resource_delta.output_tokens,
            "gen_ai.usage.cost_usd": event.resource_delta.cost_usd,
        },
    }


def export_spans(events: Sequence[Event], destination: Path) -> Path:
    """Write one JSON document of spans. Payload bodies are deliberately not
    exported: a trace viewer is not an access-control boundary."""
    spans = [event_to_span(e) for e in events]
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({"spans": spans}, indent=2), "utf-8")
    return destination
