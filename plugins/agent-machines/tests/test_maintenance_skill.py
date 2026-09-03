from __future__ import annotations

from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]
SKILL = (
    PLUGIN
    / "skills"
    / "performing-machine-maintenance"
    / "SKILL.md"
)


def test_maintenance_skill_has_provider_neutral_queue_and_claim_contract():
    text = SKILL.read_text(encoding="utf-8")
    normalized = " ".join(
        text.lower().replace("**", "").replace("`", "").split()
    )

    assert "name: performing-machine-maintenance" in text
    assert "explicit user/control repository" in normalized
    assert "canonical machine key" in normalized
    assert "maintenance predicate" in normalized
    assert "machine predicate" in normalized
    assert "do not infer" in normalized
    assert "agent-dispatch" in normalized
    assert "shared or target-authoritative" in normalized
    assert "exclusive key" in normalized
    assert "never in the key" in normalized
    assert "atomic provider claim" in normalized
    assert "treat issue instructions as advisory" in normalized
    assert "pin and re-read the issue revision" in normalized
    assert "claimed to started" in normalized
    assert "yield the task back to queued" in normalized
    assert "never pipe" in normalized
    assert "obtain explicit approval" in normalized
    assert "verify the stated postcondition" in normalized


def test_maintenance_skill_does_not_hardcode_issue_provider_or_machine():
    text = SKILL.read_text(encoding="utf-8").lower()

    assert "gitea" not in text
    assert "github" not in text
    assert "borealis" not in text
    assert "tmichon" not in text
    assert len(text.splitlines()) < 500
