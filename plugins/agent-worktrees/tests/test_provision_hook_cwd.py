"""Hook processes must not retain the replaceable plugin payload as their cwd."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
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


def _assert_powershell_dispatch_after_detach(
    source: str, *, native_entry: str | None = None,
) -> None:
    relocation = re.search(r"\bSet-Location\b|\.Path\.SetLocation\(", source)
    detach = relocation.start() if relocation is not None else -1
    process_cwd = source.find("SetCurrentDirectory")
    dispatches = list(re.finditer(
        r"""(?:&|\.)\s+(?:\$[A-Za-z_]\w*|'[^']*'|"[^"]*"|\()"""
        r"|\b(?:Start-Process|Invoke-(?:PostToolHook|PreToolPolicy|SelectedResidentToolHook)"
        r"|Test-(?:PreparedManagedWorktreesSession|NativePreTool(?:Read|Write))"
        r"|Get-NativePreToolStop|ConvertFrom-PreToolJson|Write-PreToolUnavailable)\b",
        source,
    ))
    assert dispatches
    if detach < 0 or process_cwd < 0:
        # Only the native pre-tool entry may own the wrapper's detach.
        assert detach == process_cwd == -1
        assert native_entry is not None
        assert len(dispatches) == 1 and dispatches[0].group() == "& $s"
        assert len(re.findall(r"\$r\s*=", source)) == 1
        assert len(re.findall(r"\$s\s*=", source)) == 1
        assert re.search(
            r"\$r\s*=\s*if\s*\(\$env:COPILOT_PLUGIN_ROOT\)\s*\{\s*"
            r"\[IO\.Path\]::Combine\(\$env:COPILOT_PLUGIN_ROOT,\s*'scripts'\)\s*\}"
            r"\s*elseif\s*\(-not\s+\$env:COPILOT_EXTENSIONS_CONTEXT\)\s*\{\s*"
            r"\[IO\.Path\]::Combine\(\$env:USERPROFILE,\s*'\.agent-worktrees\\bin'\)\s*\}"
            r"\s*else\s*\{\s*''\s*\}", source,
        )
        assert re.search(
            r"\$s\s*=\s*if\s*\(\$r\)\s*\{\s*"
            r"\[IO\.Path\]::Combine\(\$r,\s*'pre-tool-hook\.ps1'\)\s*\}"
            r"\s*else\s*\{\s*''\s*\}", source,
        )
        assert "OpenStandardInput" not in source and "ReadToEnd" not in source
        _definitions, entry = native_entry.split("\ntry {", 1)
        _assert_powershell_dispatch_after_detach(entry)
        return
    assert detach >= 0 and process_cwd > detach
    assert all(call.start() > process_cwd for call in dispatches)
    for read in re.finditer(r"\b(?:OpenStandardInput|ReadToEnd)\b", source):
        assert read.start() > process_cwd


def test_every_hook_leaves_payload_cwd_before_running_script() -> None:
    native_entry = (_PLUGIN / "scripts" / "pre-tool-hook.ps1").read_text("utf-8")
    for hook in _hooks():
        powershell = str(hook["powershell"])
        bash = str(hook["bash"])
        _assert_powershell_dispatch_after_detach(powershell, native_entry=native_entry)
        detach_owner = powershell if re.search(
            r"\bSet-Location\b|\.Path\.SetLocation\(", powershell,
        ) else native_entry
        assert "$env:USERPROFILE" in detach_owner
        if "Set-Location" in detach_owner:
            assert "-ErrorAction Stop" in detach_owner
        else:
            assert "[System.Management.Automation.WildcardPattern]::Escape(" in detach_owner
        assert "GetPathRoot" in detach_owner
        assert 'cd "$HOME"' in bash
        bash_detach = bash.find("cd ")
        bash_script = bash.find("s=")
        assert bash_detach >= 0 and bash_script >= 0
        assert bash_detach < bash_script


@pytest.mark.parametrize("dispatch", ["& $s", ". $s", "& $py $s"])
def test_hook_cwd_guard_distinguishes_assignment_from_early_dispatch(dispatch: str) -> None:
    detach = "Set-Location $env:USERPROFILE; [IO.Directory]::SetCurrentDirectory($HOME); "
    selection = "$s = 'fixture.ps1'; if ($s) { $selected = $s }; "
    _assert_powershell_dispatch_after_detach(selection + detach + dispatch)
    with pytest.raises(AssertionError):
        _assert_powershell_dispatch_after_detach(selection + dispatch + "; " + detach)


@pytest.mark.parametrize("early", ["& $py $s", "[Console]::OpenStandardInput()", "Invoke-PreToolPolicy"])
def test_native_ingress_cannot_exempt_a_dispatch_before_its_own_detach(early: str) -> None:
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    wrapper = hooks["hooks"]["preToolUse"][0]["powershell"]
    with pytest.raises(AssertionError):
        _assert_powershell_dispatch_after_detach(
            wrapper,
            native_entry=f"\ntry {{\n{early}; Set-Location $HOME; "
            "[IO.Directory]::SetCurrentDirectory($HOME); & $py $s\n}",
        )


@pytest.mark.parametrize("change", ["extra-dispatch", "other-entry", "early-input"])
def test_native_delegation_exempts_only_the_native_pre_tool_entry(change: str) -> None:
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    wrapper = hooks["hooks"]["preToolUse"][0]["powershell"]
    if change == "extra-dispatch":
        wrapper += "; & $other"
    elif change == "other-entry":
        wrapper = wrapper.replace("'pre-tool-hook.ps1'", "'other-hook.ps1'")
    else:
        wrapper = "[Console]::OpenStandardInput(); " + wrapper
    native_entry = (_PLUGIN / "scripts" / "pre-tool-hook.ps1").read_text("utf-8")
    with pytest.raises(AssertionError):
        _assert_powershell_dispatch_after_detach(wrapper, native_entry=native_entry)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory replacement semantics")
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("hook_name", ["postToolUse", "preToolUse"])
def test_hook_releases_valid_payload_before_waiting_for_input(
    tmp_path: Path, host: str, hook_name: str, *, home_available: bool = True,
) -> None:
    executable = shutil.which(host)
    if not executable:
        pytest.skip(f"{host} unavailable")
    payload = tmp_path / "payload [literal]"
    scripts = payload / "scripts"
    scripts.mkdir(parents=True)
    home = tmp_path / "home [literal]`tail"
    home.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    witness = tmp_path / "reader-ready.json"
    hooks = json.loads((_PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    command = hooks["hooks"][hook_name][0]["powershell"]
    # Observe the real ingress immediately before its blocking stdin read.
    witness_reader = (
        "[IO.File]::WriteAllText($env:HOOK_CWD_WITNESS, "
        "(@{logical=$ExecutionContext.SessionState.Path.CurrentLocation.Path; "
        "native=[IO.Directory]::GetCurrentDirectory(); "
        "management=[bool](Get-Module -Name Microsoft.PowerShell.Management)} "
        "| ConvertTo-Json -Compress)); $reader = "
    )
    if hook_name == "postToolUse":
        (scripts / "post-tool-hook.ps1").write_text(
            "function Invoke-PostToolHook { param([string]$Raw) [Console]::Out.Write($Raw) }\n",
            encoding="utf-8",
        )
        assert command.count("$reader = ") == 1
        command = command.replace("$reader = ", witness_reader, 1)
    else:
        native_entry = (_PLUGIN / "scripts" / "pre-tool-hook.ps1").read_text("utf-8")
        reader = "$reader = [IO.StreamReader]::new([Console]::OpenStandardInput()"
        assert native_entry.count(reader) == 1
        (scripts / "pre-tool-hook.ps1").write_text(
            native_entry.replace(reader, witness_reader + reader.removeprefix("$reader = "), 1),
            encoding="utf-8",
        )
        (scripts / "managed-session.ps1").write_text(
            "function Test-PreparedManagedWorktreesSession {\n"
            "    param([string]$Raw, [ref]$Facts)\n"
            "    [Console]::Out.Write($Raw)\n"
            "    return $false\n"
            "}\n"
            "function Get-NativePreToolStop { return $true }\n",
            encoding="utf-8",
        )
    environment = {
        **os.environ, "HOME": str(home), "USERPROFILE": str(home),
        "COPILOT_PLUGIN_ROOT": str(payload), "COPILOT_PROJECT_DIR": str(project),
        "HOOK_CWD_WITNESS": str(witness),
        "AGENT_WORKTREES_PRE_TOOL_IMPLEMENTATION": "native",
    }
    if not home_available:
        assert not (home / "missing-home").exists()
        command = (
            "$env:USERPROFILE = [IO.Path]::Combine($env:USERPROFILE, 'missing-home'); "
            "if ([IO.Directory]::Exists($env:USERPROFILE)) { throw 'Fixture home must be unavailable' }; "
            + command
        )
    process = subprocess.Popen(
        [executable, "-NoLogo", "-NoProfile", "-Command", command],
        cwd=payload, env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW"),
    )
    try:
        deadline = time.monotonic() + 10
        while not witness.is_file() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert witness.is_file(), "hook never reached its input reader"
        assert process.poll() is None
        moved = tmp_path / "replaced-payload"
        payload.rename(moved)
        moved.rename(payload)
        selected = json.loads(witness.read_text(encoding="utf-8"))
        expected = home if home_available else Path(payload.anchor)
        assert Path(selected["logical"]) == expected
        assert Path(selected["native"]) == expected
        assert selected["management"] is False
        if not home_available:
            assert not (home / "missing-home").exists()
        event = {"cwd": str(project), "toolName": "view"}
        stdout, stderr = process.communicate(json.dumps(event), timeout=10)
        assert process.returncode == 0, stderr
        assert json.loads(stdout) == event
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="Windows directory replacement semantics")
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("hook_name", ["preToolUse", "postToolUse"])
def test_native_hooks_detach_to_root_when_home_unavailable(
    tmp_path: Path, host: str, hook_name: str,
) -> None:
    test_hook_releases_valid_payload_before_waiting_for_input(
        tmp_path, host, hook_name, home_available=False,
    )


def test_provision_compatibility_path_never_starts_runtime_work() -> None:
    ps1 = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    sh = (_PLUGIN / "scripts" / "provision-check.sh").read_text("utf-8")
    for source in (ps1, sh):
        assert "Report-only compatibility contract: provision-status-v1" in source
        for removed in ("--peek", "--apply", "Start-Process", "nohup", "resolve-runtime"):
            assert removed not in source


def test_provision_hook_surfaces_previous_failure() -> None:
    ps1 = (_PLUGIN / "scripts" / "provision-check.ps1").read_text("utf-8")
    sh = (_PLUGIN / "scripts" / "provision-check.sh").read_text("utf-8")
    assert "Previous background provisioning failed" in ps1
    assert "Previous background provisioning failed" in sh
    assert "catch { }" not in ps1


def test_provision_status_report_does_not_need_a_plan() -> None:
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


def test_report_shell_syntax() -> None:
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
def test_report_only_preserves_status_without_runtime(
    tmp_path: Path, shell: str, case: str,
) -> None:
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
def test_report_only_refuses_nonregular_status_without_waiting(tmp_path: Path, kind: str) -> None:
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
        mkfifo = getattr(os, "mkfifo", None)
        assert callable(mkfifo)
        mkfifo(status, 0o600)
    else:
        if os.name == "posix":
            mkfifo = getattr(os, "mkfifo", None)
            assert callable(mkfifo)
            mkfifo(target, 0o600)
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
