"""Kernel-facing names for the shared error types.

The definitions live in `testbed_contracts.errors` so that the eval layer can
raise `PlaybackViolation` without depending on the kernel.
"""

from __future__ import annotations

from testbed_contracts.errors import (
    LimitExceeded,
    PlaybackViolation,
    PolicyBlocked,
    ResumeIncompatible,
    TestbedError,
)

KernelError = TestbedError

__all__ = [
    "KernelError",
    "LimitExceeded",
    "PlaybackViolation",
    "PolicyBlocked",
    "ResumeIncompatible",
    "TestbedError",
]
