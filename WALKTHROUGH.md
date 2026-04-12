# flywheel · end-to-end verification walkthrough

A step-by-step runbook to prove every piece of flywheel works. Follow it
linearly — each step has a command (or browser action) and what you
should see. Expected outputs are shown truncated to the interesting
lines so you can eyeball-check without drowning in noise.

Rough timing from a cold checkout:

- Part 1 (prereqs + fake data): **~3 min**
- Part 2 (hello-world labeling, reconciliation, export): **~10 min**
- Part 3 (compliance test suite): **~1 min**
- Part 4 (NHTSA real-data simulation): **~5 min** (plus however long
  downloading `FLAT_CMPL.txt` takes)

All commands assume your working directory is the repository root
(`/home/brojonat/projects/flywheel` in the original environment, or
wherever you cloned). Where a step expects you in a different directory
it's called out explicitly with a `cd`.

---

## Part 1 — Prereqs

### 1.1 · Install dev dependencies

```bash
make venv
```

What you should see:
- `uv` resolves ~75 packages, installs into `.venv/`
- No errors. If you don't have `uv` installed yet, install it from
  [astral.sh/uv](https://astral.sh/uv) first.

Sanity check the installed CLIs:

```bash
.venv/bin/datasette --version
.venv/bin/sqlite-utils --version
.venv/bin/cookiecutter --version
.venv/bin/pytest --version
```

Expected: all four commands print a version line without crashing.

### 1.2 · Generate fake synthetic data

```bash
make data
```

Expected tail:

```
wrote 40 rows × 25 cols → /home/.../flywheel/fake_data/sample.csv
```

This is the source CSV the hello-world `vehicle_safety` example uses.
Real NHTSA data is handled in Part 4.

### 1.3 · Clean any prior state

```bash
make clean
```

Wipes `_tmp_output/`. Safe to run any time you want a fresh generation.

---

## Part 2 — Hello-world end-to-end (`vehicle_safety`)

### 2.1 · Generate the project

```bash
make EXAMPLE=vehicle_safety gen
```

Expected tail:

```
==> cookiecutter scaffold → .../vehicle_safety
    project_name=Vehicle Safety Labeling (Hello World) labelers_per_record=2
==> copy example flywheel.yaml
==> load fake CSV → .../data/labeling.db (table: records, pk: id)
==> bootstrap auth (hash users.yaml → metadata.yml, seed users table)
==> wrote vehicle_safety/metadata.yml with 4 user(s)
==> seeded users table with 4 row(s)

✓ generated: .../_tmp_output/vehicle_safety
```

### 2.2 · Verify the generated layout

```bash
find _tmp_output/vehicle_safety -type f | sort
```

Expected (order may vary slightly):

```
_tmp_output/vehicle_safety/data/.gitkeep
_tmp_output/vehicle_safety/data/labeling.db
_tmp_output/vehicle_safety/flywheel.yaml
_tmp_output/vehicle_safety/metadata.yml
_tmp_output/vehicle_safety/notebooks/.gitkeep
_tmp_output/vehicle_safety/notebooks/analysis.py
_tmp_output/vehicle_safety/notebooks/export.py
_tmp_output/vehicle_safety/plugin/__init__.py
_tmp_output/vehicle_safety/plugin/flywheel_plugin.py
```

Every file is essential: `flywheel.yaml` is the source of truth,
`metadata.yml` carries the auth config, `plugin/` is the Datasette
plugin, `notebooks/` has export + analysis, and `data/labeling.db` is
the pre-loaded records table.

Quick schema check:

```bash
.venv/bin/sqlite-utils tables _tmp_output/vehicle_safety/data/labeling.db --counts
```

Expected:

```json
[{"table": "records", "count": 40},
 {"table": "users", "count": 4}]
```

40 records loaded from the fake CSV, and 4 users seeded by
`bootstrap_auth.py` (alice, bob, carol, sam). The `submissions` and
`reconciliations` tables don't exist yet — the plugin creates them on
Datasette's first startup.

Look at the auth config:

```bash
cat _tmp_output/vehicle_safety/metadata.yml
```

You should see a `plugins.datasette-auth-passwords` block with one
`<user>_password_hash` entry per user (PBKDF2 hashes, starting with
`pbkdf2_sha256$480000$...`) and an `actors` map binding each username
to `{id, role}`.

### 2.3 · Start the server

Open a **second terminal** and run:

```bash
make serve
```

Expected tail:

```
==> datasette serving on http://localhost:8001/flywheel
    log in at http://localhost:8001/-/login
```

Leave this terminal running. All remaining Part 2 steps happen either in
a browser or from your original terminal.

### 2.4 · Log in as alice in the browser

Open <http://localhost:8001/flywheel> in your browser.

Expected: Datasette redirects you to `/-/login` (the auth redirect from
`_require_actor`).

Type:

- **Username**: `alice`
- **Password**: `wonderland`

Click **Log in**. You should land on Datasette's root page. Now visit
<http://localhost:8001/flywheel>.

Expected:

- Heading: `Vehicle Safety Labeling (Hello World)`
- Small muted line: `Signed in as alice · log out`
- A **Start labeling →** link
- Footer links: Supervisor reconciliation queue · Labelers & history ·
  Datasette DB browser · submissions table

### 2.5 · Label record 1 as alice

Click **Start labeling →**. You should see:

- Heading: `Vehicle Safety Labeling (Hello World)`
- Progress line: `labeler: alice · record #1 · log out`
- A metadata grid with Submitted / Make / Model / Year / Region /
  Reporter severity rows
- The complaint narrative in a gray blockquote
- A form with one widget per field in `flywheel.yaml`:
  - **Location of failure** — dropdown
  - **Type of failure** — dropdown
  - **Affected vehicle system** — dropdown
  - **Severity** — a row of numbered chips 1–5 with endpoint labels
    (`1=Negligible · 5=Catastrophic`)
  - **Hazard → subhazard** — a cascading pair of dropdowns with a
    **+ Add entry** button. Pick a parent hazard; watch the child
    dropdown populate on the fly (client-side JS — no page reload).
  - **Analyst notes** — a textarea

Fill in:

- Location of failure: `front`
- Type of failure: `mechanical`
- Affected vehicle system: `brakes`
- Severity: `3`
- Hazard → subhazard: pick **Fire → Under-hood fire**
- Analyst notes: `test note from alice`

Click **Submit →**. You should be redirected to the same URL, now
showing record #2 (the next one alice hasn't labeled).

### 2.6 · Log out, log in as bob, label record 1 contested

Click **log out** in the top-right, then open
<http://localhost:8001/-/login> and sign in as `bob` / `bobsecret`.

Visit <http://localhost:8001/flywheel/label>. You should see **record
#1** (bob hasn't labeled it yet — it still has only 1 submission, below
the cap-at-N of 2).

Fill in the form so you **disagree with alice** on the hazard —
everything else can match:

- Location of failure: `front`
- Type of failure: `mechanical`
- Affected vehicle system: `brakes`
- Severity: `4`  ← different from alice
- Hazard → subhazard: **Collision risk → Brake failure**  ← different
- Analyst notes: `test note from bob`

Submit. You'll land on record #2 (record 1 is now at N=2 submissions and
won't be offered again).

### 2.7 · Verify submissions in the DB

Back in your original terminal:

```bash
.venv/bin/sqlite-utils _tmp_output/vehicle_safety/data/labeling.db \
  'SELECT username, values_json FROM submissions WHERE record_id=1' --json-cols
```

Expected: two rows, one for alice and one for bob, with their
respective `values_json` payloads. Both have a `"hazards"` list of dicts
like `[{"hazard": "fire", "subhazard": "underhood"}]` — confirming the
hierarchical picker stored list-of-dicts correctly.

### 2.8 · Log in as sam the supervisor

Browser: log out, then log in as `sam` / `supervisor`.

Visit <http://localhost:8001/flywheel/reconcile>.

Expected:

- Tab row: `Pending (1) · Reviewed (0)` (Pending tab active)
- Queue table with one row: `#1` · `2` submissions · `2 / 6` diffs (the
  fields they disagreed on) · `severity, hazards` in the diff column

### 2.9 · Open the reconciliation detail page

Click `#1` in the queue. You should see:

- Heading: `Reconcile record #1`
- Muted progress: `1 of 1 (pending)`
- The record's metadata grid and narrative blockquote (context for the
  supervisor)
- The reconciliation table with columns:
  - **Field** (left header)
  - **alice** (labeler column)
  - **bob** (labeler column)
  - **Custom override** (rightmost, light blue background)

The **agree rows** (the four fields alice and bob matched on) are light
gray and italicized with alice's radio pre-selected. The **diff rows**
(`severity`, `hazards`) have a pale-yellow background and no radio
pre-selected — you have to click to confirm.

The Custom override column has a schema-driven widget in every row:
dropdown for single_select, numbered chips for severity, textarea for
notes, and a full hierarchical picker for hazards (with its own **+
Add entry** button).

### 2.10 · Reconcile with a custom override

Scenario: neither alice nor bob got the hazard right. You want to pick
alice's severity but override the hazards with your own:

- Severity row: click **alice** (severity=3)
- Hazards row: click **custom value** in the Custom override column,
  then in the widget:
  - First entry: Hazard=`Leakage`, Subhazard=`Brake-fluid leak`
  - Click **+ Add entry**
  - Second entry: Hazard=`Stalling`, Subhazard=`Highway-speed stall`

(The agree rows can stay as alice. Whichever radio is pre-selected is
fine.)

Click **Accept & next →**.

Expected: the page redirects to the queue index (no more pending
records). In the DB:

```bash
.venv/bin/sqlite-utils _tmp_output/vehicle_safety/data/labeling.db \
  'SELECT * FROM reconciliations' --json-cols
```

You should see one row with `supervisor="sam"`, the agreed-on labeler
values (alice's picks) for most fields, and `hazards` =
`[{"hazard":"leakage","subhazard":"brake_fluid"},{"hazard":"stalling","subhazard":"highway"}]`
— the custom override you just typed in, NOT alice's or bob's
submission.

### 2.11 · Check the Reviewed tab + pre-fill

Visit <http://localhost:8001/flywheel/reconcile?view=reviewed>.

Expected:

- Tab row: `Pending (0) · Reviewed (1)` (Reviewed tab active)
- Queue row: `#1` · `sam` · timestamp

Click `#1`.

Expected:

- Heading is now `Review record #1` (not "Reconcile")
- Green banner: `Already reconciled by sam at <timestamp>`
- Labeler radios pre-selected on the agreement rows
- `__custom__` radio pre-selected on the hazards row, with the
  `Leakage → Brake-fluid leak` and `Stalling → Highway-speed stall`
  entries rendered in the widget (parent + child both pre-selected)
- Primary button says **Save changes →** (not "Accept & next")
- Next to it: **Undo reconciliation** (red-bordered)

### 2.12 · Test undo

Click **Undo reconciliation**. Confirm the dialog.

Expected: you land back on the record 1 detail page, but the title is
now `Reconcile record #1` again (reviewed → pending). The banner is
gone. In the DB:

```bash
.venv/bin/sqlite-utils _tmp_output/vehicle_safety/data/labeling.db \
  'SELECT COUNT(*) FROM reconciliations'
```

Expected: `0`. The row was deleted.

### 2.13 · Re-reconcile (redo)

Fill in the detail page again — this time you can pick differently or
pick the same custom values. Click **Accept & next →**.

Now:

```bash
.venv/bin/sqlite-utils _tmp_output/vehicle_safety/data/labeling.db \
  'SELECT record_id, supervisor FROM reconciliations'
```

Expected: one row, `record_id=1`, `supervisor=sam`. Reconcile → Undo →
Reconcile works cleanly, proving `INSERT OR REPLACE` semantics on the
primary key.

### 2.14 · Run the export notebook in script mode

Back in your original terminal:

```bash
cd _tmp_output/vehicle_safety/notebooks
FLYWHEEL_CONFIG="$(pwd)/../flywheel.yaml" \
  ../../../.venv/bin/python export.py
cd -
```

Expected: no errors, no output (the notebook's final cell is an
`mo.md` summary which only renders in the marimo UI).

List the exports:

```bash
find _tmp_output/vehicle_safety/exports -type f | sort
```

Expected files:

```
_tmp_output/vehicle_safety/exports/finetune/vehicle_safety.chat_messages.jsonl
_tmp_output/vehicle_safety/exports/tabular/vehicle_safety.csv
_tmp_output/vehicle_safety/exports/tabular/vehicle_safety.json
_tmp_output/vehicle_safety/exports/tabular/vehicle_safety.jsonl
_tmp_output/vehicle_safety/exports/tabular/vehicle_safety.parquet
```

Inspect the gold finetune example for record 1:

```bash
.venv/bin/python3 -c "
import json
with open('_tmp_output/vehicle_safety/exports/finetune/vehicle_safety.chat_messages.jsonl') as f:
    ex = json.loads(f.readline())
print(ex['messages'][-1]['content'])
"
```

Expected: a compact JSON string (no spaces after colons/commas) with
the fields sam picked during reconciliation — including the custom
hazards list-of-dicts. Example:

```
{"failure_location":"front","failure_type":"mechanical","hazards":[{"hazard":"leakage","subhazard":"brake_fluid"},{"hazard":"stalling","subhazard":"highway"}],"notes":"test note from alice","severity":3,"vehicle_system":"brakes"}
```

The custom override sam entered in the browser is now part of the
fine-tuning corpus as the assistant's target output. **This is the
punchline of the whole loop.**

### 2.15 · Run the analysis notebook in script mode

```bash
cd _tmp_output/vehicle_safety/notebooks
FLYWHEEL_CONFIG="$(pwd)/../flywheel.yaml" \
  ../../../.venv/bin/python analysis.py
cd -
```

Expected: no errors, no output. The notebook runs through the status
breakdown, per-user gold contribution, pairwise agreement, per-field
confusion pairs, contested triage, and label distribution cells
without crashing. (To actually see the charts, open it with
`.venv/bin/marimo edit _tmp_output/vehicle_safety/notebooks/analysis.py`
instead.)

### 2.16 · Stop the dev server

In the terminal running `make serve`, hit `Ctrl-C`.

---

## Part 3 — Compliance test suite

```bash
make test
```

Expected tail:

```
tests/compliance/test_reconciliation.py::test_undo_moves_record_back_to_pending PASSED
tests/compliance/test_reconciliation.py::test_anonymous_request_redirects_to_login PASSED
tests/compliance/test_rendering.py::test_home_page_renders PASSED
...
======================== 23 passed, 1 skipped in ~6s ==========================
```

The suite regenerates the project from scratch into a pytest tmpdir,
starts Datasette on port 8099 with `--metadata metadata.yml`, logs in as
alice / sam / anonymous, and asserts: file layout, FK enforcement, every
field kind renders, validation fires, cap-at-N works, reconciliation
flow (pending / detail / submit / custom / undo / review), anonymous
redirect, and both notebooks run in script mode producing every
expected export file. The one skipped test documents a known gap
(ordinal range enforcement on the labeling path).

If anything fails here, **stop and read the output** before moving on
— a red test is a real regression.

---

## Part 4 — Real data (NHTSA)

Skip this section if you just want to verify the hello-world path.
It's the "does this work on real complaints, not synthetic noise" lap.

### 4.1 · Download the NHTSA flat file

The simulator needs NHTSA's `FLAT_CMPL.txt` — their full consumer
complaints export, roughly 1.5 GB and 2.2M rows.

- Schema reference is already in `data/CMPL_SCHEMA.txt` (49 fields).
- Download the current `FLAT_CMPL.zip` from
  <https://www.nhtsa.gov/nhtsa-datasets-and-apis> → Complaints → Flat
  File, unzip, and drop `FLAT_CMPL.txt` into `data/`.

Sanity check:

```bash
ls -lh data/FLAT_CMPL.txt
wc -l data/FLAT_CMPL.txt
```

Expected: ~1.5 GB, ~2.2 million lines.

### 4.2 · Sample 100 records

```bash
.venv/bin/python scripts/sample_nhtsa.py --n 100 --seed 42 \
  --input data/FLAT_CMPL.txt \
  --output examples/nhtsa_complaints/data.csv
```

Expected: a progress stream reporting ~2.2M lines scanned, the final
line shows:

```
done: 2,194,682 lines scanned, 2,076,760 eligible, 0 malformed, 100 kept
wrote 100 rows → examples/nhtsa_complaints/data.csv
```

Takes 30–90 seconds depending on your disk. The sampler uses reservoir
sampling with a seed, so the same seed gives the same 100 records every
run.

Quick sanity check on the CSV shape:

```bash
head -1 examples/nhtsa_complaints/data.csv | tr ',' '\n' | head -5
wc -l examples/nhtsa_complaints/data.csv
```

Expected: first column is `id`, the header has 21 columns, and the file
has 101 lines (1 header + 100 rows).

### 4.3 · Generate the nhtsa_complaints project

```bash
make clean
make EXAMPLE=nhtsa_complaints gen
```

Expected tail:

```
==> cookiecutter scaffold → .../nhtsa_complaints
==> copy example flywheel.yaml
==> load fake CSV → .../data/labeling.db (table: records, pk: id)
==> bootstrap auth (hash users.yaml → metadata.yml, seed users table)
==> wrote nhtsa_complaints/metadata.yml with 6 user(s)
==> seeded users table with 6 row(s)

✓ generated: .../_tmp_output/nhtsa_complaints
```

Note `6 users` (alice, bob, carol, dave, eve, sam) — the NHTSA example
has more labelers so the simulator can distribute 100 submissions
evenly across 5 of them.

### 4.4 · Start datasette

In a second terminal:

```bash
make EXAMPLE=nhtsa_complaints serve
```

Default port is 8001 (same as vehicle_safety). To avoid confusion if
you had the hello-world server up, use an explicit port:

```bash
make EXAMPLE=nhtsa_complaints PORT=8013 serve
```

### 4.5 · Run the simulator

In your original terminal:

```bash
.venv/bin/python scripts/simulate_labelers.py \
  --url http://localhost:8013 \
  --config _tmp_output/nhtsa_complaints/flywheel.yaml \
  --db    _tmp_output/nhtsa_complaints/data/labeling.db \
  --users examples/nhtsa_complaints/users.yaml
```

Expected:

```
loading 6 user(s): ['alice', 'bob', 'carol', 'dave', 'eve', 'sam']
labeling 50 records × 2 labelers each = 100 submissions
pair assignment rotation (10 pairs): [('alice', 'bob'), ...]
submissions: {'ok': 100, 'fail': 0}
per user:    {'alice': 20, 'bob': 20, 'carol': 20, 'dave': 20, 'eve': 20}
```

Every submission should report `ok`. If you see `fail`, check the
datasette server log for the actual error — most commonly it's an auth
failure (wrong passwords in users.yaml) or a schema mismatch.

Spot-check the DB:

```bash
.venv/bin/sqlite-utils _tmp_output/nhtsa_complaints/data/labeling.db \
  'SELECT username, COUNT(*) FROM submissions GROUP BY username'
```

Expected: 5 rows, one per labeler, each showing 20 submissions.

### 4.6 · Peek at the reconciliation queue

In your browser (log in as `sam` / `supervisor`):

<http://localhost:8013/flywheel/reconcile>

Expected: somewhere around **30–40 contested records** in the Pending
tab (the exact count depends on the simulator's `--perturb` value,
default 15% per field). Each row shows how many fields differ and which
ones. The worst records — those with the most field-level disagreement
— tend to cluster at the top.

Open any single row and you'll see the same reconciliation UI as
Part 2, but now with **real NHTSA complaint text** in the blockquote
and real display metadata (vehicle make, model, year, crash/fire flags,
component description, state, miles). This is the "does the widget
make sense on real data" check.

Reconcile a few records however you like — either pick a labeler's
values or use the Custom override column.

### 4.7 · Run the export notebook

```bash
cd _tmp_output/nhtsa_complaints/notebooks
FLYWHEEL_CONFIG="$(pwd)/../flywheel.yaml" \
  ../../../.venv/bin/python export.py
cd -
```

Expected: no errors.

```bash
find _tmp_output/nhtsa_complaints/exports -type f | sort
wc -l _tmp_output/nhtsa_complaints/exports/finetune/*.jsonl
```

Expected: all 4 tabular formats + the chat_messages JSONL. The JSONL
line count is the **gold set size** — unanimous-agreement records plus
any records you reconciled in step 4.6. A typical run produces 10–20
gold records from the simulator's 50 contested pairs.

Inspect one gold example:

```bash
.venv/bin/python3 -c "
import json
with open('_tmp_output/nhtsa_complaints/exports/finetune/nhtsa_complaints.chat_messages.jsonl') as f:
    ex = json.loads(f.readline())
msgs = ex['messages']
print('SYSTEM:', msgs[0]['content'][:120], '...')
print()
print('USER:  ', msgs[1]['content'][:200], '...')
print()
print('ASSISTANT:', msgs[2]['content'])
"
```

Expected:

- **SYSTEM**: the NHTSA-specific domain prompt from
  `examples/nhtsa_complaints/flywheel.yaml` (`You are a vehicle safety
  analyst reviewing consumer complaints filed with NHTSA...`)
- **USER**: a real NHTSA complaint narrative (customer-written, all
  caps, sometimes redacted)
- **ASSISTANT**: a compact JSON blob with the gold labels across every
  field, including the hazard_tags multi_select

This is the file you'd hand to an unsloth fine-tuning run.

### 4.8 · Stop the server

In the terminal running `make serve`, hit `Ctrl-C`.

---

## Part 5 — Iterate confidently

The iteration loop once everything works:

1. Edit `examples/vehicle_safety/flywheel.yaml` (or nhtsa).
2. `make EXAMPLE=vehicle_safety gen` — regenerates `_tmp_output/` from
   scratch.
3. `make test` — compliance suite catches regressions.
4. `make EXAMPLE=vehicle_safety serve` — smoke-test in the browser.

Or in one shot:

```bash
make EXAMPLE=vehicle_safety dev   # clean + gen + serve
```

Editing the plugin itself (`{{cookiecutter.project_slug}}/plugin/flywheel_plugin.py`)
also requires a regen — the template is `_copy_without_render`'d so
edits are copied verbatim on the next `make gen`.

---

## Troubleshooting

**Datasette won't start — "address already in use"**: another flywheel
process is still bound to the port. `ss -tlnp | grep 8001` to find it
and kill the pid.

**`/-/login` returns 200 but `ds_actor` cookie isn't set**: username or
password mismatch. Re-check `examples/<slug>/users.yaml`. For the
`vehicle_safety` example the passwords are `wonderland`, `bobsecret`,
`carolsecret`, `supervisor`.

**`sqlite3.OperationalError: no such table: submissions`** from a
notebook: the submissions table is only created when Datasette boots
the plugin. Run `make serve` at least once, then re-run the notebook.
(Both notebooks have a table-exists guard, but some older error
states can slip through.)

**Simulator reports `fail`**: the Datasette server log usually has the
reason. If it's auth-related, double-check the `--users` flag points
at a file whose passwords match the seeded ones.

**`make test` fails on the notebooks tests**: make sure you're not
running an interactive `marimo edit` session against the same file at
the same time — marimo holds the file open and the test's subprocess
invocation can deadlock.

---

## What this walkthrough proves

Ticking every checkpoint above verifies:

- **Generation**: cookiecutter scaffolds a valid project, CSV loads,
  metadata.yml is written, users table is seeded, plugin files and
  notebooks are in place.
- **Auth**: anonymous requests redirect to `/-/login`, authenticated
  requests expose `request.actor`, both the vehicle_safety and
  nhtsa_complaints users seeds work.
- **Labeling UI**: every field kind renders correctly, the hierarchical
  picker cascades client-side, multi_select min/max and hierarchical
  child-belongs-to-parent validation fire server-side.
- **Cap-at-N**: a record saturated by two labelers is no longer
  offered to a third.
- **Reconciliation**: contested records appear in the queue, the
  detail page highlights diffs, agreement rows pre-select, the custom
  override column accepts schema-driven edits, submitting writes the
  row, auto-advance works, Review tab shows reconciled records,
  reviewed-record detail pre-fills every pick, Undo removes the row
  and flips queues, re-reconcile overwrites via INSERT OR REPLACE.
- **Export**: gold records flow through the export notebook into CSV /
  JSON / Parquet / JSONL tabular formats and the unsloth-shaped chat
  messages finetune JSONL. Reconciled records' values replace
  labeler picks; the custom override reaches the final file.
- **Analysis**: the diagnostic notebook runs without errors on a
  populated DB.
- **Simulator**: logs in per user, distributes 100 submissions evenly
  across 5 labelers, and exercises the auth + cap-at-N + hierarchical
  + reconciliation paths against real NHTSA data.
- **Compliance tests**: 23 tests regress-gate the whole thing.

If you got all the way here green, the data flywheel is working.
