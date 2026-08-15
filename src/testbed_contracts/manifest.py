"""The experiment manifest: an immutable, content-addressed description of an
experiment.

The manifest is the *only* thing that should have to change to add a topology,
an information partition, a payoff, a fault or a partner population. That is the
architectural kill criterion, and `tests/contract/test_kill_criterion.py`
enforces it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from testbed_contracts.enums import (
    EnvMode,
    EvalSetKind,
    FaultKind,
    ReproducibilityLevel,
    VisibilityPolicy,
    WorldDriverKind,
)
from testbed_contracts.ids import ContentHash, content_hash

MANIFEST_SCHEMA_VERSION = "1.0.0"

#: Manifest fields that describe *who wrote it* rather than *what it does*.
#: They are excluded from the content hash so that re-authoring a manifest with
#: a new description does not invalidate stored comparisons.
NON_SEMANTIC_FIELDS = frozenset({"created_at", "owner", "description", "tags"})


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskPackSpec(Frozen):
    """Which task cases to run, pinned."""

    name: str
    revision: str = "local"
    dataset_digest: ContentHash | None = None
    sample_selector: tuple[str, ...] = ()
    eval_set_kind: EvalSetKind = EvalSetKind.FROZEN_EVAL
    license_accepted: bool = False
    config: dict[str, Any] = Field(default_factory=dict)


class ToolGrant(Frozen):
    name: str
    scope: dict[str, Any] = Field(default_factory=dict)


class AgentSpec(Frozen):
    """One agent in the experiment.

    `adapter` names an entry point; the kernel never imports adapters directly.
    """

    agent_id: str
    adapter: str
    role: str = "worker"
    model: str = "fake/deterministic"
    prompt: str = ""
    config: dict[str, Any] = Field(default_factory=dict)
    tool_grants: tuple[ToolGrant, ...] = ()
    credentials_policy: Literal["none", "scoped", "inherited"] = "none"
    budget_model_calls: int | None = None
    budget_cost_usd: float | None = None
    reproducibility: ReproducibilityLevel = ReproducibilityLevel.BIT_EXACT

    @property
    def prompt_digest(self) -> ContentHash:
        return content_hash(self.prompt)


class CommunicationSpec(Frozen):
    """Communication policy. Everything here is a World-enforced property, not
    something a topology plugin may bypass."""

    max_message_bytes: int = 65_536
    broadcast_allowed: bool = True
    default_visibility: VisibilityPolicy = VisibilityPolicy.PUBLIC
    #: Latency in logical ticks applied to every delivery.
    delivery_latency: int = 1


class MemorySpec(Frozen):
    kind: Literal["private", "shared", "blackboard"] = "private"
    retention: int | None = None


class InformationPartition(Frozen):
    """Who may see what at the start of the run.

    `private_facts` maps agent_id -> the facts only that agent receives. This is
    how the cooperative pack gives two agents complementary information.
    """

    public_facts: dict[str, Any] = Field(default_factory=dict)
    private_facts: dict[str, dict[str, Any]] = Field(default_factory=dict)


class PayoffSpec(Frozen):
    """Incentives. `mode` selects how the verifier's per-agent payoffs combine."""

    mode: Literal["cooperative", "zero_sum", "general_sum", "mixed_motive"] = "cooperative"
    team_weight: float = 1.0
    individual_weight: float = 0.0
    welfare: Literal["sum", "min", "nash"] = "sum"

    @model_validator(mode="after")
    def _check_weights(self) -> PayoffSpec:
        if self.mode == "cooperative" and self.individual_weight != 0.0:
            raise ValueError("cooperative payoff must not carry an individual weight")
        if self.mode == "mixed_motive" and self.individual_weight == 0.0:
            raise ValueError("mixed_motive payoff requires a non-zero individual weight")
        return self


class FaultSpec(Frozen):
    """A deterministic or seeded fault. Faults are applied by World only."""

    kind: FaultKind
    #: Apply when either side of the delivery is one of these actors; empty
    #: means any. Use `senders`/`recipients` when a specific edge matters --
    #: "the hand-off from A to B" is a different fault from "anything touching
    #: B", and conflating them makes a fault experiment hard to interpret.
    targets: tuple[str, ...] = ()
    senders: tuple[str, ...] = ()
    recipients: tuple[str, ...] = ()
    #: Deterministic trigger: apply on these 1-based occurrences. Takes priority
    #: over `probability` so fixtures can be exactly reproducible.
    on_occurrences: tuple[int, ...] = ()
    probability: float = 0.0
    #: For DELAY_MESSAGE, extra logical ticks.
    delay_ticks: int = 1

    @model_validator(mode="after")
    def _check_trigger(self) -> FaultSpec:
        if not self.on_occurrences and self.probability <= 0.0:
            raise ValueError("fault needs either on_occurrences or a positive probability")
        return self


class WorldSpec(Frozen):
    driver: WorldDriverKind = WorldDriverKind.SESSION
    env_mode: EnvMode = EnvMode.AEC
    topology: str = "solo"
    topology_config: dict[str, Any] = Field(default_factory=dict)
    communication: CommunicationSpec = CommunicationSpec()
    memory: MemorySpec = MemorySpec()
    information: InformationPartition = InformationPartition()
    #: Logical ticks per scheduling round.
    clock_step: int = 1


class TeamSpec(Frozen):
    team_config_id: str = "default"
    partner_population_id: str | None = None
    partner_sampling: Literal["fixed", "uniform", "held_out"] = "fixed"


class LimitsSpec(Frozen):
    """Hard caps. Exceeding one ends the run in a distinct, non-task terminal
    state so that budget exhaustion is never scored as a task failure."""

    max_logical_time: int = 100
    max_events: int = 2_000
    max_messages: int = 200
    max_model_calls: int = 200
    max_spawns: int = 0
    max_cost_usd: float = 10.0
    max_wall_seconds: float = 600.0
    checkpoint_every_events: int = 10


class ScorerSpec(Frozen):
    """A scorer pinned by version. Judge scorers additionally pin the judge model
    and prompt so that their outputs stay attributable."""

    name: str
    version: str
    kind: Literal["deterministic", "trajectory", "coordination", "safety", "efficiency", "judge"]
    config: dict[str, Any] = Field(default_factory=dict)
    judge_model: str | None = None
    judge_prompt: str | None = None
    randomize_order: bool = True
    view: str = "omniscient"

    @model_validator(mode="after")
    def _check_judge(self) -> ScorerSpec:
        if self.kind == "judge" and not self.judge_model:
            raise ValueError("judge scorer must pin a judge model")
        return self

    @property
    def qualified_name(self) -> str:
        return f"{self.name}@{self.version}"


class SandboxSpec(Frozen):
    backend: Literal["process", "oci", "gvisor", "none"] = "process"
    image_digest: str | None = None
    network: Literal["deny", "proxy", "allow"] = "deny"
    cpu_limit: float = 1.0
    memory_mb: int = 512
    writable_mounts: tuple[str, ...] = ()


class RetentionSpec(Frozen):
    keep_payloads: bool = True
    redact_patterns: tuple[str, ...] = ()


class ExperimentManifest(Frozen):
    """The complete, immutable description of an experiment."""

    manifest_schema_version: str = MANIFEST_SCHEMA_VERSION
    experiment_id: str
    owner: str = "unknown"
    description: str = ""
    tags: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    task_pack: TaskPackSpec
    runner: str = "raw_session"
    world: WorldSpec = WorldSpec()
    agents: tuple[AgentSpec, ...]
    team: TeamSpec = TeamSpec()
    payoff: PayoffSpec = PayoffSpec()
    sandbox: SandboxSpec = SandboxSpec()
    faults: tuple[FaultSpec, ...] = ()

    seeds: tuple[int, ...] = (0,)
    repetitions: int = 1
    perturbations: tuple[str, ...] = ()
    concurrency: int = 1
    limits: LimitsSpec = LimitsSpec()
    scorers: tuple[ScorerSpec, ...] = ()
    retention: RetentionSpec = RetentionSpec()

    #: Optional pointer to a compute-matched single-agent baseline manifest.
    #: The plan requires every multi-agent configuration to have one.
    baseline_experiment: str | None = None

    @model_validator(mode="after")
    def _check_agents(self) -> ExperimentManifest:
        ids = [a.agent_id for a in self.agents]
        if not ids:
            raise ValueError("an experiment needs at least one agent")
        if len(set(ids)) != len(ids):
            raise ValueError("agent_id values must be unique")
        unknown = set(self.world.information.private_facts) - set(ids)
        if unknown:
            raise ValueError(f"private facts addressed to unknown agents: {sorted(unknown)}")
        return self

    def normalized(self) -> dict[str, Any]:
        """Semantic content of the manifest, with authorship metadata removed."""
        raw = self.model_dump(mode="json")
        return {k: v for k, v in sorted(raw.items()) if k not in NON_SEMANTIC_FIELDS}

    @property
    def manifest_hash(self) -> ContentHash:
        return content_hash(self.normalized())

    @property
    def is_multi_agent(self) -> bool:
        return len(self.agents) > 1

    def agent(self, agent_id: str) -> AgentSpec:
        for spec in self.agents:
            if spec.agent_id == agent_id:
                return spec
        raise KeyError(agent_id)

    def declared_reproducibility(self) -> ReproducibilityLevel:
        """Weakest declared level across components; a run may never claim more."""
        from testbed_contracts.enums import REPRODUCIBILITY_ORDER

        worst = ReproducibilityLevel.BIT_EXACT
        for spec in self.agents:
            if REPRODUCIBILITY_ORDER.index(spec.reproducibility) > REPRODUCIBILITY_ORDER.index(
                worst
            ):
                worst = spec.reproducibility
        return worst
