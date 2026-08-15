# ADR 0002 — World is the only scheduler on every execution path

Status: accepted

## Context

Orchestration frameworks and evaluation harnesses both want to own message
routing, turn order and retries. If either of them does, then a "supervisor
versus mesh" comparison is really a comparison of two different schedulers, and
runs executed through different harnesses are not comparable at all.

## Decision

`World` owns authoritative state, logical time, deterministic scheduling,
visibility, delivery, fault injection, snapshots and payoff accounting.

* A runner provisions dependencies and translates agent and tool calls. It must
  not own routing, spawning, timeout resolution, visibility, faults or retries.
* A topology plug-in *proposes* recipients, an ordering hint and a delay.
  `World.route` validates the proposal — dropping unknown recipients, enforcing
  the broadcast policy — and only World commits.
* Delivery order is derived from `(due_time, order_hint, content_hash,
  duplicate_index)`. Nothing in the key depends on the order in which
  deliveries physically arrived, which is what makes reordering concurrent
  inputs provably harmless (`tests/property/test_scheduler_properties.py`).

## Consequences

* A plug-in cannot express "deliver this immediately, out of order". That is the
  point; such an escape hatch would silently break comparability.
* Two drivers (`SessionDriver`, `EnvDriver`) share one World, so turn-based
  sessions and simultaneous-action environments produce the same event algebra.
