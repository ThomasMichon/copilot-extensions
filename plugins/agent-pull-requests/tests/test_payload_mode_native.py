"""Native governance transports structured probes without PowerShell argv loss."""

from pathlib import Path
import os
import shutil
import subprocess

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_governance_preserves_absent_legacy_probe_json(tmp_path, monkeypatch, host):
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    scripts = tmp_path / "payload with spaces" / "scripts"
    native = scripts / "installation-context"
    native.mkdir(parents=True)
    for name in ("payload-mode.ps1", "versioned_runtime_native.ps1"):
        shutil.copyfile(SCRIPTS / name, scripts / name)
    (native / "installation-context.ps1").write_text(
        "param($Action,$PayloadRoot,$PluginId,$LegacyRoot,$LegacyProbeJson)\n"
        "$ErrorActionPreference='Stop'\n"
        "if ($PID -ne [int]$env:EXPECTED_GOVERNANCE_PID) { throw 'second governance host' }\n"
        "$probe = $LegacyProbeJson | ConvertFrom-Json\n"
        "if ($probe.declared -ne $true -or $probe.result -cne 'absent' -or\n"
        "    $null -ne $probe.checkedAt) { throw 'legacy probe changed in transport' }\n"
        "'{\"status\":\"ready\",\"actualMode\":\"legacy\",\"desiredMode\":\"legacy\"}'\n"
        "exit 0\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-Command", "$env:EXPECTED_GOVERNANCE_PID=[string]$PID; "
         f"& '{scripts / 'payload-mode.ps1'}'"],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "legacy"


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_governance_failure_cannot_admit_success_shaped_output(tmp_path, monkeypatch, host):
    shell = shutil.which(host)
    if shell is None:
        pytest.skip(f"{host} unavailable")
    scripts = tmp_path / "payload" / "scripts"
    native = scripts / "installation-context"
    native.mkdir(parents=True)
    shutil.copyfile(SCRIPTS / "payload-mode.ps1", scripts / "payload-mode.ps1")
    (native / "installation-context.ps1").write_text(
        "param($Action,$PayloadRoot,$PluginId,$LegacyRoot,$LegacyProbeJson)\n"
        "'{\"status\":\"ready\",\"actualMode\":\"legacy\",\"desiredMode\":\"legacy\"}'\n"
        "[Console]::Error.WriteLine('deliberate governance refusal')\n"
        "exit 7\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    result = subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(scripts / "payload-mode.ps1")],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode != 0
    assert "deliberate governance refusal" in result.stderr
    assert "Installation governance could not be resolved" in result.stderr
    assert result.stdout.strip() != "legacy"
