"""Declarative capture/delivery contracts, not automatic permission or model evaluation."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.guard

_PLUGIN = Path(__file__).resolve().parents[1]
_SKILL = _PLUGIN / "skills/using-standing-authority/SKILL.md"
_RECORD = _SKILL.parent / "references/approval-evidence.md"
_DECISION = _SKILL.parent / "references/decision-preparation.md"
_TEMPLATE = _PLUGIN / "instructions/useful-autonomy.instructions.md"
_SCRIPTS = _PLUGIN.parent / "customizing-copilot/skills/reviewing-customizations/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import instruction_projections as projections  # noqa: E402
import scan_skills  # noqa: E402


def test_owned_kernel_is_declared_bounded_and_has_an_offline_fallback(tmp_path):
    source = SimpleNamespace(
        origin="copilot-extensions/agent-conduct-guidance",
        payload_root=_PLUGIN, skills_root=_PLUGIN / "skills",
        controlled=False, source="", version="",
    )
    result = projections.Result(operation="test")
    specs, unknown = projections._load_specs(tmp_path, [source], result)
    assert not unknown and result.blocking == 0, result.findings
    matches = [spec for spec in specs if spec.source_id == "useful-autonomy"]
    assert len(matches) == 1
    spec = matches[0]
    assert spec.apply_to == "**"
    assert spec.template_bytes <= projections.MAX_TEMPLATE_BYTES
    rendered = projections.render_projection(spec)
    assert rendered.byte_count <= projections.MAX_PROJECTION_BYTES
    assert rendered.marker["deliveryKind"] == "selector"
    assert rendered.fallback_content is not None
    fallback = rendered.fallback_content.decode()
    assert "[owner: agent-conduct-guidance]" in fallback
    assert "using-standing-authority" in fallback


def test_skill_and_record_are_discoverable_without_a_runtime():
    manifest = json.loads((_PLUGIN / "plugin.json").read_text())
    assert manifest["runtimeScope"] == "none"
    assert not any(manifest.get(key) for key in ("hooks", "mcpServers", "agents", "commands"))
    content = _SKILL.read_text()
    parsed = scan_skills.split_frontmatter(content)
    assert parsed is not None
    frontmatter, _ = parsed
    assert scan_skills.get_field(frontmatter, "name") == "using-standing-authority"
    assert len(scan_skills.get_field_block(frontmatter, "description")) <= 1024
    assert scan_skills.extract_triggers(frontmatter) == [
        "pre-clear this scope", "record a standing approval",
        "approval blast radius", "carry approval through a handoff",
    ]
    assert len(content.splitlines()) < 500
    assert "references/approval-evidence.md" in content
    assert "../SKILL.md" in _RECORD.read_text()
    for path in (_TEMPLATE, _SKILL, _RECORD, _DECISION):
        text = path.read_text()
        assert text.isascii()
        for forbidden in ("COPILOT_AGENT_SESSION_ID", "C:\\", "/home/", "session-state/"):
            assert forbidden not in text


@pytest.mark.parametrize("clause", [
    "operator-established charter",
    "diagnose a concrete failure read-only",
    "need applicable explicit effect authority",
    "expected recoverability never create permission",
    "Capture upfront approvals and one-time answers immediately",
    "original operator source/reference and necessary exact wording",
    "later narrowing/revocation",
    "Preserve explicit revision pins",
    "reconcile unknown prior effects",
    '"Pause here" is not an alternative',
    "not automatic tool approval",
])
def test_kernel_preserves_useful_work_and_real_authority_boundaries(clause):
    assert clause in " ".join(_TEMPLATE.read_text().split())


@pytest.mark.parametrize(("situation", "required"), [
    (
        "New source revision or corrected command remains within an unpinned repeating scope",
        "Refresh execution evidence and continue; do not manufacture a new approval boundary.",
    ),
    (
        "Original operator answer pins a revision or permits only one attempt",
        "Preserve the pin/use limit; obtain a real amendment before exceeding it.",
    ),
    (
        "Prior invocation may already have produced effects",
        "Reconcile through the existing owner with the original identity; do not blindly replay.",
    ),
    (
        "Low-risk tooling approval is reused for a prototype that mutates personal archives",
        "Keep content mutation gated; deployment recoverability does not authorize data effects.",
    ),
    (
        "Later authoritative pause, expiry, narrowing or revocation applies",
        "Record and honor it before effects; a copied older approval does not override it.",
    ),
    (
        "Original approval evidence is missing or unreadable",
        "Name the affected gap, use bounded evidence recovery and continue other authorized work.",
    ),
])
def test_record_preserves_explicit_decision_cases(situation, required):
    assert f"| {situation} | {required} |" in _RECORD.read_text()


def test_blocking_question_follows_decision_preparation_not_presence_polling():
    kernel = " ".join(_TEMPLATE.read_text().split())
    for clause in (
        "Defer a blocking question",
        "conditional future projections",
        "Autopilot is continuation, not operator-presence evidence",
        "do not default to presence polling",
        "Explicit all-work holds",
    ):
        assert clause in kernel
    skill = " ".join(_SKILL.read_text().split())
    assert "Before a blocking question, build the" in skill
    assert "references/decision-preparation.md" in skill
    packet = " ".join(_DECISION.read_text().split())
    for clause in (
        "proposal, not",
        "actual new response",
        "does not authorize unrelated data access",
        "absent trend data stays unavailable",
        "general class",
        "exact operations/resources/effects/owners/limits",
        "when only a draft exists",
        "not a daemon, approval renewal",
    ):
        assert clause in packet.lower()
