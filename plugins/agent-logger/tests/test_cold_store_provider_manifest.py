from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def test_cold_store_provider_manifest_is_attributed_and_writers_stamp_root_atomically():
    template = json.loads(
        (PLUGIN / "references" / "cold-store-provider.json").read_text(
            encoding="utf-8"
        )
    )
    assert template["schema_version"] == 1
    assert template["plugin"] == "agent-logger@copilot-extensions"
    assert template["capability"] == "session-fetch"

    powershell = (
        PLUGIN / "scripts" / "register-cold-store-provider.ps1"
    ).read_text(encoding="utf-8")
    shell = (PLUGIN / "scripts" / "register-cold-store-provider.sh").read_text(
        encoding="utf-8"
    )
    assert "plugin_root" in powershell
    assert "[System.IO.File]::Replace" in powershell
    assert "[System.IO.File]::Move($tmp, $out)" in powershell
    assert 'data["plugin_root"] = os.path.realpath(plugin_root)' in shell
    assert "os.replace(tmp, out)" in shell
    assert 'Join-Path $PluginDir "bin\\$name.cmd"' in powershell
    assert 'binstub="$PluginDir/bin/$name"' in shell
    # Distinct registry directory from the namespace-provider registration.
    assert "cold-store-providers.d" in powershell
    assert "cold-store-providers.d" in shell
    assert "AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR" in powershell
    assert "AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR" in shell


def _resolve_bash() -> str:
    """Resolve a REAL bash, not Windows' WSL-launcher `bash.exe` shim.

    A bare "bash" can resolve to a genuine WSL distro's `/bin/bash` (via the
    `C:\\Windows\\System32\\bash.exe` launcher), which strips backslashes
    from a Windows-style argv path (POSIX shells don't treat `\\` as a path
    separator), mangling `D:\\Src\\...\\script.sh` into a nonexistent
    filename. Prefer the real Git Bash location, which accepts Windows
    paths natively.
    """
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if git_bash.is_file():
        return str(git_bash)
    return "bash"


def test_posix_writer_creates_and_replaces_manifest(tmp_path):
    registry = tmp_path / "cold-store-providers.d"
    env = {**os.environ, "AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR": str(registry)}
    # Strip the Windows Store's python3/python alias stubs from PATH -- a
    # no-op unless Python is installed via the Store, but it can shadow the
    # real interpreter ahead of it, making the script's own
    # `command -v python3 || command -v python` resolve to a dud and
    # silently no-op before ever writing the manifest.
    if "PATH" in env:
        parts = env["PATH"].split(os.pathsep)
        env["PATH"] = os.pathsep.join(p for p in parts if "WindowsApps" not in p)
    command = [_resolve_bash(), str(PLUGIN / "scripts" / "register-cold-store-provider.sh")]
    subprocess.run(command, env=env, check=True, cwd=str(PLUGIN))
    subprocess.run(command, env=env, check=True, cwd=str(PLUGIN))

    manifest = json.loads(
        (registry / "agent-logger.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert manifest["capability"] == "session-fetch"
    assert Path(manifest["plugin_root"]).resolve() == PLUGIN.resolve()
    # The shell writer always emits forward-slash paths (a deliberate,
    # platform-consistent manifest convention -- see the sibling test's
    # `'binstub="$PluginDir/bin/$name"'` assertion on the script text
    # itself), so compare with `.as_posix()` rather than `str()`, which
    # would use backslashes on Windows and never match.
    assert manifest["command"] == [(PLUGIN / "bin" / "agent-logger").as_posix()]


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell compatibility")
def test_powershell_51_writer_creates_and_replaces_manifest(tmp_path):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")
    registry = tmp_path / "cold-store-providers.d"
    env = {**os.environ, "AGENT_BRIDGE_COLD_STORE_PROVIDERS_DIR": str(registry)}
    command = [
        powershell,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PLUGIN / "scripts" / "register-cold-store-provider.ps1"),
    ]
    subprocess.run(command, env=env, check=True)
    manifest = json.loads(
        (registry / "agent-logger.json").read_text(encoding="utf-8")
    )
    assert manifest["schema_version"] == 1
    assert Path(manifest["plugin_root"]).resolve() == PLUGIN.resolve()
    assert manifest["command"] == [str(PLUGIN / "bin" / "agent-logger.cmd")]
