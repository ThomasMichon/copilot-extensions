from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
VERSION = json.loads((PLUGIN / "plugin.json").read_text(encoding="utf-8"))["version"]


def test_hook_manifest_points_to_payload_scripts() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    entries = hooks["hooks"]["sessionStart"]
    assert len(entries) == 1
    assert "COPILOT_PLUGIN_ROOT" in entries[0]["powershell"]
    assert "COPILOT_PLUGIN_ROOT" in entries[0]["bash"]
    assert "} else { '{}' }" in entries[0]["powershell"]
    assert "else printf '{}'" in entries[0]["bash"]


def test_bash_hook_has_interpreter_and_json_fallbacks() -> None:
    script = (PLUGIN / "scripts" / "emit-contribution-boundary.sh").read_text(
        encoding="utf-8"
    )
    assert "command -v python3 || command -v python" in script
    assert script.count("printf '{}'") >= 3


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
    assert Path(payload["additionalContext"].split("Read: ", 1)[1]).is_file()
    assert "organization-neutral" in payload["additionalContext"]
    assert payload["additionalContext"].startswith(
        f"[owner: copilot-extensions-harness@{VERSION}]"
    )


@pytest.mark.skipif(shutil.which("pwsh") is None, reason="pwsh unavailable")
def test_powershell_hook_falls_back_to_script_location() -> None:
    env = {key: value for key, value in os.environ.items()
           if key != "COPILOT_PLUGIN_ROOT"}
    result = subprocess.run(
        ["pwsh", "-NoProfile", "-File",
         str(PLUGIN / "scripts" / "emit-contribution-boundary.ps1")],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(result.stdout)
    assert Path(payload["additionalContext"].split("Read: ", 1)[1]).is_file()
    assert payload["additionalContext"].startswith(
        f"[owner: copilot-extensions-harness@{VERSION}]"
    )


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
    assert Path(payload["additionalContext"].split("Read: ", 1)[1]).is_file()
    assert "organization-neutral" in payload["additionalContext"]
