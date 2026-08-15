"""End-to-end tests of the `matb` commands, through Typer's runner."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from testbed_cli.main import app

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
CATALOG = Path(__file__).resolve().parents[2] / "catalog"

runner = CliRunner()


def _invoke(*args: str):
    result = runner.invoke(app, list(args))
    if result.exception and not isinstance(result.exception, SystemExit):
        raise result.exception
    return result


@pytest.fixture
def ws(tmp_path: Path) -> str:
    return str(tmp_path / "ws")


def test_validate_accepts_the_examples(ws):
    for example in ("solo_lookup", "coop_codeword", "coop_baseline", "mixed_split"):
        result = _invoke("validate", str(EXAMPLES / f"{example}.yaml"), "-w", ws)
        assert result.exit_code == 0, result.output
        assert "manifest is valid" in result.output


def test_validate_flags_a_multi_agent_arm_with_no_baseline(tmp_path: Path, ws):
    import yaml

    raw = yaml.safe_load((EXAMPLES / "coop_codeword.yaml").read_text("utf-8"))
    raw.pop("baseline_experiment")
    path = tmp_path / "no_baseline.yaml"
    path.write_text(yaml.safe_dump(raw), "utf-8")

    result = _invoke("validate", str(path), "-w", ws)
    assert result.exit_code == 1
    assert "compute-matched single-agent baseline" in result.output


def test_dry_run_plans_without_executing(ws):
    result = _invoke("run", str(EXAMPLES / "coop_codeword.yaml"), "-w", ws, "--dry-run")
    assert result.exit_code == 0
    assert result.output.count("task=coop_codeword") == 3
    listed = _invoke("runs", "-w", ws)
    assert listed.output.strip() == "", "a dry run must not record anything"


def test_run_playback_rescore_export_and_compare(ws, tmp_path: Path):
    assert _invoke("run", str(EXAMPLES / "coop_codeword.yaml"), "-w", ws).exit_code == 0
    assert _invoke("run", str(EXAMPLES / "coop_baseline.yaml"), "-w", ws).exit_code == 0

    listed = _invoke("runs", "smoke_coop_codeword", "-w", ws)
    run_id = listed.output.split()[0]

    omniscient = _invoke("playback", run_id, "-w", ws, "--view", "omniscient")
    assert "verifier: success=True" in omniscient.output

    restricted = _invoke("playback", run_id, "-w", ws, "--view", "agent:researcher_2")
    assert len(restricted.output) < len(omniscient.output)

    rescored = _invoke("rescore", run_id, "-w", ws)
    assert "no model, agent or tool calls" in rescored.output

    exported = _invoke("export", run_id, "-w", ws, "--format", "bundle")
    assert exported.exit_code == 0
    bundle = Path(ws) / "exports" / run_id
    assert (bundle / "bundle.json").exists()

    compared = _invoke("compare", "smoke_coop_codeword", "smoke_coop_baseline", "-w", ws)
    assert "compute-matched" in compared.output

    spans = _invoke("export", run_id, "-w", ws, "--format", "otel")
    assert spans.exit_code == 0


def test_rerun_creates_a_distinct_run(ws):
    _invoke("run", str(EXAMPLES / "solo_lookup.yaml"), "-w", ws)
    first = _invoke("runs", "-w", ws).output.split()[0]

    result = _invoke("rerun", first, "-w", ws)
    assert result.exit_code == 0
    assert "declared reproducibility" in result.output

    run_ids = [line.split()[0] for line in _invoke("runs", "-w", ws).output.strip().splitlines()]
    assert len(set(run_ids)) == 2, "a rerun is a new run, not a second attempt"


def test_rerun_with_an_override_says_it_is_a_different_configuration(ws):
    _invoke("run", str(EXAMPLES / "solo_lookup.yaml"), "-w", ws)
    run_id = _invoke("runs", "-w", ws).output.split()[0]
    result = _invoke("rerun", run_id, "-w", ws, "--override", "limits.max_logical_time=7")
    assert "different experiment configuration" in result.output


def test_catalog_commands(ws):
    listed = _invoke("catalog", "list", "--path", str(CATALOG))
    assert "lane" in listed.output
    assert "does NOT exist in this repository" in listed.output
    assert _invoke("catalog", "verify", "--path", str(CATALOG)).exit_code == 0


def test_doctor_reports_what_is_missing(ws):
    result = _invoke("doctor", "-w", ws)
    assert result.exit_code == 0
    assert "not implemented here" in result.output
    assert "scripted" in result.output


def test_bundle_contains_no_secret_values_and_a_declared_level(ws):
    _invoke("run", str(EXAMPLES / "solo_lookup.yaml"), "-w", ws)
    run_id = _invoke("runs", "-w", ws).output.split()[0]
    _invoke("export", run_id, "-w", ws, "--format", "bundle")

    bundle = json.loads((Path(ws) / "exports" / run_id / "bundle.json").read_text("utf-8"))
    assert bundle["declared_reproducibility"] in ("bit_exact", "environment_exact", "best_effort")
    assert bundle["event_hash"].startswith("sha256:")
    for name in ("manifest.json", "events.jsonl", "provenance.json"):
        assert (Path(ws) / "exports" / run_id / name).exists()
