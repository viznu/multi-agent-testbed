# The lm-evaluation-harness bridge

## What it is

A **task pack**, not a runner. lm-eval supplies task items and metric
conventions; World still schedules, the agent port is still the agent port, and
the events an lm-eval-sourced run emits are the same events as any other run.

That direction is the whole point. It means a static reasoning item can be
compared with an agentic one under matched conditions, and that the same item
can be wrapped in a debate, jury or committee protocol by changing one line of
the manifest — which is what makes a static benchmark interesting to a
*multi-agent* testbed.

## Sources

```yaml
task_pack:
  name: lm_eval
  config:
    source: fixture        # synthetic items shipped here; needs no dependency
    source: jsonl          # materialised items from a file; needs no dependency
    source: lm_eval        # a real task; needs the lm-eval extra
    task: gsm8k
    limit: 50
    metric: numeric        # exact_match | normalized_match | numeric | multiple_choice
    num_fewshot: 3
```

All three produce the same normalised item, so prompt assembly, verification,
scoring and events are identical regardless of origin. Only loading differs.
That is why the pack declares no required import: the fixture and JSONL paths
work with nothing installed, which keeps the entire code path testable offline,
and `source: lm_eval` raises a precise error naming the extra.

JSONL doubles as an interchange format. Items exported once stay runnable after
the upstream harness has moved on.

## Two things it does not do

**It does not reproduce log-likelihood scoring.** Upstream scores
multiple-choice tasks by comparing the log-likelihood the model assigns to each
choice. Here an agent generates and submits an answer. Generative accuracy and
log-likelihood accuracy measure different things, and a number produced here
must never be compared with a published log-likelihood score.

**It does not claim parity.** `tests/parity/` compares against upstream when
`lm_eval` is installed; it is skipped by default and belongs on a scheduled run.
Until it has passed for a given task, agreement is unverified and the catalog
record stays `experimental`.

## Safeguards worth knowing about

- The expected answer lives in world state under a key the visibility projector
  hides, so no agent can observe the target it is being asked to produce.
  Verifier results and settled payoffs are `omniscient_only` for the same
  reason: a judge reading an agent view would otherwise be handed the answer key.
- Few-shot examples are removed from the evaluation set, so a few-shot run never
  scores an item it was shown.
- A bare digit is not accepted as a multiple-choice index. "2" could mean the
  second or third option depending on whether a scaffold counts from zero, and
  silently picking a convention would shift scores invisibly. Letters and choice
  text are accepted; a digit that is itself a choice matches as text.
- An unconfigured pack yields no cases at all rather than defaulting to some
  dataset.

## The two examples measure nothing

`examples/lm_eval_wiring_check.yaml` hands the agent the answer as a private
fact, so it scores 1.0 by construction, and every case it produces carries
`wiring_check_only: true`. `examples/lm_eval_floor_baseline.yaml` submits a
constant and scores 0.0. Together they demonstrate that items, prompts,
submission, verification, metrics, events and paired comparison work end to end
— and the floor arm is what would catch a pipeline that reports success for
everything.

Neither says anything about capability. The items are synthetic fixtures written
for this repository, not a benchmark. A real run needs `source: lm_eval` and a
model-backed agent adapter, which does not exist here yet.

## Contamination

Most datasets reachable this way — MMLU, GSM8K, HellaSwag and their neighbours —
are heavily contaminated in modern pretraining corpora. The catalog record says
so. Treat results as a baseline for comparison, never as evidence of novel
capability.
