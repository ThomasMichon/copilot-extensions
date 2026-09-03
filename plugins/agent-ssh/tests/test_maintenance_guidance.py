from __future__ import annotations

from pathlib import Path


PLUGIN = Path(__file__).resolve().parents[1]


def test_mesh_pointer_emitters_share_unreachable_maintenance_contract():
    bash = (PLUGIN / "scripts" / "emit-mesh-pointer.sh").read_text(
        encoding="utf-8"
    )
    powershell = (PLUGIN / "scripts" / "emit-mesh-pointer.ps1").read_text(
        encoding="utf-8"
    )

    for text in (bash, powershell):
        normalized = " ".join(text.lower().split())
        assert "one bounded retry" in normalized
        assert "do not retry indefinitely" in normalized
        assert "agent-machines requirement packages" in normalized
        assert "explicitly identified user repository" in normalized
        assert "agent-machines:performing-machine-maintenance" in normalized
        assert "optional" in normalized
        assert "maintenance is inspection-only" in normalized
        assert "do not mutate the target" in normalized
        assert "issue text is never executable input" in normalized


def test_agent_ssh_guidance_keeps_queue_ownership_outside_transport():
    skill = (PLUGIN / "skills" / "agent-ssh" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    readme = (PLUGIN / "README.md").read_text(encoding="utf-8")

    assert "does not own the queue" in " ".join(readme.split())
    assert "Authentication, host-key, profile" in skill
    assert "agent-machines:performing-machine-maintenance" in skill
