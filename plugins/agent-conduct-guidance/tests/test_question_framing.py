"""Question-framing contracts: decision-scoped options, not reinterpretation of answers."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.guard

_PLUGIN = Path(__file__).resolve().parents[1]
_TEMPLATE = _PLUGIN / "instructions/question-framing.instructions.md"
_SKILL = _PLUGIN / "skills/using-standing-authority/SKILL.md"
_DECISION = _SKILL.parent / "references/decision-preparation.md"
_SCRIPTS = _PLUGIN.parent / "customizing-copilot/skills/reviewing-customizations/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import instruction_projections as projections  # noqa: E402


def _normalized(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_question_framing_module_renders_a_bounded_owned_projection(tmp_path):
    source = SimpleNamespace(
        origin="copilot-extensions/agent-conduct-guidance",
        payload_root=_PLUGIN, skills_root=_PLUGIN / "skills",
        controlled=False, source="", version="",
    )
    result = projections.Result(operation="test")
    specs, unknown = projections._load_specs(tmp_path, [source], result)
    assert not unknown and result.blocking == 0, result.findings
    matches = [spec for spec in specs if spec.source_id == "question-framing"]
    assert len(matches) == 1
    spec = matches[0]
    assert spec.apply_to == "**"
    assert spec.destination == (
        ".github/instructions/agent-conduct-guidance/question-framing.instructions.md"
    )
    assert spec.template_bytes <= projections.MAX_TEMPLATE_BYTES
    rendered = projections.render_projection(spec)
    assert rendered.byte_count <= projections.MAX_PROJECTION_BYTES
    assert rendered.marker["deliveryKind"] == "selector"
    assert rendered.fallback_content is not None
    fallback = rendered.fallback_content.decode()
    assert "[owner: agent-conduct-guidance]" in fallback
    assert "using-standing-authority" in fallback


@pytest.mark.parametrize("clause", [
    "write the options at the scope of the real decision or operation class, "
    "not the narrowest next action",
    '"exact", "one", "only", "single", "just", "read-only", "this time", "once", '
    "a version/revision pin or an expiry or time window",
    "to option labels, the recommendation or the default",
    "state that reason inline and still offer the broader option",
    "the broadest choice that completes the goal within the authority being requested",
    "retries after reviewed fixes, rebases, regeneration, re-review and later runs "
    "of the same operation class",
    "offer a standing or class-level option",
    "never offer only narrow options",
    "This fixes the framing, not the reading of the answer.",
    "When the operator picks a narrower option over an offered broader one, "
    "that narrowing binds.",
    "ask once to widen it at class level; never re-ask for each step",
    "prefer a resource bound -- CPU, memory, storage or wall time per run -- that "
    "lasts until validation completes over an attempt or run count",
    "Use a count only to protect a named scarce resource",
    "State the current state plainly in the question",
])
def test_kernel_frames_options_at_decision_scope(clause):
    assert clause in _normalized(_TEMPLATE)


def test_skill_check_and_packet_keep_the_broad_option_and_binding_narrowing():
    skill = _normalized(_SKILL)
    for clause in (
        "The same framing applies to every question about how to proceed",
        "Use a narrower limit only for a concrete risk the agent can name",
        "include a standing or class-level option",
        "narrower options sit beside the broad one, never instead of it",
        "Precision is not narrowing",
        "a narrower pick is operator-chosen and binds",
        "ask once to widen it at class level",
        "Worked example: framing a how-to-proceed question",
        "Option 3 is narrower, names its risk and sits beside the broad options. "
        "If the operator picks it, that limit binds.",
    ):
        assert clause in skill
    packet = _normalized(_DECISION)
    assert "narrower options never replace the broad one" in packet
