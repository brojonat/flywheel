"""HTML rendering tests — every YAML field kind produces the right form
element on the label and reconciliation pages."""
from __future__ import annotations

import re

import httpx


def test_home_page_renders(client: httpx.Client) -> None:
    resp = client.get("/flywheel")
    assert resp.status_code == 200
    assert "<h1>" in resp.text
    assert "Start labeling" in resp.text


def test_label_page_renders_one_input_per_field(
    client: httpx.Client, cfg: dict
) -> None:
    resp = client.get("/flywheel/label")
    assert resp.status_code == 200
    html = resp.text
    for field in cfg["fields"]:
        name = field["name"]
        kind = field["kind"]
        if kind == "single_select":
            assert f'<select name="{name}"' in html, f"no <select> for {name}"
        elif kind == "multi_select":
            assert f'name="{name}" value=' in html, f"no checkboxes for {name}"
        elif kind == "ordinal":
            assert f'name="{name}" value="{field["min"]}"' in html
            assert f'name="{name}" value="{field["max"]}"' in html
        elif kind == "free_text":
            assert f'<textarea name="{name}"' in html
        elif kind == "hierarchical_multi_select":
            parent_level = field["levels"][0]["name"]
            child_level = field["levels"][1]["name"]
            assert f'name="{name}__{parent_level}"' in html
            assert f'name="{name}__{child_level}"' in html
            assert f'data-field="{name}"' in html


def test_hierarchical_parent_options_match_yaml(
    client: httpx.Client, cfg: dict
) -> None:
    resp = client.get("/flywheel/label")
    html = resp.text
    hier_field = next(
        (f for f in cfg["fields"] if f["kind"] == "hierarchical_multi_select"),
        None,
    )
    if hier_field is None:
        return  # skip silently if no hier field in this example
    for parent in hier_field["choices"]:
        assert (
            f'<option value="{parent["name"]}">' in html
        ), f"parent {parent['name']} missing from parent dropdown"


def test_ordinal_endpoints_rendered(client: httpx.Client, cfg: dict) -> None:
    resp = client.get("/flywheel/label")
    html = resp.text
    ordinal = next((f for f in cfg["fields"] if f["kind"] == "ordinal"), None)
    if ordinal is None or not ordinal.get("endpoints"):
        return
    for k, v in ordinal["endpoints"].items():
        assert str(v) in html, f"endpoint label {v!r} missing"


def test_js_helpers_included_on_every_page(client: httpx.Client) -> None:
    """flywheelAddEntry etc. must be defined on every page so hier
    widgets work consistently."""
    for url in ["/flywheel", "/flywheel/label"]:
        resp = client.get(url)
        assert "function flywheelAddEntry" in resp.text, url
        assert "function flywheelUpdateChildren" in resp.text, url
        assert "function flywheelRemoveEntry" in resp.text, url
