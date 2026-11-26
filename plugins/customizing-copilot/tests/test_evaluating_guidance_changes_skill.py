"""Contract checks for the evaluating-guidance-changes skill."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.guard

_PLUGIN = Path(__file__).resolve().parents[1]
_SKILL = _PLUGIN / "skills" / "evaluating-guidance-changes" / "SKILL.md"


def _frontmatter(text: str) -> str:
    return text.split("---", 2)[1]


def test_skill_frontmatter_and_negative_scope() -> None:
    text = _SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\nname: evaluating-guidance-changes\n")
    frontmatter = _frontmatter(text)
    assert "Use when" in frontmatter
    assert "Not for" in frontmatter


def test_skill_keeps_outcome_gating_and_safety_rules() -> None:
    text = _SKILL.read_text(encoding="utf-8")
    assert "never gating" in text
    assert "Outcome, not self-score" in text
    assert "## Safety" in text
    assert "## Cost limits" in text


def test_skill_is_listed_in_plugin_readme() -> None:
    readme = (_PLUGIN / "README.md").read_text(encoding="utf-8")
    assert "skills/evaluating-guidance-changes/SKILL.md" in readme
