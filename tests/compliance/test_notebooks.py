"""Notebook smoke tests — both notebooks run in script mode against the
generated project without errors. The export notebook also produces
at least the tabular CSV."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .conftest import VENV_BIN, new_client, submit_label


def _run_notebook(project_dir: Path, name: str) -> subprocess.CompletedProcess:
    notebooks_dir = project_dir / "notebooks"
    env = os.environ.copy()
    env["FLYWHEEL_CONFIG"] = str(project_dir / "flywheel.yaml")
    return subprocess.run(
        [str(VENV_BIN / "python"), name],
        cwd=notebooks_dir,
        env=env,
        capture_output=True,
        text=True,
    )


def test_analysis_notebook_runs_clean(
    project_dir: Path, clean_state, datasette_server: str
) -> None:
    """Seed one submission so analysis.py doesn't trip its empty-state
    short-circuit, then run the notebook in script mode."""
    with new_client(datasette_server, "alice", "wonderland") as c:
        submit_label(c, record_id=1)

    result = _run_notebook(project_dir, "analysis.py")
    assert result.returncode == 0, (
        f"analysis.py failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_export_notebook_writes_tabular(
    project_dir: Path, clean_state, datasette_server: str
) -> None:
    """Seed two agreeing submissions so record 1 becomes unanimous gold,
    then run export.py and verify CSV + parquet + jsonl + finetune files
    land on disk."""
    with new_client(datasette_server, "alice", "wonderland") as c:
        submit_label(c, record_id=1)
    with new_client(datasette_server, "bob", "bobsecret") as c:
        submit_label(c, record_id=1)

    result = _run_notebook(project_dir, "export.py")
    assert result.returncode == 0, (
        f"export.py failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    exports = project_dir / "exports"
    tabular = exports / "tabular"
    finetune = exports / "finetune"
    assert (tabular / "vehicle_safety.csv").exists()
    assert (tabular / "vehicle_safety.parquet").exists()
    assert (tabular / "vehicle_safety.jsonl").exists()
    assert (finetune / "vehicle_safety.chat_messages.jsonl").exists()
