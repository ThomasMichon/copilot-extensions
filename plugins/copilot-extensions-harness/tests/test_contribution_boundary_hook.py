from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
VERSION = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))["version"]
GUIDE = PLUGIN / "references" / "contribution-ground-rules.md"
CONTRIBUTION_SKILL = (
    PLUGIN / "skills" / "contributing-to-copilot-extensions" / "SKILL.md"
)


def _without_plugin_root() -> dict[str, str]:
    return {key: value for key, value in os.environ.items()
            if key != "COPILOT_PLUGIN_ROOT"}


def test_hook_manifest_points_to_payload_scripts() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["sessionStart"]
    assert len(entries) == 1
    assert "COPILOT_PLUGIN_ROOT" in entries[0]["powershell"]
    assert "COPILOT_PLUGIN_ROOT" in entries[0]["bash"]


def test_bash_hook_has_interpreter_and_json_fallbacks() -> None:
    script = (PLUGIN / "scripts" / "emit-contribution-boundary.sh").read_text(
        encoding="utf-8"
    )
    assert "command -v python3 || command -v python" in script
    assert script.count("printf '{}'") >= 3


def test_public_claim_guidance_has_scoped_identity_and_safe_fallback() -> None:
    text = CONTRIBUTION_SKILL.read_text(encoding="utf-8")

    assert (
        "repos gh ThomasMichon/copilot-extensions -- issue create"
        in text
    )
    assert "scoped `api user` result must" in text
    assert "never use `gh auth switch`" in text
    assert "`agent-issue-claim:v1`" in text
    assert "deduplicated `agent-dispatch` task" in text
    assert "must not block local implementation" in text
    assert "Never mention or link them" in text
    assert "this public repository's issue" in text
    assert "Re-run the scoped public search before publication" in text


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh unavailable")
def test_powershell_hook_emits_existing_guide() -> None:
    env = {**os.environ, "COPILOT_PLUGIN_ROOT": str(PLUGIN)}
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File",
         str(PLUGIN / "scripts" / "emit-contribution-boundary.ps1")],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(result.stdout)
    assert str(GUIDE) in payload["additionalContext"]
    assert "organization-neutral" in payload["additionalContext"]
    assert payload["additionalContext"].startswith(
        f"[owner: copilot-extensions-harness@{VERSION}]"
    )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh unavailable")
def test_powershell_hook_falls_back_to_script_location() -> None:
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File",
         str(PLUGIN / "scripts" / "emit-contribution-boundary.ps1")],
        check=True,
        capture_output=True,
        text=True,
        env=_without_plugin_root(),
    )
    payload = json.loads(result.stdout)
    assert str(GUIDE) in payload["additionalContext"]
    assert payload["additionalContext"].startswith(
        f"[owner: copilot-extensions-harness@{VERSION}]"
    )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh unavailable")
def test_powershell_manifest_fails_open_without_root(tmp_path: Path) -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    command = hooks["hooks"]["sessionStart"][0]["powershell"]
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-Command", command],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_without_plugin_root(),
    )
    assert result.stdout.strip() == "{}"


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None,
                    reason="POSIX bash payload test")
def test_bash_hook_emits_existing_guide() -> None:
    env = {**os.environ, "COPILOT_PLUGIN_ROOT": str(PLUGIN)}
    result = subprocess.run(
        ["bash", str(PLUGIN / "scripts" / "emit-contribution-boundary.sh")],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(result.stdout)
    assert str(GUIDE) in payload["additionalContext"]
    assert "organization-neutral" in payload["additionalContext"]


@pytest.mark.skipif(os.name == "nt" or shutil.which("bash") is None,
                    reason="POSIX bash payload test")
def test_bash_manifest_fails_open_without_root(tmp_path: Path) -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    command = hooks["hooks"]["sessionStart"][0]["bash"]
    result = subprocess.run(
        ["bash", "-c", command],
        check=True,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=_without_plugin_root(),
    )
    assert result.stdout.strip() == "{}"
