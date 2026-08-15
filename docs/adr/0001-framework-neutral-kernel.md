# ADR 0001 — A small framework-neutral kernel, not another agent framework

Status: accepted

## Context

Experiments on multi-agent systems need to vary the agent framework, the team
structure, the environment and the incentives independently. A testbed built on
top of one agent framework inherits that framework's object model, so the
framework stops being an experimental variable and becomes a constant.

## Decision

The kernel owns five versioned contracts — Experiment, Agent, World, Evaluation
and Event — and loads runners, packs, sandboxes and viewers as adapters. It is
not an agent framework and not a wrapper around one evaluation product.

The agent port is deliberately smaller than any framework API and smaller than
A2A: describe, invoke, cancel, health, snapshot, restore. Framework objects are
converted at adapter boundaries and never appear in kernel APIs.

## Consequences

* Adding a framework is writing an adapter, not changing the kernel.
* The kernel cannot use framework conveniences (built-in multi-agent loops,
  routing helpers), and has to implement scheduling itself.
* Two runners can be compared on the same normalised event sequence, which is
  the basis for the M2 conformance gate.

## Kill criterion

Adding a topology, an information partition, a payoff, a fault or a partner
population must be a manifest or plug-in change. `tests/contract/test_kill_criterion.py`
adds a new topology, a new payoff, a new information partition, a new fault and
a new partner population without touching kernel source. If that test ever needs
a kernel change to pass, this decision has failed.
