"""A guard that makes "scoring performs no external calls" checkable.

Rescoring and playback must never invoke a model, a tool or an agent. Rather
than trusting scorer authors, the eval layer runs them inside this guard: any
socket the code opens raises `PlaybackViolation`.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from testbed_contracts.errors import PlaybackViolation


class _BlockedSocket(socket.socket):
    def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - defensive
        raise PlaybackViolation(
            "offline stage attempted to open a socket; scoring and playback must be pure"
        )


@contextmanager
def no_external_calls() -> Iterator[None]:
    """Block network access for the duration of the block."""
    original_socket = socket.socket
    original_connect = socket.create_connection

    def blocked_connection(*args: Any, **kwargs: Any) -> Any:
        raise PlaybackViolation(
            "offline stage attempted a network connection; scoring and playback must be pure"
        )

    socket.socket = _BlockedSocket  # type: ignore[misc]
    socket.create_connection = blocked_connection
    try:
        yield
    finally:
        socket.socket = original_socket  # type: ignore[misc]
        socket.create_connection = original_connect
