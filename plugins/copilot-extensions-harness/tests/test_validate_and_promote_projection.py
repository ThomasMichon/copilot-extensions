"""Contract test for the validate-and-promote-triage static instruction
projection and its troubleshooting-index coverage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_PLUGIN = Path(__file__).resolve().parents[1]
_DECLARATION = _PLUGIN / "instruction-projections.json"
_TEMPLATE = _PLUGIN / "instructions" / "validate-and-promote-triage.instructions.md"
_TROUBLESHOOTING_INDEX = _PLUGIN / "troubleshooting-index.json"

_SCRIPTS = (
    _PLUGIN.parent
    / "customizing-copilot"
    / "skills"
    / "reviewing-customizations"
    / "scripts"
)
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import troubleshooting_index as tix  # noqa: E402


def test_projection_declaration_shape():
    declaration = json.loads(_DECLARATION.read_text(encoding="utf-8"))
    by_id = {p["id"]: p for p in declaration["projections"] if isinstance(p, dict)}
    assert "validate-and-promote-triage" in by_id
    projection = by_id["validate-and-promote-triage"]
    assert projection["template"] == "instructions/validate-and-promote-triage.instructions.md"
    assert projection["destination"] == (
        ".github/instructions/copilot-extensions-harness/"
        "validate-and-promote-triage.instructions.md"
    )
    assert projection["applyTo"] == "**"
    assert projection["legacyMarkers"] == []


def test_template_is_reviewable_static_fallback():
    content = _TEMPLATE.read_text(encoding="utf-8")
    assert content.startswith('---\napplyTo: "**"\n---\n')
    assert "validate-and-promote" in content
    assert "contributing-to-copilot-extensions" in content
    # No live/session/host state -- a checked-in static fallback must never
    # embed a resolved filesystem path or session identifier.
    for forbidden in (
        "COPILOT_AGENT_SESSION_ID",
        "session-state",
        "C:\\",
        "/home/",
    ):
        assert forbidden not in content


def test_troubleshooting_index_declares_category():
    declaration = json.loads(_TROUBLESHOOTING_INDEX.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in declaration["categories"] if isinstance(c, dict)}
    assert "validate-and-promote-failure" in by_id


def test_troubleshooting_category_is_covered_by_the_static_projection():
    uncovered = {c.id for c in tix.uncovered_categories(_PLUGIN)}
    assert "validate-and-promote-failure" not in uncovered
