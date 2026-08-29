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
        ps_detach = powershell.find("Set-Location")
        ps_script = powershell.find("$s")
        assert ps_detach >= 0 and ps_script >= 0
        assert ps_detach < ps_script
        assert 'cd "$HOME"' in bash
        bash_detach = bash.find("cd ")
        bash_script = bash.find("s=")
        assert bash_detach >= 0 and bash_script >= 0
        assert bash_detach < bash_script


def test_session_conduct_has_a_cold_start_budget():
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    hook = next(
        item
        for item in hooks["hooks"]["sessionStart"]
        if "session-conduct" in str(item)
    )
    assert hook["timeoutSec"] >= 30


def test_detached_provision_worker_runs_from_home():
    ps1 = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    sh = (_PLUGIN / "scripts" / "provision-check.sh").read_text("utf-8")
    assert "-WorkingDirectory $HOME" in ps1
    assert 'cd "$HOME" || exit 0' in sh


def test_powershell_diagnostics_are_optional():
    provision = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    launcher = (_PLUGIN / "bin" / "launch-session.ps1").read_text("utf-8")
    assert "$plan.PSObject.Properties['diagnostics']" in provision
    assert "function Write-PlanDiagnostics" in launcher
    assert "$Plan.PSObject.Properties['diagnostics']" in launcher
