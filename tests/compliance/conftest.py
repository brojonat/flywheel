"""Pytest fixtures for flywheel compliance tests.

Regenerates the vehicle_safety example project into a pytest tmp dir,
starts Datasette against it on a dedicated port, and exposes an httpx
client bound to the server. Mutable tables (submissions, users,
reconciliations) are truncated between state-mutating tests so
assertions don't depend on ordering.
"""
from __future__ import annotations

import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import yaml


ROOT = Path(__file__).resolve().parents[2]
VENV_BIN = ROOT / ".venv" / "bin"
EXAMPLE = "vehicle_safety"
TEST_PORT = 8099


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.connect(("127.0.0.1", port))
                return
            except OSError:
                time.sleep(0.1)
    raise RuntimeError(f"datasette on port {port} did not start within {timeout}s")


@pytest.fixture(scope="session")
def project_dir(tmp_path_factory) -> Path:
    """Regenerate the vehicle_safety example into a tmpdir.

    Invokes scripts/generate.sh with OUT_DIR pointed at a pytest tmp
    path so tests never contaminate ``_tmp_output``.
    """
    out_root = tmp_path_factory.mktemp("flywheel_out")
    env = os.environ.copy()
    env["OUT_DIR"] = str(out_root)
    env["PATH"] = f"{VENV_BIN}:{env.get('PATH', '')}"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts" / "generate.sh"), EXAMPLE],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"generate.sh failed:\n{result.stdout}\n{result.stderr}"
    )
    project = out_root / EXAMPLE
    assert project.is_dir(), f"generator did not create {project}"
    return project


@pytest.fixture(scope="session")
def cfg(project_dir: Path) -> dict:
    return yaml.safe_load((project_dir / "flywheel.yaml").read_text())


@pytest.fixture(scope="session")
def datasette_server(project_dir: Path):
    """Start a Datasette server against the generated project, with
    ``--metadata metadata.yml`` so ``datasette-auth-passwords`` is
    active and ``/-/login`` works."""
    env = os.environ.copy()
    env["FLYWHEEL_CONFIG"] = str(project_dir / "flywheel.yaml")
    args = [
        str(VENV_BIN / "datasette"),
        "serve",
        str(project_dir / "data" / "labeling.db"),
        "--plugins-dir",
        str(project_dir / "plugin"),
        "--port",
        str(TEST_PORT),
    ]
    metadata = project_dir / "metadata.yml"
    if metadata.exists():
        args.extend(["--metadata", str(metadata)])
    proc = subprocess.Popen(
        args,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_port(TEST_PORT)
        yield f"http://127.0.0.1:{TEST_PORT}"
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _login(client: httpx.Client, username: str, password: str) -> None:
    """Post to /-/login to acquire the ds_actor cookie on the client."""
    resp = client.post(
        "/-/login",
        content=f"username={username}&password={password}".encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert resp.status_code in (302, 303), (
        f"login failed for {username}: "
        f"{resp.status_code} {resp.text[:200]}"
    )
    assert "ds_actor" in client.cookies, (
        f"no ds_actor cookie after logging in as {username}"
    )


@pytest.fixture
def client(datasette_server: str):
    """Default client is logged in as alice (a labeler). Most tests just
    want a valid session; tests that specifically need the supervisor
    use the ``supervisor_client`` fixture."""
    with httpx.Client(base_url=datasette_server, timeout=10.0) as c:
        _login(c, "alice", "wonderland")
        yield c


@pytest.fixture
def supervisor_client(datasette_server: str):
    """Logged in as sam (role: supervisor)."""
    with httpx.Client(base_url=datasette_server, timeout=10.0) as c:
        _login(c, "sam", "supervisor")
        yield c


@pytest.fixture
def anon_client(datasette_server: str):
    """No login — for testing the redirect-to-login path."""
    with httpx.Client(base_url=datasette_server, timeout=10.0) as c:
        yield c


@pytest.fixture
def db_path(project_dir: Path) -> Path:
    return project_dir / "data" / "labeling.db"


@pytest.fixture
def clean_state(db_path: Path):
    """Truncate mutable tables before a state-mutating test runs.

    Yields a ``sqlite3.Connection`` for any extra inspection the test
    wants to do. Parent records stay put; only labeling activity is
    reset.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("DELETE FROM reconciliations")
    conn.execute("DELETE FROM submissions")
    conn.execute("DELETE FROM users")
    conn.commit()
    try:
        yield conn
    finally:
        conn.close()


def form_post(client: httpx.Client, url: str, pairs) -> httpx.Response:
    """Post an application/x-www-form-urlencoded body from a list of
    (key, value) tuples — supports multi-value params (same key appearing
    more than once), which the httpx ``data=`` shortcut does not do
    reliably across versions."""
    from urllib.parse import urlencode

    body = urlencode(list(pairs))
    return client.post(
        url,
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )


@contextmanager
def new_client(base_url: str, username: str, password: str):
    """Context manager yielding a logged-in httpx.Client for the given
    user. Use as ``with new_client(url, 'alice', 'wonderland') as c: ...``."""
    with httpx.Client(base_url=base_url, timeout=10.0) as c:
        _login(c, username, password)
        yield c


def submit_label(client: httpx.Client, record_id: int, **overrides):
    """Helper: post a labeling submission with all required fields filled
    in. The username comes from whichever user ``client`` is logged in
    as (auth is via cookie). Test callers can pass kwargs to override
    any individual field; multi-value fields accept a list.
    """
    base = {
        "record_id": str(record_id),
        "failure_location": "front",
        "failure_type": "mechanical",
        "vehicle_system": "brakes",
        "severity": "3",
        "hazards__hazard": "fire",
        "hazards__subhazard": "underhood",
        "notes": "",
    }
    pairs: list = []
    for k, v in base.items():
        if k in overrides:
            ov = overrides.pop(k)
            if isinstance(ov, list):
                pairs.extend((k, str(x)) for x in ov)
            else:
                pairs.append((k, str(ov)))
        else:
            pairs.append((k, v))
    for k, v in overrides.items():
        if isinstance(v, list):
            pairs.extend((k, str(x)) for x in v)
        else:
            pairs.append((k, str(v)))
    return form_post(client, "/flywheel/label/submit", pairs)
