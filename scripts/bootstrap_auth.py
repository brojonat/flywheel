#!/usr/bin/env python3
"""Seed auth state for a generated flywheel project.

Reads ``examples/<slug>/users.yaml``, hashes every plaintext password
via ``datasette_auth_passwords.utils.hash_password``, and emits two
things into the generated project directory:

1. ``metadata.yml`` — so Datasette's ``datasette-auth-passwords``
   plugin serves ``/-/login`` with those credentials. Also wires up an
   ``actors`` map so ``request.actor`` has a ``role`` field.

2. Seeds the ``users`` table in ``data/labeling.db`` with the same
   (username, role) rows so foreign keys from ``submissions`` and
   ``reconciliations`` resolve without waiting for the plugin's
   auto-create path.

Usage:
    python scripts/bootstrap_auth.py <example_slug> <project_dir>
"""
from __future__ import annotations

import datetime
import sqlite3
import sys
from pathlib import Path

import yaml
from datasette_auth_passwords.utils import hash_password


def main() -> None:
    if len(sys.argv) != 3:
        print(
            "usage: bootstrap_auth.py <example_slug> <project_dir>",
            file=sys.stderr,
        )
        sys.exit(2)

    example_slug = sys.argv[1]
    project_dir = Path(sys.argv[2]).resolve()
    root = Path(__file__).resolve().parent.parent
    users_yaml = root / "examples" / example_slug / "users.yaml"
    if not users_yaml.exists():
        print(
            f"no users.yaml at {users_yaml}; skipping auth bootstrap",
            file=sys.stderr,
        )
        return

    users = yaml.safe_load(users_yaml.read_text()).get("users") or []
    if not users:
        print(f"{users_yaml}: empty users list", file=sys.stderr)
        return

    # 1. metadata.yml
    plugin_config: dict = {}
    actors: dict = {}
    for u in users:
        name = u["username"]
        plaintext = u["password"]
        role = u.get("role", "labeler")
        plugin_config[f"{name}_password_hash"] = hash_password(plaintext)
        actors[name] = {"id": name, "role": role}
    plugin_config["actors"] = actors

    metadata = {
        "title": f"flywheel · {example_slug}",
        "plugins": {
            "datasette-auth-passwords": plugin_config,
        },
    }
    metadata_path = project_dir / "metadata.yml"
    metadata_path.write_text(yaml.safe_dump(metadata, sort_keys=False))
    print(
        f"==> wrote {metadata_path.relative_to(project_dir.parent)} "
        f"with {len(users)} user(s)",
        file=sys.stderr,
    )

    # 2. Seed the users table. The plugin's startup hook creates the
    # schema on first datasette boot, but we need the rows to exist
    # before any labeling happens so FKs resolve. Create the table
    # ourselves here (same shape as the plugin's CREATE) so generate.sh
    # can run without a prior datasette invocation.
    db_path = project_dir / "data" / "labeling.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "  username TEXT PRIMARY KEY,"
            "  role TEXT NOT NULL DEFAULT 'labeler',"
            "  created_at TEXT NOT NULL"
            ")"
        )
        now = datetime.datetime.utcnow().isoformat(timespec="seconds")
        for u in users:
            conn.execute(
                "INSERT OR REPLACE INTO users (username, role, created_at) "
                "VALUES (?, ?, ?)",
                (u["username"], u.get("role", "labeler"), now),
            )
        conn.commit()
    finally:
        conn.close()
    print(f"==> seeded users table with {len(users)} row(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
