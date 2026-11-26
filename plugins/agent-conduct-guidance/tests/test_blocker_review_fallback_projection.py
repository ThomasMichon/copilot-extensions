"""Contract test for the blocker-review-fallback static instruction projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_PLUGIN = Path(__file__).resolve().parents[1]
_DECLARATION = _PLUGIN / "instruction-projections.json"
_TEMPLATE = _PLUGIN / "instructions" / "blocker-review-fallback.instructions.md"
_SKILL = _PLUGIN / "skills" / "reviewing-blockers-skeptically" / "SKILL.md"


def test_projection_declaration_shape() -> None:
    declaration = json.loads(_DECLARATION.read_text(encoding="utf-8"))
    assert declaration["schema"] == "copilot-extensions.instruction-projections"
    projections = [
        p for p in declaration["projections"] if p["id"] == "blocker-review-fallback"
    ]
    assert len(projections) == 1
    projection = projections[0]
    assert projection["template"] == (
        "instructions/blocker-review-fallback.instructions.md"
    )
    assert projection["destination"] == (
        ".github/instructions/agent-conduct-guidance/"
        "blocker-review-fallback.instructions.md"
    )
    assert projection["applyTo"] == "**"
    assert projection["legacyMarkers"] == []


def test_template_is_reviewable_static_fallback() -> None:
    content = _TEMPLATE.read_bytes()
    assert content.startswith(b'---\napplyTo: "**"\n---\n')
    assert b"reviewing-blockers-skeptically" in content
    assert b"agent-conduct-guidance@" in content
    # The skeptical pass must never erode a real hard stop.
    assert b"hard stops" in content
    # No live/session/host state -- checked-in instructions never interpolate.
    for forbidden in (
        b"COPILOT_AGENT_SESSION_ID",
        b"session-state",
        b"C:\\",
        b"/home/",
    ):
        assert forbidden not in content


def test_skill_keeps_hard_stops_and_negative_scope() -> None:
    content = _SKILL.read_text(encoding="utf-8")
    assert content.startswith("---\nname: reviewing-blockers-skeptically\n")
    frontmatter = content.split("---", 2)[1]
    assert "Not for" in frontmatter
    assert "Hard stops end the attempt now" in content
