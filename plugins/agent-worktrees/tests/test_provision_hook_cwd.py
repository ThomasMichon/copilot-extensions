"""Hook processes must not retain the replaceable plugin payload as their cwd."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
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


def test_provision_compatibility_path_never_starts_runtime_work():
    ps1 = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    sh = (_PLUGIN / "scripts" / "provision-check.sh").read_text("utf-8")
    for source in (ps1, sh):
        assert "Report-only compatibility contract: provision-status-v1" in source
        for removed in ("--peek", "--apply", "Start-Process", "nohup", "resolve-runtime"):
            assert removed not in source


def test_provision_hook_surfaces_previous_failure():
    ps1 = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    sh = (_PLUGIN / "scripts" / "provision-check.sh").read_text("utf-8")
    assert "Previous background provisioning failed" in ps1
    assert "Previous background provisioning failed" in sh
    assert "catch { }" not in ps1


def test_provision_status_report_does_not_need_a_plan():
    # The launcher relocated to Worktree Manager in Phase 3b Sub-slice 2a
    # Step 2 (efforts/active/worktree-manager-control-plane/phase-3b-mux-
    # relocation.md); agent-worktrees no longer ships its own copy.
    provision = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    launcher = (
        _PLUGIN.parent.parent / "worktree-manager" / "bin" / "launch-session.ps1"
    ).read_text("utf-8")
    assert "$plan" not in provision
    assert "function Write-PlanDiagnostics" in launcher
    assert "$Plan.PSObject.Properties['diagnostics']" in launcher


def test_report_shell_syntax():
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    bash = str(git_bash) if os.name == "nt" and git_bash.is_file() else shutil.which("bash")
    if not bash or (os.name == "nt" and "system32" in bash.lower()):
        pytest.skip("Bash syntax checker unavailable")
    result = subprocess.run(
        [bash, "-n", str(_PLUGIN / "scripts/provision-check.sh")],
        capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("shell", ["ps1", "sh"])
@pytest.mark.parametrize("case", ["failed", "unreadable", "oversized", "success", "other-repo"])
def test_report_only_preserves_status_without_runtime(tmp_path, shell, case):
    if shell == "sh" and os.name == "nt":
        pytest.skip("native POSIX report reader; Git Bash with Windows Python is not Linux proof")
    executable = (
        shutil.which("powershell.exe") or shutil.which("pwsh")
        if shell == "ps1" else shutil.which("bash")
    )
    if not executable:
        pytest.skip("native report shell unavailable")
    home = tmp_path / "home"
    (home / "AppData/Roaming").mkdir(parents=True)
    root = home / ".agent-worktrees"
    status = root / "logs/provision-status.json"
    status.parent.mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    value = json.dumps({
        "ok": case != "failed" and case != "other-repo",
        "repo": str(tmp_path / "other" if case == "other-repo" else project),
        "failed": [{"service": "fixture-service"}],
    })
    if case == "unreadable":
        value = "{"
    elif case == "oversized":
        value = " " * 65537
    status.write_text(value, encoding="utf-8")
    (root / "bin").mkdir()
    resolver = root / "bin" / f"resolve-runtime.{shell}"
    resolver.write_text(
        "[IO.File]::WriteAllText((Join-Path $env:USERPROFILE 'unexpected'), 'called')\n"
        if shell == "ps1" else 'printf called >"$HOME/unexpected"\n',
        encoding="utf-8",
    )
    before = {str(path.relative_to(root)): path.read_bytes()
              for path in root.rglob("*") if path.is_file()}
    environment = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
                   "WORKTREE_NO_RECONCILE": "1", "WORKTREE_NO_PROVISION": "1",
                   "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    argv = [executable]
    if shell == "ps1":
        argv += ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
    result = subprocess.run(
        [*argv, str(_PLUGIN / "scripts" / f"provision-check.{shell}")],
        env=environment, cwd=project, capture_output=True, text=True, encoding="utf-8",
        timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0 and not result.stdout, result.stdout + result.stderr
    if case == "failed":
        assert "Previous background provisioning failed: fixture-service." in result.stderr
    elif case in {"unreadable", "oversized"}:
        assert "Could not read provisioning status:" in result.stderr
    else:
        assert not result.stderr
    assert {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob("*") if path.is_file()} == before
    assert not (home / "unexpected").exists()
    assert not (root / "versions").exists() and not (root / "snapshots").exists()
    assert not (home / ".local").exists()


@pytest.mark.parametrize("kind", ["directory", "linked", "fifo"])
def test_report_only_refuses_nonregular_status_without_waiting(tmp_path, kind):
    if os.name == "nt":
        if kind == "fifo":
            pytest.skip("FIFO control requires native POSIX Python")
        shell = "ps1"
        executable = shutil.which("powershell.exe") or shutil.which("pwsh")
    else:
        if os.name != "posix" or not all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_NONBLOCK", "mkfifo")):
            pytest.skip("qualified native POSIX file controls unavailable")
        shell = "sh"
        executable = shutil.which("bash")
    if not executable:
        pytest.skip("native report shell unavailable")
    home = tmp_path / "home"
    (home / "AppData/Roaming").mkdir(parents=True)
    status = home / ".agent-worktrees/logs/provision-status.json"
    status.parent.mkdir(parents=True)
    target = tmp_path / "linked-status"
    if kind == "directory":
        status.mkdir()
    elif kind == "fifo":
        os.mkfifo(status, 0o600)
    else:
        if os.name == "posix":
            os.mkfifo(target, 0o600)
        else:
            target.write_text('{"ok":false,"repo":"fixture"}', encoding="utf-8")
        try:
            status.symlink_to(target)
        except OSError as error:
            if getattr(error, "winerror", None) == 1314:
                pytest.skip("fixture symlink requires Windows developer mode or privilege")
            raise
    before = status.lstat()
    environment = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
                   "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"]}
    argv = [executable]
    if shell == "ps1":
        argv += ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"]
    result = subprocess.run(
        [*argv, str(_PLUGIN / "scripts" / f"provision-check.{shell}")],
        env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0 and not result.stdout, result.stdout + result.stderr
    assert "Could not read provisioning status:" in result.stderr
    after = status.lstat()
    assert (after.st_ino, after.st_mode, after.st_mtime_ns) == (
        before.st_ino, before.st_mode, before.st_mtime_ns,
    )
    assert not (home / ".agent-worktrees/versions").exists()
    assert not (home / ".local").exists()
