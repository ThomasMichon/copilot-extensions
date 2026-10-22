"""Finite child-process checks at the managed daemon-root boundary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


COMPANION = Path(__file__).resolve().parents[1] / "scripts" / "dtssh-companion.ps1"
PWSH = shutil.which("pwsh")
CARRIER = "C28FC6F98A2C44ABBBD89D6A3037D0D9_POSIX_FD_STATE"


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is unavailable")
@pytest.mark.parametrize("action", ["start", "stop", "health"])
@pytest.mark.parametrize("carrier_name", [CARRIER, CARRIER.lower()] if os.name == "nt" else [CARRIER])
def test_companion_removes_only_new_daemon_descriptor_state(
    tmp_path: Path, action: str, carrier_name: str,
) -> None:
    config = tmp_path / "dispatch-companion.json"
    config.write_text(
        json.dumps({"schema_version": 1, "alias": "fixture", "port": 1}),
        encoding="utf-8",
    )
    controls = {
        "OTHER_POSIX_FD_STATE": "unrelated",
        CARRIER + "_OTHER": "suffix-control",
        "OTHER_" + CARRIER: "prefix-control",
        "SSH_AUTH_SOCK": "synthetic-agent",
        "SSH_CONNECTION": "synthetic-connection",
        "GIT_CONFIG_COUNT": "0",
        "AGENT_SSH_FIXTURE_IDENTITY": "synthetic-identity",
    }
    names = [CARRIER, *controls]
    script = (
        "param([string]$Action, [string]$Alias, [int]$Port, "
        "[switch]$ForegroundLauncher)\n"
        "$record = [ordered]@{ action = $Action; environment = [ordered]@{} }\n"
        f"foreach ($name in @({','.join(repr(name) for name in names)})) {{\n"
        "  $record.environment[$name] = "
        "[Environment]::GetEnvironmentVariable($name, 'Process')\n"
        "}\n"
        "$record | ConvertTo-Json -Compress | Add-Content -LiteralPath "
        "(Join-Path $PSScriptRoot 'observed.jsonl')\n"
        "if ($Action -eq 'status') { Write-Output 'fixture healthy' }\n"
        "exit 0\n"
    )
    (tmp_path / "install-host.ps1").write_text(script, encoding="utf-8")
    environment = {**os.environ, **controls}
    for name in list(environment):
        if name.casefold() == CARRIER.casefold():
            del environment[name]
    environment[carrier_name] = "synthetic-descriptor-state"
    environment["AGENT_SSH_DTSSH_COMPANION_CONFIG"] = str(config)
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-File", str(COMPANION), action],
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    records = [
        json.loads(line)
        for line in (tmp_path / "observed.jsonl").read_text(encoding="utf-8-sig").splitlines()
    ]
    assert [record["action"] for record in records] == (
        ["stop", "start"] if action == "start" else ["status" if action == "health" else "stop"]
    )
    for record in records:
        expected = None if record["action"] == "start" else "synthetic-descriptor-state"
        assert record["environment"][CARRIER] == expected
        assert {name: record["environment"][name] for name in controls} == controls
    assert environment[carrier_name] == "synthetic-descriptor-state"
