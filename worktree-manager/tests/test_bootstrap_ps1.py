"""bootstrap.ps1 must fail when a native step fails.

``$ErrorActionPreference = 'Stop'`` does not apply to native exit codes, and
``pwsh -File`` reports success unless the script itself fails. Without explicit
checks a failed ``git`` fetch or ``uv`` step still exited 0, so callers only
failed later with a misleading "command is missing" error (or silently
installed a stale staged payload).
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

BOOTSTRAP = Path(__file__).resolve().parents[1] / "bootstrap.ps1"
PWSH = shutil.which("pwsh")

pytestmark = pytest.mark.skipif(
    PWSH is None or shutil.which("git") is None,
    reason="pwsh and git are required to exercise bootstrap.ps1",
)

_GIT_ID = [
    "-c", "user.email=e2e@example.invalid",
    "-c", "user.name=e2e",
    "-c", "commit.gpgsign=false",
]


_LONG_SOURCE_PATH = Path("source-fixtures") / ("nested-" * 15) / ("file-" * 20 + ".txt")


def _source_repo(root: Path, *, long_path: bool = False) -> Path:
    repo = root / "source"
    (repo / "worktree-manager").mkdir(parents=True)
    (repo / "worktree-manager" / "README.md").write_text("payload\n", "utf-8")
    if long_path:
        entry = repo / _LONG_SOURCE_PATH
        entry.parent.mkdir(parents=True)
        entry.write_text("long source path\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "core.longpaths=true", "add", "-A"], check=True,
    )
    subprocess.run(["git", "-C", str(repo), *_GIT_ID, "commit", "-q", "-m", "payload"], check=True)
    return repo


def _fake_tools(bin_dir: Path, uv_exit: int, python_exit: int) -> None:
    """A fake ``uv`` whose ``python find`` names a fake interpreter."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        python = bin_dir / "fake-python.cmd"
        python.write_text(f"@echo off\r\nexit /b {python_exit}\r\n", "utf-8")
        # A .ps1 stub: cmd.exe would treat the real ``'>=3.11'`` argument as a
        # redirection, and PowerShell resolves .ps1 before PATHEXT executables.
        (bin_dir / "uv.ps1").write_text(
            "if ($env:BOOTSTRAP_UV_TRACE) {\n"
            "  Add-Content -LiteralPath $env:BOOTSTRAP_UV_TRACE "
            "-Value ($PSCommandPath + '|' + ($args -join ' '))\n"
            "  if ($env:BOOTSTRAP_UV_POISON) {\n"
            "    $env:PATH = $env:BOOTSTRAP_UV_POISON + ';' + $env:PATH\n"
            "  }\n"
            "  if ($args[1] -eq 'install') { exit 0 }\n"
            "  if (@(Get-Content -LiteralPath $env:BOOTSTRAP_UV_TRACE).Count -eq 1) { exit 3 }\n"
            "}\n"
            f"Write-Output '{python}'\nexit {uv_exit}\n", "utf-8"
        )
    else:
        python = bin_dir / "fake-python"
        python.write_text(f"#!/bin/sh\nexit {python_exit}\n", "utf-8")
        python.chmod(0o755)
        uv = bin_dir / "uv"
        uv.write_text(f"#!/bin/sh\necho {python}\nexit {uv_exit}\n", "utf-8")
        uv.chmod(0o755)


def _run(
    tmp_path: Path, uv_exit: int, ref: str = "main", python_exit: int = 0,
    *, host: str | None = None, encoded: bool = False, linked_uv: bool = False,
    long_path: bool = False, incomplete_checkout: bool = False,
    longpaths_disabled: bool = False,
) -> subprocess.CompletedProcess[str]:
    repo = _source_repo(tmp_path, long_path=long_path)
    root = tmp_path / "manager-root"
    root.mkdir()
    (root / "config.toml").write_text(
        f'[source]\nrepo = "{repo.as_posix()}"\nref = "{ref}"\n', "utf-8"
    )
    fake_bin = tmp_path / "fake-bin"
    _fake_tools(fake_bin, uv_exit, python_exit)
    if incomplete_checkout:
        real_git = shutil.which("git")
        assert real_git is not None
        missing = root / "staging" / "worktree-manager" / "README.md"
        (fake_bin / "git.ps1").write_text(
            f"& {_quote(real_git)} @args\n"
            "$code = $LASTEXITCODE\n"
            "if ($code -eq 0 -and $args -contains 'checkout') {\n"
            f"  Remove-Item -LiteralPath {_quote(missing)}\n"
            "}\n"
            "exit $code\n",
            encoding="utf-8",
        )
    selected_bin = fake_bin
    if linked_uv:
        selected_bin = tmp_path / "linked-tools"
        subprocess.run(
            [os.environ["COMSPEC"], "/d", "/c", "mklink", "/J",
             str(selected_bin), str(fake_bin)],
            capture_output=True, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    env = dict(os.environ)
    env["WORKTREE_MANAGER_ROOT"] = str(root)
    env["USERPROFILE"] = str(tmp_path / "profile")
    env["PATH"] = str(selected_bin) + os.pathsep + env.get("PATH", "")
    if long_path:
        config = tmp_path / "global.gitconfig"
        config.write_text(
            "[core]\nlongpaths = false\n" if longpaths_disabled else "", encoding="utf-8",
        )
        env["GIT_CONFIG_GLOBAL"] = str(config)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        for key in list(env):
            if key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")) or key == "GIT_CONFIG_COUNT":
                env.pop(key)
    if linked_uv:
        poison = tmp_path / "poison"
        poison.mkdir()
        (poison / "uv.ps1").write_text("throw 'UV was reselected from PATH'\n", "utf-8")
        env["BOOTSTRAP_UV_TRACE"] = str(tmp_path / "uv-trace.txt")
        env["BOOTSTRAP_UV_POISON"] = str(poison)
    command = [host or PWSH, "-NoProfile", "-NonInteractive"]
    if encoded:
        script = str(BOOTSTRAP).replace("'", "''")
        text = (
            "[Threading.Thread]::CurrentThread.CurrentCulture = "
            "[Globalization.CultureInfo]::GetCultureInfo('de-DE'); "
            f"& '{script}' --version; exit $LASTEXITCODE"
        )
        command += ["-EncodedCommand", base64.b64encode(text.encode("utf-16le")).decode("ascii")]
    else:
        command += ["-File", str(BOOTSTRAP), "--version"]
    return subprocess.run(
        command,
        capture_output=True, text=True, env=env, timeout=120, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )


def test_failed_uv_fails_the_bootstrap(tmp_path):
    result = _run(tmp_path, uv_exit=3)
    assert result.returncode != 0
    assert "Python acquisition failed" in result.stdout + result.stderr


def test_failed_runtime_command_fails_the_bootstrap(tmp_path):
    result = _run(tmp_path, uv_exit=0, python_exit=4)
    assert result.returncode == 4
    assert "failed (4)" in result.stdout + result.stderr


def test_successful_uv_succeeds(tmp_path):
    result = _run(tmp_path, uv_exit=0)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows Git long-path checkout")
@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
def test_bootstrap_checks_out_long_source_paths_without_global_git_changes(tmp_path, host_name):
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    result = _run(tmp_path, uv_exit=0, host=host, long_path=True)
    assert result.returncode == 0, result.stdout + result.stderr
    entry = tmp_path / "manager-root" / "staging" / _LONG_SOURCE_PATH
    assert len(str(entry)) > 260
    assert entry.read_text(encoding="utf-8") == "long source path\n"
    assert "Filename too long" not in result.stdout + result.stderr
    assert (tmp_path / "global.gitconfig").read_text(encoding="utf-8") == ""
    checkout_config = (tmp_path / "manager-root" / "staging" / ".git" / "config").read_text(
        encoding="utf-8",
    )
    assert "longpaths" not in checkout_config.lower()


@pytest.mark.skipif(os.name != "nt", reason="native Windows Git long-path configuration")
def test_bootstrap_preserves_global_disable_without_accepting_partial_source(tmp_path):
    result = _run(tmp_path, uv_exit=0, long_path=True, longpaths_disabled=True)
    assert (tmp_path / "global.gitconfig").read_text(encoding="utf-8") == (
        "[core]\nlongpaths = false\n"
    )
    if result.returncode == 0:
        entry = tmp_path / "manager-root" / "staging" / _LONG_SOURCE_PATH
        assert entry.read_text(encoding="utf-8") == "long source path\n"
        assert "Filename too long" not in result.stdout + result.stderr
    else:
        assert "source checkout is incomplete or changed" in result.stdout + result.stderr
        assert not (tmp_path / "manager-root" / "current-version").exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows Git script adapter")
def test_bootstrap_refuses_incomplete_source_even_after_zero_exit_checkout(tmp_path):
    result = _run(tmp_path, uv_exit=0, incomplete_checkout=True)
    assert result.returncode != 0
    assert "source checkout is incomplete or changed" in result.stdout + result.stderr
    assert "refusing Manager construction" in result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction contract")
@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
def test_bootstrap_retains_physical_uv_across_find_install_and_path_change(tmp_path, host_name):
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    result = _run(tmp_path, uv_exit=0, host=host, linked_uv=True)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = (tmp_path / "uv-trace.txt").read_text(encoding="utf-8-sig").splitlines()
    expected = str(tmp_path / "fake-bin" / "uv.ps1")
    assert calls == [
        expected + "|python find --no-project >=3.11",
        expected + "|python install 3.11",
        expected + "|python find --no-project >=3.11",
    ]
    assert result.stdout == ""


def _resolver() -> str:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    match = re.search(r"^function Resolve-BootstrapExecutable.*?^}", source, re.M | re.S)
    assert match is not None
    return match[0]

def _python_resolvers() -> str:
    source = BOOTSTRAP.read_text(encoding="utf-8")
    functions = [_resolver()]
    for name in ("Find-PhysicalBootstrapPython", "Find-BootstrapPython"):
        match = re.search(rf"^function {name}\b.*?^}}", source, re.M | re.S)
        assert match is not None
        functions.append(match[0])
    return "\n".join(functions)


def _quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


@pytest.mark.skipif(os.name != "nt", reason="native Windows Python alias fallback")
@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("lookup_error", [448, 2])
def test_failed_python_lookup_only_reuses_physical_candidate_for_guard(
    tmp_path, host_name, lookup_error,
):
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    uv = tmp_path / "uv.ps1"
    trace = tmp_path / "trace.txt"
    uv.write_text(
        f"Add-Content -LiteralPath {_quote(trace)} -Value ($args -join ' ')\n"
        "if ($args[1] -eq 'find') {\n"
        f"  Write-Error 'interpreter query failed (os error {lookup_error})' -ErrorAction Continue\n"
        "  exit 2\n"
        "}\n"
        "exit 3\n",
        encoding="utf-8",
    )
    script = "$ErrorActionPreference = 'Stop'\n" + _python_resolvers() + "\n"
    script += (
        "function Get-Command { param($Name, $CommandType, [switch]$All, $ErrorAction)\n"
        f"  [pscustomobject]@{{ Source = {_quote(sys._base_executable)} }}\n"
        "}\n"
        f"$selected = Find-BootstrapPython {_quote(uv)}\n"
        "Write-Output $selected\n"
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    calls = trace.read_text(encoding="utf-8-sig").splitlines()
    if lookup_error == 448:
        assert result.returncode == 0, result.stdout + result.stderr
        assert Path(result.stdout.strip()).samefile(sys._base_executable)
        assert calls == ["python find --no-project >=3.11"]
    else:
        assert result.returncode != 0
        assert "Python acquisition failed" in result.stdout + result.stderr
        assert calls == ["python find --no-project >=3.11", "python install 3.11"]


@pytest.mark.skipif(os.name != "nt", reason="native Windows interpreter protocol")
@pytest.mark.parametrize("report", [
    {"version": [3, 14], "base": False},
    {"version": [3, 10], "base": True},
    {"version": [3, 14], "base": "true"},
    {"version": ["3", "14"], "base": True},
    {"version": [3, 14], "base": True, "executable": "relative-python.exe"},
    'not-json',
])
def test_physical_python_fallback_rejects_venv_old_version_and_bad_protocol(tmp_path, report):
    if isinstance(report, dict):
        report = json.dumps({"executable": sys._base_executable, **report})
    fake = tmp_path / "python.ps1"
    fake.write_text(f"Write-Output {_quote(report)}\nexit 0\n", encoding="utf-8")
    script = "$ErrorActionPreference = 'Stop'\n" + _python_resolvers() + "\n"
    script += (
        "function Get-Command { param($Name, $CommandType, [switch]$All, $ErrorAction)\n"
        f"  [pscustomobject]@{{ Source = {_quote(fake)} }}\n"
        "}\n"
        "if (Find-PhysicalBootstrapPython) { throw 'Invalid interpreter was accepted' }\n"
    )
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("after_install", [False, True])
def test_python_lookup_rejects_empty_success(tmp_path, after_install):
    uv = tmp_path / "uv.ps1"
    trace = tmp_path / "calls.txt"
    uv.write_text(
        f"Add-Content -LiteralPath {_quote(trace)} -Value ($args -join ' ')\n"
        "if ($args[1] -eq 'install') { Write-Output 'installation progress'; exit 0 }\n"
        f"if (${str(after_install).lower()} -and @(Get-Content {_quote(trace)}).Count -eq 1) "
        "{ Write-Error 'No Python found' -ErrorAction Continue; exit 2 }\n"
        "Write-Output ''; exit 0\n",
        encoding="utf-8",
    )
    script = _python_resolvers() + f"\nFind-BootstrapPython {_quote(uv)}\n"
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode != 0
    expected = "Python 3.11 or newer" if after_install else "invalid executable"
    assert expected in result.stdout + result.stderr
    assert not result.stdout.strip()


@pytest.mark.skipif(os.name != "nt", reason="native Windows RedirectionGuard")
@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
def test_guarded_python_lookup_reuses_real_physical_python_twice(tmp_path, host_name):
    import _winapi

    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    physical = Path(sys._base_executable).resolve(strict=True)
    linked = tmp_path / "python-link"
    _winapi.CreateJunction(str(physical.parent), str(linked))
    logical = linked / physical.name
    uv = tmp_path / "uv.ps1"
    uv.write_text(
        "if ($args[1] -ne 'find') { throw 'Python acquisition was attempted' }\n"
        "Write-Error 'interpreter query failed (os error 448)' -ErrorAction Continue\n"
        "exit 2\n",
        encoding="utf-8",
    )
    script = "$ErrorActionPreference = 'Stop'\n" + _python_resolvers() + "\n"
    script += r"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class BootstrapPythonGuard {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GetProcessMitigationPolicy(
        IntPtr process, int policy, out uint flags, UIntPtr size);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool SetProcessMitigationPolicy(int policy, ref uint flags, UIntPtr size);
}
'@
$size = [UIntPtr]::new([uint32]4)
$before = [uint32]0
if (-not [BootstrapPythonGuard]::GetProcessMitigationPolicy(
    [IntPtr](-1), 16, [ref]$before, $size)) { throw 'Cannot inspect caller guard' }
$requested = [uint32]($before -bor 1)
if (-not [BootstrapPythonGuard]::SetProcessMitigationPolicy(
    16, [ref]$requested, $size)) { throw 'Cannot enable isolated caller guard' }
"""
    script += (
        "function Get-Command { param($Name, $CommandType, [switch]$All, $ErrorAction)\n"
        f"  [pscustomobject]@{{ Source = {_quote(logical)} }}\n"
        "}\n"
        f"$logical = {_quote(logical)}\n"
        f"$expected = {_quote(physical)}\n"
        "foreach ($cycle in 1..2) {\n"
        "  $blocked = $false\n"
        "  try { & $logical --version 2>$null | Out-Null } catch { $blocked = $true }\n"
        "  if (-not $blocked) { throw 'Logical interpreter was not guarded' }\n"
        f"  $selected = Find-BootstrapPython {_quote(uv)}\n"
        "  if ($selected -ine $expected) { throw 'Wrong physical interpreter selected' }\n"
        "}\n"
        "$after = [uint32]0\n"
        "if (-not [BootstrapPythonGuard]::GetProcessMitigationPolicy("
        "[IntPtr](-1), 16, [ref]$after, $size)) { throw 'Cannot recheck guard' }\n"
        "if ($after -ne $requested) { throw 'Caller mitigation changed' }\n"
        "Write-Output 'physical-python-ready'\n"
    )
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "physical-python-ready"


@pytest.mark.skipif(os.name != "nt", reason="native Windows RedirectionGuard contract")
@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("link_kind", ["junction", "file"])
def test_physical_uv_runs_twice_while_logical_uv_is_guarded(tmp_path, host_name, link_kind):
    import _winapi

    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    uv = shutil.which("uv")
    assert uv is not None, "contained runner must provide UV"
    configuration = BOOTSTRAP.parents[1] / "libs" / "installer-engine" / "installer_dependency_config.py"
    spec = importlib.util.spec_from_file_location("bootstrap_uv_configuration", configuration)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    selected = module.physical_executable(uv)
    if selected.startswith("\\\\?\\UNC\\"):
        selected = "\\\\" + selected[8:]
    elif selected.startswith("\\\\?\\"):
        selected = selected[4:]
    physical = Path(selected)
    logical = tmp_path / "uv-link"
    if link_kind == "junction":
        _winapi.CreateJunction(str(physical.parent), str(logical))
    else:
        logical.mkdir()
        try:
            os.symlink(physical, logical / physical.name)
        except OSError as error:
            if error.winerror == 1314:
                pytest.skip("file symlink creation requires Windows developer mode or privilege")
            raise
    script = (
        "$ErrorActionPreference = 'Stop'\n" + _resolver() + r"""
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class BootstrapGuard {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool GetProcessMitigationPolicy(
        IntPtr process, int policy, out uint flags, UIntPtr size);
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern bool SetProcessMitigationPolicy(int policy, ref uint flags, UIntPtr size);
}
'@
$before = [uint32]0
$size = [UIntPtr]::new([uint32]4)
if (-not [BootstrapGuard]::GetProcessMitigationPolicy(
    [IntPtr](-1), 16, [ref]$before, $size)) { throw 'Cannot read caller guard' }
$requested = [uint32]($before -bor 1)
if (-not [BootstrapGuard]::SetProcessMitigationPolicy(
    16, [ref]$requested, $size)) { throw 'Cannot enable isolated caller guard' }
"""
        + f"$logical = {_quote(logical / physical.name)}\n"
        + f"$expected = {_quote(physical)}\n"
        + "$env:PATH = [IO.Path]::GetDirectoryName($logical) + ';' + $env:PATH\n"
        + "$UvCommand = "
        + BOOTSTRAP.read_text(encoding="utf-8").split("$UvCommand = ", 1)[1].split(
            "$HaveGit =", 1,
        )[0]
        + r"""
$selected = $Uv
if ($selected -ine $expected) { throw "Not the selected physical UV: $selected (expected $expected)" }
foreach ($cycle in 1..2) {
    $blocked = $false
    try { & $logical --version 2>$null | Out-Null } catch { $blocked = $true }
    if (-not $blocked) { throw 'Logical UV unexpectedly started under guard' }
    $version = & $selected --version
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^uv ') { throw 'Physical UV failed' }
}
$after = [uint32]0
if (-not [BootstrapGuard]::GetProcessMitigationPolicy(
    [IntPtr](-1), 16, [ref]$after, $size) -or $after -ne $requested) {
    throw 'Caller guard changed'
}
"""
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("kind", ["missing", "directory", "cycle", "unsupported"])
def test_bootstrap_executable_refuses_invalid_targets(tmp_path, kind):
    target = tmp_path / "tool"
    if kind == "directory":
        target.mkdir()
    mock = ""
    if kind in ("cycle", "unsupported"):
        value = _quote(target) if kind == "cycle" else "$null"
        mock = f"""
function Get-Item {{
    param($LiteralPath, [switch]$Force, $ErrorAction)
    return [pscustomobject]@{{
        Attributes = [IO.FileAttributes]::ReparsePoint
        Target = {value}
    }}
}}
"""
    script = "$ErrorActionPreference = 'Stop'\n" + _resolver() + mock
    script += f"\nResolve-BootstrapExecutable {_quote(target)}\n"
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode != 0
    assert result.stdout == ""
    expected = {
        "missing": "does not exist",
        "directory": "does not identify a regular file",
        "cycle": "excessive indirection",
        "unsupported": "unsupported link",
    }
    assert expected[kind] in result.stderr


def test_failed_git_fetch_fails_the_bootstrap(tmp_path):
    result = _run(tmp_path, uv_exit=0, ref="no-such-branch")
    assert result.returncode != 0
    assert "git fetch of" in result.stdout + result.stderr


@pytest.mark.parametrize("host_name", ["pwsh", "powershell"])
@pytest.mark.parametrize("encoded", [False, True])
def test_bootstrap_progress_is_once_on_stderr_without_information_records(
    tmp_path, host_name, encoded,
):
    host = shutil.which(host_name)
    if host is None:
        pytest.skip(f"{host_name} unavailable")
    result = _run(tmp_path, uv_exit=0, host=host, encoded=encoded)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr.count("Worktree Manager bootstrap") == 1
    assert "HostInformationMessage" not in result.stderr
    progress = [
        line for line in result.stderr.splitlines()
        if "update/bootstrap " in line
    ]
    assert len(progress) == 2, result.stderr
    assert " running Worktree Manager bootstrap " in progress[0]
    assert " warning Add " in progress[1]
    for line in progress:
        assert re.fullmatch(
            r"\[\d{2}:\d{2}:\d{2}\.\d{3}Z\] update/bootstrap "
            r"(?:running|warning) .+ \[elapsed \d+\.\d+s\]", line,
        ), line
    elapsed = [float(re.search(r"\[elapsed (\d+\.\d+)s\]$", line)[1]) for line in progress]
    assert elapsed == sorted(elapsed)
