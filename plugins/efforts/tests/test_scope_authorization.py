"""Optional effort authorization carriers; these checks do not issue grants."""

from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
_SKILL = _PLUGIN / "skills/planning-efforts/SKILL.md"
_REFERENCE = _SKILL.parent / "references/efforts.md"


def test_template_preserves_core_and_optional_approval_section():
    template = (_SKILL.parent / "assets/TEMPLATE.md").read_text()
    for heading in ("Guiding Intent", "Request", "Plan", "Validation Plan", "Journal"):
        assert f"## {heading}" in template
    assert "## Scope Approvals" in template
    assert "OPTIONAL -- omit when no additional scope approval is needed" in template
    assert template.index("## Request") < template.index("## Scope Approvals") < template.index("## Plan")
    charter = (_PLUGIN / "skills/driving-backlog-burndown/assets/CHARTER.md").read_text()
    assert "Canonical Scope Approvals" in charter
    assert "do not duplicate the ledger" in charter
    assert "current holds/revocations" in charter


def test_capture_is_immediate_source_bound_and_lean():
    skill = _SKILL.read_text()
    assert len(skill.splitlines()) < 500
    assert "references/request-capture.md" in skill
    capture = (_SKILL.parent / "references/request-capture.md").read_text()
    assert "actual words" in capture
    assert "Original approval evidence stays distinct" in capture
    assert "inception-transcript.md" in capture
    assert "public" in capture.lower()
    normalized = " ".join(skill.split())
    assert "Capture upfront approvals and one-time answers immediately" in normalized
    assert "stamp the current owner's checkpoint" in normalized
    assert "naming choice changes scope" in normalized


@pytest.mark.parametrize("clause", [
    "Existing efforts without it remain valid",
    "missing information is unresolved, not an unlimited grant",
    "standalone effort use",
    "original operator/charter reference",
    "accepted blast radius",
    "explicit pin, single attempt, expiry, pause or revocation",
    "not blind replay",
    "before effects",
    "does not create permission",
])
def test_record_reuse_does_not_change_adoption_or_effect_authority(clause):
    assert clause in " ".join(_REFERENCE.read_text().split())
