# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- `TODO.md`, `CHANGELOG.md`, `LEARNINGS.md` project bookkeeping files.
- Optional `context_field` / `context_label` in `source:` YAML block — renders a collapsible second text column below the narrative on both the labeling page and the reconciliation detail page.
- `scripts/ingest_data.py` — data ingest helper supporting CSV, Parquet, and SQLite source files.
- `generate.sh` now detects `data.{csv,parquet,db,sqlite}` in the example directory and delegates to `ingest_data.py`.
- Role-based authorization: labelers get 403 on `/flywheel/reconcile*` routes, supervisors can access everything (label + reconcile). Enforced via `_require_supervisor` helper.
- `generate.sh` now prints user credentials (username, password, role) at the end of generation so agents and developers can find them immediately.
- Expanded auth documentation in README: credentials table, programmatic login recipes (curl/httpx), adding users to a running instance, role enforcement matrix.
- New test `test_labeler_blocked_from_reconcile` verifying 403 for labeler role on reconcile routes.

### Fixed

- Record IDs that are large integers no longer render as scientific notation in HTML forms and reconciliation links. All DB-sourced `record_id` values are coerced to `int` at read time.

### Changed

- Hierarchical multi-select entries now stack vertically in card-style containers with level labels ("Hazard", "Subhazard") above each dropdown. Applies to both labeling form and reconciliation custom widget.
- Modernized input styling across all field kinds: consistent border-radius, focus rings, hover transitions on dropdowns, checkboxes (accent-color), ordinal chips (highlight on checked), buttons, and tabs. Removed inline styles in favor of stylesheet rules.

## [0.1.0] - 2026-04-27

### Added

- YAML-driven labeling template with cookiecutter generation.
- Datasette plugin with full labeling + reconciliation routes.
- Five field kinds: `single_select`, `multi_select`, `ordinal`, `free_text`, `hierarchical_multi_select`.
- Cap-at-N labeling with `strict_equality` reconciliation rule.
- Unanimous auto-promotion to gold (no supervisor step when all labelers agree).
- Reconciliation UI with diff-highlighting, custom override column, review/edit/undo flow.
- `datasette-auth-passwords` authentication with `users.yaml` seed and bootstrap script.
- marimo notebooks: `export.py` (gold export to CSV/JSON/Parquet/JSONL + finetune JSONL) and `analysis.py` (labeling quality EDA).
- `vehicle_safety` example with synthetic fake data (40 rows).
- `nhtsa_complaints` example with 100 real NHTSA consumer complaints.
- `scripts/simulate_labelers.py` for synthetic label generation against a running instance.
- `scripts/sample_nhtsa.py` for reservoir-sampling from the NHTSA flat file.
- Compliance test suite in `tests/compliance/` covering generation, rendering, submission, reconciliation, and notebooks.
- Makefile with `venv`, `gen`, `serve`, `dev`, `test`, `clean`, `nuke` targets.
- MIT license.
