#!/usr/bin/env python3
"""Reservoir-sample N records from NHTSA FLAT_CMPL.txt and write a CSV.

The NHTSA complaints file is tab-delimited, 49 fields per row, no header.
See data/CMPL_SCHEMA.txt for the full field list. This script:

- streams the file (so we don't load 1.5 GB into memory)
- skips records with empty or very-short narrative (CDESCR)
- keeps a reservoir of N uniformly-sampled records
- writes a CSV with a curated subset of columns suitable for the flywheel
  labeling UI (id, narrative, component, make/model/year, injury counts, etc.)

Usage:
    python scripts/sample_nhtsa.py [--n 100] [--seed 42] \\
        --input data/FLAT_CMPL.txt --output fake_data/nhtsa_sample.csv
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys

# 1-indexed field positions per data/CMPL_SCHEMA.txt
SCHEMA = [
    ( 1, "CMPLID",       "id"),
    ( 3, "MFR_NAME",     "manufacturer"),
    ( 4, "MAKETXT",      "vehicle_make"),
    ( 5, "MODELTXT",     "vehicle_model"),
    ( 6, "YEARTXT",      "vehicle_year"),
    ( 7, "CRASH",        "crash"),
    ( 8, "FAILDATE",     "fail_date"),
    ( 9, "FIRE",         "fire"),
    (10, "INJURED",      "injured"),
    (11, "DEATHS",       "deaths"),
    (12, "COMPDESC",     "component_description"),
    (14, "STATE",        "state"),
    (17, "LDATE",        "received_date"),
    (18, "MILES",        "miles"),
    (20, "CDESCR",       "narrative"),
    (21, "CMPL_TYPE",    "source_type"),
    (28, "DRIVE_TRAIN",  "drive_train"),
    (30, "FUEL_TYPE",    "fuel_type"),
    (31, "TRANS_TYPE",   "transmission"),
    (32, "VEH_SPEED",    "speed"),
    (46, "PROD_TYPE",    "product_type"),
]

# minimum narrative length — skip one-word dribble that won't be labelable
MIN_NARRATIVE_CHARS = 60


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", default="data/FLAT_CMPL.txt")
    p.add_argument("--output", default="fake_data/nhtsa_sample.csv")
    p.add_argument("--n", type=int, default=100, help="Number of records to keep")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--progress-every", type=int, default=250_000)
    return p.parse_args()


def extract_row(fields: list[str]) -> dict:
    out = {}
    for idx, _src_name, out_name in SCHEMA:
        raw = fields[idx - 1] if idx - 1 < len(fields) else ""
        out[out_name] = raw.strip()
    return out


def main():
    args = parse_args()
    rng = random.Random(args.seed)

    reservoir: list[dict] = []
    total_seen = 0          # every parsed line
    eligible_seen = 0       # lines that passed the filter
    malformed = 0

    # stream through the file
    with open(args.input, "r", encoding="utf-8", errors="replace") as f:
        for line_no, line in enumerate(f, 1):
            total_seen += 1
            if total_seen % args.progress_every == 0:
                print(
                    f"  scanned {total_seen:>10,} lines · eligible {eligible_seen:>8,} · "
                    f"reservoir {len(reservoir):>3}",
                    file=sys.stderr,
                )
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 49:
                malformed += 1
                continue
            row = extract_row(fields)
            if len(row["narrative"]) < MIN_NARRATIVE_CHARS:
                continue
            eligible_seen += 1

            if len(reservoir) < args.n:
                reservoir.append(row)
            else:
                # standard reservoir sampling (Algorithm R)
                j = rng.randint(0, eligible_seen - 1)
                if j < args.n:
                    reservoir[j] = row

    print(
        f"done: {total_seen:,} lines scanned, {eligible_seen:,} eligible, "
        f"{malformed:,} malformed, {len(reservoir)} kept",
        file=sys.stderr,
    )

    # rewrite ids as 1..N so flywheel's integer-PK records table stays dense
    out_cols = [out_name for _, _, out_name in SCHEMA]
    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as out:
        w = csv.DictWriter(out, fieldnames=out_cols)
        w.writeheader()
        for new_id, row in enumerate(reservoir, 1):
            row["id"] = new_id
            w.writerow(row)

    print(f"wrote {len(reservoir)} rows → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
