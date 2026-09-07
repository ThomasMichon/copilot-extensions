"""Tier-P scenarios declaring no Copilot auth must not borrow credentials."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

RIG = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(not POWERSHELL, reason="PowerShell is unavailable")
@pytest.mark.parametrize("auth", ["none", "required"])
def test_powershell_uses_manifest_auth_contract(tmp_path, auth):
    scenario = tmp_path / "scenario"
    scenario.mkdir()
    (scenario / "manifest.json").write_text(json.dumps({"tier": "P", "auth": {"copilot": auth}}), encoding="utf-8")
    source = (RIG / "run.ps1").read_text(encoding="utf-8")
    start = source.index("function Start-Container {")
    function = source[start:source.index("\nfunction Ensure-Container", start)]
    quote = lambda p: str(p).replace("'", "''")
    script = tmp_path / "probe.ps1"
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        "function Resolve-CopilotToken { throw 'authentication-requested' }\n"
        "function Test-Image { return $true }\n"
        "function docker { if ($args[0] -eq 'run') { $script:selectedImage=$args[-2] } }\n"
        f"$ScenarioDir='{quote(scenario)}'; $Results='{quote(tmp_path / 'out')}'\n"
        f"$LibDir='{quote(RIG / 'lib')}'\n"
        "$BaseTag='base-image'; $AuthTag='authed-image'; $Container='fixture'\n"
        "$PassEnv=@(); $HarnessMount=''; $ScenarioSharedLib=$null\n"
        + function + "\nStart-Container\nWrite-Output \"selected=$script:selectedImage\"\n",
        encoding="utf-8",
    )
    result = subprocess.run([POWERSHELL, "-NoProfile", "-File", str(script)], capture_output=True, text=True, timeout=30)
    if auth == "none":
        assert result.returncode == 0, result.stderr
        assert "selected=base-image" in result.stdout
    else:
        assert result.returncode != 0
        assert "authentication-requested" in result.stderr


@pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="POSIX Bash required")
def test_bash_auth_free_branch_precedes_token_lookup():
    source = (RIG / "run.sh").read_text(encoding="utf-8")
    body = source.split("start_container() {", 1)[1]
    assert body.index('no_scenario_auth="$(python3') < body.index('token="$(resolve_token)"')
    assert 'if [ "$no_scenario_auth" = true ]; then' in body


def test_version_witness_normalizes_only_dev_spelling():
    source = (RIG / "scenarios" / "agent-machines-installation-cells" / "scenario.py").read_text(encoding="utf-8")
    function = source.split("def reports_version(", 1)[1].split("\ndef next_dev_version", 1)[0]
    namespace = {}
    exec("def reports_version(" + function, namespace)
    witness = namespace["reports_version"]
    assert witness("agent-machines 1.0.0.dev3\n", "1.0.0-dev3")
    assert not witness("agent-machines 1.0.0.dev30\n", "1.0.0-dev3")
    assert not witness("not-agent-machines 1.0.0.dev3\n", "1.0.0-dev3")
