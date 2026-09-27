"""Contract test for the delegation-fallback static instruction projection."""

from __future__ import annotations

import json
from pathlib import Path

_PLUGIN = Path(__file__).resolve().parents[1]
_DECLARATION = _PLUGIN / "instruction-projections.json"
_TEMPLATE = _PLUGIN / "instructions" / "delegation-fallback.instructions.md"


def _projection_by_id(declaration: dict, projection_id: str) -> dict:
    matches = [p for p in declaration["projections"] if p["id"] == projection_id]
    assert len(matches) == 1
    return matches[0]


def test_projection_declaration_shape():
    declaration = json.loads(_DECLARATION.read_text(encoding="utf-8"))
    assert declaration["schema"] == "copilot-extensions.instruction-projections"
    projection = _projection_by_id(declaration, "delegation-fallback")
    assert projection["template"] == "instructions/delegation-fallback.instructions.md"
    assert projection["destination"] == (
        ".github/instructions/agent-conduct-guidance/delegation-fallback.instructions.md"
    )
    assert projection["applyTo"] == "**"
    assert projection["legacyMarkers"] == []


def test_template_is_reviewable_static_fallback():
    content = _TEMPLATE.read_text(encoding="utf-8")
    assert content.startswith('---\napplyTo: "**"\n---\n')
    assert "delegating-work" in content
    assert "agent-conduct-guidance@" in content
    # No live/session/host state -- checked-in instructions never interpolate.
    for forbidden in (
        "COPILOT_AGENT_SESSION_ID",
        "session-state",
        "C:\\",
        "/home/",
    ):
        assert forbidden not in content
