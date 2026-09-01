"""Hook processes must not retain the replaceable plugin payload as their cwd."""

from __future__ import annotations

import json
from pathlib import Path


_PLUGIN = Path(__file__).resolve().parents[1]


def _hooks() -> list[dict[str, object]]:
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    return [
        hook
        for entries in hooks["hooks"].values()
        for hook in entries
    ]


def test_every_hook_leaves_payload_cwd_before_running_script():
    for hook in _hooks():
        powershell = str(hook["powershell"])
        bash = str(hook["bash"])
        assert "$env:USERPROFILE" in powershell
        assert "-ErrorAction Stop" in powershell
        assert "GetPathRoot" in powershell
        ps_detach = powershell.find("Set-Location")
        ps_script = min(
            position
            for position in (powershell.find("$s"), powershell.find("$w"))
            if position >= 0
        )
        assert ps_detach >= 0 and ps_script >= 0
        assert ps_detach < ps_script
        assert 'cd "$HOME"' in bash
        bash_detach = bash.find("cd ")
        bash_script = min(
            position
            for position in (bash.find("s="), bash.find("w="))
            if position >= 0
        )
        assert bash_detach >= 0 and bash_script >= 0
        assert bash_detach < bash_script


def test_aggregate_context_has_a_cold_start_budget():
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    hook = next(
        item
        for item in hooks["hooks"]["sessionStart"]
        if "aggregate-context" in str(item)
    )
    assert hook["timeoutSec"] >= 30


def test_detached_provision_worker_runs_from_home():
    ps1 = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    sh = (_PLUGIN / "scripts" / "provision-check.sh").read_text("utf-8")
    assert "-WorkingDirectory $HOME" in ps1
    assert 'cd "$HOME" || exit 0' in sh
    assert "'--repo', $RepoArg, '--status', $StatusArg, '--apply'" in ps1
    assert "$RepoArg = " in ps1
    assert "$StatusArg = " in ps1
    assert '--repo "$repo_dir" --status "$status" --apply' in sh


def test_provision_hook_surfaces_previous_failure():
    ps1 = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    sh = (_PLUGIN / "scripts" / "provision-check.sh").read_text("utf-8")
    assert "Previous background provisioning failed" in ps1
    assert "Previous background provisioning failed" in sh
    assert "catch { }" not in ps1


def test_powershell_diagnostics_are_optional():
    provision = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    launcher = (_PLUGIN / "bin" / "launch-session.ps1").read_text("utf-8")
    assert "$plan.PSObject.Properties['diagnostics']" in provision
    assert "function Write-PlanDiagnostics" in launcher
    assert "$Plan.PSObject.Properties['diagnostics']" in launcher
