"""Actual projection and skill discovery omit arbitrary delegation policy."""

from pathlib import Path
import sys
from types import SimpleNamespace

PLUGIN = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN.parent / "customizing-copilot/skills/reviewing-customizations/scripts"
sys.path.insert(0, str(SCRIPTS))
import instruction_projections as projections  # noqa: E402
import scan_skills  # noqa: E402


def test_projection_consumer_keeps_scoped_modules_without_delegation_policy(tmp_path: Path) -> None:
    source = SimpleNamespace(
        origin="copilot-extensions/agent-conduct-guidance",
        payload_root=PLUGIN, skills_root=PLUGIN / "skills",
        controlled=False, source="", version="",
    )
    result = projections.Result(operation="test")
    specs, unknown = projections._load_specs(tmp_path, [source], result)
    assert not unknown and result.blocking == 0, result.findings
    assert {spec.source_id for spec in specs} == {
        "charter-boundary", "useful-autonomy", "question-framing", "maintenance-standby", "elevation-fallback",
        "multi-agent-rules-of-engagement", "process-hygiene-fallback",
        "scratch-space-fallback", "secret-masking-fallback", "blocker-review-fallback",
    }
    for spec in specs:
        rendered = projections.render_projection(spec)
        assert rendered.fallback_content is not None
        assert b"delegating-work" not in rendered.fallback_content
        assert b"resolve-model-routing" not in rendered.fallback_content


def test_assignment_discovery_is_explicit_not_generic_research_or_model_choice() -> None:
    parsed = scan_skills.split_frontmatter(
        (PLUGIN / "skills/delegating-work/SKILL.md").read_text(encoding="utf-8"),
    )
    assert parsed is not None
    frontmatter, body = parsed
    assert scan_skills.extract_triggers(frontmatter) == [
        "prepare a delegate assignment", "write a bounded sub-agent prompt",
    ]
    description = scan_skills.get_field_block(frontmatter, "description")
    assert "Ordinary requests to delegate or execute tasks do not select this skill." in description
    assert "Ordinary requests to delegate or execute tasks do not\nselect it." in body
    assert "do not\ncreate child agents unless" in body
    assert "Never delegate back to the same agent type" in body
    assert "own managed worktree" in body
    assert "worker-evidence-and-readiness.md" in body
