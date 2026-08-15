"""Identifier and hashing helpers.

Everything that identifies a run, an action or a manifest is derived
deterministically from content, so two machines that execute the same
experiment agree on identity without coordinating.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

ContentHash = str
"""A lowercase hex sha256 digest, prefixed with the algorithm."""


def _canonical_json(value: Any) -> str:
    """Serialise to a stable string: sorted keys, no insignificant whitespace."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def content_hash(value: Any) -> ContentHash:
    """Content-address any JSON-serialisable value."""
    digest = hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def short_hash(value: Any, length: int = 12) -> str:
    return content_hash(value).split(":", 1)[1][:length]


def derive_run_id(
    *,
    experiment_id: str,
    task_id: str,
    env_seed: int,
    team_config_id: str,
    partner_population_id: str | None,
    perturbation_id: str | None,
    repetition: int,
) -> str:
    """Derive the run identity defined in the plan.

    The tuple deliberately excludes the attempt number: infrastructure retries
    create new *attempts* on the same run, so they cannot inflate evaluation
    counts.
    """
    key = {
        "experiment_id": experiment_id,
        "task_id": task_id,
        "env_seed": env_seed,
        "team_config_id": team_config_id,
        "partner_population_id": partner_population_id,
        "perturbation_id": perturbation_id,
        "repetition": repetition,
    }
    return f"run_{short_hash(key, 16)}"


def idempotency_key(*, run_id: str, logical_action: Mapping[str, Any]) -> str:
    """Key for one logical scheduler action.

    Two attempts of the same run that reach the same logical action produce the
    same key, so the store rejects the duplicate instead of committing the work
    twice.
    """
    return f"idem_{short_hash({'run_id': run_id, 'action': dict(logical_action)}, 24)}"


def normalize_for_hash(value: Any, *, drop_keys: frozenset[str] = frozenset()) -> Any:
    """Recursively drop volatile keys (wall-clock stamps, absolute paths) so that
    two executions of the same seeded fixture can be compared byte for byte."""
    if isinstance(value, Mapping):
        return {
            k: normalize_for_hash(v, drop_keys=drop_keys)
            for k, v in sorted(value.items())
            if k not in drop_keys
        }
    if isinstance(value, (list, tuple)):
        return [normalize_for_hash(v, drop_keys=drop_keys) for v in value]
    return value
