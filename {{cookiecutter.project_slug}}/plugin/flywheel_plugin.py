"""Hello-world flywheel labeling plugin for Datasette.

Reads `flywheel.yaml` from the project root, generates a labeling form,
persists submissions into a `submissions` table, and surfaces the next
unlabeled record per labeler. No reconciliation, dashboard, or auth yet —
those are next iterations.
"""
from __future__ import annotations

import datetime
import html
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode

import yaml
from datasette import hookimpl
from datasette.utils.asgi import Response


_CONFIG_CACHE: dict | None = None


def _config_path() -> Path:
    return Path(os.environ.get("FLYWHEEL_CONFIG", "flywheel.yaml")).resolve()


def load_config() -> dict:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is None:
        _CONFIG_CACHE = yaml.safe_load(_config_path().read_text())
    return _CONFIG_CACHE


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


JS = """
<script>
const FLYWHEEL_CHOICES  = {};   // field → { parentName: [{name,label}, ...] }
const FLYWHEEL_LEVELS   = {};   // field → [parentLevelName, childLevelName]
const FLYWHEEL_PARENTS  = {};   // field → [{name,label}, ...]

function flywheelRegisterHier(field, levels, levelLabels, parents, children) {
  FLYWHEEL_LEVELS[field] = levels;
  FLYWHEEL_PARENTS[field] = parents;
  FLYWHEEL_PARENTS[field]._levelLabels = levelLabels;
  FLYWHEEL_CHOICES[field] = children;
}

function _flywheelBuildChildOptions(field, parentValue) {
  const opts = (FLYWHEEL_CHOICES[field] || {})[parentValue] || [];
  let html = '<option value=""></option>';
  for (const c of opts) {
    html += '<option value="' + c.name + '">' + c.label + '</option>';
  }
  return html;
}

function flywheelUpdateChildren(parentSelect) {
  const entry = parentSelect.closest('.hier-entry');
  const group = entry.closest('.hier-group');
  const field = group.dataset.field;
  const childLevel = FLYWHEEL_LEVELS[field][1];
  const childSelect = entry.querySelector('select[name="' + field + '__' + childLevel + '"]');
  // Try to preserve the current selection if still valid for the new parent.
  const current = childSelect.value;
  childSelect.innerHTML = _flywheelBuildChildOptions(field, parentSelect.value);
  if (current) {
    for (const opt of childSelect.options) {
      if (opt.value === current) { childSelect.value = current; break; }
    }
  }
}

function flywheelAddEntry(field) {
  const group = document.getElementById('field-' + field);
  const max = parseInt(group.dataset.max || '99', 10);
  const count = group.querySelectorAll('.hier-entry').length;
  if (count >= max) return;
  const levels = FLYWHEEL_LEVELS[field];
  const parents = FLYWHEEL_PARENTS[field];
  let parentOpts = '<option value=""></option>';
  for (const p of parents) {
    parentOpts += '<option value="' + p.name + '">' + p.label + '</option>';
  }
  const entry = document.createElement('div');
  entry.className = 'hier-entry';
  const pLabel = (FLYWHEEL_PARENTS[field]._levelLabels || [levels[0], levels[1]]);
  entry.innerHTML =
    '<div class="hier-level-label">' + pLabel[0] + '</div>'
    + '<select name="' + field + '__' + levels[0] + '" onchange="flywheelUpdateChildren(this)">' + parentOpts + '</select>'
    + '<div class="hier-level-label">' + pLabel[1] + '</div>'
    + '<select name="' + field + '__' + levels[1] + '"><option value=""></option></select>'
    + '<button type="button" class="hier-remove" onclick="flywheelRemoveEntry(this)" title="Remove this entry">&times;</button>';
  group.appendChild(entry);
}

function flywheelRemoveEntry(btn) {
  const entry = btn.closest('.hier-entry');
  const group = entry.closest('.hier-group');
  const min = parseInt(group.dataset.min || '0', 10);
  const count = group.querySelectorAll('.hier-entry').length;
  if (count <= Math.max(min, 1)) return;
  entry.remove();
}
</script>
"""


CSS = """
<style>
  /* -- base -- */
  body { font-family: system-ui, sans-serif; max-width: 960px; margin: 2em auto; padding: 0 1em; color: #1a1a1a; line-height: 1.5; }
  h1 { margin-bottom: 0.2em; }
  .muted { color: #666; font-size: 0.9em; }
  blockquote { background: #f8f9fa; border-left: 3px solid #888; padding: 0.8em 1em; white-space: pre-wrap; border-radius: 0 6px 6px 0; }
  .context-block { margin: 0.5em 0 1em; }
  .context-block summary { cursor: pointer; font-weight: 600; }
  blockquote.context { border-left-color: #b0c4de; background: #f0f4f8; }
  .errors { background: #ffe8e8; border: 1px solid #d44; padding: 0.6em 1em; border-radius: 6px; }
  dl.meta { display: grid; grid-template-columns: max-content 1fr; gap: 0.2em 1em; font-size: 0.9em; }
  dl.meta dt { color: #666; }

  /* -- form inputs -- */
  fieldset { margin: 1em 0; padding: 0.8em 1em; border: 1px solid #ddd; border-radius: 8px; }
  legend { font-weight: 600; padding: 0 0.3em; }
  select, textarea {
    font-family: inherit; font-size: 1em; color: #1a1a1a;
    padding: 0.5em 0.7em; border: 1px solid #ccc; border-radius: 6px; background: #fff;
    transition: border-color 0.15s, box-shadow 0.15s;
  }
  select:focus, textarea:focus { outline: none; border-color: #2a6df4; box-shadow: 0 0 0 3px rgba(42,109,244,0.12); }
  textarea { width: 100%; box-sizing: border-box; }
  input[type="checkbox"] { accent-color: #2a6df4; width: 1.1em; height: 1.1em; vertical-align: middle; cursor: pointer; }
  .check-group label { display: flex; align-items: center; gap: 0.4em; padding: 0.35em 0; cursor: pointer; }
  .check-group label:hover { color: #2a6df4; }

  /* -- ordinal chips -- */
  .chips { display: flex; gap: 0.35em; flex-wrap: wrap; }
  .chips label {
    display: inline-flex; align-items: center; justify-content: center;
    min-width: 2.4em; padding: 0.45em 0.85em;
    border: 1.5px solid #ccc; border-radius: 999px;
    cursor: pointer; background: #fff; color: #444;
    transition: all 0.15s;
  }
  .chips label:hover { border-color: #2a6df4; color: #2a6df4; background: #f0f4ff; }
  .chips input { display: none; }
  .chips input:checked + span { font-weight: 700; }
  .chips label:has(input:checked) { border-color: #2a6df4; background: #eef3ff; color: #2a6df4; }

  /* -- buttons -- */
  button {
    background: #2a6df4; color: white; border: 0;
    padding: 0.6em 1.2em; border-radius: 6px; font-size: 1em; cursor: pointer;
    transition: background 0.15s;
  }
  button:hover { background: #1b5ad4; }

  /* -- reconciliation table -- */
  table.reconcile { border-collapse: collapse; width: 100%; margin: 1em 0; }
  table.reconcile th, table.reconcile td { text-align: left; padding: 0.6em 0.8em; border-bottom: 1px solid #e0e0e0; vertical-align: top; }
  table.reconcile th { background: #fafafa; font-weight: 600; }
  table.reconcile th.field { width: 14em; }
  table.reconcile tr.agree { background: #f7f7f7; color: #666; }
  table.reconcile tr.agree td { font-style: italic; }
  table.reconcile tr.diff { background: #fff8dc; }
  table.reconcile tr.diff td { font-weight: 500; }
  table.reconcile td.pick label { display: block; cursor: pointer; padding: 0.3em 0.5em; border-radius: 3px; }
  table.reconcile td.pick label:hover { background: rgba(0,0,0,0.05); }
  table.reconcile td.pick input[type="radio"] { margin-right: 0.4em; }
  table.reconcile td.pick .free-text { white-space: pre-wrap; font-weight: normal; font-style: normal; max-width: 26em; }
  .progress { background: #eef; border: 1px solid #ccd; padding: 0.4em 0.8em; border-radius: 6px; display: inline-block; font-size: 0.9em; }
  .reconcile-nav { display: flex; gap: 1em; align-items: center; justify-content: space-between; margin-top: 1em; }
  nav.tabs { margin: 0.3em 0 1em; }
  nav.tabs a { padding: 0.4em 0.9em; border: 1px solid #ccd; border-radius: 6px; text-decoration: none; color: #455; margin-right: 0.3em; transition: all 0.15s; }
  nav.tabs a:hover { border-color: #2a6df4; color: #2a6df4; }
  nav.tabs a.tab-active { background: #2a6df4; color: white; border-color: #2a6df4; font-weight: 600; }
  .banner { background: #e9f7ef; border: 1px solid #b7e1c3; padding: 0.7em 1em; border-radius: 6px; margin: 0.6em 0; }
  .banner.warn { background: #fff4d8; border-color: #e7c66a; }
  form.undo-form { display: inline; }
  button.undo-btn { background: #eee; color: #a00; border: 1px solid #d88; padding: 0.4em 0.9em; border-radius: 6px; font-size: 0.9em; cursor: pointer; }
  button.undo-btn:hover { background: #fdd; }

  /* -- hierarchical picker (vertical stacking) -- */
  .hier-group .hier-entry {
    display: flex; flex-direction: column; gap: 0.5em;
    padding: 0.7em 0.9em; margin: 0.5em 0;
    border: 1px solid #e0e0e0; border-radius: 8px; background: #fafafa;
    position: relative;
  }
  .hier-group .hier-entry select { width: 100%; }
  .hier-group .hier-entry .hier-level-label { font-size: 0.8em; color: #666; margin-bottom: -0.3em; }
  .hier-group .hier-remove {
    position: absolute; top: 0.5em; right: 0.5em;
    background: none; color: #999; border: none;
    padding: 0.2em 0.5em; font-size: 1.1em; line-height: 1; cursor: pointer;
    transition: color 0.15s;
  }
  .hier-group .hier-remove:hover { color: #d44; background: none; }
  .hier-add {
    background: transparent; color: #2a6df4; border: 1px dashed #b0c4de;
    padding: 0.4em 0.9em; margin-top: 0.4em; font-size: 0.9em; cursor: pointer;
    border-radius: 6px; transition: all 0.15s;
  }
  .hier-add:hover { background: #f0f4ff; border-color: #2a6df4; }

  /* -- custom reconciliation column -- */
  table.reconcile td.custom { background: #f2f6ff; min-width: 14em; }
  table.reconcile td.custom .custom-widget { margin-top: 0.3em; padding-left: 1.4em; font-weight: normal; font-style: normal; }
  table.reconcile td.custom select { font-size: 0.9em; padding: 0.2em 0.4em; max-width: 100%; }
  table.reconcile td.custom .custom-multi { font-size: 0.85em; }
  table.reconcile td.custom .custom-check { display: inline-block; margin-right: 0.6em; }
  table.reconcile td.custom .custom-chips { display: flex; gap: 0.2em; flex-wrap: wrap; }
  table.reconcile td.custom .custom-chip { padding: 0.15em 0.5em; border: 1px solid #ccd; border-radius: 999px; cursor: pointer; font-size: 0.85em; transition: all 0.15s; }
  table.reconcile td.custom .custom-chip input { display: none; }
  table.reconcile td.custom .custom-chip input:checked + span { font-weight: 700; color: #2a6df4; }
</style>
"""


def _render_field(field: dict) -> str:
    kind = field["kind"]
    name = field["name"]
    label = field.get("label", name)
    required_attr = "required" if field.get("required") else ""

    if kind == "single_select":
        opts = '<option value=""></option>' + "".join(
            f'<option value="{_esc(c)}">{_esc(c)}</option>' for c in field["choices"]
        )
        return (
            f'<fieldset><legend>{_esc(label)}</legend>'
            f'<select name="{_esc(name)}" {required_attr}>{opts}</select>'
            f'</fieldset>'
        )

    if kind == "multi_select":
        min_sel = field.get("min_selections", 0)
        max_sel = field.get("max_selections", len(field["choices"]))
        boxes = "".join(
            f'<label><input type="checkbox" name="{_esc(name)}" value="{_esc(c)}"> {_esc(c)}</label>'
            for c in field["choices"]
        )
        return (
            f'<fieldset><legend>{_esc(label)} '
            f'<span class="muted">(pick {min_sel}–{max_sel})</span></legend>'
            f'<div class="check-group">{boxes}</div></fieldset>'
        )

    if kind == "ordinal":
        lo, hi = field["min"], field["max"]
        endpoints = field.get("endpoints", {}) or {}
        chips = "".join(
            f'<label><input type="radio" name="{_esc(name)}" value="{i}" {required_attr}>'
            f'<span>{i}</span></label>'
            for i in range(lo, hi + 1)
        )
        endpoint_text = ""
        if endpoints:
            parts = [f"{k}={_esc(v)}" for k, v in endpoints.items()]
            endpoint_text = f'<div class="muted">{" · ".join(parts)}</div>'
        return (
            f'<fieldset><legend>{_esc(label)}</legend>'
            f'<div class="chips">{chips}</div>{endpoint_text}'
            f'</fieldset>'
        )

    if kind == "free_text":
        rows = int(field.get("rows", 3))
        max_length = int(field.get("max_length", 2000))
        placeholder = field.get("placeholder", "")
        hint = field.get("hint", "")
        hint_html = f'<div class="muted">{_esc(hint)}</div>' if hint else ""
        return (
            f'<fieldset><legend>{_esc(label)}</legend>'
            f'<textarea name="{_esc(name)}" rows="{rows}" maxlength="{max_length}" '
            f'placeholder="{_esc(placeholder)}" {required_attr}></textarea>'
            f'{hint_html}</fieldset>'
        )

    if kind == "hierarchical_multi_select":
        levels = field.get("levels") or []
        if len(levels) != 2:
            return (
                f'<p class="errors">Field <code>{_esc(name)}</code>: '
                f'hierarchical_multi_select requires exactly 2 levels.</p>'
            )
        parent_level = levels[0]["name"]
        child_level = levels[1]["name"]
        parent_label = levels[0].get("label", parent_level)
        child_label = levels[1].get("label", child_level)
        min_entries = int(field.get("min_entries", 0))
        max_entries = int(field.get("max_entries", 99))
        starting = max(min_entries, 1)

        parents_data = []
        children_map: dict = {}
        for parent in field.get("choices") or []:
            parents_data.append({"name": parent["name"], "label": parent.get("label", parent["name"])})
            children_map[parent["name"]] = [
                {"name": c["name"], "label": c.get("label", c["name"])}
                for c in parent.get("children") or []
            ]

        parent_options = '<option value=""></option>' + "".join(
            f'<option value="{_esc(p["name"])}">{_esc(p["label"])}</option>'
            for p in parents_data
        )

        def _entry_html() -> str:
            return (
                '<div class="hier-entry">'
                f'<div class="hier-level-label">{_esc(parent_label)}</div>'
                f'<select name="{_esc(name)}__{_esc(parent_level)}" '
                f'onchange="flywheelUpdateChildren(this)">{parent_options}</select>'
                f'<div class="hier-level-label">{_esc(child_label)}</div>'
                f'<select name="{_esc(name)}__{_esc(child_level)}">'
                '<option value=""></option></select>'
                '<button type="button" class="hier-remove" '
                'onclick="flywheelRemoveEntry(this)" title="Remove">&times;</button>'
                '</div>'
            )

        entries_html = "".join(_entry_html() for _ in range(starting))
        js_payload = json.dumps({
            "field": name,
            "levels": [parent_level, child_level],
            "levelLabels": [parent_label, child_label],
            "parents": parents_data,
            "children": children_map,
        })
        return (
            f'<fieldset><legend>{_esc(label)} '
            f'<span class="muted">(pick {min_entries}–{max_entries})</span></legend>'
            f'<div id="field-{_esc(name)}" class="hier-group" '
            f'data-field="{_esc(name)}" data-min="{min_entries}" data-max="{max_entries}">'
            f'{entries_html}</div>'
            f'<button type="button" class="hier-add" '
            f'onclick="flywheelAddEntry(\'{_esc(name)}\')">+ Add entry</button>'
            f'<script>(function(){{var d={js_payload};'
            f'flywheelRegisterHier(d.field,d.levels,d.levelLabels,d.parents,d.children);}})();</script>'
            f'</fieldset>'
        )

    return f'<p class="muted">Unsupported field kind: {_esc(kind)}</p>'


def _render_custom_widget(field: dict, prefill=None) -> str:
    """Render a schema-driven editable widget for the Custom column of
    the reconciliation table. Form names are prefixed with ``custom_``
    so they don't collide with the ``pick_`` radios or the normal
    labeling submission field names. Only honored when the supervisor
    also selects the ``__custom__`` radio for this row.

    If ``prefill`` is given, its value is pre-populated into the widget
    (used when editing an already-reconciled record's custom value).
    """
    kind = field["kind"]
    name = field["name"]
    custom_name = f"custom_{name}"

    if kind == "single_select":
        opts = '<option value=""></option>' + "".join(
            f'<option value="{_esc(c)}"'
            f'{" selected" if prefill == c else ""}>{_esc(c)}</option>'
            for c in field["choices"]
        )
        return f'<select name="{_esc(custom_name)}">{opts}</select>'

    if kind == "multi_select":
        selected = set(prefill or [])
        boxes = "".join(
            f'<label class="custom-check"><input type="checkbox" '
            f'name="{_esc(custom_name)}" value="{_esc(c)}"'
            f'{" checked" if c in selected else ""}> {_esc(c)}</label>'
            for c in field["choices"]
        )
        return f'<div class="custom-multi">{boxes}</div>'

    if kind == "ordinal":
        lo, hi = field["min"], field["max"]
        chips = "".join(
            f'<label class="custom-chip"><input type="radio" '
            f'name="{_esc(custom_name)}" value="{i}"'
            f'{" checked" if prefill == i else ""}><span>{i}</span></label>'
            for i in range(lo, hi + 1)
        )
        return f'<div class="custom-chips">{chips}</div>'

    if kind == "free_text":
        rows = int(field.get("rows", 3))
        max_length = int(field.get("max_length", 2000))
        body = _esc(prefill) if prefill else ""
        return (
            f'<textarea name="{_esc(custom_name)}" rows="{rows}" '
            f'maxlength="{max_length}" style="font-size:0.9em">'
            f'{body}</textarea>'
        )

    if kind == "hierarchical_multi_select":
        levels = field.get("levels") or []
        if len(levels) != 2:
            return '<span class="muted">(schema error)</span>'
        parent_level = levels[0]["name"]
        child_level = levels[1]["name"]
        parent_label = levels[0].get("label", parent_level)
        child_label = levels[1].get("label", child_level)
        min_entries = int(field.get("min_entries", 0))
        max_entries = int(field.get("max_entries", 99))

        parents_data: list = []
        children_map: dict = {}
        for parent in field.get("choices") or []:
            parents_data.append(
                {"name": parent["name"], "label": parent.get("label", parent["name"])}
            )
            children_map[parent["name"]] = [
                {"name": c["name"], "label": c.get("label", c["name"])}
                for c in parent.get("children") or []
            ]

        def _parent_options(selected: str = "") -> str:
            return '<option value=""></option>' + "".join(
                f'<option value="{_esc(p["name"])}"'
                f'{" selected" if p["name"] == selected else ""}>{_esc(p["label"])}</option>'
                for p in parents_data
            )

        def _child_options(parent_key: str, selected: str = "") -> str:
            kids = children_map.get(parent_key, [])
            return '<option value=""></option>' + "".join(
                f'<option value="{_esc(c["name"])}"'
                f'{" selected" if c["name"] == selected else ""}>{_esc(c["label"])}</option>'
                for c in kids
            )

        def _entry_html(parent_val: str = "", child_val: str = "") -> str:
            return (
                '<div class="hier-entry">'
                f'<div class="hier-level-label">{_esc(parent_label)}</div>'
                f'<select name="{_esc(custom_name)}__{_esc(parent_level)}" '
                f'onchange="flywheelUpdateChildren(this)">{_parent_options(parent_val)}</select>'
                f'<div class="hier-level-label">{_esc(child_label)}</div>'
                f'<select name="{_esc(custom_name)}__{_esc(child_level)}">'
                f'{_child_options(parent_val, child_val)}</select>'
                '<button type="button" class="hier-remove" '
                'onclick="flywheelRemoveEntry(this)" title="Remove">&times;</button>'
                '</div>'
            )

        if prefill and isinstance(prefill, list):
            entries_html = "".join(
                _entry_html(
                    e.get(parent_level, ""),
                    e.get(child_level, ""),
                )
                for e in prefill
            )
        else:
            entries_html = _entry_html()

        js_payload = json.dumps(
            {
                "field": custom_name,
                "levels": [parent_level, child_level],
                "levelLabels": [parent_label, child_label],
                "parents": parents_data,
                "children": children_map,
            }
        )
        return (
            f'<div id="field-{_esc(custom_name)}" class="hier-group" '
            f'data-field="{_esc(custom_name)}" '
            f'data-min="{min_entries}" data-max="{max_entries}">'
            f'{entries_html}</div>'
            f'<button type="button" class="hier-add" '
            f'onclick="flywheelAddEntry(\'{_esc(custom_name)}\')">+ Add entry</button>'
            f'<script>(function(){{var d={js_payload};'
            f'flywheelRegisterHier(d.field,d.levels,d.levelLabels,d.parents,d.children);}})();</script>'
        )

    return '<span class="muted">(unsupported)</span>'


def _page(body: str) -> Response:
    return Response.html(
        f"<!doctype html><html><head>{CSS}{JS}</head><body>{body}</body></html>"
    )


def _get_db(datasette):
    cfg = load_config()
    name = Path(cfg["source"]["sqlite_path"]).stem
    if name in datasette.databases:
        return datasette.databases[name]
    for key, db in datasette.databases.items():
        if db.is_mutable and not key.startswith("_"):
            return db
    raise RuntimeError(f"no usable database (looked for {name!r})")


@hookimpl
def prepare_connection(conn, database, datasette):
    conn.execute("PRAGMA foreign_keys = ON")


@hookimpl
def startup(datasette):
    async def inner():
        db = _get_db(datasette)
        await db.execute_write(
            "CREATE TABLE IF NOT EXISTS users ("
            "  username TEXT PRIMARY KEY,"
            "  role TEXT NOT NULL DEFAULT 'labeler',"
            "  created_at TEXT NOT NULL"
            ")"
        )
        await db.execute_write(
            "CREATE TABLE IF NOT EXISTS submissions ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  record_id INTEGER NOT NULL REFERENCES records(id),"
            "  username TEXT NOT NULL REFERENCES users(username),"
            "  submitted_at TEXT NOT NULL,"
            "  values_json TEXT NOT NULL,"
            "  UNIQUE(record_id, username)"
            ")"
        )
        await db.execute_write(
            "CREATE TABLE IF NOT EXISTS reconciliations ("
            "  record_id INTEGER PRIMARY KEY REFERENCES records(id),"
            "  supervisor TEXT NOT NULL REFERENCES users(username),"
            "  reconciled_at TEXT NOT NULL,"
            "  values_json TEXT NOT NULL"
            ")"
        )
    return inner


@hookimpl
def skip_csrf(datasette, scope):
    return scope.get("path", "").startswith("/flywheel")


def _require_actor(request):
    """Return the authenticated username, or None if the request is
    unauthenticated. Callers pair this with ``_login_redirect`` to
    send anonymous users through ``/-/login`` before serving the page.
    """
    actor = getattr(request, "actor", None)
    if actor and actor.get("id"):
        return actor["id"]
    scope = getattr(request, "scope", None)
    if scope:
        actor = scope.get("actor")
        if actor and actor.get("id"):
            return actor["id"]
    return None


def _actor_role(request):
    """Return the role string from the authenticated actor, or None."""
    actor = getattr(request, "actor", None) or {}
    return actor.get("role")


def _require_supervisor(request):
    """Return a 403 Response if the authenticated user is not a supervisor,
    or None if they are. Callers should check the return value and return
    it immediately if non-None."""
    role = _actor_role(request)
    if role == "supervisor":
        return None
    body = """
      <h1>Forbidden</h1>
      <p>Reconciliation requires the <strong>supervisor</strong> role.
      You are logged in as a <strong>labeler</strong>.</p>
      <p><a href="/flywheel">&larr; Home</a></p>
    """
    html = f"<!doctype html><html><head>{CSS}{JS}</head><body>{body}</body></html>"
    return Response.html(html, status=403)


def _login_redirect(request) -> Response:
    path = getattr(request, "path", "/flywheel")
    # Include the original destination in a ?next= hint so a future
    # iteration of the login handler can redirect back (today
    # datasette-auth-passwords always sends users to /).
    return Response.redirect(f"/-/login?next={quote(path, safe='/')}" )


@hookimpl
def register_routes():
    return [
        (r"^/$", redirect_home),
        (r"^/flywheel$", home),
        (r"^/flywheel/label$", label_next),
        (r"^/flywheel/label/submit$", submit_label),
        (r"^/flywheel/users$", users_index),
        (r"^/flywheel/reconcile$", reconcile_index),
        (r"^/flywheel/reconcile/(?P<record_id>\d+)$", reconcile_detail),
        (r"^/flywheel/reconcile/(?P<record_id>\d+)/submit$", reconcile_submit),
        (r"^/flywheel/reconcile/(?P<record_id>\d+)/undo$", reconcile_undo),
    ]


async def redirect_home(scope, receive, datasette, request):
    return Response.redirect("/flywheel")


async def home(scope, receive, datasette, request):
    actor_id = _require_actor(request)
    if actor_id is None:
        return _login_redirect(request)
    cfg = load_config()
    name = _esc(cfg["project"]["name"])
    desc = _esc(cfg["project"].get("description", ""))
    body = f"""
      <h1>{name}</h1>
      <p class="muted">{desc}</p>
      <p>Signed in as <strong>{_esc(actor_id)}</strong> ·
      <a href="/-/logout">log out</a></p>
      <p><a href="/flywheel/label"><strong>Start labeling →</strong></a></p>
      <hr>
      <p class="muted">
        <a href="/flywheel/reconcile">Supervisor reconciliation queue</a> ·
        <a href="/flywheel/users">Labelers &amp; history</a> ·
        <a href="/-/databases">Datasette DB browser</a> ·
        <a href="/labeling/submissions">submissions table</a>
      </p>
    """
    return _page(body)


async def users_index(scope, receive, datasette, request):
    actor_id = _require_actor(request)
    if actor_id is None:
        return _login_redirect(request)
    db = _get_db(datasette)
    result = await db.execute(
        "SELECT u.username, u.role, u.created_at, "
        "       COUNT(s.id) AS submission_count "
        "FROM users u LEFT JOIN submissions s ON s.username = u.username "
        "GROUP BY u.username ORDER BY submission_count DESC, u.username"
    )
    rows = list(result.rows)
    if not rows:
        body_inner = '<p class="muted">No labelers yet — submit something first.</p>'
    else:
        items = "".join(
            f'<tr><td><strong>{_esc(r["username"])}</strong></td>'
            f'<td>{_esc(r["role"])}</td>'
            f'<td>{_esc(r["submission_count"])}</td>'
            f'<td class="muted">{_esc(r["created_at"])}</td></tr>'
            for r in rows
        )
        body_inner = (
            '<table style="border-collapse:collapse;width:100%">'
            '<thead><tr><th align="left">User</th><th align="left">Role</th>'
            '<th align="left">Submissions</th><th align="left">Joined</th></tr></thead>'
            f'<tbody>{items}</tbody></table>'
        )
    body = f"""
      <h1>Labelers</h1>
      {body_inner}
      <p><a href="/flywheel">← Home</a></p>
    """
    return _page(body)


async def label_next(scope, receive, datasette, request):
    labeler = _require_actor(request)
    if labeler is None:
        return _login_redirect(request)
    cfg = load_config()
    db = _get_db(datasette)
    source = cfg["source"]
    table = source["table"]
    id_field = source["id_field"]
    text_field = source["text_field"]
    context_field = source.get("context_field")
    display_fields = source.get("display_fields") or []

    n_labelers = int(cfg.get("reconciliation", {}).get("min_labelers", 2))
    labeling_cfg = cfg.get("labeling") or {}
    strategy = labeling_cfg.get("strategy", "queue")
    queue_sort = labeling_cfg.get("queue_sort", id_field)
    order_clause = "RANDOM()" if strategy == "random" else f'r."{queue_sort}"'

    cols = [id_field, text_field] + ([context_field] if context_field else []) + [f["name"] for f in display_fields]
    cols_sql = ", ".join(f'r."{c}"' for c in cols)
    sql = (
        f'SELECT {cols_sql} FROM "{table}" r '
        f'WHERE (SELECT COUNT(*) FROM submissions WHERE record_id = r."{id_field}") < :n_labelers '
        f'  AND r."{id_field}" NOT IN ('
        f'    SELECT record_id FROM submissions WHERE username = :username'
        f'  ) '
        f'ORDER BY {order_clause} '
        f'LIMIT 1'
    )
    result = await db.execute(sql, {"username": labeler, "n_labelers": n_labelers})
    rows = list(result.rows)
    if not rows:
        body = f"""
          <h1>All caught up, {_esc(labeler)} 🎉</h1>
          <p>No more records to label.</p>
          <p><a href="/flywheel">← Home</a></p>
        """
        return _page(body)

    row = rows[0]
    rid = int(row[id_field])
    text = row[text_field]
    context_html = ""
    if context_field and row[context_field]:
        context_label = source.get("context_label", "Additional context")
        context_html = (
            f'<details class="context-block" open>'
            f'<summary class="muted">{_esc(context_label)}</summary>'
            f'<blockquote class="context">{_esc(row[context_field])}</blockquote>'
            f'</details>'
        )
    meta_html = ""
    if display_fields:
        items = "".join(
            f"<dt>{_esc(f.get('label', f['name']))}</dt><dd>{_esc(row[f['name']])}</dd>"
            for f in display_fields
        )
        meta_html = f'<dl class="meta">{items}</dl>'

    fields_html = "\n".join(_render_field(f) for f in cfg["fields"])

    body = f"""
      <h1>{_esc(cfg['project']['name'])}</h1>
      <p class="muted">labeler: <strong>{_esc(labeler)}</strong> · record #{_esc(rid)}
      · <a href="/-/logout">log out</a></p>
      {meta_html}
      <blockquote>{_esc(text)}</blockquote>
      {context_html}
      <form method="post" action="/flywheel/label/submit">
        <input type="hidden" name="record_id" value="{_esc(rid)}">
        {fields_html}
        <div style="margin-top:1em"><button type="submit">Submit →</button></div>
      </form>
    """
    return _page(body)


async def submit_label(scope, receive, datasette, request):
    labeler = _require_actor(request)
    if labeler is None:
        return _login_redirect(request)
    cfg = load_config()
    body_bytes = await request.post_body()
    parsed = parse_qs(body_bytes.decode("utf-8"))

    record_id = int(parsed["record_id"][0])

    values: dict = {}
    errors: list[str] = []

    for f in cfg["fields"]:
        name = f["name"]
        kind = f["kind"]
        required = f.get("required", False)
        raw = parsed.get(name, [])

        if kind == "multi_select":
            values[name] = raw
            min_sel = f.get("min_selections", 0)
            max_sel = f.get("max_selections")
            if required and not raw:
                errors.append(f"{f.get('label', name)}: required")
            if len(raw) < min_sel:
                errors.append(f"{f.get('label', name)}: pick at least {min_sel}")
            if max_sel is not None and len(raw) > max_sel:
                errors.append(f"{f.get('label', name)}: pick at most {max_sel}")
        elif kind == "ordinal":
            v = raw[0] if raw else ""
            if required and not v:
                errors.append(f"{f.get('label', name)}: required")
            values[name] = int(v) if v else None
        elif kind == "hierarchical_multi_select":
            levels = f.get("levels") or []
            if len(levels) != 2:
                errors.append(f"{f.get('label', name)}: schema must declare exactly 2 levels")
                values[name] = []
                continue
            parent_level = levels[0]["name"]
            child_level = levels[1]["name"]
            parents_raw = parsed.get(f"{name}__{parent_level}", [])
            children_raw = parsed.get(f"{name}__{child_level}", [])
            # Zip in document order, drop fully-empty rows (user left a blank entry)
            entries = []
            valid_children_by_parent = {
                p["name"]: {c["name"] for c in (p.get("children") or [])}
                for p in (f.get("choices") or [])
            }
            for pval, cval in zip(parents_raw, children_raw):
                if not pval and not cval:
                    continue
                if not pval or not cval:
                    errors.append(
                        f"{f.get('label', name)}: each entry needs both a {parent_level} and a {child_level}"
                    )
                    continue
                if pval not in valid_children_by_parent:
                    errors.append(
                        f"{f.get('label', name)}: {parent_level}={pval!r} is not in the choices list"
                    )
                    continue
                if cval not in valid_children_by_parent[pval]:
                    errors.append(
                        f"{f.get('label', name)}: {child_level}={cval!r} is not a valid child of {pval!r}"
                    )
                    continue
                entries.append({parent_level: pval, child_level: cval})
            min_entries = int(f.get("min_entries", 0))
            max_entries = int(f.get("max_entries", 99))
            if required and not entries:
                errors.append(f"{f.get('label', name)}: required")
            if len(entries) < min_entries:
                errors.append(
                    f"{f.get('label', name)}: pick at least {min_entries} entries"
                )
            if len(entries) > max_entries:
                errors.append(
                    f"{f.get('label', name)}: pick at most {max_entries} entries"
                )
            values[name] = entries
        else:  # single_select, free_text
            v = raw[0] if raw else ""
            if required and not v:
                errors.append(f"{f.get('label', name)}: required")
            values[name] = v

    if errors:
        items = "".join(f"<li>{_esc(e)}</li>" for e in errors)
        body = f"""
          <h1>Validation errors</h1>
          <div class="errors"><ul>{items}</ul></div>
          <p><a href="/flywheel/label?{urlencode({'labeler': labeler})}">← Back</a></p>
        """
        return _page(body)

    db = _get_db(datasette)
    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    await db.execute_write(
        "INSERT OR IGNORE INTO users (username, role, created_at) VALUES (?, 'labeler', ?)",
        [labeler, now],
    )
    await db.execute_write(
        "INSERT OR IGNORE INTO submissions (record_id, username, submitted_at, values_json) "
        "VALUES (?, ?, ?, ?)",
        [record_id, labeler, now, json.dumps(values)],
    )
    return Response.redirect(f"/flywheel/label?{urlencode({'labeler': labeler})}")


def _canon(v):
    if isinstance(v, list):
        if v and isinstance(v[0], dict):
            # hierarchical entries: canonicalize as a sorted tuple of
            # sorted-items tuples so insertion order and key order don't
            # affect equality
            return tuple(sorted(tuple(sorted(e.items())) for e in v))
        return tuple(sorted(v))
    return v


async def _contested_queue(datasette, cfg):
    """Return a sorted list of *pending* contested record_ids (those
    needing a first reconciliation), along with the full per-record
    submission map restricted to pending records."""
    db = _get_db(datasette)
    n_labelers = int(cfg["reconciliation"]["min_labelers"])
    label_fields = [f["name"] for f in cfg["fields"]]

    result = await db.execute(
        "SELECT record_id, username, submitted_at, values_json FROM submissions "
        "WHERE record_id NOT IN (SELECT record_id FROM reconciliations) "
        "ORDER BY record_id, submitted_at"
    )
    by_record: dict = {}
    for row in result.rows:
        by_record.setdefault(int(row["record_id"]), []).append(
            {
                "username": row["username"],
                "submitted_at": row["submitted_at"],
                "values": json.loads(row["values_json"]),
            }
        )

    contested = []
    for rid, subs in by_record.items():
        if len(subs) < n_labelers:
            continue
        for field in label_fields:
            vals = {_canon(s["values"].get(field)) for s in subs}
            if len(vals) > 1:
                contested.append(rid)
                break
    return sorted(contested), by_record


async def _reviewed_queue(datasette):
    """Return a sorted list of record_ids that already have a
    reconciliation, plus lookup dicts for the reconciliation row and
    the per-record submissions (so the detail page can still show
    labeler context next to the current gold value)."""
    db = _get_db(datasette)

    rec_result = await db.execute(
        "SELECT record_id, supervisor, reconciled_at, values_json "
        "FROM reconciliations ORDER BY record_id"
    )
    reconciliation_by_record: dict = {}
    for row in rec_result.rows:
        reconciliation_by_record[int(row["record_id"])] = {
            "supervisor": row["supervisor"],
            "reconciled_at": row["reconciled_at"],
            "values": json.loads(row["values_json"]),
        }

    if not reconciliation_by_record:
        return [], {}, {}

    rids = list(reconciliation_by_record.keys())
    placeholders = ",".join("?" * len(rids))
    sub_result = await db.execute(
        f"SELECT record_id, username, submitted_at, values_json "
        f"FROM submissions WHERE record_id IN ({placeholders}) "
        f"ORDER BY record_id, submitted_at",
        rids,
    )
    subs_by_record: dict = {}
    for row in sub_result.rows:
        subs_by_record.setdefault(int(row["record_id"]), []).append(
            {
                "username": row["username"],
                "submitted_at": row["submitted_at"],
                "values": json.loads(row["values_json"]),
            }
        )

    return sorted(rids), reconciliation_by_record, subs_by_record


async def reconcile_index(scope, receive, datasette, request):
    supervisor = _require_actor(request)
    if supervisor is None:
        return _login_redirect(request)
    forbidden = _require_supervisor(request)
    if forbidden:
        return forbidden
    cfg = load_config()
    view = request.args.get("view", "pending")
    if view not in ("pending", "reviewed"):
        view = "pending"

    label_fields = [f["name"] for f in cfg["fields"]]
    qs = urlencode({"supervisor": supervisor})

    # pending count is cheap to compute up-front for the tab label
    contested, contested_by_record = await _contested_queue(datasette, cfg)
    reviewed_ids, reconciliation_by_record, reviewed_subs = await _reviewed_queue(datasette)

    def _tab(link_view: str, title: str, count: int) -> str:
        active = ' class="tab-active"' if link_view == view else ""
        return (
            f'<a{active} href="/flywheel/reconcile?'
            f'{urlencode({"supervisor": supervisor, "view": link_view})}">'
            f'{title} ({count})</a>'
        )

    tabs_html = (
        f'<nav class="tabs">{_tab("pending", "Pending", len(contested))} · '
        f'{_tab("reviewed", "Reviewed", len(reviewed_ids))}</nav>'
    )

    if view == "pending":
        if not contested:
            body = f"""
              <h1>Reconciliation queue</h1>
              {tabs_html}
              <p>Nothing to reconcile — all records with ≥ {cfg['reconciliation']['min_labelers']}
              submissions are either unanimous or already reconciled. 🎉</p>
              <p><a href="/flywheel">← Home</a></p>
            """
            return _page(body)

        rows_html = ""
        for rid in contested:
            subs = contested_by_record[rid]
            diffs = [
                f for f in label_fields
                if len({_canon(s["values"].get(f)) for s in subs}) > 1
            ]
            rows_html += (
                f'<tr><td><a href="/flywheel/reconcile/{rid}?'
                f'{urlencode({"supervisor": supervisor, "view": "pending"})}">#{rid}</a></td>'
                f'<td>{len(subs)}</td>'
                f'<td><strong>{len(diffs)}</strong> / {len(label_fields)}</td>'
                f'<td class="muted">{_esc(", ".join(diffs))}</td></tr>'
            )

        body = f"""
          <h1>Reconciliation queue</h1>
          {tabs_html}
          <p class="muted">supervisor: <strong>{_esc(supervisor)}</strong>
          · <span class="progress">{len(contested)} pending record(s)</span></p>
          <p>Click a record to reconcile. Records with the most fields in
          disagreement are listed first.</p>
          <table class="reconcile">
            <thead><tr><th>Record</th><th>Submissions</th><th>Diffs</th><th>Fields that differ</th></tr></thead>
            <tbody>{rows_html}</tbody>
          </table>
          <p><a href="/flywheel">← Home</a></p>
        """
        return _page(body)

    # view == "reviewed"
    if not reviewed_ids:
        body = f"""
          <h1>Reconciliation queue</h1>
          {tabs_html}
          <p>No reconciled records yet. Go to the Pending tab to decide on the
          first contested record.</p>
          <p><a href="/flywheel">← Home</a></p>
        """
        return _page(body)

    rows_html = ""
    for rid in reviewed_ids:
        rec = reconciliation_by_record[rid]
        rows_html += (
            f'<tr><td><a href="/flywheel/reconcile/{rid}?'
            f'{urlencode({"supervisor": supervisor, "view": "reviewed"})}">#{rid}</a></td>'
            f'<td>{_esc(rec["supervisor"])}</td>'
            f'<td class="muted">{_esc(rec["reconciled_at"])}</td></tr>'
        )

    body = f"""
      <h1>Reconciliation queue</h1>
      {tabs_html}
      <p class="muted">supervisor: <strong>{_esc(supervisor)}</strong>
      · <span class="progress">{len(reviewed_ids)} reconciled record(s)</span></p>
      <p>Click a record to review or edit its reconciliation. You can also
      undo a decision from the detail page, which returns the record to the
      Pending queue.</p>
      <table class="reconcile">
        <thead><tr><th>Record</th><th>Decided by</th><th>When</th></tr></thead>
        <tbody>{rows_html}</tbody>
      </table>
      <p><a href="/flywheel">← Home</a></p>
    """
    return _page(body)


async def reconcile_detail(scope, receive, datasette, request):
    supervisor = _require_actor(request)
    if supervisor is None:
        return _login_redirect(request)
    forbidden = _require_supervisor(request)
    if forbidden:
        return forbidden
    cfg = load_config()
    view = request.args.get("view", "pending")
    if view not in ("pending", "reviewed"):
        view = "pending"
    record_id = int(request.url_vars["record_id"])

    # Pull both queues — we use the one matching the current view for
    # prev/next nav, and check both to know whether the record is
    # pending, reviewed, or neither.
    pending, contested_subs = await _contested_queue(datasette, cfg)
    reviewed, reconciliation_by_record, reviewed_subs = await _reviewed_queue(datasette)

    # Locate the record
    is_reviewed = record_id in reconciliation_by_record
    if is_reviewed:
        subs = reviewed_subs.get(record_id, [])
        reconciliation = reconciliation_by_record[record_id]
    elif record_id in contested_subs:
        subs = contested_subs[record_id]
        reconciliation = None
    else:
        return _page(
            f'<h1>Record #{record_id}</h1><p class="muted">Not in the queue — '
            f'no submissions for this record, or it does not exist.</p>'
            f'<p><a href="/flywheel/reconcile?'
            f'{urlencode({"supervisor": supervisor})}">← Queue</a></p>'
        )

    # If the view query param mismatches reality, fix it so the nav
    # uses the right queue.
    if is_reviewed and view == "pending":
        view = "reviewed"
    elif not is_reviewed and view == "reviewed":
        view = "pending"

    nav_queue = reviewed if view == "reviewed" else pending

    label_fields_meta = cfg["fields"]
    label_fields = [f["name"] for f in label_fields_meta]

    # pull the source record for context
    source = cfg["source"]
    context_field = source.get("context_field")
    display_fields = source.get("display_fields") or []
    display_cols = [f["name"] for f in display_fields]
    extra_cols = ([context_field] if context_field else [])
    cols_sql = ", ".join(
        f'"{c}"' for c in [source["id_field"], source["text_field"]] + extra_cols + display_cols
    )
    db = _get_db(datasette)
    src_res = await db.execute(
        f'SELECT {cols_sql} FROM "{source["table"]}" WHERE "{source["id_field"]}" = :rid',
        {"rid": record_id},
    )
    src_row = list(src_res.rows)[0]

    # queue progress
    try:
        position = nav_queue.index(record_id) + 1
    except ValueError:
        position = 0
    total = len(nav_queue)
    next_rid = nav_queue[position] if position < total else None
    prev_rid = nav_queue[position - 2] if position >= 2 else None

    # render display fields
    meta_html = ""
    if display_fields:
        items = "".join(
            f"<dt>{_esc(f.get('label', f['name']))}</dt><dd>{_esc(src_row[f['name']])}</dd>"
            for f in display_fields
        )
        meta_html = f'<dl class="meta">{items}</dl>'

    # render context field
    context_html = ""
    if context_field and src_row[context_field]:
        context_label = source.get("context_label", "Additional context")
        context_html = (
            f'<details class="context-block" open>'
            f'<summary class="muted">{_esc(context_label)}</summary>'
            f'<blockquote class="context">{_esc(src_row[context_field])}</blockquote>'
            f'</details>'
        )

    # build reconciliation table
    header_cells = (
        "".join(f'<th>{_esc(s["username"])}</th>' for s in subs)
        + '<th class="custom-col">Custom override</th>'
    )
    rows_html = ""

    def _display_value(raw):
        if isinstance(raw, list):
            if not raw:
                return "∅"
            if isinstance(raw[0], dict):
                # hierarchical entries: "parent → child; parent → child"
                return "; ".join(" → ".join(str(v) for v in e.values()) for e in raw)
            return ", ".join(raw)
        return str(raw)

    # For already-reconciled records, compute which labeler (if any)
    # the current gold value for each field matches, so we can pre-
    # select the right radio. If no labeler matches, we pre-select
    # __custom__ and pre-fill the custom widget with the gold value.
    def _pick_for(name: str):
        if reconciliation is None:
            return None, None
        gold = reconciliation["values"].get(name)
        gold_canon = _canon(gold)
        for s in subs:
            if _canon(s["values"].get(name)) == gold_canon:
                return s["username"], None
        return "__custom__", gold

    for f in label_fields_meta:
        name = f["name"]
        label = f.get("label", name)
        kind = f.get("kind", "")
        vals_canon = [_canon(s["values"].get(name)) for s in subs]
        all_agree = len(set(vals_canon)) == 1
        row_class = "agree" if all_agree else "diff"

        pick_user, custom_prefill = _pick_for(name)

        cells = ""
        for i, s in enumerate(subs):
            username = s["username"]
            raw = s["values"].get(name)
            display = _display_value(raw)
            if reconciliation is not None:
                checked = "checked" if pick_user == username else ""
            else:
                checked = "checked" if all_agree and i == 0 else ""
            if kind == "free_text":
                body_html = (
                    f'<div class="free-text">{_esc(display) if display else "<em>∅</em>"}</div>'
                )
            else:
                body_html = f'<strong>{_esc(display)}</strong>'
            cells += (
                f'<td class="pick"><label>'
                f'<input type="radio" name="pick_{_esc(name)}" '
                f'value="{_esc(username)}" {checked} required>'
                f'{body_html}'
                f'</label></td>'
            )

        # Custom-override cell
        custom_widget = _render_custom_widget(f, prefill=custom_prefill)
        custom_checked = "checked" if pick_user == "__custom__" else ""
        cells += (
            f'<td class="pick custom"><label>'
            f'<input type="radio" name="pick_{_esc(name)}" value="__custom__" {custom_checked}>'
            f'<em>custom value</em>'
            f'</label>'
            f'<div class="custom-widget">{custom_widget}</div>'
            f'</td>'
        )

        rows_html += (
            f'<tr class="{row_class}">'
            f'<th class="field">{_esc(label)}</th>'
            f'{cells}</tr>'
        )

    diff_count = sum(
        1 for fn in label_fields
        if len({_canon(s["values"].get(fn)) for s in subs}) > 1
    )
    progress = f'{position} of {total}' if total else "—"
    nav_qs = urlencode({"supervisor": supervisor, "view": view})

    def _nav_link(target_rid, label_text: str, is_back: bool) -> str:
        if target_rid is None:
            return f'<span class="muted">{label_text}</span>'
        return (
            f'<a href="/flywheel/reconcile/{target_rid}?{nav_qs}">{label_text}</a>'
        )

    prev_link = _nav_link(prev_rid, "← Prev", True)
    next_link = _nav_link(next_rid, "Next →", False)

    # Banner + undo form for reviewed records
    banner_html = ""
    undo_html = ""
    submit_label_text = "Accept &amp; next →"
    if reconciliation is not None:
        banner_html = (
            f'<div class="banner">'
            f'<strong>Already reconciled</strong> by '
            f'<code>{_esc(reconciliation["supervisor"])}</code> at '
            f'<code>{_esc(reconciliation["reconciled_at"])}</code>. '
            f'You can change any pick and submit to overwrite the decision, '
            f'or <em>Undo</em> to return this record to the Pending queue.'
            f'</div>'
        )
        undo_html = (
            f'<form class="undo-form" method="post" '
            f'action="/flywheel/reconcile/{record_id}/undo">'
            f'<input type="hidden" name="view" value="{_esc(view)}">'
            f'<button type="submit" class="undo-btn" '
            f'onclick="return confirm(\'Undo this reconciliation? The record '
            f'will go back to the Pending queue.\');">Undo reconciliation</button>'
            f'</form>'
        )
        submit_label_text = "Save changes →"

    body = f"""
      <h1>{"Review" if reconciliation else "Reconcile"} record #{record_id}</h1>
      <p class="muted">supervisor: <strong>{_esc(supervisor)}</strong>
      · <span class="progress">{progress} ({view})</span>
      · <strong>{diff_count}</strong> field(s) in disagreement
      · <a href="/flywheel/reconcile?{nav_qs}">queue</a></p>
      {banner_html}
      {meta_html}
      <blockquote>{_esc(src_row[source["text_field"]])}</blockquote>
      {context_html}
      <form method="post" action="/flywheel/reconcile/{record_id}/submit">
        <input type="hidden" name="view" value="{_esc(view)}">
        <table class="reconcile">
          <thead><tr><th class="field">Field</th>{header_cells}</tr></thead>
          <tbody>{rows_html}</tbody>
        </table>
        <div class="reconcile-nav">
          <span>{prev_link}</span>
          <span>
            <button type="submit">{submit_label_text}</button>
            {undo_html}
          </span>
          <span>{next_link}</span>
        </div>
        <p class="muted" style="margin-top:1em">
          Agreement rows are pre-selected and grayed out. Diff rows are highlighted —
          click which labeler's answer should become the gold value for that field.
          If neither labeler is right, select <em>custom value</em> in the rightmost
          column and fill in the widget.
        </p>
      </form>
    """
    return _page(body)


async def reconcile_undo(scope, receive, datasette, request):
    supervisor = _require_actor(request)
    if supervisor is None:
        return _login_redirect(request)
    forbidden = _require_supervisor(request)
    if forbidden:
        return forbidden
    cfg = load_config()
    record_id = int(request.url_vars["record_id"])
    body_bytes = await request.post_body()
    parsed = parse_qs(body_bytes.decode("utf-8"))

    db = _get_db(datasette)
    await db.execute_write(
        "DELETE FROM reconciliations WHERE record_id = ?", [record_id]
    )

    # Redirect back to the same record detail (now pending again).
    return Response.redirect(
        f"/flywheel/reconcile/{record_id}?"
        f"{urlencode({'supervisor': supervisor, 'view': 'pending'})}"
    )


def _parse_custom_value(field_meta: dict, parsed: dict) -> tuple:
    """Parse a single field's custom-override value from a parsed form body.

    Returns ``(value, errors)``. Errors is a list of human-readable
    strings — empty on success. Mirrors the validation rules used by
    ``submit_label`` but reads from the ``custom_<name>`` namespace.
    """
    name = field_meta["name"]
    kind = field_meta["kind"]
    label = field_meta.get("label", name)
    custom_key = f"custom_{name}"
    errors: list = []

    if kind == "single_select":
        v = (parsed.get(custom_key) or [""])[0]
        if not v:
            errors.append(f"{label}: custom value required")
        elif v not in (field_meta.get("choices") or []):
            errors.append(f"{label}: custom value {v!r} not in choices")
        return v or None, errors

    if kind == "multi_select":
        raw = parsed.get(custom_key) or []
        allowed = set(field_meta.get("choices") or [])
        bad = [x for x in raw if x not in allowed]
        if bad:
            errors.append(f"{label}: custom values {bad!r} not in choices")
        min_sel = field_meta.get("min_selections", 0)
        max_sel = field_meta.get("max_selections")
        if len(raw) < min_sel:
            errors.append(f"{label}: custom value needs at least {min_sel}")
        if max_sel is not None and len(raw) > max_sel:
            errors.append(f"{label}: custom value can have at most {max_sel}")
        return list(raw), errors

    if kind == "ordinal":
        v = (parsed.get(custom_key) or [""])[0]
        if not v:
            errors.append(f"{label}: custom value required")
            return None, errors
        try:
            iv = int(v)
        except ValueError:
            errors.append(f"{label}: custom value must be an integer")
            return None, errors
        lo, hi = field_meta["min"], field_meta["max"]
        if not (lo <= iv <= hi):
            errors.append(f"{label}: custom value out of range [{lo},{hi}]")
        return iv, errors

    if kind == "free_text":
        v = (parsed.get(custom_key) or [""])[0]
        if field_meta.get("required") and not v:
            errors.append(f"{label}: custom value required")
        return v, errors

    if kind == "hierarchical_multi_select":
        levels = field_meta.get("levels") or []
        if len(levels) != 2:
            errors.append(f"{label}: schema must declare exactly 2 levels")
            return [], errors
        p_level = levels[0]["name"]
        c_level = levels[1]["name"]
        parents_raw = parsed.get(f"{custom_key}__{p_level}", [])
        children_raw = parsed.get(f"{custom_key}__{c_level}", [])
        valid = {
            p["name"]: {c["name"] for c in (p.get("children") or [])}
            for p in (field_meta.get("choices") or [])
        }
        entries: list = []
        for pval, cval in zip(parents_raw, children_raw):
            if not pval and not cval:
                continue
            if not pval or not cval:
                errors.append(
                    f"{label}: custom entry needs both a {p_level} and a {c_level}"
                )
                continue
            if pval not in valid:
                errors.append(
                    f"{label}: custom {p_level}={pval!r} not in choices"
                )
                continue
            if cval not in valid[pval]:
                errors.append(
                    f"{label}: custom {c_level}={cval!r} not a valid child of {pval!r}"
                )
                continue
            entries.append({p_level: pval, c_level: cval})
        min_entries = int(field_meta.get("min_entries", 0))
        max_entries = int(field_meta.get("max_entries", 99))
        if field_meta.get("required") and not entries:
            errors.append(f"{label}: custom value required")
        if len(entries) < min_entries:
            errors.append(f"{label}: custom value needs at least {min_entries} entries")
        if len(entries) > max_entries:
            errors.append(f"{label}: custom value can have at most {max_entries} entries")
        return entries, errors

    errors.append(f"{label}: unsupported kind {kind!r}")
    return None, errors


async def reconcile_submit(scope, receive, datasette, request):
    supervisor = _require_actor(request)
    if supervisor is None:
        return _login_redirect(request)
    forbidden = _require_supervisor(request)
    if forbidden:
        return forbidden
    cfg = load_config()
    record_id = int(request.url_vars["record_id"])

    body_bytes = await request.post_body()
    parsed = parse_qs(body_bytes.decode("utf-8"))
    view = parsed.get("view", ["pending"])[0]
    if view not in ("pending", "reviewed"):
        view = "pending"

    # fetch the current submissions for this record so we can look up
    # the chosen labeler's value per field
    db = _get_db(datasette)
    result = await db.execute(
        "SELECT username, values_json FROM submissions WHERE record_id = :rid",
        {"rid": record_id},
    )
    subs_by_user = {row["username"]: json.loads(row["values_json"]) for row in result.rows}

    fields_meta = cfg["fields"]
    gold_values: dict = {}
    issues: list = []
    for f in fields_meta:
        name = f["name"]
        picked_user = (parsed.get(f"pick_{name}") or [""])[0]
        if not picked_user:
            issues.append(f"{f.get('label', name)}: no pick selected")
            continue
        if picked_user == "__custom__":
            value, errs = _parse_custom_value(f, parsed)
            if errs:
                issues.extend(errs)
                continue
            gold_values[name] = value
            continue
        if picked_user not in subs_by_user:
            issues.append(
                f"{f.get('label', name)}: picked user {picked_user!r} not found"
            )
            continue
        gold_values[name] = subs_by_user[picked_user].get(name)

    if issues:
        items = "".join(f"<li>{_esc(m)}</li>" for m in issues)
        return _page(
            f'<h1>Reconciliation incomplete</h1>'
            f'<div class="errors"><p>Issues:</p><ul>{items}</ul></div>'
            f'<p><a href="/flywheel/reconcile/{record_id}?{urlencode({"supervisor": supervisor})}">← Back</a></p>'
        )

    now = datetime.datetime.utcnow().isoformat(timespec="seconds")
    await db.execute_write(
        "INSERT OR IGNORE INTO users (username, role, created_at) VALUES (?, 'supervisor', ?)",
        [supervisor, now],
    )
    await db.execute_write(
        "INSERT OR REPLACE INTO reconciliations "
        "(record_id, supervisor, reconciled_at, values_json) VALUES (?, ?, ?, ?)",
        [record_id, supervisor, now, json.dumps(gold_values)],
    )

    # auto-advance: next record in whichever queue we came from
    if view == "reviewed":
        reviewed, _, _ = await _reviewed_queue(datasette)
        try:
            idx = reviewed.index(record_id) + 1
        except ValueError:
            idx = 0
        if idx < len(reviewed):
            return Response.redirect(
                f"/flywheel/reconcile/{reviewed[idx]}?"
                f"{urlencode({'supervisor': supervisor, 'view': 'reviewed'})}"
            )
        return Response.redirect(
            f"/flywheel/reconcile?{urlencode({'supervisor': supervisor, 'view': 'reviewed'})}"
        )

    contested, _ = await _contested_queue(datasette, cfg)
    if contested:
        return Response.redirect(
            f"/flywheel/reconcile/{contested[0]}?"
            f"{urlencode({'supervisor': supervisor, 'view': 'pending'})}"
        )
    return Response.redirect(
        f"/flywheel/reconcile?{urlencode({'supervisor': supervisor, 'view': 'pending'})}"
    )
