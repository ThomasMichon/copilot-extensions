from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILL = PLUGIN_ROOT / "skills" / "borrowing-containers" / "SKILL.md"
FORBIDDEN = (
    "odsp",
    "onedrive",
    "sharepoint",
    "tmichon",
    "dotfiles",
    "/workspaces/" + "odsp-web",
)


def test_borrowing_containers_skill_contract():
    assert SKILL.is_file()
    text = SKILL.read_text(encoding="utf-8")
    lowered = text.lower()

    assert "name: borrowing-containers" in text
    assert "description:" in text
    assert "borrow a container for an effort" in lowered
    assert "release the effort's container" in lowered

    assert "`containers-fleet` skill owns" in text
    assert "provisioning, readiness" in lowered
    assert "user's state repo" in lowered
    assert "**Container:**" in text
    assert "<agent-containers catalog argv prefix> borrow <effort-slug>" in text
    assert "<agent-bridge catalog argv prefix> send container:<name>" in text
    assert "<agent-containers catalog argv prefix> release <effort-slug>" in text
    assert "<agent-containers catalog argv prefix> leases" in text
    assert "never substitute a same-named" in text
    assert "marketplace-isolation: allow agent-bridge-management" not in text

    assert not any(term in lowered for term in FORBIDDEN)
