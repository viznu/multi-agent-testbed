"""Manifest loading, validation and override application."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from testbed_contracts.manifest import ExperimentManifest


def load_manifest(path: Path) -> ExperimentManifest:
    raw = yaml.safe_load(Path(path).read_text("utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: a manifest must be a mapping")
    return ExperimentManifest.model_validate(raw)


def _coerce(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def apply_overrides(manifest: ExperimentManifest, overrides: list[str]) -> ExperimentManifest:
    """Apply `--override a.b=value` pairs, producing a *new* manifest.

    Because the manifest is content-addressed, any override changes the manifest
    hash, so an overridden rerun can never be confused with the original.
    """
    if not overrides:
        return manifest
    raw = manifest.model_dump(mode="json")
    for override in overrides:
        if "=" not in override:
            raise ValueError(f"override {override!r} must look like key.path=value")
        path, _, value = override.partition("=")
        node: Any = raw
        parts = path.split(".")
        for part in parts[:-1]:
            node = node[int(part)] if part.isdigit() and isinstance(node, list) else node[part]
        last = parts[-1]
        if last.isdigit() and isinstance(node, list):
            node[int(last)] = _coerce(value)
        else:
            node[last] = _coerce(value)
    return ExperimentManifest.model_validate(raw)
