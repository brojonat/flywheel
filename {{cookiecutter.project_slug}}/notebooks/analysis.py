import marimo

__generated_with = "0.23.1"
app = marimo.App(width="medium")


@app.cell
def _():
    import marimo as mo

    return (mo,)


@app.cell(hide_code=True)
def _(mo):
    mo.md("""
    # flywheel · labeling quality notebook

    Reads the labeling SQLite, classifies every record as **gold** (unanimous),
    **contested** (N labelers disagree), or **partial** (< N labelers), and
    surfaces a handful of diagnostic views: per-user accuracy against gold,
    per-field agreement rate, per-field confusion matrices, a contested-record
    triage table, and label distribution histograms.

    Read-only. The supervisor reconciliation write path lives in the Datasette
    plugin, not here.
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    import os
    from pathlib import Path
    import yaml

    NOTEBOOK_DIR = Path(mo.notebook_dir() or Path.cwd())
    PROJECT_ROOT = NOTEBOOK_DIR.parent
    CONFIG_PATH = Path(
        os.environ.get("FLYWHEEL_CONFIG", PROJECT_ROOT / "flywheel.yaml")
    )
    cfg = yaml.safe_load(CONFIG_PATH.read_text())

    MIN_LABELERS = int(cfg["reconciliation"]["min_labelers"])
    LABEL_FIELDS = [f["name"] for f in cfg["fields"]]


    def _field_choices(_f):
        _kind = _f["kind"]
        if _kind == "ordinal":
            return list(range(_f["min"], _f["max"] + 1))
        if _kind == "hierarchical_multi_select":
            # Flatten to "parent/child" strings so downstream histograms
            # and confusion tables have something uniform to group on.
            return [
                f"{_p['name']}/{_c['name']}"
                for _p in (_f.get("choices") or [])
                for _c in (_p.get("children") or [])
            ]
        if _kind == "free_text":
            return []  # unbounded text; no enumeration
        return _f.get("choices") or []


    FIELD_CHOICES = {f["name"]: _field_choices(f) for f in cfg["fields"]}

    {
        "project": cfg["project"]["name"],
        "min_labelers": MIN_LABELERS,
        "fields": LABEL_FIELDS,
    }
    return LABEL_FIELDS, MIN_LABELERS, PROJECT_ROOT, cfg


@app.cell(hide_code=True)
def _(PROJECT_ROOT, cfg, mo):
    import sqlite3

    DB_PATH = PROJECT_ROOT / cfg["source"]["sqlite_path"]
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row


    def table_exists(name: str) -> bool:
        return (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
                (name,),
            ).fetchone()
            is not None
        )


    if not table_exists("submissions"):
        mo.stop(
            True,
            mo.md(
                "**No `submissions` table yet.** Start datasette on this project and label a few records, then re-run this notebook."
            ),
        )

    submission_rows = conn.execute(
        "SELECT record_id, username, submitted_at, values_json FROM submissions "
        "ORDER BY record_id, submitted_at"
    ).fetchall()

    reconciled_ids = set()
    if table_exists("reconciliations"):
        reconciled_ids = {
            _row["record_id"]
            for _row in conn.execute(
                "SELECT record_id FROM reconciliations"
            ).fetchall()
        }

    {"rows": len(submission_rows), "reconciled": len(reconciled_ids)}
    return conn, reconciled_ids, submission_rows


@app.cell(hide_code=True)
def _(LABEL_FIELDS, MIN_LABELERS, mo, reconciled_ids, submission_rows):
    import json
    from collections import defaultdict

    subs_by_record: dict[int, list[dict]] = defaultdict(list)
    for _r in submission_rows:
        subs_by_record[_r["record_id"]].append(
            {
                "username": _r["username"],
                "submitted_at": _r["submitted_at"],
                "values": json.loads(_r["values_json"]),
            }
        )


    def canon_value(v):
        """Canonicalize so list/key/insertion order does not affect equality.

        Handles three shapes: scalars, multi_select lists (list of str),
        and hierarchical_multi_select lists (list of dicts)."""
        if isinstance(v, list):
            if v and isinstance(v[0], dict):
                return tuple(sorted(tuple(sorted(e.items())) for e in v))
            return tuple(sorted(v))
        return v


    def _field_equal(subs, field):
        vals = [canon_value(s["values"].get(field)) for s in subs]
        return len(set(vals)) == 1


    def _classify(rid, subs):
        if rid in reconciled_ids:
            return "reconciled"
        if len(subs) < MIN_LABELERS:
            return "partial"
        return (
            "gold"
            if all(_field_equal(subs, f) for f in LABEL_FIELDS)
            else "contested"
        )


    record_status = {rid: _classify(rid, subs) for rid, subs in subs_by_record.items()}

    status_counts = {"gold": 0, "reconciled": 0, "contested": 0, "partial": 0}
    for _s in record_status.values():
        status_counts[_s] += 1

    mo.md(f"""## Record status breakdown

    - **gold** (all {MIN_LABELERS} labelers unanimous on every field): **{status_counts["gold"]}**
    - **reconciled** (supervisor picked gold values for a formerly-contested record): **{status_counts["reconciled"]}**
    - **contested** (≥ {MIN_LABELERS} labelers, disagreement, no supervisor decision yet): **{status_counts["contested"]}**
    - **partial** (< {MIN_LABELERS} submissions — still awaiting a labeler): **{status_counts["partial"]}**
    - **Total records with any submission:** **{len(record_status)}**
    """)
    return canon_value, record_status, subs_by_record


@app.cell(hide_code=True)
def _(
    LABEL_FIELDS,
    canon_value,
    mo,
    record_status,
    subs_by_record: dict[int, list[dict]],
):
    import pandas as pd

    # For each user, the most honest metric we can compute without an external
    # ground truth is: of the records they submitted on, how many ended up as
    # unanimous gold?  A consistently-bad labeler will drag records into the
    # contested bucket; a consistently-good one will reinforce agreement.
    #
    # We also compute a per-field "match-vs-plurality" rate as a secondary
    # signal, but note: with N=2 labelers the plurality is just "the first
    # insertion" for any disagreement, so that metric biases toward
    # earlier-inserted submitters. Read it as "relative across users in the
    # same pair cohort," not as absolute accuracy.

    from collections import Counter


    def _plurality(values):
        return Counter(canon_value(v) for v in values).most_common(1)[0][0]


    per_record_truth = {
        rid: {
            f: _plurality([s["values"].get(f) for s in subs]) for f in LABEL_FIELDS
        }
        for rid, subs in subs_by_record.items()
    }

    _user_rows = []
    for _rid, _subs in subs_by_record.items():
        _truth = per_record_truth[_rid]
        _status = record_status[_rid]
        for _sub in _subs:
            _row = {
                "username": _sub["username"],
                "record_id": _rid,
                "record_status": _status,
            }
            _hits = 0
            for _field in LABEL_FIELDS:
                _h = int(canon_value(_sub["values"].get(_field)) == _truth[_field])
                _hits += _h
                _row[f"match_{_field}"] = _h
            _row["field_hits"] = _hits
            _user_rows.append(_row)

    per_submission_df = pd.DataFrame(_user_rows)

    user_summary = (
        per_submission_df.groupby("username")
        .agg(
            submissions=("record_id", "count"),
            gold_contributions=(
                "record_status",
                lambda _s: int((_s == "gold").sum()),
            ),
            gold_contribution_rate=(
                "record_status",
                lambda _s: round((_s == "gold").mean(), 3),
            ),
            contested_load=(
                "record_status",
                lambda _s: int((_s == "contested").sum()),
            ),
            field_match_rate=(
                "field_hits",
                lambda _s: round(_s.sum() / (len(_s) * len(LABEL_FIELDS)), 3),
            ),
        )
        .sort_values("gold_contribution_rate", ascending=True)
    )

    mo.vstack(
        [
            mo.md("## Per-user contribution to gold"),
            mo.md(
                "_Sorted worst-first by **`gold_contribution_rate`** — the fraction of this user's "
                "submissions that landed on records which ended up **unanimous gold**. A user whose "
                "labels drag records into the **contested** bucket will have a low rate. "
                "`field_match_rate` is a secondary, plurality-based signal and is biased on ties, "
                "so trust it relatively rather than absolutely._"
            ),
            mo.ui.table(user_summary.reset_index(), pagination=False),
        ]
    )
    return (pd,)


@app.cell(hide_code=True)
def _(mo, pd, record_status, subs_by_record: dict[int, list[dict]]):
    # Pairwise agreement matrix: for every pair of users who co-labeled any
    # records, what fraction of their shared records reached unanimous gold?

    import itertools

    _pair_stats: dict[tuple[str, str], dict] = {}
    for _rid, _subs in subs_by_record.items():
        if len(_subs) < 2:
            continue
        _names = sorted({_s["username"] for _s in _subs})
        for _a, _b in itertools.combinations(_names, 2):
            _key = (_a, _b)
            _cell = _pair_stats.setdefault(_key, {"shared": 0, "gold": 0})
            _cell["shared"] += 1
            if record_status[_rid] == "gold":
                _cell["gold"] += 1

    _users_sorted = sorted({u for pair in _pair_stats for u in pair})
    _matrix = pd.DataFrame(
        index=_users_sorted, columns=_users_sorted, dtype=object
    )
    for _u in _users_sorted:
        _matrix.loc[_u, _u] = "—"
    for (_a, _b), _stat in _pair_stats.items():
        _rate = _stat["gold"] / _stat["shared"] if _stat["shared"] else 0
        _text = f"{_rate:.2f} ({_stat['gold']}/{_stat['shared']})"
        _matrix.loc[_a, _b] = _text
        _matrix.loc[_b, _a] = _text

    mo.vstack(
        [
            mo.md("## Pairwise agreement matrix"),
            mo.md(
                "_Each cell shows the fraction of records co-labeled by that user pair "
                "which reached unanimous gold, with the raw count. Low cells flag pairs "
                "who disagree often (either one of them is off, or the pair sees hard records)._"
            ),
            mo.ui.table(
                _matrix.reset_index().rename(columns={"index": "user"}),
                pagination=False,
            ),
        ]
    )
    return


@app.cell(hide_code=True)
def _(
    LABEL_FIELDS,
    MIN_LABELERS,
    canon_value,
    mo,
    pd,
    subs_by_record: dict[int, list[dict]],
):
    # Per-field agreement rate: for every label field, what fraction of
    # records with N labelers reach unanimity on *that field alone*. Low
    # fraction = this field is genuinely hard / ambiguous / the choice list
    # is too granular.

    _field_rows = []
    _eligible = [
        rid for rid, subs in subs_by_record.items() if len(subs) >= MIN_LABELERS
    ]
    for _f in LABEL_FIELDS:
        _total = 0
        _agree = 0
        for _rid in _eligible:
            _subs = subs_by_record[_rid]
            _vals = {canon_value(_s["values"].get(_f)) for _s in _subs}
            _total += 1
            if len(_vals) == 1:
                _agree += 1
        _rate = _agree / _total if _total else 0
        _field_rows.append(
            {
                "field": _f,
                "agreements": _agree,
                "total": _total,
                "agreement_rate": round(_rate, 3),
            }
        )

    field_agreement_df = pd.DataFrame(_field_rows).sort_values("agreement_rate")

    mo.vstack(
        [
            mo.md("## Per-field agreement rate"),
            mo.md(
                "_Fraction of `>= min_labelers` records where labelers unanimously agreed on that "
                "specific field. Lowest rate at the top — those are the fields the schema is "
                "struggling with. Consider expanding / tightening the choice list or clarifying "
                "definitions._"
            ),
            mo.ui.table(field_agreement_df, pagination=False),
        ]
    )
    return


@app.cell(hide_code=True)
def _(
    LABEL_FIELDS,
    canon_value,
    cfg,
    mo,
    pd,
    record_status,
    subs_by_record: dict[int, list[dict]],
):
    # Per-field confusion matrices for contested records.
    # For single_select and ordinal fields, symmetric co-disagreement count:
    # cell (A, B) = number of contested records where one labeler said A and
    # another said B (A != B). High cells tell you which choices are being
    # routinely confused — prime candidates for a tightened schema or a
    # labeler training session.


    def _pair_disagreements(subs, field):
        """Return list of (a, b) tuples for each disagreeing pair on a field."""
        vals = [canon_value(s["values"].get(field)) for s in subs]
        out = []
        for i in range(len(vals)):
            for j in range(i + 1, len(vals)):
                if vals[i] != vals[j]:
                    a, b = sorted((str(vals[i]), str(vals[j])))
                    out.append((a, b))
        return out


    _confusion_per_field = {}
    for _f in LABEL_FIELDS:
        _f_meta = next(x for x in cfg["fields"] if x["name"] == _f)
        if _f_meta["kind"] == "multi_select":
            continue  # handled separately below
        _mat: dict[tuple[str, str], int] = {}
        for _rid, _subs in subs_by_record.items():
            if record_status[_rid] != "contested":
                continue
            for _pair in _pair_disagreements(_subs, _f):
                _mat[_pair] = _mat.get(_pair, 0) + 1
        if _mat:
            _rows = [
                {"a": a, "b": b, "count": c}
                for (a, b), c in sorted(_mat.items(), key=lambda kv: -kv[1])
            ]
            _confusion_per_field[_f] = pd.DataFrame(_rows)

    confusion_tables = mo.vstack(
        [mo.md("## Disagreement pairs by field")]
        + [
            mo.vstack([mo.md(f"### `{_f}`"), mo.ui.table(df, pagination=False)])
            for _f, df in _confusion_per_field.items()
        ]
        + [
            mo.md(
                "_Ordered by frequency — top rows are the most common confusions._"
            )
        ]
    )
    confusion_tables
    return


@app.cell(hide_code=True)
def _(
    LABEL_FIELDS,
    canon_value,
    cfg,
    conn,
    mo,
    pd,
    record_status,
    subs_by_record: dict[int, list[dict]],
):
    # Contested records triage table. Flatten each contested record into a
    # row with one column per (labeler, field), so the supervisor can scan
    # left-to-right and spot disagreements. A compact `diff_fields` column
    # lists which fields actually differ so they can sort to the hardest
    # records first.


    def _record_text_snippet(rid: int, limit: int = 140) -> str:
        _row = conn.execute(
            f'SELECT "{cfg["source"]["text_field"]}" AS t FROM "{cfg["source"]["table"]}" WHERE "{cfg["source"]["id_field"]}" = ?',
            (rid,),
        ).fetchone()
        if _row is None:
            return ""
        _t = _row["t"] or ""
        return _t if len(_t) <= limit else _t[:limit].rstrip() + "…"


    def _diff_fields(subs):
        out = []
        for _f in LABEL_FIELDS:
            _vals = {canon_value(_s["values"].get(_f)) for _s in subs}
            if len(_vals) > 1:
                out.append(_f)
        return out


    _triage_rows = []
    for _rid, _subs in subs_by_record.items():
        if record_status[_rid] != "contested":
            continue
        _diffs = _diff_fields(_subs)
        _row = {
            "record_id": _rid,
            "diff_count": len(_diffs),
            "diff_fields": ", ".join(_diffs),
            "narrative": _record_text_snippet(_rid),
        }
        for _sub in sorted(_subs, key=lambda s: s["username"]):
            _u = _sub["username"]
            for _f in LABEL_FIELDS:
                _v = _sub["values"].get(_f)
                if isinstance(_v, list):
                    if _v and isinstance(_v[0], dict):
                        _row[f"{_u}:{_f}"] = "; ".join(
                            " → ".join(str(_x) for _x in _e.values()) for _e in _v
                        )
                    else:
                        _row[f"{_u}:{_f}"] = ",".join(_v)
                else:
                    _row[f"{_u}:{_f}"] = str(_v)
        _triage_rows.append(_row)

    triage_df = (
        pd.DataFrame(_triage_rows).sort_values("diff_count", ascending=False)
        if _triage_rows
        else pd.DataFrame()
    )

    mo.vstack(
        [
            mo.md("## Contested records triage"),
            mo.md(
                "_Sorted worst-first by number of fields in disagreement. `diff_fields` names the "
                "specific fields that differ. Each `<user>:<field>` column shows what that labeler "
                "picked — the supervisor compares the columns side-by-side to choose the gold value._"
            ),
            mo.ui.table(triage_df, page_size=20)
            if not triage_df.empty
            else mo.md("_(no contested records)_"),
        ]
    )
    return


@app.cell(hide_code=True)
def _(LABEL_FIELDS, mo, pd, subs_by_record: dict[int, list[dict]]):
    # Label distribution histograms per field. Class imbalance shows up
    # here: any bar that dominates means that label will be over-represented
    # in training data and the model will learn a strong prior toward it.

    _dist_blocks = [mo.md("## Label distribution per field")]
    for _f in LABEL_FIELDS:
        _counts: dict[str, int] = {}
        for _subs in subs_by_record.values():
            for _sub in _subs:
                _v = _sub["values"].get(_f)
                if isinstance(_v, list):
                    for _x in _v:
                        if isinstance(_x, dict):
                            _k = "/".join(str(_y) for _y in _x.values())
                            _counts[_k] = _counts.get(_k, 0) + 1
                        else:
                            _counts[str(_x)] = _counts.get(str(_x), 0) + 1
                else:
                    _counts[str(_v)] = _counts.get(str(_v), 0) + 1
        _df_dist = (
            pd.DataFrame([{"value": k, "count": v} for k, v in _counts.items()])
            .sort_values("count", ascending=False)
            .reset_index(drop=True)
        )
        _dist_blocks.append(mo.md(f"### `{_f}`"))
        _dist_blocks.append(mo.ui.table(_df_dist, pagination=False))

    mo.vstack(_dist_blocks)
    return


if __name__ == "__main__":
    app.run()
