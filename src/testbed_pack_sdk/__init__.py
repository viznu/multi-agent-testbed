"""The stable surface that packs and topology plugins are written against.

It declares exactly five hook families and contains no runtime implementation:

1. task            -- `TaskProvider` produces stable, content-addressed cases.
2. topology        -- `TopologyPlugin` *proposes* routing; World commits it.
3. World action    -- `ActionHandler` interprets a domain action against state.
4. verifier        -- `Verifier` deterministically judges final state.
5. scorer          -- `Scorer` reads an immutable event view, offline.

Preference, control, discovery and optimisation are composition recipes over
these hooks. They must not add a sixth protocol.
"""

from testbed_pack_sdk.hooks import (
    ActionHandler,
    Pack,
    Scorer,
    ScorerContext,
    TaskCase,
    TaskProvider,
    TopologyPlugin,
    Verifier,
)
from testbed_pack_sdk.world_ports import (
    Delivery,
    Proposal,
    RoutingDecision,
    StateChange,
    WorldAction,
    WorldPorts,
    WorldSnapshotView,
)

PACK_SDK_VERSION = "1.0.0"

__all__ = [
    "PACK_SDK_VERSION",
    "ActionHandler",
    "Delivery",
    "Pack",
    "Proposal",
    "RoutingDecision",
    "Scorer",
    "ScorerContext",
    "StateChange",
    "TaskCase",
    "TaskProvider",
    "TopologyPlugin",
    "Verifier",
    "WorldAction",
    "WorldPorts",
    "WorldSnapshotView",
]
