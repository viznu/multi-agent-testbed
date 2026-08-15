# ADR 0003 — One distribution, layered packages, `src/` layout

Status: accepted

## Context

The implementation plan sketches `adapters/`, `plugins/` and `packs/` as
top-level directories beside `src/`. That shape suggests several distributions.

## Decision

Ship one distribution with layered top-level packages under `src/`:
`testbed_contracts`, `testbed_pack_sdk`, `testbed_kernel`, `testbed_store`,
`testbed_eval`, `testbed_catalog`, `testbed_cli`, plus `testbed_adapters`,
`testbed_plugins` and `testbed_packs` for the edge.

The layering the plan cares about is enforced by *import rules*, not by
directory nesting or by splitting distributions:

* `.importlinter` states the rules for CI.
* `tests/contract/test_import_boundaries.py` enforces the same rules from
  pytest by parsing the source, so the architecture stays checked even when the
  linter is not installed.

## Consequences

* One `pip install` and one version number during early development.
* Third-party packs and adapters are still first-class: they are discovered
  through the `testbed.*` entry-point groups, exactly like the built-in ones.
* If a component later needs an independent release cadence (a heavyweight
  adapter with conflicting dependencies, say) it can be split out; the import
  rules already forbid the couplings that would make that painful.
