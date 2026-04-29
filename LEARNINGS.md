# Learnings

Hard-won knowledge from building this project. Each entry: what happened, why
it was surprising, how to avoid it.

## httpx multi-value form params

**What happened:** httpx interprets `data=[("key", "val"), ...]` as raw
bytes content, not as form-encoded key-value pairs. Tests that POST
multi-value form fields (e.g. multi_select checkboxes with the same `name`
appearing multiple times) silently send garbage.

**Why surprising:** `requests` handles `data=[(...)]` as form pairs. httpx
looks like a drop-in but isn't here.

**How to avoid:** Use the `form_post(client, url, pairs)` helper in
`tests/compliance/conftest.py`, which encodes tuples correctly via
`urllib.parse.urlencode`.

## Hierarchical field cascading must work before JS runs

**What happened:** The reconciliation detail page pre-fills
`hierarchical_multi_select` custom widgets server-side. If child `<select>`
options are only populated by client-side JS, the prefill is empty on first
render.

**Why surprising:** Every other cascading dropdown on the labeling form can
rely on JS because the form starts blank. The reconciliation page starts
pre-filled.

**How to avoid:** Render child options server-side whenever a parent value is
already known (reconciliation detail, reviewed-record detail). JS takes over
for *new* entries added dynamically.

## Large integers render as scientific notation in HTML

**What happened:** When a record's ID (or other large integer field) is
rendered into an HTML form value, the browser (or Python's default string
formatting) can produce scientific notation like `1.23e+10`. The form then
POSTs that string instead of the actual integer, causing the backend lookup
or insert to fail.

**Why surprising:** Small IDs work fine in dev. The bug only surfaces with
real data that has large numeric identifiers — easy to miss until you hit
production-scale IDs.

**How to avoid:** Ensure integer values are explicitly formatted as integers
(e.g. `str(int(value))` or `{:d}`) when rendering into HTML attributes and
form hidden fields. Never rely on default float-to-string conversion for IDs.

## SQLite PRAGMA foreign_keys is per-connection

**What happened:** `PRAGMA foreign_keys = ON` doesn't persist across
connections. Without the `prepare_connection` Datasette hook, FK constraints
silently don't fire.

**Why surprising:** Most pragmas are per-database or per-session in other
databases. SQLite's default is OFF and it resets every connection.

**How to avoid:** Always set FK enforcement via the Datasette
`prepare_connection` hook, never assume it carries over.

## datasette-auth-passwords actor role is only available if you configure it

**What happened:** The `datasette-auth-passwords` plugin sets `request.actor`
from the `actors` map in `metadata.yml`. If you don't include a `role` field
in the actors config, `request.actor` is just `{"id": "username"}` — no role,
no way to do authorization. The role isn't read from the database; it comes
purely from the metadata config.

**Why surprising:** You might expect the plugin to look up additional actor
fields from a database table. It doesn't — the actor dict is exactly what you
put in the `actors` map, nothing more.

**How to avoid:** Always include `role` in the `actors` map when writing
`metadata.yml` (the bootstrap script does this). If you change a user's role,
you must update `metadata.yml` and restart Datasette — editing the `users`
table alone won't change the actor's role on their next request.
