"""Contract test for the scratch-space-fallback static instruction projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_PLUGIN = Path(__file__).resolve().parents[1]
_DECLARATION = _PLUGIN / "instruction-projections.json"
_TEMPLATE = _PLUGIN / "instructions" / "scratch-space-fallback.instructions.md"
_SKILL = _PLUGIN / "skills" / "using-scratch-space" / "SKILL.md"


def test_projection_declaration_shape():
    declaration = json.loads(_DECLARATION.read_text(encoding="utf-8"))
    assert declaration["schema"] == "copilot-extensions.instruction-projections"
    projections = [
        p for p in declaration["projections"] if p["id"] == "scratch-space-fallback"
    ]
    assert len(projections) == 1
    projection = projections[0]
    assert projection["template"] == (
        "instructions/scratch-space-fallback.instructions.md"
    )
    assert projection["destination"] == (
        ".github/instructions/agent-conduct-guidance/"
        "scratch-space-fallback.instructions.md"
    )
    assert projection["applyTo"] == "**"
    assert projection["legacyMarkers"] == []


def test_template_is_reviewable_static_fallback():
    content = _TEMPLATE.read_text(encoding="utf-8")
    assert content.startswith('---\napplyTo: "**"\n---\n')
    assert "using-scratch-space" in content
    assert "agent-conduct-guidance@" in content
    assert "timestamp" in content.lower()
    # No live/session/host state, and no hardcoded path for any operator or
    # machine -- the resolution order is the portable part, not a literal.
    for forbidden in (
        "COPILOT_AGENT_SESSION_ID",
        "session-state",
        "C:\\",
        "/home/",
    ):
        assert forbidden not in content


def test_scratch_guidance_requires_attributable_task_folders():
    template = _TEMPLATE.read_text(encoding="utf-8")
    skill = _SKILL.read_text(encoding="utf-8")
    assert "worktree-stamped" in template
    assert "<owner-stamp>-<task-slug>-<YYYYMMDD-HHMMSS>" in skill
    for content in (template, skill):
        content = " ".join(content.split())
        assert "scratch-owner.json" in content
        assert "retention" in content
        assert "per-invocation" in content
        assert "global settings" in content
        assert "framework-owned artifacts" in content


def test_scratch_breadcrumb_does_not_replace_release_or_confirmation():
    template = _TEMPLATE.read_text(encoding="utf-8")
    skill = _SKILL.read_text(encoding="utf-8")
    assert "not a lease or deletion permission" in template
    assert "not a lease, permission grant" in skill
    for content in (template, skill):
        assert "existing owner" in content
        assert "deletion-confirmation policy" in content
    assert "If custody is unknown" in skill
