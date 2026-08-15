"""Versioned contracts shared by every part of the testbed.

This package is the only module that every other module is allowed to import.
It contains data models, identifiers, enumerations and ports (protocols).
It must never import an adapter, plugin, pack, or any kernel implementation.
"""

from testbed_contracts.enums import (
    AgentEventKind,
    EvalSetKind,
    EventKind,
    FaultKind,
    Maturity,
    ReproducibilityLevel,
    RunState,
    Runtime,
    VisibilityPolicy,
    WorldDriverKind,
)
from testbed_contracts.events import Event, EventView, canonical_event_hash
from testbed_contracts.ids import (
    ContentHash,
    content_hash,
    derive_run_id,
    idempotency_key,
)
from testbed_contracts.manifest import (
    AgentSpec,
    ExperimentManifest,
    FaultSpec,
    LimitsSpec,
    PayoffSpec,
    ScorerSpec,
    TaskPackSpec,
    TeamSpec,
    WorldSpec,
)
from testbed_contracts.ports import (
    AgentDescriptor,
    AgentEvent,
    AgentHandle,
    AgentRequest,
    ArtifactStore,
    BlobRef,
    EventStore,
    Health,
    Runner,
    Sandbox,
)
from testbed_contracts.results import (
    Attempt,
    Checkpoint,
    RunRecord,
    RunResult,
    Score,
    ScoreSet,
)

SCHEMA_VERSION = "1.0.0"
"""Version of the contract bundle as a whole (manifest + event envelope)."""

__all__ = [
    "SCHEMA_VERSION",
    "AgentDescriptor",
    "AgentEvent",
    "AgentEventKind",
    "AgentHandle",
    "AgentRequest",
    "AgentSpec",
    "ArtifactStore",
    "Attempt",
    "BlobRef",
    "Checkpoint",
    "ContentHash",
    "EvalSetKind",
    "Event",
    "EventKind",
    "EventStore",
    "EventView",
    "ExperimentManifest",
    "FaultKind",
    "FaultSpec",
    "Health",
    "LimitsSpec",
    "Maturity",
    "PayoffSpec",
    "ReproducibilityLevel",
    "RunRecord",
    "RunResult",
    "RunState",
    "Runner",
    "Runtime",
    "Score",
    "ScoreSet",
    "ScorerSpec",
    "Sandbox",
    "TaskPackSpec",
    "TeamSpec",
    "VisibilityPolicy",
    "WorldDriverKind",
    "WorldSpec",
    "canonical_event_hash",
    "content_hash",
    "derive_run_id",
    "idempotency_key",
]
