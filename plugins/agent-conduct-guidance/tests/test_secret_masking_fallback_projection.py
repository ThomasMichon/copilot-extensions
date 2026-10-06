"""Contract test for the secret-masking-fallback static instruction projection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_PLUGIN = Path(__file__).resolve().parents[1]
_DECLARATION = _PLUGIN / "instruction-projections.json"
_TEMPLATE = _PLUGIN / "instructions" / "secret-masking-fallback.instructions.md"


def test_projection_declaration_shape():
    declaration = json.loads(_DECLARATION.read_text(encoding="utf-8"))
    assert declaration["schema"] == "copilot-extensions.instruction-projections"
    projections = [
        p for p in declaration["projections"] if p["id"] == "secret-masking-fallback"
    ]
    assert len(projections) == 1
    projection = projections[0]
    assert projection["template"] == (
        "instructions/secret-masking-fallback.instructions.md"
    )
    assert projection["destination"] == (
        ".github/instructions/agent-conduct-guidance/"
        "secret-masking-fallback.instructions.md"
    )
    assert projection["applyTo"] == "**"
    assert projection["legacyMarkers"] == []


def test_template_is_reviewable_static_fallback():
    content = _TEMPLATE.read_bytes()
    assert content.startswith(b'---\napplyTo: "**"\n---\n')
    assert b"avoiding-secret-pattern-masking" in content
    assert b"agent-conduct-guidance@" in content
    # Never a literal six-asterisk masked run -- the whole point of this
    # instruction is to describe the masking hazard without reproducing
    # the exact trigger shape that causes it.
    assert (b"*" * 6) not in content
    # No live/session/host state -- checked-in instructions never interpolate.
    for forbidden in (
        b"COPILOT_AGENT_SESSION_ID",
        b"session-state",
        b"C:\\",
        b"/home/",
    ):
        assert forbidden not in content


def test_skill_doc_never_reproduces_the_masked_placeholder():
    skill = _PLUGIN / "skills" / "avoiding-secret-pattern-masking" / "SKILL.md"
    content = skill.read_bytes()
    assert content.startswith(b"---\nname: avoiding-secret-pattern-masking\n")
    assert (b"*" * 6) not in content
