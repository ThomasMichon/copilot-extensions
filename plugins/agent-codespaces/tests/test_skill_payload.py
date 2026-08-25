# ruff: noqa: S101

from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SKILLS = PLUGIN_ROOT / "skills"
FORBIDDEN = (
    "odsp",
    "onedrive",
    "sharepoint",
    "tmichon",
    "dotfiles",
    "/workspaces/" + "odsp-web",
)


def _read(name: str) -> str:
    path = SKILLS / name / "SKILL.md"
    assert path.is_file()
    return path.read_text(encoding="utf-8")


def test_cleaning_codespaces_skill_contract():
    text = _read("cleaning-codespaces")
    lowered = text.lower()

    assert "name: cleaning-codespaces" in text
    assert "description:" in text
    assert "clean up codespaces" in lowered
    assert "delete an old codespace" in lowered

    assert "`codespaces-lifecycle` skill owns" in text
    assert "`borrowing-codespaces` skill owns" in text
    assert "provider-neutral safety report" in lowered
    assert "explicit confirmation" in lowered
    assert "optional repository export hook" in lowered
    assert "user's state repo" in lowered
    assert "agent-codespaces finalize <name> --delete" in text
    assert "agent-codespaces mark <name> prunable" in text
    assert "agent-codespaces prune" in text
    assert "agent-containers release <effort-slug>" in text

    assert not any(term in lowered for term in FORBIDDEN)


def test_recovering_codespaces_skill_contract():
    text = _read("recovering-codespaces")
    lowered = text.lower()

    assert "name: recovering-codespaces" in text
    assert "description:" in text
    assert "recover a codespace" in lowered
    assert "rebuild a corrupted codespace" in lowered

    assert "`codespaces-lifecycle` owns" in text
    assert "`agent-bridge` owns" in text
    assert "`borrowing-codespaces` owns" in text
    assert "never-destroy-live-session gate" in lowered
    for phase in (
        "phase 1: preserve",
        "phase 2: audit",
        "phase 3: confirm",
        "phase 4: force-delete",
        "phase 5: recreate",
        "phase 6: bootstrap",
        "phase 7: restore",
    ):
        assert phase in lowered
    assert "agent-codespaces delete <name> --force" in text
    assert "<owner/repo>" in text
    assert "configured source-control provider" in lowered

    assert not any(term in lowered for term in FORBIDDEN)
