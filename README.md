# flywheel

**A cookiecutter template for standing up YAML-configured text-labeling
services**, backed by Datasette + SQLite and paired with marimo notebooks
for export and quality analysis. One generated project = one labeling
exercise. A single `flywheel.yaml` is the source of truth for the whole
thing: it drives the form, the validation, the storage schema, the
reconciliation page, and the notebooks.

The goal is to replace heavier in-house labeling setups with something a
single developer can stand up, understand end-to-end, and regenerate in
minutes when the schema changes. Edit one YAML, regenerate the project,
start labeling.

---

## Why

Small teams building text classifiers usually need the same small loop,
over and over:

1. Surface an unlabeled record to a human.
2. Let them pick labels from a predefined schema.
3. When multiple humans labeled the same record and they disagree, have a
   supervisor pick the gold value.
4. Export the gold-labeled records in formats that scikit-learn pipelines
   and LLM fine-tuning jobs can consume directly.
5. Do enough analysis on the labeled data to know whether a specific
   labeler is drifting, whether a specific field is too ambiguous, and
   where to spend the next labeling effort.

Flywheel is that loop — stripped down, configured by YAML, and
regeneratable via cookiecutter so you can spin up a new labeling exercise
for a new domain without writing a new app.

**Out of scope on purpose.** Fine-tuning the actual model, training-run
dashboards, MLflow integration, and active-learning/model-in-the-loop
suggestions are handled by downstream projects. Flywheel produces the
labeled dataset; the model lives elsewhere.

---

## Quickstart

```bash
# 1. install dev dependencies (uv-managed venv)
make venv

# 2. generate the hello-world project into _tmp_output/vehicle_safety/
make gen                    # uses fake synthetic data

# 3. serve it on http://localhost:8001/flywheel
make serve
```

Open [http://localhost:8001/-/login](http://localhost:8001/-/login), sign in
(defaults for the `vehicle_safety` example are `alice` / `wonderland`),
and you'll land on `/flywheel` ready to label. See
[Authentication](#authentication) for the full seed list.

To run the compliance test suite:

```bash
make test
```

To generate the NHTSA real-data example instead:

```bash
make clean
make EXAMPLE=nhtsa_complaints gen
make EXAMPLE=nhtsa_complaints serve
```

One-shot dev cycle (clean + gen + serve in one command):

```bash
make EXAMPLE=nhtsa_complaints dev
```

---

## What you get when you label

The generated project ships a single Datasette plugin that owns every
flywheel route. Below is every URL the plugin exposes, what it does, and
where the data ends up.

### Routes

All `/flywheel/*` routes require an authenticated session — anonymous
requests redirect to `/-/login`. The authenticated user's name comes
from `request.actor["id"]`, so the "who am I" for a labeling or
reconciliation action is whoever owns the `ds_actor` cookie.

| URL                                    | Who           | What it does                                                            |
| -------------------------------------- | ------------- | ----------------------------------------------------------------------- |
| `GET /`                                | auth required | Redirects to `/flywheel`.                                               |
| `GET /flywheel`                        | auth required | Home page. Shows the logged-in user and links into the labeling flow.  |
| `GET /flywheel/label`                  | auth required | Shows the next record the authenticated user hasn't labeled that hasn't hit the cap-at-N. Renders the form driven by `fields:` in the YAML. |
| `POST /flywheel/label/submit`          | auth required | Validates the form, inserts a `submissions` row keyed on the actor's username, redirects to the next record. |
| `GET /flywheel/users`                  | auth required | Per-user index with submission counts — the "labeler history" view.    |
| `GET /flywheel/reconcile?view=pending` | auth required | Pending queue: contested records with no reconciliation yet. Tab links switch between `view=pending` and `view=reviewed`. |
| `GET /flywheel/reconcile?view=reviewed`| auth required | Reviewed queue: records that already have a reconciliation, showing who decided and when. Click to re-open for editing. |
| `GET /flywheel/reconcile/<id>?view=...`| auth required | Detail page. For pending records: side-by-side labeler columns with **diff rows highlighted**, agreement rows pre-selected, Custom override column with a schema-driven editable widget. For reviewed records: same form but every pick is pre-filled from the existing reconciliation, with a green banner showing who decided it and an Undo button. |
| `POST /flywheel/reconcile/<id>/submit` | auth required | Looks up the chosen labeler's actual values per field (or parses the custom override), writes a `reconciliations` row via `INSERT OR REPLACE` keyed on the actor's username as `supervisor`, auto-advances to the next record in the same queue. |
| `POST /flywheel/reconcile/<id>/undo`   | auth required | Deletes the reconciliation row and redirects back to the same record — which now appears in the Pending queue again. |
| `GET /-/login`                         | anyone        | Login form served by `datasette-auth-passwords`. Sets the `ds_actor` cookie on successful auth. |
| `GET /-/logout`                        | anyone        | Datasette's built-in logout — clears the `ds_actor` cookie.            |
| `GET /-/databases`                     | anyone        | Datasette's built-in DB browser. Useful for ad-hoc SQL, debugging, and verifying writes. |

### The labeling loop in four phases

1. **Label.** A labeler opens `/flywheel/label?labeler=<name>` and is
   handed the next record matching two rules: (a) they haven't submitted
   for it yet, and (b) the record has fewer than `min_labelers` total
   submissions (the cap-at-N rule). Both strategies — `queue` (sort by
   `queue_sort` column) and `random` — honor the cap.
2. **Auto-promote to gold.** When a record reaches `min_labelers`
   submissions and every field is unanimous across all submissions, it's
   promoted to gold automatically — no supervisor step. (Rule: `strict_equality`.)
3. **Reconcile when contested.** When a record hits `min_labelers` but
   any field differs, it enters the supervisor queue at
   `/flywheel/reconcile`. The supervisor walks the queue, picks the
   right value per field (via a radio click — they never re-type
   anything), and each submit writes a `reconciliations` row and
   auto-advances to the next contested record.
4. **Export.** A marimo notebook reads gold records (unanimous +
   reconciled) and writes them to disk in both tabular flavors (CSV /
   JSON / Parquet / JSONL rows) and an LLM fine-tuning flavor (JSONL,
   `chat_messages` or `alpaca` shape). See [Notebooks](#notebooks).

### Reconciliation UX

The reconciliation detail page is the one place where we leaned on
visuals. Rows where every labeler agreed are grayed out and italicized
and have their radio pre-selected (the supervisor just has to eyeball
them and move on). Rows where labelers disagreed get a pale-yellow
background with the first radio *unset* — the supervisor is forced to
click to confirm a pick, and the visual difference makes finding the
disagreements trivial. This is deliberate: the hard part of
reconciliation in practice is *finding* the diffs fast, not deciding
once you see them.

A progress counter at the top of each detail page shows `X of Y`, and
there are Prev / Next buttons so the supervisor can scrub through the
queue without bouncing back to the index.

**Custom override.** Each row has a **Custom override** column at the
right with a `__custom__` radio and a schema-driven editable widget
(dropdown for `single_select`, checkboxes for `multi_select`, chip row
for `ordinal`, textarea for `free_text`, full cascading picker for
`hierarchical_multi_select`). When neither labeler is right, the
supervisor selects the custom radio and fills in the widget. Custom
values are validated the same way labeling submissions are: `choices`
enforcement, `min_selections`/`max_selections`, ordinal range bounds,
and hierarchical "child belongs to selected parent" — invalid custom
values return an error page listing every problem. The custom value is
only honored if the supervisor explicitly selects the `__custom__`
radio for that row; filling in the widget without selecting the radio
is a no-op.

**Review / edit / undo.** The reconciliation queue has two tabs:
**Pending** (records awaiting a first decision) and **Reviewed**
(records with an existing reconciliation). Clicking a reviewed record
opens the same detail page — but with every pick pre-filled to match
the current gold value:

- If a field's gold value equals one of the labelers' submissions, that
  labeler's radio is pre-checked.
- If it doesn't match any labeler (i.e. the supervisor originally
  picked a custom value), the `__custom__` radio is pre-checked and the
  custom widget is pre-populated with the gold value. For
  `hierarchical_multi_select` this means the saved `(parent, child)`
  entries are rendered as `.hier-entry` rows with both selects pre-
  selected, and the child `<select>` contains only the children of the
  pre-selected parent (rendered server-side so the prefill works even
  before any JS runs).

The primary button on a reviewed record's detail page says **Save
changes →** instead of *Accept & next →*. Submitting overwrites the
existing reconciliation row via `INSERT OR REPLACE` (same PK).
Alongside the save button there's an **Undo reconciliation** button
that POSTs to `/flywheel/reconcile/<id>/undo`, which deletes the row
and redirects back to the same record — now appearing in the Pending
queue again, ready to be decided differently or left alone. A
confirmation dialog protects against stray clicks.

The title on the detail page switches between `Reconcile record #N`
(pending) and `Review record #N` (reviewed), and a green banner at
the top of the reviewed view shows who originally reconciled the
record and when, so the supervisor knows what they're about to
overwrite.

---

## What's in a generated project

```
_tmp_output/<slug>/
├── flywheel.yaml              # the config — everything reads this at runtime
├── data/
│   └── labeling.db            # SQLite: records + users + submissions + reconciliations
├── plugin/
│   ├── __init__.py
│   └── flywheel_plugin.py     # the Datasette plugin (routes, startup schema, HTML)
└── notebooks/
    ├── export.py              # marimo: gold export (CSV / JSON / Parquet / JSONL + finetune)
    └── analysis.py            # marimo: labeling quality EDA
```

- `flywheel.yaml` is the single source of truth. It comes from
  `examples/<slug>/flywheel.yaml` — `scripts/generate.sh` cookiecuts the
  skeleton, then copies the example's YAML over it.
- `data/labeling.db` is created by `sqlite-utils insert` from
  `examples/<slug>/data.csv` (or `fake_data/sample.csv` for the default
  hello-world). The plugin then creates `users`, `submissions`, and
  `reconciliations` tables on startup if they don't exist.
- `plugin/flywheel_plugin.py` is a single Python file with every route.
  Ugly enough to read top-to-bottom in one sitting, deliberately not
  split into modules.
- `notebooks/*` ship as marimo notebooks that also double as script-mode
  CLIs (see [Notebooks](#notebooks)).

---

## Configuration reference (`flywheel.yaml`)

```yaml
project:
  name: Vehicle Safety Labeling
  slug: vehicle_safety
  description: |
    Free-form description of the labeling task. Shown on the home page.

source:
  sqlite_path: data/labeling.db        # where the SQLite file lives, relative to project root
  table:       records                 # the table holding the unlabeled rows
  id_field:    id                      # integer primary key in `table`
  text_field:  narrative               # column to render as the thing to label
  display_fields:                      # metadata columns surfaced above the form
    - { name: submitted_at, label: Submitted }
    - { name: vehicle_make, label: Make }

labeling:
  strategy:   queue   # queue | random
  queue_sort: id      # column to ORDER BY when strategy=queue

fields:
  - name: failure_location
    kind: single_select
    label: Location of failure
    required: true
    choices: [front, rear, left, right, underbody, cabin, engine_bay, other]

  - name: hazard_tags
    kind: multi_select
    label: Hazard tags
    required: true
    min_selections: 1
    max_selections: 3
    choices: [fire, collision, injury, stalling, loss_of_control]

  - name: severity
    kind: ordinal
    label: Severity
    required: true
    min: 1
    max: 5
    endpoints: { 1: Negligible, 5: Catastrophic }

reconciliation:
  min_labelers:        2                # cap-at-N; how many labelers per record
  rule:                strict_equality  # the only rule implemented
  unanimous_auto_gold: true             # skip supervisor when every field agrees

export:
  tabular:
    formats: [parquet, csv, jsonl]      # which tabular flavors to emit
  finetune:
    shape: chat_messages                # chat_messages | alpaca
    system_prompt: |
      You are a vehicle safety analyst. Classify the customer complaint
      into the hazard, location, type, system, and severity fields.
    user_template:      "{text}"
    assistant_template: "{labels_json}"
```

### Field kinds

| kind                          | Renders as                                  | Notes                                                                                 |
| ----------------------------- | ------------------------------------------- | ------------------------------------------------------------------------------------- |
| `single_select`               | dropdown                                    | Exactly one value from `choices`.                                                     |
| `multi_select`                | checkbox group                              | `min_selections` and `max_selections` are **hard-enforced server-side** on submit.    |
| `ordinal`                     | row of numbered chips                       | Integer scale from `min` to `max` inclusive. Optional `endpoints` for endpoint labels. |
| `free_text`                   | `<textarea>`                                | Optional `rows`, `max_length`, `placeholder`, `hint`. Stored as a plain string. Reconciliation cells render with `white-space: pre-wrap`, no bold. |
| `hierarchical_multi_select`   | repeating group of linked parent/child dropdowns | Exactly **2 levels**. `min_entries` / `max_entries` hard-enforced. On submit, each child is validated to actually belong to its parent. Cascading is done client-side with a small embedded JS payload — no server roundtrip. Stored as a list of dicts (e.g. `[{"hazard":"fire","subhazard":"underhood"}]`). |

#### `hierarchical_multi_select` YAML shape

```yaml
- name: hazards
  kind: hierarchical_multi_select
  label: Hazard → subhazard
  required: true
  min_entries: 1
  max_entries: 3
  levels:
    - { name: hazard,    label: Hazard }
    - { name: subhazard, label: Subhazard }
  choices:
    - name: fire
      label: Fire
      children:
        - { name: underhood, label: Under-hood fire }
        - { name: battery,   label: Battery / HV pack fire }
        - { name: fuel,      label: Fuel-system fire }
    - name: leakage
      label: Leakage
      children:
        - { name: fuel,      label: Fuel leak }
        - { name: coolant,   label: Coolant leak }
        - { name: oil,       label: Oil leak }
```

A submission for this field looks like:

```json
{"hazards": [
  {"hazard": "fire",    "subhazard": "underhood"},
  {"hazard": "leakage", "subhazard": "fuel"}
]}
```

Canonicalization (used by the cap-at-N / reconciliation rule) is a
sorted tuple of sorted-items tuples, so two labelers who submit the
same `(parent, child)` pairs in different order are still treated as
unanimous. In tabular exports each entry is flattened as `parent/child`
and joined with `;` (e.g. `fire/underhood;leakage/fuel`). In finetune
JSONL the entries are emitted as a JSON array of objects verbatim — the
downstream model learns the structured shape directly.

### Labeling strategy

A `labeling:` block controls how the next record is picked for a given
labeler. Both strategies honor the cap-at-N rule — a record stops being
offered once it has `reconciliation.min_labelers` submissions total,
regardless of who submitted.

- `queue` (default) — `ORDER BY <queue_sort>` ascending. Deterministic,
  makes "60% through the pile" obvious.
- `random` — `ORDER BY RANDOM()`. Spreads work when many labelers
  contend for the head of the queue.

There is **no record-level reservation/lock**. With bounded
`labelers_per_record` and small user counts the worst case is one record
overshooting by one. Add row-level locking later if many labelers ever
need to contend simultaneously.

### Reconciliation

- `min_labelers` — cap-at-N. Same value as the `labelers_per_record`
  cookiecutter prompt; `scripts/generate.sh` keeps them in sync.
- `rule: strict_equality` — two submissions agree on a field iff **every
  value** (including every element of a multi_select list) is
  identical. Any diff anywhere means the record is contested. This is
  deliberately conservative; it's the only rule implemented.
- `unanimous_auto_gold: true` — if every field is unanimous, the record
  bypasses the supervisor queue entirely.

### Export block

Drives the export notebook's defaults:

- `tabular.formats` — a hint for documentation; the notebook's
  `ModelParams.formats` is the actual runtime knob.
- `finetune.shape` — `chat_messages` (unsloth's standard) or `alpaca`
  (instruction/input/output triples).
- `finetune.system_prompt` — the system message in every training
  example. Domain-specific instructions go here.
- `finetune.user_template` — template for the user turn. `{text}` is
  substituted with the record's `text_field`.
- `finetune.assistant_template` — template for the assistant turn.
  `{labels_json}` is substituted with a **compact, key-sorted JSON
  string** of the gold values. The assistant emits JSON text, and the
  compact separators (`,` / `:` with no spaces) are chosen on purpose to
  reduce token count and formatting variance.

---

## Notebooks

### `notebooks/export.py`

Reads the labeling SQLite, computes the gold set (unanimous submissions
**∪** supervisor reconciliations), and writes exports. Runs in two modes:

- **Interactive** — `marimo edit notebooks/export.py` opens a form at
  the top of the notebook where you pick `limit`, `include_contested`,
  `formats`, and `shape`. Click submit and the downstream cells rerun.
- **Script** — `python notebooks/export.py [-- --key value ...]` runs
  end-to-end against `ModelParams` defaults (or CLI overrides).

The params are a Pydantic `BaseModel`, so the form and the CLI share a
schema:

| param               | type                           | default                                 | purpose                                                       |
| ------------------- | ------------------------------ | --------------------------------------- | ------------------------------------------------------------- |
| `limit`             | `int \| None`                  | `None`                                  | Cap on gold records exported. `0` on CLI means no cap.        |
| `include_contested` | `bool`                         | `False`                                 | Also emit contested records as a separate triage JSONL.       |
| `formats`           | `list[str]`                    | `["csv","json","parquet","jsonl"]`      | Which serialization formats to write. JSONL covers both flavors. |
| `shape`             | `"chat_messages" \| "alpaca"`  | `"chat_messages"`                       | Finetune output shape.                                        |

Outputs land under `exports/`:

```
exports/
├── tabular/
│   ├── <slug>.csv
│   ├── <slug>.json
│   ├── <slug>.parquet
│   └── <slug>.jsonl
├── finetune/
│   └── <slug>.chat_messages.jsonl      # or .alpaca.jsonl
└── contested/                          # only if include_contested=True
    └── <slug>.contested.jsonl
```

The gold set = **every record with a unanimous set of submissions**
unioned with **every record that has a `reconciliations` row**. When a
record is both, the reconciliation wins (supervisor is authoritative).
The gold tabular DataFrame has one row per gold record with columns
`id`, the text field, all display fields, and one `label_<field>` column
per label field (multi_select lists are joined with `;`).

The finetune JSONL shape for `chat_messages`:

```json
{"messages":[
  {"role":"system","content":"<system_prompt from YAML>"},
  {"role":"user","content":"<record text>"},
  {"role":"assistant","content":"{\"failure_location\":\"front\",...}"}
]}
```

The assistant `content` is a **compact, key-sorted JSON string**. The
escape slashes you see in the file are a JSON-in-JSON serialization
artifact — when the training pipeline does `json.loads(line)` the
`content` comes back as a clean Python string with no escaping.

### `notebooks/analysis.py`

Read-only diagnostic notebook. Surfaces the questions you'll actually
ask about a labeling run:

- **Record status breakdown** — gold / reconciled / contested / partial
  counts.
- **Per-user gold contribution** — for each labeler, how many of their
  submissions landed on records that became unanimous gold. The honest
  "is this labeler dragging records into the contested bucket" metric.
- **Pairwise agreement matrix** — for every user pair, the fraction of
  their co-labeled records that reached gold. Low cells flag pair-level
  friction.
- **Per-field agreement rate** — for each label field, what fraction of
  records where ≥ N labelers submitted reach unanimity on that specific
  field. Lowest rate first — those are the fields the schema is
  struggling with.
- **Per-field confusion pairs** — for each `single_select` / `ordinal`
  field, the most common `(value_A, value_B)` disagreement pairs on
  contested records. Tells you *what* labelers are confusing.
- **Contested records triage** — a table sorted worst-first by number of
  fields in disagreement, showing the narrative snippet + each
  labeler's picks side-by-side. The "start here" view for a supervisor.
- **Label distribution histograms** — per field, so class imbalance is
  obvious.

The notebook is read-only on purpose — actual reconciliation writes live
in the Datasette plugin, not here. The notebook is for *understanding*
the population; the plugin is for *acting* on it.

Run it as a script (same pattern as `export.py`):

```bash
FLYWHEEL_CONFIG=path/to/flywheel.yaml python notebooks/analysis.py
```

Or interactively: `marimo edit notebooks/analysis.py`.

---

## Scripts reference

| Script                          | Purpose                                                                                     |
| ------------------------------- | ------------------------------------------------------------------------------------------- |
| `scripts/generate.sh <slug>`    | Runs `cookiecutter` against `examples/<slug>/flywheel.yaml`, copies the YAML over the rendered placeholder, and loads `examples/<slug>/data.csv` (or `fake_data/sample.csv`) into `data/labeling.db` via `sqlite-utils insert --csv --detect-types --pk=id`. |
| `scripts/serve.sh <project_dir> [port]` | Starts Datasette on the generated project with `--plugins-dir plugin` and the right `FLYWHEEL_CONFIG` env var. Defaults to port 8001. |
| `scripts/sample_nhtsa.py`       | Reservoir-samples N records from `data/FLAT_CMPL.txt` (the NHTSA consumer complaints flat file, 1.5 GB, 2.2M rows, tab-delimited, 49 fields). Streams the file, filters out records with narratives shorter than 60 chars, and writes a compact CSV with a curated column subset. See [`data/CMPL_SCHEMA.txt`](data/CMPL_SCHEMA.txt). |
| `scripts/simulate_labelers.py`  | Posts synthetic labels to a running datasette instance. 5 users × 20 records each, pair-assigned across all 10 user-pairs (so each user labels 20 records and every record gets exactly 2 labels). Uses heuristic keyword matching over the narrative + metadata to pick "canonical" labels, then perturbs each field with configurable probability (`--perturb`, default 0.15) so you get a realistic mix of gold and contested records. |
| `fake_data/generate.py`         | Deterministic fake-data generator (seeded). Produces `fake_data/sample.csv` with ~25 varied columns, used by the `vehicle_safety` example. |

### Makefile reference

```
make help           # print all targets
make venv           # install dev deps via uv into .venv/
make data           # run fake_data/generate.py → fake_data/sample.csv
make gen            # generate EXAMPLE (default: vehicle_safety) into _tmp_output/
make serve          # start Datasette against _tmp_output/<example>/ on port $PORT
make dev            # clean + gen + serve
make clean          # wipe _tmp_output/
make nuke           # clean + remove .venv
```

Overrides:

```bash
make EXAMPLE=nhtsa_complaints PORT=8005 dev
```

---

## Examples included

Two examples ship in `examples/`:

### `vehicle_safety` (the hello-world)

Synthetic fake data (40 rows generated by `fake_data/generate.py`). Use
this for iteration on the template itself — fast regen, fast smoke test.
The YAML has five label fields (`failure_location`, `failure_type`,
`vehicle_system`, `hazard_tags` multi-select 1–3, `severity` 1–5).

```bash
make gen && make serve
```

### `nhtsa_complaints` (the real one)

100 real NHTSA consumer safety complaints sampled from the public
`FLAT_CMPL.txt` file (2.2M rows total). The YAML expands the hazard
taxonomy and `vehicle_system` choices to match NHTSA's world (including
`fuel_system`, `cooling`, `transmission`, `restraints`, `tires`,
`exhaust`, `electrical_system`). Display fields show the NHTSA
component description, make/model/year, crash/fire flags, injury and
death counts, mileage, and state.

To (re-)sample from the NHTSA file:

```bash
# download FLAT_CMPL.txt and CMPL_SCHEMA.txt into data/ first
python scripts/sample_nhtsa.py --n 100 --seed 42 \
    --input data/FLAT_CMPL.txt \
    --output examples/nhtsa_complaints/data.csv
```

Full end-to-end smoke test with simulated labelers:

```bash
make EXAMPLE=nhtsa_complaints dev    # terminal 1: server on port 8001

# terminal 2:
python scripts/simulate_labelers.py \
    --url http://localhost:8001 \
    --config _tmp_output/nhtsa_complaints/flywheel.yaml \
    --db    _tmp_output/nhtsa_complaints/data/labeling.db

# then walk into the reconciliation queue in the browser:
# http://localhost:8001/flywheel/reconcile?supervisor=sam

# and finally, export:
cd _tmp_output/nhtsa_complaints/notebooks
FLYWHEEL_CONFIG=../flywheel.yaml python export.py
```

---

## Data model

The labeling state is **always** modeled as a many-to-many between three
tables. This is non-negotiable.

```
            ┌───────────┐           ┌─────────────────────┐          ┌───────────┐
            │  records  │◀──────────│    submissions      │─────────▶│   users   │
            │           │ record_id │                     │ username │           │
            │ id (PK)   │           │ id (PK)             │          │ username  │
            │ narrative │           │ record_id FK        │          │ role      │
            │ ...       │           │ username FK         │          │ created_at│
            └─────┬─────┘           │ submitted_at        │          └─────▲─────┘
                  │                 │ values_json (dict)  │                │
                  │                 │ UNIQUE(record_id,   │                │
                  │                 │        username)    │                │
                  │                 └─────────────────────┘                │
                  │                                                        │
                  │                 ┌─────────────────────┐                │
                  └────────────────▶│   reconciliations   │────────────────┘
                            record_id                      supervisor
                                    │ record_id PK, FK    │
                                    │ supervisor FK       │
                                    │ reconciled_at       │
                                    │ values_json (dict)  │
                                    └─────────────────────┘
```

- **`records`** — the unlabeled text rows. Loaded from a CSV at generate
  time via `sqlite-utils insert --csv --detect-types --pk=id`. The
  source schema is whatever the CSV has; the YAML names the `id_field`,
  `text_field`, and optional `display_fields`.
- **`users`** — every human who has touched the system. Columns:
  `username` (PK), `role` (`labeler` | `supervisor`), `created_at`.
  Auto-populated on first submission or first reconciliation.
- **`submissions`** — the M2M join between records and users. One row
  per `(record_id, username)`, holding that user's field values as a
  JSON blob in `values_json`. `UNIQUE(record_id, username)` prevents a
  labeler from double-submitting the same record.
- **`reconciliations`** — the supervisor's final word on contested
  records. Primary-keyed on `record_id` so there's exactly one
  reconciliation per record. FK-referenced from the supervisor
  `username`. `values_json` holds the chosen gold values.

Foreign keys are **enforced at runtime** via a `prepare_connection`
Datasette plugin hook that runs `PRAGMA foreign_keys = ON` on every
connection. If you try to insert a submission or reconciliation with a
bogus `record_id`, SQLite rejects it.

**Anti-pattern (do not do this):** adding per-user columns to `records`
(`label_alice`, `label_bob`, ...). It does not scale, breaks the schema
every time someone joins, and makes the "show me Alice's history across
the whole app" view miserable. The M2M shape gives that view for free.

The `labelers_per_record` cookiecutter parameter bounds N per project at
scaffold time. Bounding N per project means we never need to support an
unbounded labeler count inside a single project — the M2M scales fine.

---

## Authentication

Authentication is delegated to
[`datasette-auth-passwords`](https://datasette.io/plugins/datasette-auth-passwords),
a Datasette plugin that serves a login page at `/-/login`, verifies
PBKDF2-hashed passwords from `metadata.yml`, and sets a signed
`ds_actor` cookie via Datasette's built-in actor machinery. Every
`/flywheel/*` route in the plugin calls `_require_actor(request)` at
the top and redirects anonymous users to `/-/login`.

**The user manifest.** Each example declares its users in plaintext at
`examples/<slug>/users.yaml`:

```yaml
users:
  - { username: alice, password: wonderland,  role: labeler }
  - { username: bob,   password: bobsecret,   role: labeler }
  - { username: sam,   password: supervisor,  role: supervisor }
```

Plaintext is on purpose — this is a per-example seed, not a production
secret store. Every generated project ships with the same set of
example credentials; swap them out or override `metadata.yml` out of
band before exposing an instance to untrusted users.

**The bootstrap step.** `scripts/generate.sh` invokes
`scripts/bootstrap_auth.py` which does two things:

1. Hashes every password via
   `datasette_auth_passwords.utils.hash_password` (PBKDF2-SHA256,
   480k iterations) and writes
   `_tmp_output/<slug>/metadata.yml` with:

   ```yaml
   plugins:
     datasette-auth-passwords:
       alice_password_hash: pbkdf2_sha256$480000$...
       bob_password_hash:   pbkdf2_sha256$480000$...
       sam_password_hash:   pbkdf2_sha256$480000$...
       actors:
         alice: { id: alice, role: labeler }
         bob:   { id: bob,   role: labeler }
         sam:   { id: sam,   role: supervisor }
   ```

   The `actors` map gives each authenticated user a `role` field on
   their actor dict so `request.actor["role"]` is available (the
   plugin's default actor is just `{"id": username}`).

2. Seeds the `users` table in `data/labeling.db` with one row per
   user so foreign keys from `submissions` and `reconciliations`
   resolve cleanly from the very first request — no lazy
   auto-creation needed.

`scripts/serve.sh` passes `--metadata metadata.yml` to `datasette
serve` whenever a metadata file is present.

**Inside the plugin**, the old URL-query-parameter approach
(`?labeler=alice`, `?supervisor=sam`) is gone. Every route reads the
authenticated username from `request.actor["id"]` via a small
`_require_actor` helper. Forms no longer carry hidden `labeler` /
`supervisor` fields — the cookie is the source of truth. A "log out"
link on the home page and the labeling form points at Datasette's
built-in `/-/logout`.

**Role-based authorization is intentionally minimal.** The
`users.role` column and the `actor.role` field exist, but the plugin
doesn't currently *enforce* role-based access (any authenticated user
can hit the reconciliation queue). For a trusted internal team that's
fine; tighten it when you need to.

## Compliance test suite

The `tests/compliance/` directory holds a pytest suite that regenerates
the `vehicle_safety` example into a pytest tmpdir, starts Datasette on
port 8099, logs in as `alice` / `sam` / etc., and asserts every piece
of the generated project behaves correctly.

Run with:

```bash
make test
```

Coverage areas:

- **`test_generation.py`** — expected files/dirs exist after
  `scripts/generate.sh` runs; `records` table is populated; the
  plugin's startup hook creates `users`/`submissions`/`reconciliations`;
  PRAGMA foreign_keys is enforced per connection (a POST with a bogus
  `record_id` returns HTTP 500 with a FK violation).
- **`test_rendering.py`** — every field kind in `flywheel.yaml`
  produces the right form element (single_select → `<select>`,
  multi_select → checkboxes, ordinal → radio chips, free_text →
  `<textarea>`, hierarchical → `.hier-group` with the right parent
  options); ordinal endpoint labels are rendered; the JS helper
  functions (`flywheelAddEntry` etc.) are included on every page.
- **`test_submission.py`** — valid submissions persist; required-field
  validation fires; hierarchical child-belongs-to-parent validation
  fires; cap-at-N correctly skips a record saturated by two other
  labelers when a third labeler hits `/flywheel/label`.
- **`test_reconciliation.py`** — contested records appear in the
  pending queue; the detail page has the Custom override column with
  all the expected widgets; submitting writes a `reconciliations` row
  and flips the tab counts; custom override with an invalid child is
  rejected; reviewed-record detail page pre-fills labeler radios and
  custom widgets with the existing gold values; undo deletes the row
  and returns the record to pending; anonymous requests to
  `/flywheel/*` redirect to `/-/login`.
- **`test_notebooks.py`** — both `notebooks/export.py` and
  `notebooks/analysis.py` run cleanly in script mode against a seeded
  database, and `export.py` writes every expected tabular + finetune
  output file.

**Fixtures** (in `tests/compliance/conftest.py`):

| fixture              | scope    | what it gives you                                                         |
| -------------------- | -------- | ------------------------------------------------------------------------- |
| `project_dir`        | session  | Regenerated project at a pytest tmp path (`OUT_DIR` env override).        |
| `datasette_server`   | session  | Live Datasette URL on port 8099 with `--metadata metadata.yml`.           |
| `client`             | function | httpx.Client logged in as `alice` (labeler).                              |
| `supervisor_client`  | function | httpx.Client logged in as `sam` (supervisor).                             |
| `anon_client`        | function | httpx.Client with no login — for redirect-to-login assertions.            |
| `clean_state`        | function | sqlite3.Connection; truncates submissions/users/reconciliations pre-test. |

**Helpers** (`conftest.py`):

- `form_post(client, url, pairs)` — posts an
  `application/x-www-form-urlencoded` body from a list of `(key,
  value)` tuples, supporting multi-value params (the same key
  appearing more than once). Works around an httpx quirk where
  `data=[(...)]` is interpreted as raw content.
- `new_client(base_url, username, password)` — context manager that
  yields an httpx.Client pre-logged-in as the given user. Used by
  tests that need to submit from multiple distinct users in a single
  test function.
- `submit_label(client, record_id, **overrides)` — posts a valid
  labeling submission for the given record, with sensible defaults
  for every required field. Kwargs override individual fields; list
  kwargs become multi-value form entries.

## Architecture decisions (locked in)

These are durable choices carried across iterations. If you're adding a
feature, don't re-litigate these without a strong reason.

- **One Datasette plugin** owns every flywheel route (labeling,
  reconciliation, users index). Not split into modules. Easier to read
  top-to-bottom, easier to ship.
- **M2M data model** for labels (`records × users × submissions`). Per-
  user columns on `records` is the boneheaded anti-pattern.
- **`labelers_per_record` is a cookiecutter parameter**, threaded into
  `reconciliation.min_labelers` in the generated YAML. One source of
  truth per project, bounded at scaffold time.
- **Reconciliation rule:** strict equality across every value at every
  level. Any diff = contested. This is intentionally conservative; it
  keeps the "is this a diff?" logic simple enough to render cleanly in
  the UI.
- **Unanimous agreement auto-promotes to gold** without a supervisor
  step. Supervisor only sees contested records.
- **`max_selections` is hard-enforced server-side** on every submission,
  not a soft warning.
- **Severity-style ordinals render as numbered chips** (not sliders, not
  radios). Confirmed by user preference.
- **Reconciliation UI highlights exactly where labelers differ.**
  Agreement rows grayed and pre-selected, diff rows highlighted with a
  warm background. This is load-bearing, not cosmetic — the user was
  explicit that the production pain point is *finding* diffs fast, not
  deciding once you see them.
- **Data-science work lives in marimo notebooks**, not `.py` scripts.
  `export.py` and `analysis.py` are both authored interactively via
  `marimo edit` and ship as the same file you edit and the same file
  you run as a CLI.
- **Finetune targets are one multi-target instruct LLM**, not N
  specialists. The export notebook emits one JSON object per record
  covering every label field; downstream fine-tuning projects train a
  single model to predict the whole thing in one shot.
- **Compact JSON in finetune assistant turn.** `json.dumps` uses
  `separators=(",", ":")` and `sort_keys=True` — fewer tokens, less
  formatting variance for the model to learn, deterministic output
  across runs.
- **Finetune is out of scope for this project.** Flywheel produces the
  labeled JSONL; training a model on it is a separate downstream
  project's responsibility.
- **Auth deferred.** When needed, bolt on an existing `datasette-auth-*`
  plugin rather than roll our own. Today every labeler name is just a
  URL query parameter, which is fine for a small team behind a VPN.

---

## Known gaps / future work

- **Role-based authorization** — today every authenticated user can
  reach every `/flywheel/*` route. Enforcing labelers-can't-reconcile
  and supervisors-can't-label would be a small `_require_role` helper
  per route.
- **Real secret handling** — plaintext passwords in `users.yaml` are
  fine for per-example seeding but not for production. Swap in a
  password-hash-only manifest or a different Datasette auth plugin
  when deploying externally.
- **Hosting docs** — a generated project is just a Datasette instance
  with a plugin dir, a metadata file, and a SQLite database; any
  Datasette hosting story works, but we haven't written one down.
- **More examples** — easy to add. Drop `examples/<slug>/flywheel.yaml`,
  `examples/<slug>/data.csv`, and `examples/<slug>/users.yaml` and
  `make EXAMPLE=<slug> gen`.
