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
    # flywheel export notebook

    Reads `flywheel.yaml`, pulls gold records out of SQLite, and writes two export flavors:

    - **tabular** — one row per gold record, labels as columns, in CSV / JSON / Parquet.
    - **finetune** — unsloth-style instruct JSONL (messages format) using the `export.finetune` template from the YAML.
    """)
    return


@app.cell(hide_code=True)
def _():
    from pydantic import BaseModel, Field
    from typing import Literal


    class ModelParams(BaseModel):
        """Source of truth for the export run. Driven from the UI form in
        notebook mode or from `mo.cli_args()` in script mode."""

        limit: int | None = Field(
            default=None,
            description="Cap on how many gold records to export. None = all.",
        )
        include_contested: bool = Field(
            default=False,
            description="Also emit records that have min_labelers submissions but disagree, into a separate contested_*.jsonl for supervisor triage.",
        )
        formats: list[str] = Field(
            default_factory=lambda: ["csv", "json", "parquet", "jsonl"],
            description="Subset of serialization formats to emit. JSONL covers both tabular jsonl and finetune shape.",
        )
        shape: Literal["chat_messages", "alpaca"] = Field(
            default="chat_messages",
            description="Finetune output shape. chat_messages = unsloth-style messages list; alpaca = instruction/input/output.",
        )


    ModelParams
    return (ModelParams,)


@app.cell(hide_code=True)
def _(mo):
    form = (
        mo.md(
            """
        Tune the export parameters and hit submit. These defaults are the same as
        running `python export.py` with no flags.

        {limit}

        {include_contested}

        {formats}

        {shape}
        """
        )
        .batch(
            limit=mo.ui.number(
                start=0, stop=10000, value=0, step=1, label="Limit (0 = no cap)"
            ),
            include_contested=mo.ui.checkbox(
                value=False, label="Include contested records"
            ),
            formats=mo.ui.multiselect(
                options=["csv", "json", "parquet", "jsonl"],
                value=["csv", "json", "parquet", "jsonl"],
                label="Formats",
            ),
            shape=mo.ui.dropdown(
                options=["chat_messages", "alpaca"],
                value="chat_messages",
                label="Finetune shape",
            ),
        )
        .form(show_clear_button=True)
    )
    form
    return (form,)


@app.cell(hide_code=True)
def _(ModelParams, form, mo):
    def _coerce_cli(raw: dict) -> dict:
        out = {}
        for _k, _v in raw.items():
            _key = _k.replace("-", "_")
            if _key == "help":
                continue
            if _key == "limit":
                out[_key] = None if int(_v) == 0 else int(_v)
            elif _key == "include_contested":
                out[_key] = str(_v).lower() in ("1", "true", "yes", "y")
            elif _key == "formats":
                out[_key] = [x.strip() for x in str(_v).split(",") if x.strip()]
            else:
                out[_key] = _v
        return out


    if mo.app_meta().mode == "script":
        _cli = mo.cli_args()
        if "help" in _cli:
            print("Usage: python export.py [--key value ...]")
            print()
            for _name, _field in ModelParams.model_fields.items():
                _default = (
                    f" (default: {_field.default})"
                    if _field.default is not None
                    else ""
                )
                print(
                    f"  --{_name.replace('_', '-'):20s} {_field.description}{_default}"
                )
            import sys

            sys.exit(0)
        params = ModelParams(**_coerce_cli(dict(_cli)))
    else:
        _ui = dict(form.value) if form.value else {}
        if _ui.get("limit") == 0:
            _ui["limit"] = None
        params = ModelParams(**_ui)

    params
    return (params,)


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
    cfg
    return PROJECT_ROOT, cfg


@app.cell(hide_code=True)
def _(PROJECT_ROOT, cfg):
    import sqlite3

    DB_PATH = PROJECT_ROOT / cfg["source"]["sqlite_path"]
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    def table_exists(name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (name,),
        ).fetchone()
        return row is not None

    def _table_count(name: str) -> int:
        if not table_exists(name):
            return 0
        return conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]

    counts = {
        "records": _table_count(cfg["source"]["table"]),
        "submissions": _table_count("submissions"),
        "users": _table_count("users"),
    }
    counts
    return conn, table_exists


@app.cell(hide_code=True)
def _(cfg, conn, params, table_exists):
    import json
    from collections import defaultdict

    MIN_LABELERS = int(cfg["reconciliation"]["min_labelers"])
    LABEL_FIELDS = [f["name"] for f in cfg["fields"]]

    _rows = (
        conn.execute(
            "SELECT record_id, username, submitted_at, values_json FROM submissions "
            "ORDER BY record_id, submitted_at"
        ).fetchall()
        if table_exists("submissions")
        else []
    )

    submissions_by_record: dict[int, list[dict]] = defaultdict(list)
    for _r in _rows:
        submissions_by_record[_r["record_id"]].append(
            {
                "username": _r["username"],
                "submitted_at": _r["submitted_at"],
                "values": json.loads(_r["values_json"]),
            }
        )

    # Supervisor reconciliations (gold-by-decision). These override
    # submission-derived gold for the same record_id and are themselves
    # included in the gold export.
    reconciled_values: dict[int, dict] = {}
    if table_exists("reconciliations"):
        for _r in conn.execute(
            "SELECT record_id, values_json FROM reconciliations"
        ).fetchall():
            reconciled_values[_r["record_id"]] = json.loads(_r["values_json"])


    def _canonical_value(_v):
        if isinstance(_v, list):
            if _v and isinstance(_v[0], dict):
                return tuple(sorted(tuple(sorted(_e.items())) for _e in _v))
            return tuple(sorted(_v))
        return _v


    def _is_unanimous(_subs):
        if len(_subs) < MIN_LABELERS:
            return False
        _first = _subs[0]["values"]
        for _s in _subs[1:]:
            for _field in LABEL_FIELDS:
                if _canonical_value(_first.get(_field)) != _canonical_value(
                    _s["values"].get(_field)
                ):
                    return False
        return True


    _unanimous_ids = [
        _id for _id, _subs in submissions_by_record.items() if _is_unanimous(_subs)
    ]
    # Gold = unanimous-from-submissions UNION reconciled-by-supervisor.
    # Reconciled records take precedence when both exist.
    _gold_all = sorted(set(_unanimous_ids) | set(reconciled_values.keys()))
    _contested_all = [
        _id
        for _id, _subs in submissions_by_record.items()
        if len(_subs) >= MIN_LABELERS
        and not _is_unanimous(_subs)
        and _id not in reconciled_values
    ]

    GOLD_RECORD_IDS = (
        _gold_all if params.limit is None else _gold_all[: params.limit]
    )
    CONTESTED_RECORD_IDS = _contested_all

    {
        "gold": GOLD_RECORD_IDS,
        "contested": CONTESTED_RECORD_IDS,
        "reconciled": len(reconciled_values),
    }
    return (
        CONTESTED_RECORD_IDS,
        GOLD_RECORD_IDS,
        LABEL_FIELDS,
        json,
        reconciled_values,
        submissions_by_record,
    )


@app.cell(hide_code=True)
def _(
    GOLD_RECORD_IDS,
    LABEL_FIELDS,
    cfg,
    conn,
    reconciled_values: dict[int, dict],
    submissions_by_record: dict[int, list[dict]],
):
    import pandas as pd

    source = cfg["source"]
    id_field = source["id_field"]
    text_field = source["text_field"]
    display_field_names = [f["name"] for f in (source.get("display_fields") or [])]
    source_cols = [id_field, text_field] + display_field_names

    placeholders = ",".join("?" * len(GOLD_RECORD_IDS)) or "NULL"
    cols_sql = ", ".join(f'"{c}"' for c in source_cols)
    source_rows = (
        conn.execute(
            f'SELECT {cols_sql} FROM "{source["table"]}" WHERE "{id_field}" IN ({placeholders})',
            GOLD_RECORD_IDS,
        ).fetchall()
        if GOLD_RECORD_IDS
        else []
    )


    def _canonical_for(rid):
        # Prefer supervisor reconciliation over unanimous-submissions.
        if rid in reconciled_values:
            return reconciled_values[rid]
        return submissions_by_record[rid][0]["values"]


    def _flatten_label(v):
        # hierarchical list-of-dicts → "parent/child;parent/child"
        # multi_select list-of-strings → "a;b;c"
        # scalar → passthrough
        if isinstance(v, list):
            if not v:
                return ""
            if isinstance(v[0], dict):
                return ";".join("/".join(str(x) for x in e.values()) for e in v)
            return ";".join(v)
        return v

    tabular_rows = []
    for sr in source_rows:
        rid = sr[id_field]
        canonical_values = _canonical_for(rid)
        row = {id_field: rid, text_field: sr[text_field]}
        for c in display_field_names:
            row[c] = sr[c]
        for field in LABEL_FIELDS:
            row[f"label_{field}"] = _flatten_label(canonical_values.get(field))
        tabular_rows.append(row)

    # SQLite's dynamic typing stores empty-string '' alongside ints in the
    # same "INTEGER" column when the source CSV has blank cells. Replace
    # '' with pd.NA so convert_dtypes() can pick a nullable integer dtype
    # and parquet writes don't choke on mixed-type object columns.
    gold_df = pd.DataFrame(tabular_rows).replace("", pd.NA)
    gold_df
    return gold_df, id_field, source_rows, text_field


@app.cell(hide_code=True)
def _(PROJECT_ROOT, cfg, gold_df, params):
    EXPORT_ROOT = PROJECT_ROOT / "exports"
    TABULAR_DIR = EXPORT_ROOT / "tabular"
    TABULAR_DIR.mkdir(parents=True, exist_ok=True)
    STEM = cfg["project"]["slug"]

    tabular_paths = {}
    if not gold_df.empty:
        # SQLite's typeless columns come back as Python object dtype with
        # a mix of ints and Nones; normalize to pandas nullable types so
        # pyarrow doesn't choke on heterogeneous object columns.
        _df = gold_df.convert_dtypes()
        if "csv" in params.formats:
            _p = TABULAR_DIR / f"{STEM}.csv"
            _df.to_csv(_p, index=False)
            tabular_paths["csv"] = _p
        if "json" in params.formats:
            _p = TABULAR_DIR / f"{STEM}.json"
            _df.to_json(_p, orient="records", indent=2)
            tabular_paths["json"] = _p
        if "parquet" in params.formats:
            _p = TABULAR_DIR / f"{STEM}.parquet"
            _df.to_parquet(_p, index=False)
            tabular_paths["parquet"] = _p
        if "jsonl" in params.formats:
            _p = TABULAR_DIR / f"{STEM}.jsonl"
            _df.to_json(_p, orient="records", lines=True)
            tabular_paths["jsonl"] = _p

    tabular_paths
    return EXPORT_ROOT, STEM, tabular_paths


@app.cell(hide_code=True)
def _(
    EXPORT_ROOT,
    GOLD_RECORD_IDS,
    cfg,
    id_field,
    json,
    params,
    reconciled_values: dict[int, dict],
    source_rows,
    submissions_by_record: dict[int, list[dict]],
    text_field,
):
    FINETUNE_DIR = EXPORT_ROOT / "finetune"
    FINETUNE_DIR.mkdir(parents=True, exist_ok=True)

    finetune_cfg = cfg.get("export", {}).get("finetune", {})
    system_prompt = finetune_cfg.get(
        "system_prompt", "Classify the record."
    ).strip()
    user_template = finetune_cfg.get("user_template", "{text}")
    assistant_template = finetune_cfg.get("assistant_template", "{labels_json}")


    def _text_for(_rid):
        return next(
            _sr[text_field] for _sr in source_rows if _sr[id_field] == _rid
        )


    def _labels_for(_rid):
        # Prefer the supervisor's reconciliation over the first submission.
        # Compact separators — drops the default ", " and ": " in favor of
        # "," and ":" so the assistant target has fewer tokens and less
        # formatting variance for the model to learn.
        _vals = (
            reconciled_values[_rid]
            if _rid in reconciled_values
            else submissions_by_record[_rid][0]["values"]
        )
        return json.dumps(_vals, sort_keys=True, separators=(",", ":"))


    def build_chat_messages(_rid):
        _text = _text_for(_rid)
        _labels = _labels_for(_rid)
        return {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_template.format(text=_text)},
                {
                    "role": "assistant",
                    "content": assistant_template.format(
                        labels_json=_labels, text=_text
                    ),
                },
            ]
        }


    def build_alpaca(_rid):
        _text = _text_for(_rid)
        _labels = _labels_for(_rid)
        return {
            "instruction": system_prompt,
            "input": user_template.format(text=_text),
            "output": assistant_template.format(labels_json=_labels, text=_text),
        }


    _builders = {"chat_messages": build_chat_messages, "alpaca": build_alpaca}
    _build = _builders[params.shape]

    finetune_examples = [_build(_id) for _id in GOLD_RECORD_IDS]
    finetune_examples[0] if finetune_examples else None
    return FINETUNE_DIR, finetune_examples


@app.cell(hide_code=True)
def _(
    CONTESTED_RECORD_IDS,
    EXPORT_ROOT,
    FINETUNE_DIR,
    STEM,
    finetune_examples,
    json,
    params,
    submissions_by_record: dict[int, list[dict]],
):
    finetune_paths = {}
    if finetune_examples and "jsonl" in params.formats:
        jsonl_path = FINETUNE_DIR / f"{STEM}.{params.shape}.jsonl"
        with open(jsonl_path, "w") as f:
            for _ex in finetune_examples:
                f.write(json.dumps(_ex) + "\n")
        finetune_paths["jsonl"] = jsonl_path

    contested_paths = {}
    if params.include_contested and CONTESTED_RECORD_IDS:
        _contested_dir = EXPORT_ROOT / "contested"
        _contested_dir.mkdir(parents=True, exist_ok=True)
        if "jsonl" in params.formats:
            _contested_path = _contested_dir / f"{STEM}.contested.jsonl"
            with open(_contested_path, "w") as f:
                for _id in CONTESTED_RECORD_IDS:
                    f.write(
                        json.dumps(
                            {
                                "record_id": _id,
                                "submissions": submissions_by_record[_id],
                            }
                        )
                        + "\n"
                    )
            contested_paths["jsonl"] = _contested_path

    {"finetune": finetune_paths, "contested": contested_paths}
    return contested_paths, finetune_paths


@app.cell(hide_code=True)
def _(
    CONTESTED_RECORD_IDS,
    GOLD_RECORD_IDS,
    LABEL_FIELDS,
    contested_paths,
    finetune_paths,
    mo,
    params,
    tabular_paths,
):
    summary = {
        "params": params.model_dump(),
        "gold_count": len(GOLD_RECORD_IDS),
        "contested_count": len(CONTESTED_RECORD_IDS),
        "label_fields": LABEL_FIELDS,
        "tabular_exports": {k: str(v) for k, v in tabular_paths.items()},
        "finetune_exports": {k: str(v) for k, v in finetune_paths.items()},
        "contested_exports": {k: str(v) for k, v in contested_paths.items()},
    }
    mo.md(f"""## Export summary

    - **Params:** `limit={params.limit}` · `include_contested={params.include_contested}` · `formats={params.formats}` · `shape={params.shape}`
    - **{summary["gold_count"]}** gold records exported
    - **{summary["contested_count"]}** contested records {"(exported)" if params.include_contested else "(skipped — set include_contested to emit)"}
    - **Labels exported:** `{", ".join(summary["label_fields"])}`

    ### Tabular exports
    {chr(10).join(f"- `{v}`" for v in summary["tabular_exports"].values()) or "_(none)_"}

    ### Finetune exports
    {chr(10).join(f"- `{v}`" for v in summary["finetune_exports"].values()) or "_(none)_"}

    ### Contested exports
    {chr(10).join(f"- `{v}`" for v in summary["contested_exports"].values()) or "_(none)_"}
    """)
    return


if __name__ == "__main__":
    app.run()
