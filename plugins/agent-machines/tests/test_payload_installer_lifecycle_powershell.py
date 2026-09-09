"""Bounded, offline helper lifecycle coverage against real PowerShell source."""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
HOSTS = [
    path for path in (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "PowerShell" / "7" / "pwsh.exe",
    ) if path.is_file()
] if os.name == "nt" else []
pytestmark = pytest.mark.skipif(not HOSTS, reason="Windows PowerShell lifecycle")

DRIVER = r"""
$ErrorActionPreference = 'Stop'
$hostExe = (Get-Process -Id $PID).Path
$context = ''; $marketplaceId = ''
$tokens = $null; $errors = $null
$ast = [Management.Automation.Language.Parser]::ParseFile(
    $env:FIXTURE_SOURCE, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'Dispatcher parse failed' }
$function = $ast.Find({
    param($node)
    $node -is [Management.Automation.Language.FunctionDefinitionAst] -and
        $node.Name -eq 'Invoke-AgentMachinesInstaller'
}, $true)
if (-not $function) { throw 'Installer helper not found' }
. ([scriptblock]::Create($function.Extent.Text))
function Start-Process {
    [CmdletBinding()]
    param([string]$FilePath, [string[]]$ArgumentList, [switch]$NoNewWindow,
        [switch]$PassThru, [switch]$Wait, [string]$RedirectStandardOutput,
        [string]$RedirectStandardError)
    if ($env:FIXTURE_FAILURE -eq 'launch') { throw 'original-launch-failure' }
    $child = Microsoft.PowerShell.Management\Start-Process @PSBoundParameters
    [IO.File]::AppendAllText($env:FIXTURE_PIDS, "$($child.Id)`n")
    return $child
}
function Get-Item {
    [CmdletBinding()]
    param([string]$LiteralPath)
    if ($env:FIXTURE_FAILURE -eq 'read') { throw 'original-read-failure' }
    Microsoft.PowerShell.Management\Get-Item @PSBoundParameters
}
$script:cleanupCount = 0
function Remove-Item {
    [CmdletBinding()]
    param([string]$LiteralPath, [switch]$Force)
    [IO.File]::AppendAllText($env:FIXTURE_CLEANUPS, "$LiteralPath`n")
    $script:cleanupCount++
    if ($env:FIXTURE_DELETE_FAILURE -and $script:cleanupCount -eq 1) {
        throw 'injected-delete-failure'
    }
    Microsoft.PowerShell.Management\Remove-Item @PSBoundParameters
}
try {
    $result = Invoke-AgentMachinesInstaller $env:FIXTURE_INSTALLER 'install'
    if ($env:FIXTURE_DESCENDANT) {
        $descendant = [int](Get-Content (Join-Path $env:FIXTURE_ROOT 'descendant.pid'))
        if (-not (Get-Process -Id $descendant -ErrorAction SilentlyContinue)) {
            throw 'Helper waited for descendant'
        }
        [Console]::Error.WriteLine('descendant-still-running')
        [IO.File]::WriteAllText((Join-Path $env:FIXTURE_ROOT 'release'), 'release')
        $deadline = [DateTime]::UtcNow.AddSeconds(5)
        while (Get-Process -Id $descendant -ErrorAction SilentlyContinue) {
            if ([DateTime]::UtcNow -ge $deadline) { throw 'Descendant did not exit' }
            Start-Sleep -Milliseconds 20
        }
    }
    exit $result
} catch {
    [Console]::Error.WriteLine("caught-original: $($_.Exception.Message)")
    exit 73
}
"""


@pytest.fixture(params=HOSTS, ids=lambda host: host.stem)
def sandbox(tmp_path: Path, request):
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_MACHINES_", "FIXTURE_"))
        and key != "AGENT_WORKTREES_OWNER_REF"
    }
    for key in (
        "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP",
        "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME",
    ):
        directory = tmp_path / key.lower()
        directory.mkdir()
        env[key] = str(directory)
    env.update({
        "COPILOT_EXTENSIONS_TEST_CONTAINED": "1",
        "FIXTURE_ROOT": str(tmp_path),
        "FIXTURE_SOURCE": str(PLUGIN / "scripts" / "invoke-payload-runtime.ps1"),
        "FIXTURE_PIDS": str(tmp_path / "pids"),
        "FIXTURE_CLEANUPS": str(tmp_path / "cleanups"),
        "FIXTURE_INSTALLER": str(tmp_path / "installer.ps1"),
    })
    (tmp_path / "driver.ps1").write_text(DRIVER, encoding="utf-8")
    return tmp_path, str(request.param), env


def _alive(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


def _owned(root: Path, env: dict[str, str]) -> set[int]:
    pids = set()
    for name in ("pids", "descendant.pid"):
        path = root / name
        if path.exists():
            pids.update(int(line) for line in path.read_text().splitlines())
    smoke = Path(env["USERPROFILE"]) / ".agent-machines" / "smoke.json"
    if smoke.exists():
        data = json.loads(smoke.read_text(encoding="utf-8-sig"))
        pids.update(data[key] for key in ("child_pid", "grandchild_pid") if data[key])
    return pids


@contextmanager
def _run(sandbox):
    root, host, env = sandbox
    process = subprocess.Popen(
        [host, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(root / "driver.ps1")],
        env=env, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace",
    )
    try:
        stdout, stderr = process.communicate(timeout=18)
        yield subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)
    finally:
        pids = _owned(root, env) | {process.pid}
        for pid in pids:
            if _alive(pid):
                subprocess.run(
                    [str(Path(os.environ["SystemRoot"]) / "System32" / "taskkill.exe"),
                     "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=4, env=env, cwd=root,
                )
        process.wait(timeout=4)
        assert not [pid for pid in pids if _alive(pid)], "Fixture leaked owned processes"


def test_cleanup_does_not_replace_result_or_original_exception(sandbox) -> None:
    root, _, env = sandbox
    env["FIXTURE_DELETE_FAILURE"] = "1"
    for scenario, expected in (("37", 37), ("0", 0), ("launch", 73), ("read", 73)):
        env["FIXTURE_FAILURE"] = scenario
        Path(env["FIXTURE_INSTALLER"]).write_text(
            "param([string]$Action)\n"
            "[Console]::Error.WriteLine('installer diagnostic')\n"
            + (f"exit {scenario}\n" if scenario.isdigit() else "Start-Sleep -Seconds 8\n"),
            encoding="utf-8",
        )
        Path(env["FIXTURE_CLEANUPS"]).unlink(missing_ok=True)
        Path(env["FIXTURE_PIDS"]).unlink(missing_ok=True)
        with _run(sandbox) as result:
            assert result.returncode == expected, result.stderr
            assert result.stdout == ""
            assert "injected-delete-failure" in result.stderr
            paths = Path(env["FIXTURE_CLEANUPS"]).read_text().splitlines()
            assert len(paths) == 2 and paths[0] != paths[1]
            assert Path(paths[0]).is_file()
            assert not Path(paths[1]).exists(), "Second capture cleanup was skipped"
            if scenario.isdigit():
                assert "installer diagnostic" in result.stderr
            else:
                assert f"caught-original: original-{scenario}-failure" in result.stderr
            assert not any(_alive(pid) for pid in _owned(root, env))
        Path(paths[0]).unlink()


def test_real_init_stages_and_watchdog_preserves_124(sandbox) -> None:
    root, _, env = sandbox
    home = Path(env["USERPROFILE"])
    payload = home / ".copilot" / "installed-plugins" / "example" / "agent-machines"
    scripts = payload / "scripts"
    probe = scripts / "installation-context" / "legacy-entrypoint-probe.ps1"
    probe.parent.mkdir(parents=True)
    # Only the external governance probe is stubbed; staging/watchdog/smoke are real.
    probe.write_text("exit 0\n", encoding="utf-8")
    shutil.copyfile(PLUGIN / "scripts" / "init.ps1", scripts / "init.ps1")
    shutil.copyfile(PLUGIN / "plugin.json", payload / "plugin.json")
    env["FIXTURE_INSTALLER"] = str(scripts / "init.ps1")
    env["COPILOT_PLUGIN_INSTALL_SMOKE"] = "1"
    smoke_home = home / ".agent-machines"
    for sleep, deadline, expected in (("0", "8", 0), ("12", "3", 124)):
        env["COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP"] = sleep
        env["COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"] = deadline
        (smoke_home / "smoke.json").unlink(missing_ok=True)
        Path(env["FIXTURE_PIDS"]).unlink(missing_ok=True)
        with _run(sandbox) as result:
            assert result.returncode == expected, (result.stdout, result.stderr)
            assert result.stdout == ""
            record = json.loads((smoke_home / "smoke.json").read_text(encoding="utf-8-sig"))
            assert record["staged"] is True
            assert Path(record["staged_from"]) == payload
            assert (smoke_home / ".install-stage") in Path(record["ran_from"]).parents
            assert record["grandchild_pid"] == 0
            assert not any(_alive(pid) for pid in _owned(root, env))
            assert not list(Path(env["TEMP"]).iterdir())
            if expected == 124:
                log = (smoke_home / "reconcile.err.log").read_text(encoding="utf-8-sig")
                assert "WATCHDOG-KILL" in log and f"child pid {record['child_pid']}" in log


def test_helper_waits_for_child_not_owned_descendant(sandbox) -> None:
    root, _, env = sandbox
    env["FIXTURE_DESCENDANT"] = "1"
    (root / "descendant.ps1").write_text(r"""
$root = $env:FIXTURE_ROOT
[IO.File]::WriteAllText((Join-Path $root 'ready'), 'ready')
$deadline = [DateTime]::UtcNow.AddSeconds(10)
while (-not (Test-Path (Join-Path $root 'release'))) {
    if ([DateTime]::UtcNow -ge $deadline) { exit 88 }
    Start-Sleep -Milliseconds 20
}
[IO.File]::WriteAllText((Join-Path $root 'released'), 'released')
""", encoding="utf-8")
    Path(env["FIXTURE_INSTALLER"]).write_text(r"""
param([string]$Action)
$ErrorActionPreference = 'Stop'
$root = $env:FIXTURE_ROOT
$entry = Join-Path $root 'descendant.ps1'
$child = Start-Process -FilePath (Get-Process -Id $PID).Path -PassThru -NoNewWindow `
    -RedirectStandardOutput (Join-Path $root 'descendant.out') `
    -RedirectStandardError (Join-Path $root 'descendant.err') `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$entry`"")
[IO.File]::WriteAllText((Join-Path $root 'descendant.pid'), [string]$child.Id)
$deadline = [DateTime]::UtcNow.AddSeconds(5)
while (-not (Test-Path (Join-Path $root 'ready'))) {
    if ([DateTime]::UtcNow -ge $deadline) { exit 89 }
    Start-Sleep -Milliseconds 20
}
exit 0
""", encoding="utf-8")
    with _run(sandbox) as result:
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        assert "descendant-still-running" in result.stderr
        assert (root / "released").read_text() == "released"
        assert not any(_alive(pid) for pid in _owned(root, env))


def test_live_capture_preserves_split_boms_and_multibyte_characters(sandbox) -> None:
    root, _, env = sandbox
    (root / "driver.ps1").write_text(
        "$previousEncoding = [Console]::OutputEncoding\ntry {\n"
        "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n"
        + DRIVER
        + "\n} finally { [Console]::OutputEncoding = $previousEncoding }\n",
        encoding="utf-8",
    )
    text = "x\u00e9\U0001f642\n"
    for encoding, bom in (
        ("utf-8", b""),
        ("utf-8", b"\xef\xbb\xbf"),
        ("utf-16-le", b"\xff\xfe"),
        ("utf-16-be", b"\xfe\xff"),
        ("utf-32-le", b"\xff\xfe\x00\x00"),
        ("utf-32-be", b"\x00\x00\xfe\xff"),
    ):
        data = bom + text.encode(encoding)
        first = 1 if bom else 0
        split = len(bom) + len("x\u00e9".encode(encoding)) + 1
        Path(env["FIXTURE_INSTALLER"]).write_text(
            "param([string]$Action)\n"
            f"$bytes = [byte[]]({','.join(str(value) for value in data)})\n"
            "$outputs = @([Console]::OpenStandardOutput(), [Console]::OpenStandardError())\n"
            f"foreach ($output in $outputs) {{ $output.Write($bytes, 0, {first}); $output.Flush() }}\n"
            "Start-Sleep -Milliseconds 150\n"
            f"foreach ($output in $outputs) {{ $output.Write($bytes, {first}, {split - first}); $output.Flush() }}\n"
            "Start-Sleep -Milliseconds 150\n"
            f"foreach ($output in $outputs) {{ $output.Write($bytes, {split}, {len(data) - split}); $output.Flush() }}\n"
            "exit 0\n",
            encoding="utf-8",
        )
        with _run(sandbox) as result:
            assert result.returncode == 0, result.stderr
            assert result.stdout == ""
            assert result.stderr == text * 2, (encoding, bom, result.stderr)
            assert not list(Path(env["TEMP"]).iterdir())
