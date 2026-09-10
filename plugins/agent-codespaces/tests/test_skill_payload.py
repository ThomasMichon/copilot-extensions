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
        "bootstrap-check",
        "register-bridge-provider",
        "write-session-guidance",
    ]

    assert len(session_hooks) == 3
    assert [
        next(name for name in expected_order if name in hook["bash"])
        for hook in session_hooks
    ] == expected_order
    for hook in session_hooks:
        for shell in ("bash", "powershell"):
            command = hook[shell]
            assert "COPILOT_PLUGIN_ROOT" in command
            assert "'{}'" in command

def test_provider_management_boundary_stays_explicit():
    text = _read("codespaces-lifecycle")
    normalized = " ".join(text.split())

    assert "registered **management entry point**" in text
    assert "Session command catalogs do not replace this provider/supervisor boundary" in normalized
    assert "<agent-bridge catalog argv[0]> send codespace:" in text
    assert "marketplace-isolation: allow agent-bridge-management" not in text


def test_lifecycle_routes_explicit_terminals_without_acp_fallback():
    text = _read("codespaces-lifecycle")
    normalized = " ".join(text.split())
    assert "explicitly selected caller-owned interactive terminals" in normalized
    assert "Do not redirect an explicitly selected interactive terminal to ACP dispatch." in normalized
    assert "Routine **ACP dispatch** goes through **agent-bridge**" in normalized
    assert "## SSH (Diagnostic Only)" not in text
    for flag in (
        "--interactive-command-file", "--local-forward", "--reverse-forward",
        "--no-plugin-staging", "--require-relay",
    ):
        assert flag in text
    assert "<agent-bridge catalog argv[0]> service start" in text
    assert "<agent-bridge catalog argv[0]> installer-readiness" in text
    assert "module `agent-bridge/runtime`, state `ready`" in normalized
    assert "through their official installation flow" in normalized
    assert "Keep missing parity visible as a blocker" in normalized


def test_lifecycle_preserves_incumbents_and_native_control_identity():
    normalized = " ".join(_read("codespaces-lifecycle").split())
    assert "including an idle ACP or native session" in normalized
    assert "Do not open diagnostic SSH against an active incumbent" in normalized
    assert "mode choice does not authorize implicit `--force`, `--force-claim`, or disabled claims" in normalized
    assert "`--expected-session-id`" in normalized
    assert "missing representation cannot fall through to ACP" in normalized
    assert "dispatch is **stopped/idle**" not in normalized


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
