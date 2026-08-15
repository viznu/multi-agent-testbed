# Multi-Agent Testbed

A framework-neutral, event-sourced testbed for running controlled, reproducible
experiments on single- and multi-agent AI systems.

An experiment here means a comparison under matched conditions: the same task,
the same seeds, the same compute budget, with one thing changed — the number of
agents, how they are organised, what each of them is allowed to know, what they
are paid for, or which failures are injected into their communication. The
testbed records everything that happened as an append-only event log, so a
result can be replayed, re-scored and audited long after the run finished.

**Scope.** This repository implements the contracts layer and a complete
end-to-end vertical slice: a deterministic scheduler, a local event store,
three small fixture tasks, offline scoring, playback, resume and paired
comparison. Integrations with external evaluation frameworks and benchmarks
(Inspect, Harbor, PettingZoo, ControlArena and the rest) are **not**
implemented; they exist only as catalog entries that state their status
honestly. See [docs/ROADMAP.md](docs/ROADMAP.md) for the full line between what
runs and what does not.

## What problem it addresses

Claims about multi-agent systems are easy to make and hard to check. A team of
five agents that beats one agent may simply be spending five times the compute.
A coordination score may be measuring the harness rather than the agents. A
"reproducible" run may depend on a model API that changed last week. An
infrastructure crash counted as a task failure quietly makes a system look worse
than it is.

The design responds to each of those in a structural way rather than by
convention:

- **Every multi-agent configuration names a compute-matched single-agent
  baseline**, and the comparison report states whether the arms really were
  matched.
- **The scheduler lives in one place.** Orchestration patterns — supervisor,
  pipeline, mesh, debate — are plug-ins that *propose* routing; only the World
  object orders and commits it. Two different orchestration styles therefore
  differ in what they propose, not in how their runs are scheduled.
- **Reproducibility is declared, not promised.** Each component reports
  `bit_exact`, `environment_exact` or `best_effort`, and a run reports the
  weakest level of any of its parts.
- **Infrastructure failure is not task failure.** A crashed controller, an
  exhausted budget and a failed task are three distinct terminal states, and
  only the last one is scored.

## Core ideas

**Events are the source of truth.** Every meaningful action becomes an
append-only event with a logical timestamp, a causal link and a visibility
policy. The omniscient event is stored once; what any individual agent could see
is a *projection* of it, computed on demand. That makes a specific question
answerable: did this agent's behaviour depend on information it was never
supposed to have?

**Everything an experiment varies lives in the manifest.** The manifest is
immutable and content-addressed. Adding a topology, an information partition, a
payoff rule, a fault or a partner population is a manifest or plug-in change —
never a change to the scheduler. A test suite enforces this by adding all five
from outside the source tree and checking that nothing in the kernel had to move.

**Scoring is offline and pure.** Scorers read an immutable event view after the
fact. While they run, network access is blocked, so "re-scoring performs no
model calls" is enforced rather than asserted. Model-judged scores are flagged
and never pooled with deterministic success.

## Try it

```bash
uv venv && uv pip install -e ".[dev]"
```

Run the cooperative fixture — two agents, each holding half of a code word that
neither can complete alone:

```bash
matb run examples/coop_codeword.yaml
```

```text
  run_926057f90e2694ad  coop_codeword    complete       success=True  events=20  model_calls=3
      hard  task_success@1.0.0         1.0
      hard  efficiency@1.0.0           3.0
      hard  coordination@1.0.0         1.0
      hard  safety@1.0.0               0.0
```

Now run its compute-matched single-agent baseline and compare the two:

```bash
matb run examples/coop_baseline.yaml
matb compare smoke_coop_codeword smoke_coop_baseline
```

```text
smoke_coop_codeword vs smoke_coop_baseline on success
  n=3 pairs; A=1.0000 B=1.0000; A is 0.0000 lower (95% CI [0.0000, 0.0000], d=0.000);
  interval includes zero; difference is not resolved at this sample size
  arms are compute-matched (3.0 vs 3.0 model calls)
  attrition: 0/3 vs 0/3
```

Three paired runs of a fixture cannot resolve a difference, and the report says
so instead of reporting a winner.

Replay what one agent could see, as opposed to what happened:

```bash
matb playback <RUN_ID> --view agent:researcher_2
```

Other commands: `matb validate`, `matb resume`, `matb rescore`, `matb rerun`,
`matb export --format bundle|parquet|otel|jsonl`, `matb catalog list|verify`,
`matb integrations list|enable|disable`, `matb doctor`.

## Switching integrations on and off

The catalog describes about a hundred tools across the fifteen lanes of the
evaluation landscape. Each record states honestly what it is:

```bash
matb integrations list
```

```text
  active         testbed/lm-eval-pack       packs:lm_eval
  not_installed  someone/thing              packs:thing
      missing thing_sdk; install with: pip install 'multi-agent-testbed[thing]'

  8 active, 0 switched off, 0 not installed, 90 catalogued with no adapter here
```

Four states, never conflated: `active`, `disabled` (switched off on purpose),
`not_installed` (an adapter exists, its dependencies do not), and `no_adapter`
(a catalog record only — nothing has been built). Naming a plug-in that is off
or missing fails the run with a remediation; it never silently falls back to
something else.

Most of the catalog is `no_adapter` today, and commercial hosted services are
recorded as excluded with the reason. See
[docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) for coverage per adapter and how to
add one.

## Static benchmarks through the lm-eval bridge

lm-evaluation-harness is integrated as a **task source, not a runner**: it
supplies items and metric conventions, World still schedules, and the events it
produces are the same events as any other run. That is what lets a static
reasoning item be compared with an agentic one — or wrapped in a debate or jury
protocol by changing the topology.

```bash
matb run examples/lm_eval_wiring_check.yaml
matb run examples/lm_eval_floor_baseline.yaml
matb compare lm_eval_wiring_check lm_eval_floor_baseline
```

```text
  n=4 pairs; A=1.0000 B=0.0000; A is 1.0000 higher
  (95% CI [1.0000, 1.0000], d=undefined (every pair moved by the same amount));
  interval excludes zero; only 4 pairs, treat as a pilot
  arms are compute-matched (1.0 vs 1.0 model calls)
```

**Both examples measure nothing.** The first hands the agent the answer; the
second submits a constant. They demonstrate the plumbing, and the floor arm is
what would catch a pipeline reporting success for everything. Real datasets need
`source: lm_eval` (the `lm-eval` extra) and a model-backed agent adapter, which
does not exist here yet. See [docs/LM_EVAL.md](docs/LM_EVAL.md), particularly on
why generative accuracy is not comparable with published log-likelihood scores.

## The three fixture tasks

These exist to exercise the contracts. **They are not capability benchmarks**,
and no number produced by them should be reported as a capability result.

1. `solo_lookup` — one agent, a deterministic tool call, a submission.
2. `coop_codeword` — two agents holding complementary private information.
   Success requires the hand-off actually to happen; dropping that one message
   makes the task fail.
3. `mixed_split` — two agents claim shares of a fixed pot. If the claims
   together exceed the pot, everyone gets nothing, so individual and team
   payoffs are genuinely in tension.

## How it is put together

```text
src/testbed_contracts/   data models, ports, enums          (imports nothing of ours)
src/testbed_pack_sdk/    the five hooks packs implement     (imports contracts only)
src/testbed_kernel/      World, scheduler, lifecycle, playback
src/testbed_store/       SQLite event log, artifacts, exports
src/testbed_eval/        scorers, purity guard, paired statistics
src/testbed_catalog/     tool metadata and its honesty rules
src/testbed_cli/         the composition root — the only place plug-ins are discovered
src/testbed_adapters/    agent, runner, sandbox and telemetry adapters
src/testbed_plugins/     topology plug-ins
src/testbed_packs/       the three fixture tasks, and the lm-eval task pack
```

The kernel never imports an adapter, a plug-in or a pack; the composition root
wires them in through entry points. The rule is enforced twice — by
`.importlinter` in CI and by an architecture test that parses the source, so it
holds even without the linter installed.

A pack or plug-in implements at most five hooks: a task provider, a topology
proposal, a World-action handler, a verifier and a scorer. Preference protocols,
control experiments and discovery loops are meant to be *compositions* of those
five, not a sixth kind of hook.

## Reading the output honestly

- `stub` and `external` in the catalog mean the integration **does not exist
  here**. `matb catalog list` prints that in plain words.
- `policy_blocked` means a budget ran out. The run is attrition, not a failure,
  and it is excluded from paired statistics.
- A judge score records the judge model, prompt digest, transcript view and
  seed. The judge that ships is a deterministic stand-in for offline testing —
  it is not a model and its ratings carry no semantic validity.
- The process sandbox reduces accidents. It shares the host kernel and is **not**
  an isolation boundary for untrusted code.
- The testbed produces evidence that can be mapped to governance frameworks. It
  does not certify NIST, EU AI Act, responsible-scaling or any other conformity.

## Development

```bash
pytest                 # 182 tests: contracts, integration, reproducibility, properties
ruff check src tests
mypy
lint-imports           # import boundaries
```

The test suite is entirely offline and deterministic: it runs against a scripted
fixture agent, so the whole thing is reproducible on any machine.

## Documentation

- [docs/CONCEPTS.md](docs/CONCEPTS.md) — run identity, views, reproducibility
  levels, eval-set kinds, terminal states.
- [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) — the four states, how switching
  resolves, coverage per adapter, what is excluded and why.
- [docs/LM_EVAL.md](docs/LM_EVAL.md) — the lm-eval bridge and its limits.
- [docs/ROADMAP.md](docs/ROADMAP.md) — what is implemented and what is not.
- [docs/adr/](docs/adr/) — the architectural decisions and their consequences.

## License

MIT
