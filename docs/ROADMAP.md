# Roadmap and current status

This repository implements the first two milestones of the implementation plan.
Everything else is described here so that the gap between what exists and what is
planned stays explicit.

## Implemented

### M0 — Contracts and architecture tests

| Deliverable | Status |
|---|---|
| Versioned contracts (Experiment, Agent, World, Evaluation, Event) | done |
| Ports as protocols; no adapter types in kernel APIs | done |
| Manifest normalisation and content-addressed hashing | done |
| Extensible catalog capability tags plus runtime and maturity fields | done |
| Import-boundary enforcement (`.importlinter` + a pytest architecture test) | done |
| Event-schema compatibility fixtures | done |
| Architecture decision records | done in `docs/adr/` |

Gate: a fake agent, world, scorer, store and runner compose without importing a
concrete adapter (`tests/contract/test_m0_composition.py`), and stored event
fixtures still deserialise (`tests/contract/test_event_schema_compat.py`).

### M1 — End-to-end vertical slice

| Deliverable | Status |
|---|---|
| Raw session runner | done |
| Deterministic scheduler and World | done |
| Local store (SQLite WAL + content-addressed artifacts) | done |
| Process/fake sandbox | done |
| CLI: validate, run, resume, rescore, playback, rerun, compare, export, doctor | done |
| Three tiny packs (solo, cooperative, mixed-motive) | done |
| Compute-matched single-agent baseline for the cooperative task | done |
| Deterministic verifiers plus one versioned judge scorer | done |
| `EnvDriver` with AEC and parallel modes | done (M3 item, landed early) |
| Fault injection, topology plug-ins, partner-population fields | done |

Gate, all covered by tests:

* controller termination at a checkpoint boundary resumes without duplicating
  completed work (`tests/reproducibility/test_resume.py`);
* playback and offline rescoring perform zero model or tool calls
  (`tests/reproducibility/test_playback_and_purity.py`);
* repeated seeded runs have identical event hashes once wall-clock fields are
  removed (`tests/reproducibility/test_determinism.py`);
* played-back agent views contain no unauthorised payloads (same file);
* reruns report their declared reproducibility level rather than promising
  equality (`matb rerun`).

### Integration switching and the lm-eval bridge

| Deliverable | Status |
|---|---|
| Four-state integration model with remediation messages | done |
| `integrations.toml` switches, resolved through an explicit path order | done |
| Catalog carrying pip extra, required imports/binaries and plug-in binding | done |
| `matb integrations list\|enable\|disable\|verify` | done |
| Per-experiment pack configuration (`task_pack.config`) | done |
| lm-eval task pack: fixture, JSONL and `lm_eval` sources | done |
| Answer metrics: exact, normalised, numeric, multiple choice | done |
| Scorer-parity job against upstream | written, runs only where `lm_eval` is installed |

The lm-eval pack is `experimental`, not `certified`: parity with upstream
scoring is unverified until that job has run for a given task.

## Not implemented

Nothing below exists in code here. The catalog carries a `stub` or `external`
record for each, and `matb doctor` lists them.

* **M1 follow-on** — the rootless OCI sandbox adapter and its security smoke
  tests. The shipped process sandbox reduces accidents and is explicitly *not*
  an isolation boundary for untrusted code.
* **M2** — the Inspect bridge, a certified Inspect eval, the pack registry and
  lockfile, official-scorer parity tests, and Parquet/DuckDB comparison reports.
  Parquet export exists; the analysis reports do not.
* **M3** — framework adapters (LangGraph, AutoGen, …), MCP tools, A2A remote
  agents, Harbor, a browser pack and the native blinded pairwise/team preference
  protocol.
* **M4** — Postgres and object storage, queue workers, a Kubernetes sandbox
  backend, egress proxy, short-lived credentials, quotas, an OTel exporter to a
  real collector and a trace UI. Only the span *projection* exists.
* **M5** — Inspect Scout scanners, Petri 3.x and Petri Bloom, ControlArena,
  transcript clustering, human review and quarantine-to-regression promotion.
  The `eval_set_kind` machinery that keeps quarantine out of leaderboards is
  already in place and tested; the discovery loops that would fill quarantine
  are not.
* **M6** — interpretability probes (TransformerLens, SAELens, NNsight) and
  assurance-profile exporters.

## Deliberate scope limits

* No agent-building DSL, model gateway, prompt manager, hosted leaderboard, web
  UI, Kubernetes control plane or RL trainer. Those are integrations.
* The three shipped packs are contract fixtures, not capability benchmarks. No
  number produced by them should be reported as a capability result.
* The testbed produces evidence that can be mapped to governance frameworks. It
  does not certify NIST, EU AI Act, responsible-scaling or any other legal or
  policy conformity.
