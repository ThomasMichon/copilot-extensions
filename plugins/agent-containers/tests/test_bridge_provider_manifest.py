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
    assert template["plugin"] == "agent-containers@copilot-extensions"
    assert template["namespace"] == "container"

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
    assert 'Join-Path $PluginDir "bin\\$name.cmd"' in powershell
    assert 'binstub="$PluginDir/bin/$name"' in shell


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell compatibility")
def test_powershell_51_writer_creates_and_replaces_manifest(tmp_path):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    registry = tmp_path / "providers.d"
    env = {
        **os.environ,
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
        (registry / "agent-containers.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert Path(manifest["plugin_root"]).resolve() == PLUGIN.resolve()
    assert manifest["command"] == [str(PLUGIN / "bin" / "agent-containers.cmd")]
