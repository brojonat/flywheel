#!/usr/bin/env python3
"""Simulate 5 labelers submitting mostly-agreeing labels against a running
flywheel datasette server, using keyword-based heuristics over the NHTSA
record narrative + metadata to pick a "canonical" label per record.

Design:

- 5 users, labels the first 50 records, each record by exactly 2 users.
- Record → pair assignment cycles through all C(5,2)=10 pairs, so each
  user ends up labeling 20 records (10 pair slots × 2 records/cycle).
- For each (record, user) submission, each field has a small chance to
  "perturb" (pick a random other choice) so some records come out
  contested and some come out gold.
- Each simulated user logs into /-/login once to acquire a ds_actor
  cookie and reuses it for every subsequent labeling POST, so the
  script works against an auth-enabled Datasette.

Usage:
    python scripts/simulate_labelers.py \\
        --url http://localhost:8013 \\
        --config _tmp_output/nhtsa_complaints/flywheel.yaml \\
        --db    _tmp_output/nhtsa_complaints/data/labeling.db \\
        --users examples/nhtsa_complaints/users.yaml \\
        [--seed 42] [--perturb 0.15]
"""
from __future__ import annotations

import argparse
import itertools
import random
import sqlite3
import sys
from pathlib import Path

import httpx
import yaml


DEFAULT_USERS = [
    {"username": "alice", "password": "wonderland"},
    {"username": "bob", "password": "bobsecret"},
    {"username": "carol", "password": "carolsecret"},
    {"username": "dave", "password": "davesecret"},
    {"username": "eve", "password": "evesecret"},
]
RECORDS_TO_LABEL = 50  # → 100 submissions (50 × 2 labelers/record)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", default="http://localhost:8013")
    p.add_argument("--config", required=True)
    p.add_argument("--db", required=True)
    p.add_argument(
        "--users",
        default=None,
        help="Path to a users.yaml with username/password entries. "
             "Defaults to alice/bob/carol/dave/eve with well-known passwords.",
    )
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--perturb", type=float, default=0.15,
                   help="Per-field probability of picking a different choice")
    return p.parse_args()


def load_users(users_yaml: str | None) -> list[dict]:
    """Return a list of {username, password} dicts. Uses the defaults
    when no path is given, otherwise reads an examples/<slug>/users.yaml."""
    if not users_yaml:
        return DEFAULT_USERS
    data = yaml.safe_load(Path(users_yaml).read_text())
    users = data.get("users") or []
    return [{"username": u["username"], "password": u["password"]} for u in users]


def login_sessions(url: str, users: list[dict]) -> dict[str, httpx.Client]:
    """Open an httpx.Client per user and log each one in via /-/login.
    Returns a dict mapping username → live Client. Caller must close
    them."""
    sessions: dict[str, httpx.Client] = {}
    for u in users:
        c = httpx.Client(base_url=url, timeout=10.0)
        resp = c.post(
            "/-/login",
            content=f"username={u['username']}&password={u['password']}".encode(),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        if resp.status_code not in (302, 303) or "ds_actor" not in c.cookies:
            c.close()
            print(
                f"  ! login failed for {u['username']}: "
                f"{resp.status_code} {resp.text[:200]}",
                file=sys.stderr,
            )
            continue
        sessions[u["username"]] = c
    return sessions


def load_records(db_path: str, text_field: str, component_field: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f'SELECT id, "{text_field}" AS narrative, "{component_field}" AS component, '
        "crash, fire, injured, deaths FROM records ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def pick_canonical(record: dict, field_choices: dict) -> dict:
    """Heuristic ground-truth label, driven by keyword matching on the
    narrative plus the NHTSA-supplied metadata."""
    narrative = (record.get("narrative") or "").lower()
    component = (record.get("component") or "").lower()
    crash = (record.get("crash") or "").upper() == "Y"
    fire = (record.get("fire") or "").upper() == "Y"
    injured = int(record.get("injured") or 0)
    deaths = int(record.get("deaths") or 0)

    # --- failure_location ---
    loc = "other"
    for kw, val in [
        ("front", "front"),
        ("rear", "rear"),
        ("underneath", "underbody"),
        ("underbody", "underbody"),
        ("cabin", "cabin"),
        ("interior", "cabin"),
        ("engine bay", "engine_bay"),
        ("engine compartment", "engine_bay"),
    ]:
        if kw in narrative:
            loc = val
            break

    # --- vehicle_system --- driven primarily by NHTSA component code
    sys_map = [
        ("brake", "brakes"),
        ("steering", "steering"),
        ("fuel", "fuel_system"),
        ("engine", "powertrain"),
        ("powertrain", "powertrain"),
        ("suspension", "suspension"),
        ("tire", "tires"),
        ("transmission", "transmission"),
        ("restraint", "restraints"),
        ("air bag", "restraints"),
        ("seat belt", "restraints"),
        ("airbag", "restraints"),
        ("exhaust", "exhaust"),
        ("cooling", "cooling"),
        ("radiator", "cooling"),
        ("hvac", "hvac"),
        ("heater", "hvac"),
        ("electrical", "electrical_system"),
        ("battery", "battery"),
    ]
    veh_sys = "other"
    for kw, val in sys_map:
        if kw in component or kw in narrative:
            veh_sys = val
            break

    # --- failure_type ---
    if fire or "fire" in narrative or "smoke" in narrative:
        ftype = "fire"
    elif "leak" in narrative:
        ftype = "fluid_leak"
    elif "electrical" in narrative or "electrical" in component:
        ftype = "electrical"
    elif "crack" in narrative or "broke" in narrative or "broken" in narrative:
        ftype = "structural"
    elif "rust" in narrative or "corrod" in narrative:
        ftype = "corrosion"
    elif "wear" in narrative or "worn" in narrative:
        ftype = "wear"
    elif "recall" in narrative or "defect" in narrative:
        ftype = "manufacturing_defect"
    else:
        ftype = "mechanical"

    # --- hazard_tags ---
    tags: list[str] = []
    if fire or "fire" in narrative:
        tags.append("fire")
    if crash or "crash" in narrative or "collision" in narrative or "accident" in narrative:
        tags.append("collision")
    if injured > 0 or "injur" in narrative:
        tags.append("injury")
    if "stall" in narrative:
        tags.append("stalling")
    if "control" in narrative and ("lost" in narrative or "loss" in narrative):
        tags.append("loss_of_control")
    if "visib" in narrative or "glass" in narrative or "windshield" in narrative:
        tags.append("visibility")
    if "leak" in narrative:
        tags.append("leakage")
    if "noise" in narrative or "noisy" in narrative:
        tags.append("noise")
    if "vibrat" in narrative:
        tags.append("vibration")
    if "rollaway" in narrative or "rolled" in narrative:
        tags.append("rollaway")
    if "accelerat" in narrative and "unintend" in narrative:
        tags.append("unintended_acceleration")
    if not tags:
        tags = ["loss_of_power"]
    tags = tags[:3]

    # --- severity ---
    if deaths > 0:
        sev = 5
    elif injured > 0:
        sev = 4
    elif fire or crash:
        sev = 3
    else:
        sev = 2

    # clamp every value to the YAML-declared allowed set
    def _in(field, val, fallback):
        return val if val in field_choices[field] else fallback

    return {
        "failure_location": _in("failure_location", loc, "other"),
        "failure_type":     _in("failure_type",     ftype, "mechanical"),
        "vehicle_system":   _in("vehicle_system",   veh_sys, "other"),
        "hazard_tags":      [t for t in tags if t in field_choices["hazard_tags"]] or ["loss_of_power"],
        "severity":         sev,
    }


def perturb(labels: dict, field_choices: dict, rng: random.Random, p: float) -> dict:
    """Return a perturbed copy — each field has probability p of flipping."""
    out = dict(labels)
    for field, choices in field_choices.items():
        if rng.random() >= p:
            continue
        if field == "hazard_tags":
            # swap one tag for a different choice
            current = list(out["hazard_tags"])
            if current and rng.random() < 0.5 and len(choices) > 1:
                idx = rng.randrange(len(current))
                alt = rng.choice([c for c in choices if c not in current]) if len(current) < len(choices) else current[idx]
                current[idx] = alt
            out["hazard_tags"] = current[:3] or [rng.choice(choices)]
        elif field == "severity":
            options = [v for v in (1, 2, 3, 4, 5) if v != out["severity"]]
            out["severity"] = rng.choice(options)
        else:
            options = [c for c in choices if c != out[field]]
            if options:
                out[field] = rng.choice(options)
    return out


def post_submission(
    session: httpx.Client, record_id: int, username: str, labels: dict
) -> int:
    from urllib.parse import urlencode

    pairs: list[tuple[str, str]] = [
        ("record_id", str(record_id)),
        ("failure_location", labels["failure_location"]),
        ("failure_type", labels["failure_type"]),
        ("vehicle_system", labels["vehicle_system"]),
        ("severity", str(labels["severity"])),
    ]
    for tag in labels["hazard_tags"]:
        pairs.append(("hazard_tags", tag))
    body = urlencode(pairs)
    resp = session.post(
        "/flywheel/label/submit",
        content=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    if resp.status_code not in (302, 303):
        print(
            f"  ! HTTP {resp.status_code} for record {record_id} / {username}: "
            f"{resp.text[:200]!r}",
            file=sys.stderr,
        )
    return resp.status_code


def main():
    args = parse_args()
    rng = random.Random(args.seed)

    cfg = yaml.safe_load(open(args.config).read())
    text_field = cfg["source"]["text_field"]
    component_field = "component_description"  # matches our example YAML

    field_choices = {}
    for f in cfg["fields"]:
        if f["kind"] == "ordinal":
            field_choices[f["name"]] = list(range(f["min"], f["max"] + 1))
        else:
            field_choices[f["name"]] = f["choices"]

    users = load_users(args.users)
    usernames = [u["username"] for u in users]
    print(f"loading {len(users)} user(s): {usernames}")

    records = load_records(args.db, text_field, component_field)
    records = records[:RECORDS_TO_LABEL]
    print(f"labeling {len(records)} records × 2 labelers each = {2*len(records)} submissions")

    sessions = login_sessions(args.url, users)
    if len(sessions) < 5:
        print(
            f"  ! only {len(sessions)}/5 logins succeeded — aborting",
            file=sys.stderr,
        )
        for c in sessions.values():
            c.close()
        sys.exit(1)

    try:
        pairs = list(itertools.combinations(usernames[:5], 2))
        print(f"pair assignment rotation ({len(pairs)} pairs): {pairs}")

        counts = {"ok": 0, "fail": 0}
        per_user = {u: 0 for u in usernames[:5]}

        for i, record in enumerate(records):
            rid = record["id"]
            pair = pairs[i % len(pairs)]
            canonical = pick_canonical(record, field_choices)

            for user in pair:
                labels = perturb(canonical, field_choices, rng, args.perturb)
                status = post_submission(sessions[user], rid, user, labels)
                if status in (302, 303):
                    counts["ok"] += 1
                else:
                    counts["fail"] += 1
                per_user[user] += 1

        print(f"submissions: {counts}")
        print(f"per user:    {per_user}")
    finally:
        for c in sessions.values():
            c.close()


if __name__ == "__main__":
    main()
