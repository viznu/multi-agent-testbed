# Integrations: how switching works, and what it costs

## The four states

An integration is in exactly one of these, and they are never conflated:

| State | Meaning | Fix |
|---|---|---|
| `active` | Installed, switched on, loadable | — |
| `disabled` | Present, switched off on purpose | `matb integrations enable <id>` |
| `not_installed` | An adapter exists; its dependencies do not | `pip install 'multi-agent-testbed[<extra>]'` |
| `no_adapter` | A catalog record only. Nothing has been built | Write the adapter |

The state that matters most is the one that does *not* exist: a manifest naming
an integration and silently getting something else, or a run that quietly
skipped it. Naming a plug-in that is disabled or missing fails the run with a
remediation.

```bash
matb integrations list          # everything with a switch
matb integrations list --all    # plus the catalogue-only records
matb integrations disable testbed/smoke-pack
matb integrations verify        # packaging details still unconfirmed
```

## Where the catalog and switches come from

Resolved in a fixed order, and `matb doctor` prints what it resolved:

1. `--path` (catalog only)
2. `$MATB_CATALOG` / `$MATB_SWITCHES`
3. the nearest `catalog/` directory or `integrations.toml` at or above the
   working directory
4. the catalog packaged inside the distribution; no switches (nothing off)

This order exists because both were once resolved from the working directory,
which meant running `matb` from anywhere else silently stopped enforcing every
switch. A named catalog path that does not exist is now an error, not an empty
catalog.

## Adding an integration

1. **Add a catalog record** with the binding: which port it plugs into
   (`plugin_group`), the name a manifest uses (`plugin_name`), the pip extra,
   and the imports or binaries that prove it is present.
2. **Add the extra** to `pyproject.toml`. If the distribution name is not
   confirmed, leave it out — `matb integrations verify` lists the gap. Guessing
   installs the wrong package with full confidence.
3. **Write the adapter** against an existing port. It may import contracts and
   the Pack SDK, nothing else of ours.
4. **Register an entry point** in the `testbed.*` group.
5. **Set maturity honestly.** `experimental` until its conformance and parity
   gates have actually run; `certified` requires a pinned revision, a reviewed
   licence, a verified source URL and a smoke fixture.

## Coverage per adapter

The catalog names about a hundred tools. That is not a hundred adapters — most
of them arrive through a handful:

| Adapter | Reaches |
|---|---|
| lm-evaluation-harness | most of lane 3: MMLU, BBH, HellaSwag, ARC, GSM8K, MATH, HumanEval, TruthfulQA, GPQA, DROP, … |
| Inspect | Inspect Evals, plus Petri, Bloom, Scout and ControlArena, which all build on it |
| OpenTelemetry sink | Langfuse, Phoenix, MLflow |
| One framework agent adapter, six times | LangGraph, AutoGen, CAMEL, CrewAI, OpenAI Agents SDK, MetaGPT |
| PettingZoo `EnvDriver` | Melting Pot, Concordia, social-dilemma packs |

Lanes 4 and 5 do not share an adapter: each benchmark needs its own image,
verifier and licence review, and all of them are blocked on a container sandbox
that does not exist here yet.

## Deliberately excluded

Commercial and hosted-only services: LangSmith, Braintrust, Humanloop, Galileo,
Weave, the hosted OpenAI Evals API. They add a paid dependency, a network
dependency and an account boundary to a testbed whose value is that someone else
can reproduce a result later. Each is recorded as `external` with the reason, so
the exclusion is visible and reversible.

Not excluded on cost, but still not runnable: unreleased frontier-lab suites,
live leaderboards with no runnable interface, and position papers. They are
recorded so the gaps in coverage are explicit rather than invisible.
