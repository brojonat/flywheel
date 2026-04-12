"""Reconciliation flow — queue / detail / submit / custom / undo / review."""
from __future__ import annotations

import json
import sqlite3

import httpx

from .conftest import form_post, new_client, submit_label


def _contest_record(datasette_server: str, record_id: int) -> None:
    """Post two disagreeing submissions (from alice and bob) so the
    record lands in the reconciliation queue."""
    with new_client(datasette_server, "alice", "wonderland") as c:
        submit_label(
            c,
            record_id=record_id,
            hazards__hazard="fire",
            hazards__subhazard="underhood",
        )
    with new_client(datasette_server, "bob", "bobsecret") as c:
        submit_label(
            c,
            record_id=record_id,
            severity="5",
            hazards__hazard="collision",
            hazards__subhazard="brake_failure",
        )


def test_contested_record_appears_in_pending_queue(
    supervisor_client: httpx.Client,
    datasette_server: str,
    clean_state: sqlite3.Connection,
) -> None:
    _contest_record(datasette_server, 1)
    resp = supervisor_client.get("/flywheel/reconcile?view=pending")
    assert resp.status_code == 200
    assert "Pending (1)" in resp.text
    assert "Reviewed (0)" in resp.text
    assert "reconcile/1?" in resp.text


def test_detail_page_has_custom_column(
    supervisor_client: httpx.Client,
    datasette_server: str,
    clean_state: sqlite3.Connection,
) -> None:
    _contest_record(datasette_server, 1)
    resp = supervisor_client.get("/flywheel/reconcile/1?view=pending")
    assert resp.status_code == 200
    assert "Custom override" in resp.text
    assert 'value="__custom__"' in resp.text
    assert 'name="custom_failure_location"' in resp.text  # single_select
    assert 'name="custom_severity"' in resp.text  # ordinal
    assert 'name="custom_notes"' in resp.text  # free_text
    assert "field-custom_hazards" in resp.text  # hierarchical


def test_reconcile_submit_writes_row_and_flips_queues(
    supervisor_client: httpx.Client,
    datasette_server: str,
    clean_state: sqlite3.Connection,
) -> None:
    _contest_record(datasette_server, 1)
    resp = form_post(
        supervisor_client,
        "/flywheel/reconcile/1/submit",
        [
            ("view", "pending"),
            ("pick_failure_location", "alice"),
            ("pick_failure_type", "alice"),
            ("pick_vehicle_system", "alice"),
            ("pick_severity", "alice"),
            ("pick_notes", "alice"),
            ("pick_hazards", "alice"),
        ],
    )
    assert resp.status_code == 302
    row = clean_state.execute(
        "SELECT supervisor, values_json FROM reconciliations WHERE record_id = 1"
    ).fetchone()
    assert row is not None
    assert row[0] == "sam"
    vals = json.loads(row[1])
    assert vals["hazards"] == [{"hazard": "fire", "subhazard": "underhood"}]

    queue = supervisor_client.get("/flywheel/reconcile").text
    assert "Pending (0)" in queue
    assert "Reviewed (1)" in queue


def test_custom_override_persists_supervisor_values(
    supervisor_client: httpx.Client,
    datasette_server: str,
    clean_state: sqlite3.Connection,
) -> None:
    _contest_record(datasette_server, 1)
    resp = form_post(
        supervisor_client,
        "/flywheel/reconcile/1/submit",
        [
            ("view", "pending"),
            ("pick_failure_location", "alice"),
            ("pick_failure_type", "alice"),
            ("pick_vehicle_system", "alice"),
            ("pick_severity", "alice"),
            ("pick_notes", "alice"),
            ("pick_hazards", "__custom__"),
            ("custom_hazards__hazard", "leakage"),
            ("custom_hazards__subhazard", "fuel"),
        ],
    )
    assert resp.status_code == 302
    vals = json.loads(
        clean_state.execute(
            "SELECT values_json FROM reconciliations WHERE record_id = 1"
        ).fetchone()[0]
    )
    assert vals["hazards"] == [{"hazard": "leakage", "subhazard": "fuel"}]


def test_custom_override_invalid_child_rejects(
    supervisor_client: httpx.Client,
    datasette_server: str,
    clean_state: sqlite3.Connection,
) -> None:
    _contest_record(datasette_server, 1)
    resp = form_post(
        supervisor_client,
        "/flywheel/reconcile/1/submit",
        [
            ("view", "pending"),
            ("pick_failure_location", "alice"),
            ("pick_failure_type", "alice"),
            ("pick_vehicle_system", "alice"),
            ("pick_severity", "alice"),
            ("pick_notes", "alice"),
            ("pick_hazards", "__custom__"),
            ("custom_hazards__hazard", "fire"),
            ("custom_hazards__subhazard", "brake_fluid"),
        ],
    )
    assert resp.status_code == 200
    assert "not a valid child of" in resp.text
    assert (
        clean_state.execute(
            "SELECT COUNT(*) FROM reconciliations WHERE record_id = 1"
        ).fetchone()[0]
        == 0
    )


def test_reviewed_detail_pre_fills_form(
    supervisor_client: httpx.Client,
    datasette_server: str,
    clean_state: sqlite3.Connection,
) -> None:
    _contest_record(datasette_server, 1)
    form_post(
        supervisor_client,
        "/flywheel/reconcile/1/submit",
        [
            ("view", "pending"),
            ("pick_failure_location", "alice"),
            ("pick_failure_type", "alice"),
            ("pick_vehicle_system", "alice"),
            ("pick_severity", "alice"),
            ("pick_notes", "alice"),
            ("pick_hazards", "__custom__"),
            ("custom_hazards__hazard", "leakage"),
            ("custom_hazards__subhazard", "fuel"),
        ],
    )

    resp = supervisor_client.get("/flywheel/reconcile/1?view=reviewed")
    assert resp.status_code == 200
    assert "Review record #1" in resp.text
    assert "Already reconciled" in resp.text
    assert "Undo reconciliation" in resp.text
    assert 'value="alice" checked' in resp.text
    assert 'value="__custom__" checked' in resp.text


def test_undo_moves_record_back_to_pending(
    supervisor_client: httpx.Client,
    datasette_server: str,
    clean_state: sqlite3.Connection,
) -> None:
    _contest_record(datasette_server, 1)
    form_post(
        supervisor_client,
        "/flywheel/reconcile/1/submit",
        [
            ("view", "pending"),
            ("pick_failure_location", "alice"),
            ("pick_failure_type", "alice"),
            ("pick_vehicle_system", "alice"),
            ("pick_severity", "alice"),
            ("pick_notes", "alice"),
            ("pick_hazards", "alice"),
        ],
    )
    assert (
        clean_state.execute("SELECT COUNT(*) FROM reconciliations").fetchone()[0] == 1
    )

    resp = form_post(
        supervisor_client,
        "/flywheel/reconcile/1/undo",
        [("view", "reviewed")],
    )
    assert resp.status_code == 302
    assert (
        clean_state.execute("SELECT COUNT(*) FROM reconciliations").fetchone()[0] == 0
    )
    queue = supervisor_client.get("/flywheel/reconcile").text
    assert "Pending (1)" in queue
    assert "Reviewed (0)" in queue


def test_anonymous_request_redirects_to_login(anon_client: httpx.Client) -> None:
    resp = anon_client.get("/flywheel/label", follow_redirects=False)
    assert resp.status_code == 302
    assert "/-/login" in resp.headers["location"]
