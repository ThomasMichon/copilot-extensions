# ruff: noqa: S101

import json
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


def test_session_start_hooks_use_payload_root_and_fail_open():
    hooks = json.loads((PLUGIN_ROOT / "hooks.json").read_text(encoding="utf-8"))
    session_hooks = hooks["hooks"]["sessionStart"]
    expected_order = [
        "readiness-context",
        "bootstrap-check",
        "emit-command-catalog",
        "register-bridge-provider",
        "emit-codespace-map",
    ]

    assert len(session_hooks) == 5
    assert [
        next(name for name in expected_order if name in hook["bash"])
        for hook in session_hooks
    ] == expected_order
    for hook in session_hooks:
        for shell in ("bash", "powershell"):
            command = hook[shell]
            assert "COPILOT_PLUGIN_ROOT" in command
            assert "'{}'" in command


def test_codespace_map_timeout_covers_registry_cold_path():
    declaration = json.loads(
        (PLUGIN_ROOT / "session-context.json").read_text(encoding="utf-8")
    )
    contributor = next(
        item
        for item in declaration["contributors"]
        if item["id"] == "codespace-map"
    )

    assert 8 <= contributor["timeoutSeconds"] <= 15


def test_provider_management_boundary_stays_explicit():
    text = _read("codespaces-lifecycle")
    normalized = " ".join(text.split())

    assert "registered **management entry point**" in text
    assert "Session command catalogs do not replace this provider/supervisor boundary" in normalized
    assert "<agent-bridge catalog argv[0]> send codespace:" in text
    assert "marketplace-isolation: allow agent-bridge-management" not in text


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
    assert "<agent-codespaces catalog argv[0]> finalize <name> --delete" in text
    assert "<agent-codespaces catalog argv[0]> mark <name> prunable" in text
    assert "<agent-codespaces catalog argv[0]> prune" in text
    assert "<agent-containers catalog argv[0]> release <effort-slug>" in text
    assert "never substitute a same-named command" in lowered

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
    assert "<agent-codespaces catalog argv[0]> delete <name> --force" in text
    assert "never substitute a same-named command" in lowered
    assert "<owner/repo>" in text
    assert "configured source-control provider" in lowered

    assert not any(term in lowered for term in FORBIDDEN)
