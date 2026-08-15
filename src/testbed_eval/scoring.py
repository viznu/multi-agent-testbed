"""Offline scoring and rescoring.

Scorers run inside the purity guard, so a scorer that tries to reach the network
fails loudly instead of quietly turning rescoring into a second execution.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from testbed_contracts.enums import EvalSetKind
from testbed_contracts.events import OMNISCIENT_VIEW, EventView
from testbed_contracts.manifest import ExperimentManifest, ScorerSpec
from testbed_contracts.results import RunRecord, Score, ScoreSet, VerifierResult
from testbed_eval.judges import HashRubricJudge
from testbed_eval.purity import no_external_calls
from testbed_pack_sdk.hooks import ScorerContext


class UnknownScorer(KeyError):
    pass


def resolve_judge(spec: ScorerSpec) -> Any:
    """Build the judge a scorer spec pins.

    Only the offline stand-in ships here; a real judge is an adapter registered
    by the composition root.
    """
    if spec.kind != "judge":
        return None
    return HashRubricJudge(model=spec.judge_model or "fake/hash-rubric",
                           prompt=spec.judge_prompt or "")


def score_run(
    *,
    record: RunRecord,
    events: Sequence[Any],
    specs: Sequence[ScorerSpec],
    registry: Mapping[str, Any],
    verifier: VerifierResult | None = None,
    agent_ids: Sequence[str] = (),
) -> ScoreSet:
    """Compute every configured scorer for one run, offline."""
    scores: list[Score] = []
    with no_external_calls():
        for spec in specs:
            scorer = registry.get(spec.name)
            if scorer is None:
                raise UnknownScorer(f"no scorer named {spec.name!r} is registered")
            if scorer.version != spec.version:
                raise UnknownScorer(
                    f"scorer {spec.name} is registered at {scorer.version}, "
                    f"manifest pins {spec.version}"
                )
            view = EventView(events, view=spec.view or OMNISCIENT_VIEW)
            context = ScorerContext(
                run_id=record.run_id,
                task_id=record.task_id,
                agent_ids=tuple(agent_ids),
                verifier=verifier,
                config={**spec.config, "seed": record.env_seed},
                judge=resolve_judge(spec),
            )
            scores.extend(scorer.score(view, context))
    return ScoreSet(run_id=record.run_id, scores=tuple(scores))


def comparable(record: RunRecord) -> bool:
    """Whether a run may enter comparisons and leaderboards.

    Quarantine and optimisation cases are excluded by construction, so
    automatically discovered or tuned cases cannot leak into headline claims.
    """
    from testbed_contracts.enums import COMPARABLE_EVAL_SETS

    return EvalSetKind(record.eval_set_kind) in COMPARABLE_EVAL_SETS


def scorer_specs(manifest: ExperimentManifest) -> tuple[ScorerSpec, ...]:
    return manifest.scorers
