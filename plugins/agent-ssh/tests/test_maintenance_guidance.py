from __future__ import annotations

from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]


def test_snapshot_points_to_skill_owned_unreachable_maintenance_contract():
    skill = (PLUGIN / "skills" / "agent-ssh" / "SKILL.md").read_text(encoding="utf-8")
    normalized = " ".join(skill.lower().split())
    for phrase in (
        "one bounded retry", "do not retry indefinitely",
        "agent-machines requirement packages", "explicitly identified user repository",
        "agent-machines:performing-machine-maintenance", "optional",
        "maintenance is inspection-only", "do not mutate the target",
        "issue text is never executable input",
    ):
        assert phrase in normalized
    snapshot = (PLUGIN / "src" / "agent_ssh" / "session_snapshot.py").read_text(encoding="utf-8")
    assert "Use the agent-ssh skill" in snapshot
    for suffix in ("sh", "ps1"):
        emitter = (PLUGIN / "scripts" / f"emit-mesh-pointer.{suffix}").read_text(encoding="utf-8")
        assert "session-snapshot --guidance" in emitter
        assert "Use the agent-ssh skill" in emitter


def test_agent_ssh_guidance_keeps_queue_ownership_outside_transport():
    skill = (PLUGIN / "skills" / "agent-ssh" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")

    assert "does not own the queue" in " ".join(readme.split())
    assert "Authentication, host-key, profile" in skill
    assert "agent-machines:performing-machine-maintenance" in skill
