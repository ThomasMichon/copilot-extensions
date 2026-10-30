"""Hook processes must not retain the replaceable plugin payload as their cwd."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest


_PLUGIN = Path(__file__).resolve().parents[1]


def _hooks() -> list[dict[str, object]]:
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    return [
        hook
        for entries in hooks["hooks"].values()
        for hook in entries
    ]


def _assert_powershell_dispatch_after_detach(source: str, *, native_entry: str | None = None):
    detach = source.find("Set-Location")
    process_cwd = source.find("SetCurrentDirectory")
    assert detach >= 0 and process_cwd > detach
    dispatches = list(re.finditer(
        r"(?:&|\.)\s+\$[A-Za-z_]\w*|\bInvoke-(?:PostToolHook|PreToolPolicy)\b",
        source,
    ))
    assert dispatches
    early = [call for call in dispatches if call.start() < process_cwd]
    if early:
        # The native pre-tool ingress owns its own detach before policy/children.
        assert native_entry is not None and len(early) == 1
        assert early[0].group() == "& $s" and "'scripts\\pre-tool-hook.ps1'" in source
        _definitions, entry = native_entry.split("\ntry {", 1)
        _assert_powershell_dispatch_after_detach(entry)
    assert all(call.start() > process_cwd for call in dispatches if call not in early)


def test_every_hook_leaves_payload_cwd_before_running_script():
    native_entry = (_PLUGIN / "scripts" / "pre-tool-hook.ps1").read_text("utf-8")
    for hook in _hooks():
        powershell = str(hook["powershell"])
        bash = str(hook["bash"])
        assert "$env:USERPROFILE" in powershell
        assert "-ErrorAction Stop" in powershell
        assert "GetPathRoot" in powershell
        _assert_powershell_dispatch_after_detach(powershell, native_entry=native_entry)
        assert 'cd "$HOME"' in bash
        bash_detach = bash.find("cd ")
        bash_script = bash.find("s=")
        assert bash_detach >= 0 and bash_script >= 0
        assert bash_detach < bash_script


@pytest.mark.parametrize("dispatch", ["& $s", ". $s", "& $py $s"])
def test_hook_cwd_guard_distinguishes_assignment_from_early_dispatch(dispatch):
    detach = "Set-Location $env:USERPROFILE; [IO.Directory]::SetCurrentDirectory($HOME); "
    selection = "$s = 'fixture.ps1'; if ($s) { $selected = $s }; "
    _assert_powershell_dispatch_after_detach(selection + detach + dispatch)
    with pytest.raises(AssertionError):
        _assert_powershell_dispatch_after_detach(selection + dispatch + "; " + detach)


def test_native_ingress_cannot_exempt_a_dispatch_before_its_own_detach():
    wrapper = "'scripts\\pre-tool-hook.ps1'; & $s; " + (
        "Set-Location $HOME; [IO.Directory]::SetCurrentDirectory($HOME); & $py $s"
    )
    with pytest.raises(AssertionError):
        _assert_powershell_dispatch_after_detach(
            wrapper,
            native_entry="\ntry {\n& $py $s; Set-Location $HOME; "
            "[IO.Directory]::SetCurrentDirectory($HOME)\n}",
        )


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
    # The launcher relocated to Worktree Manager in Phase 3b Sub-slice 2a
    # Step 2 (efforts/active/worktree-manager-control-plane/phase-3b-mux-
    # relocation.md); agent-worktrees no longer ships its own copy.
    provision = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    launcher = (
        _PLUGIN.parent.parent / "worktree-manager" / "bin" / "launch-session.ps1"
    ).read_text("utf-8")
    assert "$plan.PSObject.Properties['diagnostics']" in provision
    assert "function Write-PlanDiagnostics" in launcher
    assert "$Plan.PSObject.Properties['diagnostics']" in launcher
