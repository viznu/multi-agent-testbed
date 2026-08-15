"""Records produced by a run: attempts, checkpoints, scores and results."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from testbed_contracts.enums import EvalSetKind, ReproducibilityLevel, RunState
from testbed_contracts.ids import ContentHash
from testbed_contracts.ports import BlobRef


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class Attempt(Frozen):
    """One execution attempt of a run. Retries create attempts, not runs."""

    attempt_id: str
    run_id: str
    attempt_number: int
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    finished_at: datetime | None = None
    state: RunState = RunState.PLANNED
    resumed_from_checkpoint: str | None = None


class Checkpoint(Frozen):
    """Everything needed to resume: the consumed event sequence, world snapshot,
    driver state, agent snapshot references, pending deliveries and RNG state."""

    checkpoint_id: str
    run_id: str
    attempt_id: str
    sequence: int
    logical_time: int
    world_snapshot: dict[str, Any]
    driver_state: dict[str, Any]
    agent_snapshots: dict[str, BlobRef] = Field(default_factory=dict)
    pending_deliveries: tuple[dict[str, Any], ...] = ()
    rng_state: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RunRecord(Frozen):
    """Metadata about a run, independent of its attempts."""

    run_id: str
    experiment_id: str
    manifest_hash: ContentHash
    task_id: str
    env_seed: int
    team_config_id: str
    partner_population_id: str | None = None
    perturbation_id: str | None = None
    repetition: int = 0
    eval_set_kind: EvalSetKind = EvalSetKind.FROZEN_EVAL
    state: RunState = RunState.PLANNED
    reproducibility: ReproducibilityLevel = ReproducibilityLevel.BEST_EFFORT
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Score(Frozen):
    """One scorer's output for one run.

    `is_judge` keeps model-judged numbers separable from hard success at every
    downstream stage; aggregation never merges the two.
    """

    run_id: str
    scorer: str
    version: str
    kind: str
    value: float
    is_judge: bool = False
    view: str = "omniscient"
    detail: dict[str, Any] = Field(default_factory=dict)
    computed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @property
    def qualified_name(self) -> str:
        return f"{self.scorer}@{self.version}"


class ScoreSet(Frozen):
    run_id: str
    scores: tuple[Score, ...] = ()

    def get(self, scorer: str) -> Score | None:
        for score in self.scores:
            if score.scorer == scorer:
                return score
        return None

    @property
    def hard(self) -> tuple[Score, ...]:
        return tuple(s for s in self.scores if not s.is_judge)

    @property
    def judged(self) -> tuple[Score, ...]:
        return tuple(s for s in self.scores if s.is_judge)


class VerifierResult(Frozen):
    """A deterministic verifier's view of the final world state."""

    success: bool
    reward: float = 0.0
    per_agent_payoff: dict[str, float] = Field(default_factory=dict)
    constraints_satisfied: bool = True
    detail: dict[str, Any] = Field(default_factory=dict)


class RunResult(Frozen):
    """The complete outcome of one run."""

    run: RunRecord
    attempt_id: str
    state: RunState
    verifier: VerifierResult | None = None
    scores: ScoreSet | None = None
    event_count: int = 0
    logical_time: int = 0
    measures: dict[str, float] = Field(default_factory=dict)
    attrition_reason: str | None = None
    reproducibility: ReproducibilityLevel = ReproducibilityLevel.BEST_EFFORT

    @property
    def is_evaluable(self) -> bool:
        """Infrastructure failures are attrition, not evaluation outcomes."""
        from testbed_contracts.enums import EVALUABLE_STATES

        return self.state in EVALUABLE_STATES


class BundleManifest(Frozen):
    """Index of a reproducibility bundle. Secrets are references, never values."""

    run_id: str
    manifest_hash: ContentHash
    event_hash: ContentHash
    declared_reproducibility: ReproducibilityLevel
    tool_versions: dict[str, str] = Field(default_factory=dict)
    files: tuple[str, ...] = ()
    secret_references: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    kind: Literal["bundle"] = "bundle"
