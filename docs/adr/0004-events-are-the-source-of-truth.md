# ADR 0004 — Canonical events are the source of truth; views are projections

Status: accepted

## Context

A multi-agent run has to answer two different questions: "what actually
happened?" and "what could this particular agent see?". Storing a per-agent
transcript answers the second but loses ground truth; storing only ground truth
makes it easy to leak information into a judge or a viewer by accident.

## Decision

The omniscient event is stored exactly once. Every other view is derived:

* `Event.project(view)` returns a copy or `None`; it never mutates the stored
  event. A partitioned event reaches an authorised viewer intact, but the list
  of *other* authorised viewers is itself private and is stripped.
* `EventView(events, view=...)` requires the view to be named. Playback, judge
  scoring and the CLI all go through it, so producing an omniscient transcript
  is always a deliberate act.
* Snapshots are not attached to ordinary events. Only checkpoint records and
  `world.snapshot.created` reference them, which keeps state-sized payloads out
  of the log while preserving recovery and auditability.
* OpenTelemetry spans are an *export*. Span conventions for multi-agent systems
  are still moving and the testbed's semantics must not depend on them.

## Consequences

* Visibility is testable as noninterference: changing an event an agent is not
  authorised to see cannot change that agent's view.
* Storage is a single append-only table plus a content-addressed artifact
  directory; Postgres and object storage are later implementations of the same
  schema, not a new design.
