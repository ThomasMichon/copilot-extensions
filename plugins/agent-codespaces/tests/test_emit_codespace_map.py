"""Tests for the emit_codespace_map sessionStart hook logic."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

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
    assert r["role"] == "product"
    assert r["locus"] == "codespace"
    assert r["delegate"] == "agent-codespaces"


def test_render_is_brief_markdown():
    md = mod._render(mod._codespace_delegated(RELATED))
    assert md.startswith("## CodeSpace-delegated repos")
    assert "**example-web**" in md
    assert "/workspaces/example-web" in md
    # Only the delegated repo appears.
    assert "sample-sibling" not in md
    assert "sample-standard" not in md


def test_missing_codespace_locus_is_not_a_codespace_route():
    related = [{
        "name": "bare",
        "delegate": "agent-codespaces",
        "locus": {},
    }]
    assert mod._codespace_delegated(related) == []


def test_aggregate_render_is_owned_and_bounded():
    rows = mod._codespace_delegated(RELATED)
    context = mod._render_aggregate(rows * 20, "1.2.3")
    assert context.startswith("[owner: agent-codespaces@1.2.3]\n")
    assert "No local checkout" in context
    assert "delegate=agent-codespaces" in context
    assert "example-web(role=product,locus=codespace)" in context
    assert "+16 more" in context
    assert len(mod._serialize_context(context).encode("utf-8")) <= 384


def test_aggregate_render_includes_exact_route_fields():
    context = mod._render_aggregate(mod._codespace_delegated(RELATED), "1.2.3")
    assert (
        "CodeSpace routes (delegate=agent-codespaces): "
        "example-web(role=product,locus=codespace)"
    ) in context


def test_missing_preferred_locus_uses_related_default_and_is_filtered():
    assert mod._codespace_delegated(
        [
            {
                "name": "bare",
                "role": "tooling",
                "delegate": "agent-codespaces",
                "locus": {},
            }
        ]
    ) == []


def test_preferred_locus_kind_is_case_insensitive():
    rows = mod._codespace_delegated(
        [
            {
                "name": "case-route",
                "role": "tooling",
                "delegate": "agent-codespaces",
                "locus": {"preferred": "CodeSpace"},
            }
        ]
    )

    assert rows[0]["locus"] == "codespace"


def test_empty_when_no_delegated_repos():
    assert mod._codespace_delegated([]) == []
    assert mod._codespace_delegated(RELATED[1:]) == []


def test_additional_context_shape():
    # The rendered payload round-trips as the hook contract JSON.
    md = mod._render(mod._codespace_delegated(RELATED))
    payload = json.dumps({"additionalContext": md})
    assert json.loads(payload)["additionalContext"] == md


def test_empty_emission_has_no_record_separator(capsys):
    with pytest.raises(SystemExit):
        mod._emit_empty()

    assert capsys.readouterr().out == "{}"


def test_powershell_wrapper_preserves_newline_free_output():
    wrapper = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / "emit-codespace-map.ps1"
    ).read_text(encoding="utf-8")

    assert "Write-Output" not in wrapper
    assert "[Console]::Out.Write([string]$out)" in wrapper
