"""The kernel: World, deterministic scheduling, lifecycle, visibility, playback.

It imports contracts and the Pack SDK only. It never imports an adapter, a
topology plugin or a benchmark pack; the composition root injects those.
"""

from testbed_kernel.controller import (
    Composition,
    RunController,
    SimulatedControllerDeath,
    run_event_hash,
)
from testbed_kernel.drivers import DriverOutcome, DriverState, EnvDriver, SessionDriver
from testbed_kernel.errors import (
    KernelError,
    LimitExceeded,
    PlaybackViolation,
    PolicyBlocked,
    ResumeIncompatible,
)
from testbed_kernel.journal import EventJournal
from testbed_kernel.measures import compute_measures
from testbed_kernel.playback import Playback, playback, unauthorized_payloads
from testbed_kernel.rng import DeterministicRng
from testbed_kernel.scheduler import DeliveryQueue, ScheduledDelivery
from testbed_kernel.world import World

__all__ = [
    "Composition",
    "DeliveryQueue",
    "DeterministicRng",
    "DriverOutcome",
    "DriverState",
    "EnvDriver",
    "EventJournal",
    "KernelError",
    "LimitExceeded",
    "Playback",
    "PlaybackViolation",
    "PolicyBlocked",
    "ResumeIncompatible",
    "RunController",
    "ScheduledDelivery",
    "SessionDriver",
    "SimulatedControllerDeath",
    "World",
    "compute_measures",
    "playback",
    "run_event_hash",
    "unauthorized_payloads",
]
