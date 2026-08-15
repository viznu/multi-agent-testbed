# ADR 0005 — The system reports its own limits

Status: accepted

## Context

The easiest way for a testbed to mislead is to report a number that looks like a
measurement but is not one: an infrastructure failure scored as a task failure,
a judge rating pooled into hard success, a rerun described as reproducible when
a closed model API cannot support that claim, or a catalog entry that implies an
integration exists when it does not.

## Decision

Four separations are structural, not conventional:

1. **Terminal states.** `complete`, `task_failed`, `policy_blocked`,
   `cancelled` and `infra_failed` are distinct. Only `complete` and
   `task_failed` are evaluable; everything else is attrition and is reported
   separately. Budget exhaustion is `policy_blocked`, never a task failure.
2. **Judge versus hard.** `Score.is_judge` flags judge output, and
   `ScoreSet.hard` / `.judged` keep them apart at every downstream stage. A
   judge score records its model, prompt digest, transcript view and seed, or it
   is not attributable.
3. **Reproducibility level.** Each component declares `bit_exact`,
   `environment_exact` or `best_effort`; a run reports the weakest level of any
   component. An adapter that cannot snapshot is `playback_only` and must not
   claim bit-exactness.
4. **Catalog maturity.** `certified | experimental | stub | external`. `stub`
   and `external` mean the integration does not exist in this repository, and
   `matb catalog list` says so in plain words. A `certified` claim without a
   pinned revision, a licence review and an entry point fails
   `matb catalog verify`.

Measures that are *inferred* rather than observed — per-agent contribution,
ablation, specialisation — are deliberately absent from `compute_measures`.
They belong to separate counterfactual experiments or optional scorers.

## Consequences

* Some outputs look worse than a more permissive testbed's would: a starved run
  produces no score at all rather than a zero.
* Reports carry uncertainty by default: comparisons state pair counts, effect
  size, a confidence interval and whether the arms were compute-matched.
