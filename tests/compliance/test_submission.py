"""Labeling submission flow — validation, persistence, cap-at-N."""
from __future__ import annotations

import json
import sqlite3

import httpx
import pytest

from .conftest import new_client, submit_label


def _count_submissions(conn: sqlite3.Connection, record_id: int = None) -> int:
    if record_id is None:
        return conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
    return conn.execute(
        "SELECT COUNT(*) FROM submissions WHERE record_id = ?", (record_id,)
    ).fetchone()[0]


def test_valid_submission_persists(
    client: httpx.Client, clean_state: sqlite3.Connection
) -> None:
    resp = submit_label(client, record_id=1)
    assert resp.status_code == 302
    row = clean_state.execute(
        "SELECT username, values_json FROM submissions WHERE record_id = 1"
    ).fetchone()
    assert row is not None
    assert row[0] == "alice"  # the client fixture is logged in as alice
    vals = json.loads(row[1])
    assert vals["failure_location"] == "front"
    assert vals["hazards"] == [{"hazard": "fire", "subhazard": "underhood"}]


def test_missing_required_field_rejects(
    client: httpx.Client, clean_state: sqlite3.Connection
) -> None:
    from .conftest import form_post

    # Drop failure_location (required)
    resp = form_post(
        client,
        "/flywheel/label/submit",
        [
            ("record_id", "1"),
            ("failure_type", "mechanical"),
            ("vehicle_system", "brakes"),
            ("severity", "3"),
            ("hazards__hazard", "fire"),
            ("hazards__subhazard", "underhood"),
        ],
    )
    assert resp.status_code == 200
    assert "Validation errors" in resp.text
    assert _count_submissions(clean_state, record_id=1) == 0


def test_hierarchical_bad_child_rejects(
    client: httpx.Client, clean_state: sqlite3.Connection
) -> None:
    # brake_fluid is not a child of fire
    resp = submit_label(
        client,
        record_id=1,
        hazards__hazard="fire",
        hazards__subhazard="brake_fluid",
    )
    assert resp.status_code == 200
    assert "Validation errors" in resp.text
    assert _count_submissions(clean_state, record_id=1) == 0


def test_ordinal_out_of_range_rejects() -> None:
    # The ordinal path accepts any int via `int(v)`, which then fails
    # server-side only if the form submits empty. An out-of-range value
    # is happily accepted today, so this test documents the gap: we do
    # NOT enforce ordinal bounds on labeling (only on custom reconcile).
    # Skip until we add that check.
    pytest.skip("ordinal range enforcement not implemented on labeling path")


def test_cap_at_n_skips_saturated_record(
    datasette_server: str,
    clean_state: sqlite3.Connection,
    cfg: dict,
) -> None:
    """With min_labelers=2, labeling record 1 with alice and bob should
    saturate it — carol's /flywheel/label should then hand out a
    different record, never record 1."""
    import re

    min_n = int(cfg["reconciliation"]["min_labelers"])
    seed_users = [("alice", "wonderland"), ("bob", "bobsecret")]
    for user, pw in seed_users[:min_n]:
        with new_client(datasette_server, user, pw) as c:
            resp = submit_label(c, record_id=1)
            assert resp.status_code == 302

    with new_client(datasette_server, "carol", "carolsecret") as c:
        resp = c.get("/flywheel/label")
    assert resp.status_code == 200
    m = re.search(r"record #(\d+)", resp.text)
    assert m is not None
    offered = int(m.group(1))
    assert offered != 1, (
        f"cap-at-N broken: record 1 still offered after {min_n} labels"
    )
