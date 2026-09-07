"""Contract test for the contribution-boundary static instruction projection."""

from __future__ import annotations

import json
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[1]
_DECLARATION = _PLUGIN / "instruction-projections.json"
_TEMPLATE = _PLUGIN / "instructions" / "contribution-boundary.instructions.md"


def test_projection_declaration_shape():
    declaration = json.loads(_DECLARATION.read_text(encoding="utf-8"))
    assert declaration["schema"] == "copilot-extensions.instruction-projections"
    projections = declaration["projections"]
    assert len(projections) == 1
    projection = projections[0]
    assert projection["id"] == "contribution-boundary"
    assert projection["template"] == "instructions/contribution-boundary.instructions.md"
    assert projection["destination"] == (
        ".github/instructions/copilot-extensions-harness/"
        "contribution-boundary.instructions.md"
    )
    assert projection["applyTo"] == "**"
    assert projection["legacyMarkers"] == []


def test_template_is_reviewable_static_fallback():
    content = _TEMPLATE.read_text(encoding="utf-8")
    assert content.startswith('---\napplyTo: "**"\n---\n')
    assert "contributing-to-copilot-extensions" in content
    assert "copilot-extensions-harness@" in content
    assert "organization-neutral" in content
    # No live/session/host state, and no resolved filesystem path to the
    # reference doc -- the additionalContext contributor embeds a literal
    # path, which is exactly what a checked-in static fallback must avoid.
    for forbidden in (
        "COPILOT_AGENT_SESSION_ID",
        "session-state",
        "C:\\",
        "/home/",
        "references/contribution-ground-rules.md",
    ):
        assert forbidden not in content
