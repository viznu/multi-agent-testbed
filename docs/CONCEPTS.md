# Concepts

## Run identity, attempts and repetitions

A **run** is identified by
`(experiment_id, task_id, env_seed, team_config_id, partner_population_id, perturbation_id, repetition)`,
hashed into a stable `run_id`. Nothing about *when* or *how many times* the
software tried to execute it enters that identity.

An **attempt** is one execution of a run. An infrastructure retry creates a new
attempt on the same run, so retries can never inflate the number of evaluations.

A **rerun** is a new run from the same manifest, recorded at the next repetition
index. It may produce different outputs, and `matb rerun` prints the declared
reproducibility level rather than promising equality.

## Playback, resume and rerun

| Term | What it does | External calls |
|---|---|---|
| `playback` | Reconstruct stored events, state and authorised views | none |
| `resume` | Continue an interrupted attempt from a checkpoint | yes |
| `rerun` | Execute a new run from the same manifest | yes |

## Views

Every view must be named: `omniscient`, `public`, or an `agent_id`. The stored
omniscient event is never mutated; a view is a projection of it. Fault records
are `omniscient_only`, so an agent can never observe that it was the target of
one.

## Reproducibility levels

* `bit_exact` — identical artifacts and events.
* `environment_exact` — pinned environment and deterministic scheduler, but
  external model output may differ.
* `best_effort` — an unpinnable external dependency.

A run reports the weakest level of any component. A closed model API normally
caps a rerun at `environment_exact` or `best_effort`.

## Eval-set kinds

* `frozen_eval` — versioned cases eligible for comparisons and leaderboards.
* `quarantine` — discovered, adversarial or unreviewed cases; excluded from
  comparisons *by schema*, not by convention.
* `regression` — a human-reviewed failure promoted with immutable provenance.
* `optimization` — cases exposed to prompt, team or architecture search; never
  eligible for held-out claims.

## Terminal states

`complete` and `task_failed` are evaluation outcomes. `cancelled`,
`policy_blocked` and `infra_failed` are attrition and are reported separately.
Exhausting a message, cost or model-call budget is `policy_blocked`; running out
of logical time or events is an ordinary episode end, and the verifier still
judges what the agents achieved.
