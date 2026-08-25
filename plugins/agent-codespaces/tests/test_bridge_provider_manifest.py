from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]


def test_bridge_provider_manifest_is_attributed_and_writers_stamp_root_atomically():
    template = json.loads(
        (PLUGIN / "references" / "bridge-provider.json").read_text(encoding="utf-8")
    )
    assert template["schema_version"] == 1
    assert template["plugin"] == "agent-codespaces@copilot-extensions"

    powershell = (PLUGIN / "scripts" / "register-bridge-provider.ps1").read_text(
        encoding="utf-8"
    )
    shell = (PLUGIN / "scripts" / "register-bridge-provider.sh").read_text(
        encoding="utf-8"
    )
    assert "plugin_root" in powershell
    assert "[System.IO.File]::Replace" in powershell
    assert "[System.IO.File]::Move($tmp, $out)" in powershell
    assert 'data["plugin_root"] = os.path.realpath(plugin_root)' in shell
    assert "os.replace(tmp, out)" in shell


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell compatibility")
def test_powershell_51_writer_creates_and_replaces_manifest(tmp_path):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    home = tmp_path / "home"
    binstub = home / ".local" / "bin" / "agent-codespaces.cmd"
    binstub.parent.mkdir(parents=True)
    binstub.write_text("@echo off\r\n", encoding="utf-8")
    registry = tmp_path / "providers.d"
    env = {
        **os.environ,
        "USERPROFILE": str(home),
        "AGENT_BRIDGE_PROVIDERS_DIR": str(registry),
    }
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PLUGIN / "scripts" / "register-bridge-provider.ps1"),
    ]
    subprocess.run(command, env=env, check=True)
    subprocess.run(command, env=env, check=True)
    manifest = json.loads(
        (registry / "agent-codespaces.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert Path(manifest["plugin_root"]).resolve() == PLUGIN.resolve()
    assert manifest["command"] == [str(binstub)]
