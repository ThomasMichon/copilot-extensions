"""Contract test for the cross-repo-debug-tracking static instruction projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_PLUGIN = Path(__file__).resolve().parents[1]
_DECLARATION = _PLUGIN / "instruction-projections.json"
_TEMPLATE = _PLUGIN / "instructions" / "cross-repo-debug-tracking.instructions.md"


def test_projection_declaration_shape():
    declaration = json.loads(_DECLARATION.read_text(encoding="utf-8"))
    assert declaration["schema"] == "copilot-extensions.instruction-projections"
    by_id = {p["id"]: p for p in declaration["projections"] if isinstance(p, dict)}
    assert "cross-repo-debug-tracking" in by_id
    projection = by_id["cross-repo-debug-tracking"]
    assert projection["template"] == (
        "instructions/cross-repo-debug-tracking.instructions.md"
    )
    assert projection["destination"] == (
        ".github/instructions/copilot-extensions-harness/"
        "cross-repo-debug-tracking.instructions.md"
    )
    assert projection["applyTo"] == "**"
    assert projection["legacyMarkers"] == []


def test_template_is_reviewable_static_fallback():
    content = _TEMPLATE.read_text(encoding="utf-8")
    assert content.startswith('---\napplyTo: "**"\n---\n')
    assert "copilot-extensions-harness@" in content
    assert "related resolve" in content
    assert "working-cross-repo" in content
    assert "cross-link" in content
    # No live/session/host state -- a checked-in static fallback must never
    # embed a resolved path or session identifier.
    for forbidden in (
        "COPILOT_AGENT_SESSION_ID",
        "session-state",
        "C:\\",
        "/home/",
    ):
        assert forbidden not in content
