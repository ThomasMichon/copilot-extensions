"""Tests for the emit_codespace_map sessionStart hook logic."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT = (
    Path(__file__).resolve().parents[1] / "scripts" / "emit_codespace_map.py"
)
_spec = importlib.util.spec_from_file_location("emit_codespace_map", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


RELATED = [
    {
        "name": "example-web",
        "role": "product",
        "summary": "Example web product monorepo.",
        "delegate": "agent-codespaces",
        "locus": {
            "preferred": "codespace",
            "codespace": {
                "repo": "example-org/example-web-codespaces",
                "machine": "largePremiumLinux256gb",
                "location": "EastUs",
                "workspace_folder": "/workspaces/example-web",
            },
        },
    },
    {
        "name": "sample-sibling",
        "role": "sibling",
        "summary": "sample sibling.",
        "delegate": "agent-bridge",
        "locus": {"preferred": "local", "codespace": {}},
    },
    {
        "name": "sample-standard",
        "role": "sibling",
        "summary": "standard convention repo.",
        "delegate": "none",
        "locus": {"preferred": "local", "codespace": {}},
    },
]


def test_filters_only_codespace_delegated():
    rows = mod._codespace_delegated(RELATED)
    assert [r["name"] for r in rows] == ["example-web"]
    r = rows[0]
    assert r["vessel"] == "example-org/example-web-codespaces"
    assert r["workspace_folder"] == "/workspaces/example-web"
    assert r["machine"] == "largePremiumLinux256gb"


def test_render_is_brief_markdown():
    md = mod._render(mod._codespace_delegated(RELATED))
    assert md.startswith("## CodeSpace-delegated repos")
    assert "**example-web**" in md
    assert "/workspaces/example-web" in md
    # Only the delegated repo appears.
    assert "sample-sibling" not in md
    assert "sample-standard" not in md


def test_render_survives_missing_codespace_block():
    related = [{
        "name": "bare",
        "delegate": "agent-codespaces",
        "locus": {},
    }]
    rows = mod._codespace_delegated(related)
    assert rows[0]["name"] == "bare"
    md = mod._render(rows)
    assert "**bare**" in md


def test_aggregate_render_is_owned_and_bounded():
    rows = mod._codespace_delegated(RELATED)
    context = mod._render_aggregate(rows * 20, "1.2.3")
    assert context.startswith("[owner: agent-codespaces@1.2.3]\n")
    assert "no local checkout" in context
    assert "`agent-codespaces` skill" in context
    assert len(context.encode("utf-8")) <= 384


def test_empty_when_no_delegated_repos():
    assert mod._codespace_delegated([]) == []
    assert mod._codespace_delegated(RELATED[1:]) == []


def test_additional_context_shape():
    # The rendered payload round-trips as the hook contract JSON.
    md = mod._render(mod._codespace_delegated(RELATED))
    payload = json.dumps({"additionalContext": md})
    assert json.loads(payload)["additionalContext"] == md
