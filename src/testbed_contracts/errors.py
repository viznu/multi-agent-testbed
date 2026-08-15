"""Shared error types.

They live in contracts because more than one layer must raise and catch them,
and a shared exception is not a reason for the eval layer to depend on the
kernel.
"""

from __future__ import annotations


class TestbedError(RuntimeError):
    """Base class for every error the testbed raises deliberately."""


class LimitExceeded(TestbedError):
    """A manifest budget was exhausted. Reported as attrition, never as a task
    failure."""

    def __init__(self, limit: str, detail: str = "") -> None:
        super().__init__(f"limit exceeded: {limit} {detail}".strip())
        self.limit = limit
        self.detail = detail


class PolicyBlocked(TestbedError):
    def __init__(self, rule: str, reason: str) -> None:
        super().__init__(f"policy blocked: {rule}: {reason}")
        self.rule = rule
        self.reason = reason


class PlaybackViolation(TestbedError):
    """Raised when an offline stage (playback, scoring) attempts an external
    call."""


class ResumeIncompatible(TestbedError):
    """The stored checkpoint does not match the manifest being resumed."""
