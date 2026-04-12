"""Structural tests — the generated project has the expected layout and
the SQLite schema matches the plugin's startup hook."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def test_expected_files_exist(project_dir: Path) -> None:
    for rel in [
        "flywheel.yaml",
        "plugin/flywheel_plugin.py",
        "plugin/__init__.py",
        "notebooks/export.py",
        "notebooks/analysis.py",
        "data/labeling.db",
    ]:
        assert (project_dir / rel).exists(), f"missing {rel}"


def test_records_table_populated(db_path: Path, cfg: dict) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(f'SELECT COUNT(*) FROM "{cfg["source"]["table"]}"')
        (n,) = cur.fetchone()
    finally:
        conn.close()
    assert n > 0, "records table is empty — generator should have loaded CSV"


def test_auxiliary_tables_created_on_server_start(
    datasette_server: str, db_path: Path
) -> None:
    """The plugin's startup hook creates users/submissions/reconciliations.
    Touching the server (via the datasette_server fixture) triggers
    startup; verify the schema landed."""
    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    assert "users" in tables
    assert "submissions" in tables
    assert "reconciliations" in tables


def test_foreign_keys_enforced(client) -> None:
    """The plugin's prepare_connection hook turns on PRAGMA foreign_keys
    per connection; verify it's actually on when Datasette connects."""
    from .conftest import form_post

    # This POST should fail because record_id 99999 does not exist.
    resp = form_post(
        client,
        "/flywheel/label/submit",
        [
            ("record_id", "99999"),
            ("failure_location", "front"),
            ("failure_type", "mechanical"),
            ("vehicle_system", "brakes"),
            ("severity", "3"),
            ("hazards__hazard", "fire"),
            ("hazards__subhazard", "underhood"),
            ("notes", ""),
        ],
    )
    assert resp.status_code == 500, (
        "expected FK violation to surface as 500, got "
        f"{resp.status_code}"
    )
