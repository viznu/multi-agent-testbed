"""`matb` -- the command line for the testbed."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Annotated

import typer

from testbed_catalog import load_catalog
from testbed_catalog.availability import IntegrationState, Switches
from testbed_cli.composition import Registry, Workspace, compose
from testbed_cli.integrations import (
    describe,
    load_index,
    resolved_locations,
    switches_path,
)
from testbed_cli.loader import apply_overrides, load_manifest
from testbed_cli.paths import resolve_catalog
from testbed_cli.session import resume_run, run_experiment
from testbed_contracts.enums import RunState
from testbed_contracts.events import OMNISCIENT_VIEW
from testbed_contracts.manifest import ScorerSpec
from testbed_contracts.results import RunResult, VerifierResult
from testbed_eval import compare_experiments, score_run
from testbed_kernel import RunController, run_event_hash
from testbed_kernel import playback as kernel_playback

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="A framework-neutral, event-sourced testbed for multi-agent experiments.",
)

DEFAULT_WORKSPACE = Path(".matb")

WorkspaceOpt = Annotated[Path, typer.Option("--workspace", "-w", help="Where run state lives.")]


def _echo(message: str) -> None:
    typer.echo(message)


def _fail(message: str) -> None:
    typer.secho(message, fg=typer.colors.RED, err=True)
    raise typer.Exit(code=1)


# -- validate --------------------------------------------------------------


@app.command()
def validate(
    manifest_path: Annotated[Path, typer.Argument(help="Path to an experiment YAML file.")],
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
) -> None:
    """Check a manifest, resolve its plug-ins and print what it would run."""
    try:
        manifest = load_manifest(manifest_path)
    except Exception as exc:
        _fail(f"invalid manifest: {exc}")
        return

    registry = Registry.discover()
    problems: list[str] = []
    if manifest.task_pack.name not in registry.packs:
        problems.append(f"unknown pack {manifest.task_pack.name!r}")
    if manifest.world.topology not in registry.topologies:
        problems.append(f"unknown topology {manifest.world.topology!r}")
    if manifest.runner not in registry.runners:
        problems.append(f"unknown runner {manifest.runner!r}")
    for spec in manifest.agents:
        if spec.adapter not in registry.agents:
            problems.append(f"agent {spec.agent_id}: unknown adapter {spec.adapter!r}")
    for spec in manifest.scorers:
        scorer = registry.scorers.get(spec.name)
        if scorer is None:
            problems.append(f"unknown scorer {spec.name!r}")
        elif scorer.version != spec.version:
            problems.append(
                f"scorer {spec.name} is registered at {scorer.version}, "
                f"manifest pins {spec.version}"
            )
    if manifest.is_multi_agent and not manifest.baseline_experiment:
        problems.append(
            "multi-agent experiment has no `baseline_experiment`: every multi-agent "
            "configuration should name a compute-matched single-agent baseline"
        )

    _echo(f"experiment      {manifest.experiment_id}")
    _echo(f"manifest hash   {manifest.manifest_hash}")
    _echo(f"pack            {manifest.task_pack.name}@{manifest.task_pack.revision}")
    _echo(f"runner          {manifest.runner}")
    _echo(f"driver          {manifest.world.driver} / topology {manifest.world.topology}")
    _echo(f"agents          {', '.join(a.agent_id for a in manifest.agents)}")
    _echo(f"payoff          {manifest.payoff.mode}")
    _echo(f"reproducibility {manifest.declared_reproducibility()}")

    try:
        composition, _, store, _ = compose(manifest, Workspace(workspace), registry)
        records = RunController(composition).plan(manifest)
        store.close()
        _echo(f"planned runs    {len(records)}")
    except Exception as exc:
        problems.append(str(exc))

    if problems:
        _echo("")
        for problem in problems:
            typer.secho(f"  problem: {problem}", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    typer.secho("manifest is valid", fg=typer.colors.GREEN)


# -- catalog ---------------------------------------------------------------

catalog_app = typer.Typer(help="Inspect the tool catalog.", no_args_is_help=True)
app.add_typer(catalog_app, name="catalog")


@catalog_app.command("list")
def catalog_list(
    path: Annotated[Path | None, typer.Option("--path", help="Catalog directory.")] = None,
    lane: Annotated[int | None, typer.Option(help="Show one market lane only.")] = None,
    runnable_only: Annotated[bool, typer.Option("--runnable", help="Only what runs here.")] = False,
) -> None:
    """List catalog records with their honest maturity."""
    catalog = load_catalog(resolve_catalog(path).path)
    records = catalog.by_lane(lane) if lane else catalog.records
    if runnable_only:
        records = [r for r in records if r.is_runnable_here]
    for record in records:
        _echo(
            f"  lane {record.lane:>2}  {record.maturity:<12} {record.runtime:<11} "
            f"{record.record_id}"
        )
    _echo("")
    _echo(f"{len(records)} records; totals by maturity: {catalog.summary()}")
    _echo(
        "note: 'stub' and 'external' mean the integration does NOT exist in this "
        "repository; they are catalogue entries, not capabilities."
    )


@catalog_app.command("verify")
def catalog_verify(
    path: Annotated[Path | None, typer.Option("--path", help="Catalog directory.")] = None,
) -> None:
    """Check that no record overstates its own status and every lane is covered."""
    catalog = load_catalog(resolve_catalog(path).path)
    problems = catalog.verify()
    unknown = catalog.unknown_capabilities()
    if unknown:
        _echo(f"capability tags outside the suggested vocabulary (allowed): {unknown}")
    if problems:
        for problem in problems:
            typer.secho(f"  problem: {problem}", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    typer.secho(f"catalog is consistent: {len(catalog)} records, all 15 lanes covered",
                fg=typer.colors.GREEN)


# -- integrations ----------------------------------------------------------

integrations_app = typer.Typer(
    help="Switch integrations on and off, and see why one is unavailable.",
    no_args_is_help=True,
)
app.add_typer(integrations_app, name="integrations")

_STATE_COLOUR = {
    IntegrationState.ACTIVE: typer.colors.GREEN,
    IntegrationState.DISABLED: typer.colors.YELLOW,
    IntegrationState.NOT_INSTALLED: typer.colors.BRIGHT_BLACK,
}


@integrations_app.command("list")
def integrations_list(
    path: Annotated[Path | None, typer.Option("--path", help="Catalog directory.")] = None,
    show_all: Annotated[
        bool, typer.Option("--all", help="Include records that have no adapter at all.")
    ] = False,
) -> None:
    """Show every integration that has, or could have, a switch."""
    index = load_index(path)
    rows = describe(index)
    for record_id, status in rows:
        colour = _STATE_COLOUR.get(status.state, typer.colors.WHITE)
        typer.secho(f"  {status.state:<14}", fg=colour, nl=False)
        binding = (
            f"{status.plugin_group}:{status.plugin_name}"
            if status.plugin_group
            else "(not a plug-in)"
        )
        _echo(f"{record_id:<44} {binding}")
        if not status.usable:
            _echo(f"      {status.reason()}")

    counts = index.counts()
    _echo("")
    _echo(
        f"{counts['active']} active, {counts['disabled']} switched off, "
        f"{counts['not_installed']} not installed, "
        f"{counts['no_adapter']} catalogued with no adapter here"
    )
    if show_all:
        _echo("")
        _echo("records with no adapter (catalogue entries only):")
        for record_id, status in index.status.items():
            if status.state is IntegrationState.NO_ADAPTER:
                _echo(f"  {record_id}")


def _set_switch(record_id: str, *, enabled: bool, path: Path | None) -> None:
    index = load_index(path)
    if record_id not in index.records:
        _fail(f"no catalog record {record_id!r}; run `matb integrations list`")
        return
    record = index.records[record_id]
    if record.entry_point is None:
        _fail(
            f"{record_id} has no adapter in this repository, so there is nothing to switch. "
            "Its catalog record describes the intended binding only."
        )
        return
    destination = switches_path()
    updated = Switches.load(destination).with_change(record_id, enabled=enabled)
    updated.save(destination)
    verb = "enabled" if enabled else "disabled"
    typer.secho(f"{record_id} {verb} in {destination}", fg=typer.colors.GREEN)

    status = load_index(path).status[record_id]
    if enabled and not status.usable:
        _echo(f"  still unavailable: {status.reason()}")


@integrations_app.command("enable")
def integrations_enable(
    record_id: Annotated[str, typer.Argument(help="A catalog record id.")],
    path: Annotated[Path | None, typer.Option("--path")] = None,
) -> None:
    """Switch an integration on."""
    _set_switch(record_id, enabled=True, path=path)


@integrations_app.command("disable")
def integrations_disable(
    record_id: Annotated[str, typer.Argument(help="A catalog record id.")],
    path: Annotated[Path | None, typer.Option("--path")] = None,
) -> None:
    """Switch an integration off without uninstalling it.

    A manifest that names it will then fail with a clear error rather than
    quietly running something else.
    """
    _set_switch(record_id, enabled=False, path=path)


@integrations_app.command("verify")
def integrations_verify(
    path: Annotated[Path | None, typer.Option("--path")] = None,
) -> None:
    """List integrations whose packaging details are still unconfirmed.

    An extra that is named in the catalog but absent from `pyproject.toml`, or a
    required import with no extra to install it, means somebody still has to
    confirm the distribution name. Guessing one installs the wrong package with
    full confidence, so these are reported rather than assumed.
    """
    import tomllib

    index = load_index(path)
    declared: set[str] = set()
    pyproject = Path("pyproject.toml")
    if pyproject.exists():
        raw = tomllib.loads(pyproject.read_text("utf-8"))
        declared = set(raw.get("project", {}).get("optional-dependencies", {}))

    unconfirmed: list[str] = []
    for record in index.records.values():
        if record.extra and record.extra not in declared:
            unconfirmed.append(f"  {record.record_id}: extra {record.extra!r} is not in pyproject")
        if record.requires and not record.extra:
            unconfirmed.append(
                f"  {record.record_id}: requires {list(record.requires)} but declares no extra"
            )
    if unconfirmed:
        _echo("packaging details still to confirm:")
        for line in sorted(unconfirmed):
            _echo(line)
        _echo("")
    _echo(f"{len(declared)} extras declared: {', '.join(sorted(declared)) or '(none)'}")



# -- run -------------------------------------------------------------------


def _print_results(results: list[RunResult]) -> None:
    for result in results:
        success = result.verifier.success if result.verifier else None
        _echo(
            f"  {result.run.run_id}  {result.run.task_id:<16} {result.state:<14} "
            f"success={success}  events={result.event_count}  "
            f"model_calls={result.measures.get('model_calls', 0):.0f}"
        )
        for score in (result.scores.scores if result.scores else ()):
            tag = "judge" if score.is_judge else "hard "
            _echo(f"      {tag} {score.qualified_name:<26} {score.value}")
    evaluable = [r for r in results if r.is_evaluable]
    attrition = [r for r in results if not r.is_evaluable]
    _echo("")
    _echo(f"{len(evaluable)} evaluable run(s), {len(attrition)} attrition")
    if attrition:
        for result in attrition:
            _echo(f"  attrition: {result.run.run_id} {result.state} {result.attrition_reason}")


@app.command()
def run(
    manifest_path: Annotated[Path, typer.Argument(help="Path to an experiment YAML file.")],
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Plan without executing.")] = False,
) -> None:
    """Execute an experiment."""
    manifest = load_manifest(manifest_path)
    if dry_run:
        composition, _, store, _ = compose(manifest, Workspace(workspace))
        for record in RunController(composition).plan(manifest):
            _echo(
                f"  {record.run_id}  task={record.task_id} seed={record.env_seed} "
                f"rep={record.repetition} eval_set={record.eval_set_kind}"
            )
        store.close()
        return
    results = run_experiment(manifest, workspace)
    _print_results(results)


@app.command()
def resume(
    run_id: Annotated[str, typer.Argument(help="The run to continue.")],
    manifest_path: Annotated[Path, typer.Option("--manifest", help="Manifest for the run.")],
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
) -> None:
    """Continue an interrupted run from its last complete checkpoint."""
    manifest = load_manifest(manifest_path)
    _print_results([resume_run(manifest, workspace, run_id)])


@app.command()
def rescore(
    run_id: Annotated[str, typer.Argument()],
    scorer: Annotated[
        list[str] | None,
        typer.Option("--scorer", help="NAME@VERSION; repeatable. Defaults to the manifest's."),
    ] = None,
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
) -> None:
    """Recompute scores offline. Performs no model, agent or tool calls."""
    store, _ = Workspace(workspace).open()
    registry = Registry.discover()
    record = store.get_run(run_id)
    manifest = store.get_manifest(record.manifest_hash)

    specs: list[ScorerSpec] = list(manifest.scorers)
    if scorer:
        specs = []
        for item in scorer:
            name, _, version = item.partition("@")
            known = registry.scorers.get(name)
            if known is None:
                _fail(f"unknown scorer {name!r}")
                return
            specs.append(
                ScorerSpec(
                    name=name,
                    version=version or known.version,
                    kind=known.kind,
                    judge_model="fake/hash-rubric" if known.kind == "judge" else None,
                )
            )

    stored = store.get_result(run_id) or {}
    verifier = VerifierResult.model_validate(stored["verifier"]) if stored.get("verifier") else None
    scores = score_run(
        record=record,
        events=store.read(run_id),
        specs=specs,
        registry=registry.scorers,
        verifier=verifier,
        agent_ids=tuple(a.agent_id for a in manifest.agents),
    )
    store.put_scores(scores.scores)
    for score in scores.scores:
        tag = "judge" if score.is_judge else "hard "
        _echo(f"  {tag} {score.qualified_name:<26} {score.value}  view={score.view}")
    _echo("")
    _echo("rescoring made no model, agent or tool calls (enforced by the purity guard)")
    store.close()


@app.command()
def playback(
    run_id: Annotated[str, typer.Argument()],
    view: Annotated[
        str, typer.Option("--view", help="omniscient | public | agent:AGENT_ID")
    ] = OMNISCIENT_VIEW,
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
) -> None:
    """Replay a run for one explicitly named view, with zero external calls."""
    store, _ = Workspace(workspace).open()
    resolved = view.split("agent:", 1)[1] if view.startswith("agent:") else view
    result = kernel_playback(store, run_id, view=resolved)
    _echo(f"view: {result.view}   events: {len(result.events)}")
    _echo(result.render())
    _echo("")
    _echo(f"final state (as visible in this view): {json.dumps(result.final_state, default=str)}")
    store.close()


@app.command()
def rerun(
    run_id: Annotated[str, typer.Argument()],
    override: Annotated[
        list[str] | None, typer.Option("--override", help="key.path=value; repeatable.")
    ] = None,
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
) -> None:
    """Execute a new run from the same manifest.

    A rerun is a *new* run: it is recorded at the next repetition index, and it
    reports the declared reproducibility level rather than promising equality.
    """
    store, _ = Workspace(workspace).open()
    record = store.get_run(run_id)
    manifest = store.get_manifest(record.manifest_hash)
    store.close()
    updated = apply_overrides(manifest, list(override or []))
    results = run_experiment(updated, workspace, repetition_offset=record.repetition + 1)
    _print_results(results)
    if updated.manifest_hash != manifest.manifest_hash:
        _echo("")
        _echo(
            "overrides changed the manifest hash; this rerun is a different experiment "
            "configuration and must not be pooled with the original."
        )
    _echo(f"declared reproducibility: {updated.declared_reproducibility()}")


@app.command()
def compare(
    experiment_a: Annotated[str, typer.Argument()],
    experiment_b: Annotated[str, typer.Argument()],
    metric: Annotated[str, typer.Option("--metric")] = "success",
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
) -> None:
    """Compare two experiments on paired task seeds."""
    store, _ = Workspace(workspace).open()
    results_a = store.results_for(experiment_a)
    results_b = store.results_for(experiment_b)
    if not results_a or not results_b:
        _fail("both experiments need stored results; run them first")
        return
    report = compare_experiments(
        results_a,
        results_b,
        experiment_a=experiment_a,
        experiment_b=experiment_b,
        metric=metric,
    )
    _echo(report.summary())
    store.close()


@app.command("export")
def export_cmd(
    run_id: Annotated[str, typer.Argument()],
    fmt: Annotated[
        str, typer.Option("--format", help="bundle | parquet | otel | jsonl")
    ] = "bundle",
    out: Annotated[Path | None, typer.Option("--out")] = None,
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
) -> None:
    """Export a run as a reproducibility bundle, Parquet, OTel spans or JSONL."""
    from testbed_store.export import export_bundle, export_jsonl, export_parquet

    space = Workspace(workspace)
    store, _ = space.open()
    events = list(store.read(run_id))
    destination = out or (space.root / "exports" / run_id)

    if fmt == "bundle":
        path = export_bundle(store, run_id, destination, artifacts_dir=space.artifacts_path)
    elif fmt == "parquet":
        path = export_parquet(events, Path(str(destination) + ".parquet"))
    elif fmt == "jsonl":
        path = export_jsonl(events, Path(str(destination) + ".jsonl"))
    elif fmt == "otel":
        from testbed_adapters.telemetry.otel.exporter import export_spans

        path = export_spans(events, Path(str(destination) + ".otel.json"))
    else:
        _fail(f"unknown format {fmt!r}")
        return
    _echo(f"wrote {path}")
    _echo(f"canonical event hash: {run_event_hash(store, run_id)}")
    store.close()


@app.command()
def doctor(workspace: WorkspaceOpt = DEFAULT_WORKSPACE) -> None:
    """Report what is installed, what is discoverable and what is missing."""
    _echo(f"python          {sys.version.split()[0]}")
    registry = Registry.discover()
    for group, names in registry.describe().items():
        _echo(f"{group:<15} {', '.join(names) or '(none)'}")

    try:
        import pyarrow  # noqa: F401

        _echo("parquet extra   installed")
    except ModuleNotFoundError:
        _echo("parquet extra   not installed (matb export --format parquet will fail)")

    space = Workspace(workspace)
    try:
        store, _ = space.open()
        runs = store.list_runs()
        store.close()
        _echo(f"workspace       {space.root} ({len(runs)} runs recorded)")
    except Exception as exc:
        _echo(f"workspace       unusable: {exc}")

    catalog_location, switches_location = resolved_locations()
    _echo(f"catalog         {catalog_location}")
    _echo(f"switches        {switches_location}")
    index = load_index()
    counts = index.counts()
    _echo(
        f"integrations    {counts['active']} active, {counts['disabled']} off, "
        f"{counts['not_installed']} not installed, "
        f"{counts['no_adapter']} catalogued with no adapter"
    )
    for status in index.blocked():
        _echo(f"  {status.record_id}: {status.reason()}")

    _echo("")
    _echo("Most of the catalog has no adapter here yet: OCI/gVisor sandboxes, the Inspect")
    _echo("bridge, remote agents (A2A), MCP tools, scanners, control and interpretability")
    _echo("integrations. Run `matb integrations list --all` for the full picture and see")
    _echo("docs/ROADMAP.md for where each one lands.")


@app.command("runs")
def runs_cmd(
    experiment_id: Annotated[str | None, typer.Argument()] = None,
    workspace: WorkspaceOpt = DEFAULT_WORKSPACE,
) -> None:
    """List recorded runs."""
    store, _ = Workspace(workspace).open()
    for record in store.list_runs(experiment_id=experiment_id):
        marker = "" if RunState(record.state) is RunState.COMPLETE else "  <-- not complete"
        _echo(
            f"  {record.run_id}  {record.experiment_id:<28} {record.task_id:<16} "
            f"{record.state}{marker}"
        )
    store.close()


if __name__ == "__main__":  # pragma: no cover
    app()
