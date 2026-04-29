#!/usr/bin/env python3
"""Load a data file (CSV, Parquet, or SQLite) into a labeling.db records table.

Usage: ingest_data.py <source_file> <dest_db>

Supported extensions: .csv, .parquet, .db, .sqlite
"""
import subprocess
import sqlite3
import sys
from pathlib import Path


def ingest_csv(src: Path, db_path: Path) -> int:
    subprocess.run(
        [
            "sqlite-utils", "insert", str(db_path), "records", str(src),
            "--csv", "--detect-types", "--pk=id",
        ],
        check=True,
    )
    conn = sqlite3.connect(str(db_path))
    count = conn.execute("SELECT count(*) FROM records").fetchone()[0]
    conn.close()
    return count


def ingest_parquet(src: Path, db_path: Path) -> int:
    import pyarrow.parquet as pq
    import sqlite_utils

    table = pq.read_table(str(src))
    rows = table.to_pylist()
    db = sqlite_utils.Database(str(db_path))
    db["records"].insert_all(rows, pk="id")
    return len(rows)


def ingest_sqlite(src: Path, db_path: Path) -> int:
    src_conn = sqlite3.connect(str(src))

    create_sql = src_conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='records'"
    ).fetchone()
    if create_sql is None:
        print(f"error: source database {src} has no 'records' table", file=sys.stderr)
        sys.exit(1)

    dst_conn = sqlite3.connect(str(db_path))
    dst_conn.execute(create_sql[0])

    cols = [d[0] for d in src_conn.execute("SELECT * FROM records LIMIT 0").description]
    rows = src_conn.execute("SELECT * FROM records").fetchall()
    if rows:
        placeholders = ",".join(["?"] * len(cols))
        col_names = ",".join(cols)
        dst_conn.executemany(
            f"INSERT INTO records ({col_names}) VALUES ({placeholders})", rows
        )

    dst_conn.commit()
    count = len(rows)
    src_conn.close()
    dst_conn.close()
    return count


INGESTERS = {
    ".csv": ingest_csv,
    ".parquet": ingest_parquet,
    ".db": ingest_sqlite,
    ".sqlite": ingest_sqlite,
}

SUPPORTED = ", ".join(INGESTERS)


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <source_file> <dest_db>", file=sys.stderr)
        sys.exit(1)

    src = Path(sys.argv[1])
    db_path = Path(sys.argv[2])

    ext = src.suffix.lower()
    ingester = INGESTERS.get(ext)
    if ingester is None:
        print(f"error: unsupported file type '{ext}' (expected: {SUPPORTED})", file=sys.stderr)
        sys.exit(1)

    count = ingester(src, db_path)
    print(f"    loaded {count} records from {src.name}", file=sys.stderr)


if __name__ == "__main__":
    main()
