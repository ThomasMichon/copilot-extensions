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
    "not its narrowest past invocation",
    "a failure followed by a reviewed fix is such a retry",
    "accepted rerun caps",
    "a pin or one-shot limit in the agent's only approving option is its proposal",
    "seek class-level pre-clearance",
    "managing its own PRs and the branches it created never needs an approval prompt",
    "push or force-push its own branch",
    "re-request review and reply to threads",
    "A mechanical rebase or conflict-only push is not a new revision of intent",
    "caps bind intent-changing revisions",
    "add, move or delete files in its change",
    "An accepted expiry binds, so never invent one (default: until task completion)",
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
    (
        "Agent wrote a single attempt or revision pin into its own question and the operator "
        "chose the only approving option",
        "Record it as an agent-proposed precaution, not an operator pin; reuse the granted class "
        "within its effects.",
    ),
    (
        "A run under a cleared class fails and a reviewed fix lands",
        "Treat the rerun as an ordinary bounded retry; refresh execution evidence instead of "
        "asking again.",
    ),
    (
        "The same operation class would need approval a second time",
        "Ask once for class-level pre-clearance with a bounded rerun policy, not per invocation.",
    ),
    (
        "Operator accepts a class-level rerun policy with a run cap",
        "The cap binds as granted scope; ask for an amendment before exceeding it.",
    ),
    (
        "Author agent's own approved PR conflicts with its target branch",
        "Rebase its own branch, resolve the understood conflict, regenerate derived artifacts "
        "and push without an approval prompt; required review and CI apply to the new head.",
    ),
    (
        "A mechanical rebase or conflict-only push meets a review-round, revision or attempt cap",
        "Do not count it against the cap; caps bind intent-changing revisions.",
    ),
    (
        "Agent drafts a how-to-proceed question whose options are all narrow "
        '("exact", "one", "only", "read-only", a revision pin)',
        "Rewrite it: recommend the broadest option within the requested authority, add a "
        "standing option if it can recur and keep narrower options beside it with a named risk.",
    ),
    (
        "Operator picks a narrower option offered beside a broader one",
        "The narrowing binds; record it as operator-chosen and ask once at class level if the "
        "work later needs more.",
    ),
    (
        "Agent is drafting an approving option and considers adding an expiry or time window",
        "Omit it unless a real constraint requires one; an expiry in an option the operator "
        "accepts binds, even when the agent drafted it.",
    ),
    (
        "Author agent's own reviewed change deletes or moves files, including superseded "
        "same-purpose paths found during a rebase",
        "Treat it as an ordinary source edit visible in review, not a destructive deletion; "
        "data outside source control keeps its gates.",
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


def test_skill_checks_recorded_grants_and_limit_origin_before_asking():
    skill = " ".join(_SKILL.read_text().split())
    for clause in (
        "Before asking for approval of an operation, run this self-check",
        "Classify each recorded revision pin or one-shot limit by origin",
        "Accepted rerun caps, budgets and deadlines bind as granted scope",
        "only agent-proposed limits stand in the way",
        "Offer narrower one-shot or revision-pinned options only as clearly labeled alternatives",
        "Worked example: repeated qualification runs",
    ):
        assert clause in skill


def test_skill_treats_own_pr_maintenance_as_delivery_not_a_new_revision():
    skill = " ".join(_SKILL.read_text().split())
    for clause in (
        "never needs an operator approval prompt",
        "pushing or force-pushing its own branch",
        "regenerating derived artifacts",
        "re-requesting review and replying to review threads",
        "does not count against any review-round, revision or attempt cap",
        "Branches and PRs the agent did not create stay with their owners",
        "Worked example: rebasing an approved PR",
    ):
        assert clause in skill
