"""Closed enumerations used across the contracts.

Catalog *capability tags* are deliberately not an enum (the plan requires them to
stay open); see `testbed_catalog`. The enumerations here describe kernel
semantics, which must stay closed so that stored events remain interpretable.
"""

from __future__ import annotations

from enum import StrEnum


class RunState(StrEnum):
    """Lifecycle of a run. The four failure states are deliberately distinct so
    that infrastructure problems are never reported as task failures."""

    PLANNED = "planned"
    PROVISIONING = "provisioning"
    RUNNING = "running"
    VERIFYING = "verifying"
    SCORING = "scoring"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    TASK_FAILED = "task_failed"
    POLICY_BLOCKED = "policy_blocked"
    INFRA_FAILED = "infra_failed"


TERMINAL_STATES = frozenset(
    {
        RunState.COMPLETE,
        RunState.CANCELLED,
        RunState.TASK_FAILED,
        RunState.POLICY_BLOCKED,
        RunState.INFRA_FAILED,
    }
)

#: Terminal states that represent a completed *evaluation*. Anything else is
#: attrition and must be reported separately rather than scored as a failure.
EVALUABLE_STATES = frozenset({RunState.COMPLETE, RunState.TASK_FAILED})


class EventKind(StrEnum):
    """Kinds of canonical domain event.

    New kinds may be added; existing kinds and their payload keys may not change
    meaning. Compatibility fixtures in `schemas/fixtures` pin this.
    """

    RUN_STARTED = "run.started"
    RUN_FINISHED = "run.finished"
    ATTEMPT_STARTED = "attempt.started"

    AGENT_REGISTERED = "agent.registered"
    AGENT_SPAWNED = "agent.spawned"
    AGENT_INVOKED = "agent.invoked"
    AGENT_MESSAGE = "agent.message"
    AGENT_FINAL = "agent.final"
    AGENT_ERROR = "agent.error"

    TOOL_CALLED = "tool.called"
    TOOL_RESULT = "tool.result"

    WORLD_ACTION = "world.action"
    WORLD_STATE_CHANGED = "world.state_changed"
    WORLD_MESSAGE_DELIVERED = "world.message_delivered"
    WORLD_CLOCK_ADVANCED = "world.clock_advanced"
    WORLD_SNAPSHOT_CREATED = "world.snapshot.created"

    FAULT_INJECTED = "fault.injected"
    POLICY_DECISION = "policy.decision"

    VERIFIER_RESULT = "verifier.result"
    PAYOFF_ASSIGNED = "payoff.assigned"


class AgentEventKind(StrEnum):
    """What an agent adapter may emit. Adapters translate framework-native
    output into exactly these kinds; the kernel normalises them into events."""

    MESSAGE = "message"
    TOOL_CALL = "tool_call"
    WORLD_ACTION = "world_action"
    FINAL = "final"
    ERROR = "error"


class VisibilityPolicy(StrEnum):
    """How an event may be projected into agent views."""

    PUBLIC = "public"
    PRIVATE = "private"
    PARTITION = "partition"
    OMNISCIENT_ONLY = "omniscient_only"


class WorldDriverKind(StrEnum):
    SESSION = "session"
    ENV = "env"


class EnvMode(StrEnum):
    """Sub-mode for the environment driver."""

    AEC = "aec"
    PARALLEL = "parallel"


class FaultKind(StrEnum):
    DROP_MESSAGE = "drop_message"
    DELAY_MESSAGE = "delay_message"
    DUPLICATE_MESSAGE = "duplicate_message"
    CORRUPT_MESSAGE = "corrupt_message"
    AGENT_DROPOUT = "agent_dropout"
    TOOL_FAILURE = "tool_failure"


class ReproducibilityLevel(StrEnum):
    """Declared, not promised. A run reports the weakest level of any component."""

    BIT_EXACT = "bit_exact"
    ENVIRONMENT_EXACT = "environment_exact"
    BEST_EFFORT = "best_effort"


#: Ordered weakest-last so that a run can take the minimum over its components.
REPRODUCIBILITY_ORDER = (
    ReproducibilityLevel.BIT_EXACT,
    ReproducibilityLevel.ENVIRONMENT_EXACT,
    ReproducibilityLevel.BEST_EFFORT,
)


class Maturity(StrEnum):
    """Honest maturity of a catalog record. `stub` and `external` mean the
    integration does not exist in this repository."""

    CERTIFIED = "certified"
    EXPERIMENTAL = "experimental"
    STUB = "stub"
    EXTERNAL = "external"


class Runtime(StrEnum):
    IN_PROCESS = "in_process"
    SUBPROCESS = "subprocess"
    OCI = "oci"
    REMOTE = "remote"


class EvalSetKind(StrEnum):
    """Lifecycle state of benchmark cases. Leaderboard/comparison queries filter
    on this by construction, so quarantine and optimisation cases cannot leak
    into headline results."""

    FROZEN_EVAL = "frozen_eval"
    QUARANTINE = "quarantine"
    REGRESSION = "regression"
    OPTIMIZATION = "optimization"


#: Only these case kinds may appear in comparisons and leaderboards.
COMPARABLE_EVAL_SETS = frozenset({EvalSetKind.FROZEN_EVAL, EvalSetKind.REGRESSION})
