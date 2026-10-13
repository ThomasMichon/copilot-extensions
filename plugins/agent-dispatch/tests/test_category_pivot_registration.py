"""Fresh registration contract without provisioning a service."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from agent_procutil import no_window_kwargs

PLUGIN = Path(__file__).resolve().parents[1]
NAMES = ("agent-dispatch.json", "agent-dispatch-maintenance.json")


def test_both_platform_installers_register_and_remove_both_contributions():
    for script in ("install.ps1", "install.sh"):
        text = (PLUGIN / "scripts" / script).read_text()
        for name in NAMES:
            assert text.count(name) >= 2


@pytest.mark.skipif(os.name != "nt", reason="native PowerShell registration contract")
def test_fresh_windows_category_pivot_registration_is_independent_and_idempotent(tmp_path):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell

    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    script = (
        "$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null; "
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile({quote(PLUGIN / 'scripts' / 'install.ps1')},"
        "[ref]$tokens,[ref]$errors); if($errors.Count){throw 'Installer parse failure'}; "
        "$definition=$ast.Find({param($node) "
        "$node -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq 'Register-PickerPivot'},$true); "
        "Invoke-Expression $definition.Extent.Text; "
        "function Write-Ok {param($message)}; function Write-Skip {param($message)}; "
        f"$PluginDir={quote(PLUGIN)}; $env:USERPROFILE={quote(tmp_path)}; "
        "Register-PickerPivot; Register-PickerPivot"
    )
    subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
        **no_window_kwargs(),
    )
    copied = tmp_path / ".agent-worktrees" / "pivots"
    for name, category in zip(NAMES, ("work", "maintenance")):
        manifest = json.loads((copied / name).read_text())
        assert manifest["list"][-2:] == ["--category", category]
        assert (copied / name).read_bytes() == (PLUGIN / "pivots" / name).read_bytes()
