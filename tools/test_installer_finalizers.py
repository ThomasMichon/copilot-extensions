"""Exercise production cleanup in a still-live PowerShell host."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
ENGINE = REPO / "libs" / "installer-engine"


def _purge_fixture(tmp_path):
    root, outside = tmp_path / "runtime", tmp_path / "outside"
    (root / ".install-stage" / "owner").mkdir(parents=True)
    (root / ".install-stage" / "owner" / "running.ps1").write_text("active", encoding="utf-8")
    for name in (".install.lock", ".install.lock.pid", ".install.lock.pid.reap"):
        (root / name).write_text("coordination", encoding="utf-8")
    (root / "versions" / "old").mkdir(parents=True)
    (root / "versions" / "old" / "data").write_text("runtime", encoding="utf-8")
    for name in (".hidden-state", "a[1]", "-state"):
        (root / name).write_text("state", encoding="utf-8")
    outside.mkdir()
    (outside / "sentinel").write_text("outside", encoding="utf-8")
    return root, outside


def _purge_ps(tmp_path, host, root, body):
    executable = shutil.which(host)
    if os.name != "nt" or executable is None:
        pytest.skip(f"native Windows {host} unavailable")
    runner = tmp_path / "purge.ps1"
    runner.write_text(
        "param($Engine, $Root, $Outside)\n$ErrorActionPreference = 'Stop'\n. $Engine\n"
        + body, encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(runner),
         str(ENGINE / "installer-engine.ps1"), str(root), str(tmp_path / "outside")],
        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
    )


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_purge_keeps_lock_stage_and_junction_target(tmp_path, host):
    root, outside = _purge_fixture(tmp_path)
    result = _purge_ps(tmp_path, host, root, r"""
New-Item -ItemType Junction -Path (Join-Path $Root 'versions\outside') -Target $Outside | Out-Null
$readonly = Get-Item -LiteralPath (Join-Path $Root 'a[1]') -Force
$readonly.IsReadOnly = $true
$lock = [IO.File]::Open((Join-Path $Root '.install.lock'), 'Open', 'ReadWrite', 'None')
$stage = [IO.File]::Open((Join-Path $Root '.install-stage\owner\running.ps1'), 'Open', 'Read', 'None')
try {
    $alias = Join-Path (Split-Path $Root) 'runtime-parent-alias'
    New-Item -ItemType Junction -Path $alias -Target (Split-Path $Root) | Out-Null
    Remove-InstallRuntimeContent -InstallRoot (Join-Path $alias (Split-Path $Root -Leaf))
    $contended = $false
    try {
        $other = [IO.File]::Open((Join-Path $Root '.install.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
        $other.Dispose()
    } catch [IO.IOException] { $contended = $true }
    if (-not $contended) { throw 'purge replaced the held lock' }
    Write-Output 'purged-with-original-lock-held'
} finally { $stage.Dispose(); $lock.Dispose() }
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "purged-with-original-lock-held" in result.stdout
    assert sorted(p.name for p in root.iterdir()) == [
        ".install-stage", ".install.lock", ".install.lock.pid", ".install.lock.pid.reap",
    ]
    assert (outside / "sentinel").read_text(encoding="utf-8") == "outside"
    assert (root / ".install-stage" / "owner" / "running.ps1").read_text() == "active"


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_purge_reports_a_busy_runtime_file(tmp_path, host):
    root, _ = _purge_fixture(tmp_path)
    result = _purge_ps(tmp_path, host, root, r"""
$busy = [IO.File]::Open((Join-Path $Root '.hidden-state'), 'Open', 'ReadWrite', 'None')
try {
    Remove-InstallRuntimeContent -InstallRoot $Root
    Write-Output 'unexpected-purge-success'
} finally { $busy.Dispose() }
""")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "unexpected-purge-success" not in result.stdout
    assert result.stderr
    assert (root / ".hidden-state").is_file()


@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
def test_native_purge_refuses_home_and_link_roots(tmp_path, host):
    root, outside = _purge_fixture(tmp_path)
    result = _purge_ps(tmp_path, host, root, r"""
New-Item -ItemType Junction -Path (Join-Path (Split-Path $Root) 'linked-root') -Target $Outside | Out-Null
$alias = Join-Path (Split-Path $Root) 'home-parent-alias'
New-Item -ItemType Junction -Path $alias -Target (Split-Path $env:USERPROFILE) | Out-Null
[IO.File]::WriteAllText((Join-Path $env:USERPROFILE 'sentinel'), 'home')
$aliasedHome = Join-Path $alias (Split-Path $env:USERPROFILE -Leaf)
foreach ($unsafe in @($env:USERPROFILE, (Join-Path (Split-Path $Root) 'linked-root'), $aliasedHome)) {
    $refused = $false
    try { Remove-InstallRuntimeContent -InstallRoot $unsafe }
    catch {
        $expected = if ($unsafe -eq (Join-Path (Split-Path $Root) 'linked-root')) {
            'Runtime purge requires a real install directory, not a link'
        } else { 'Refusing to purge a user home' }
        if ($_.Exception.Message -ne $expected) { throw }
        $refused = $true
    }
    if (-not $refused) { throw 'unsafe purge root accepted' }
}
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (outside / "sentinel").read_text(encoding="utf-8") == "outside"
    assert (tmp_path / "home" / "sentinel").read_text(encoding="utf-8") == "home"
    assert (root / "versions" / "old" / "data").is_file()


def _purge_bash(tmp_path, root, body):
    bash = shutil.which("bash") if os.name != "nt" else None
    if bash is None:
        pytest.skip("native POSIX Bash required")
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return subprocess.run(
        [bash, "-c", 'set -euo pipefail; . "$1"; ROOT="$2"; OUTSIDE="$3"; ' + body,
         "_", str(ENGINE / "installer-engine.sh"), str(root), str(tmp_path / "outside")],
        capture_output=True, text=True, timeout=15,
        env={**os.environ, "HOME": str(home)},
    )


def test_posix_purge_keeps_the_locked_inode_and_link_target(tmp_path):
    if shutil.which("flock") is None:
        pytest.skip("native flock unavailable")
    root, outside = _purge_fixture(tmp_path)
    inode = (root / ".install.lock").stat().st_ino
    result = _purge_bash(tmp_path, root, r"""
ln -s "$OUTSIDE" "$ROOT/versions/outside"
exec 8>>"$ROOT/.install.lock"; flock -n 8
remove_install_runtime_content "$ROOT"
exec 9>>"$ROOT/.install.lock"
if flock -n 9; then echo 'purge replaced the held inode' >&2; exit 1; fi
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / ".install.lock").stat().st_ino == inode
    assert sorted(p.name for p in root.iterdir()) == [
        ".install-stage", ".install.lock", ".install.lock.pid", ".install.lock.pid.reap",
    ]
    assert (outside / "sentinel").read_text(encoding="utf-8") == "outside"


def test_posix_purge_keeps_the_live_pid_fallback(tmp_path):
    root, _ = _purge_fixture(tmp_path)
    result = _purge_bash(tmp_path, root, r"""
rm "$ROOT/.install.lock.pid"
ln -s "$$" "$ROOT/.install.lock.pid"
remove_install_runtime_content "$ROOT"
test "$(readlink "$ROOT/.install.lock.pid")" = "$$"
kill -0 "$$"
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / ".install.lock.pid").is_symlink()


def test_posix_purge_propagates_removal_failure(tmp_path):
    root, _ = _purge_fixture(tmp_path)
    result = _purge_bash(tmp_path, root, r"""
rm() { return 23; }
remove_install_runtime_content "$ROOT"
echo 'unexpected-purge-success'
""")
    assert result.returncode != 0, result.stdout + result.stderr
    assert "Runtime purge failed" in result.stderr
    assert "unexpected-purge-success" not in result.stdout


@pytest.mark.parametrize("legacy", [False, True], ids=["current", "negative-control"])
def test_posix_purge_refuses_unreadable_root(tmp_path, monkeypatch, legacy):
    check = (
        '    if [[ ! -r "$root" || ! -x "$root" ]]; then\n'
        "        printf '%s\\n' 'Runtime purge requires a readable and searchable install directory' >&2\n"
        "        return 1\n"
        "    fi\n"
    )
    source = (ENGINE / "installer-engine.sh").read_text(encoding="utf-8")
    assert source.count(check) == 1
    if os.name == "nt" or os.geteuid() == 0:
        pytest.skip("unprivileged native POSIX permission checks required")
    if shutil.which("flock") is None:
        pytest.skip("native flock unavailable")
    if legacy:
        engine = tmp_path / "legacy-engine"
        engine.mkdir()
        (engine / "installer-engine.sh").write_text(source.replace(check, ""), encoding="utf-8")
        monkeypatch.setitem(globals(), "ENGINE", engine)
    root, _ = _purge_fixture(tmp_path)
    root.chmod(0o300)
    try:
        result = _purge_bash(tmp_path, root, r"""
exec 8>>"$ROOT/.install.lock"; flock -n 8
remove_install_runtime_content "$ROOT"
echo 'unexpected-purge-success'
""")
    finally:
        root.chmod(0o700)
    if legacy:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "unexpected-purge-success" in result.stdout
    else:
        assert result.returncode != 0, result.stdout + result.stderr
        assert "readable and searchable" in result.stderr
        assert "unexpected-purge-success" not in result.stdout
    assert (root / "versions" / "old" / "data").read_text(encoding="utf-8") == "runtime"


def test_posix_purge_refuses_home_and_link_roots(tmp_path):
    root, outside = _purge_fixture(tmp_path)
    result = _purge_bash(tmp_path, root, r"""
ln -s "$OUTSIDE" "$OUTSIDE-link"
if remove_install_runtime_content "$HOME"; then exit 1; fi
for suffix in '' / /. /./ /.//./; do
    if remove_install_runtime_content "$OUTSIDE-link$suffix"; then exit 1; fi
done
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (outside / "sentinel").read_text(encoding="utf-8") == "outside"


def test_posix_purge_uses_the_physical_directory_holding_the_lock(tmp_path):
    if shutil.which("flock") is None:
        pytest.skip("native flock unavailable")
    root, outside = _purge_fixture(tmp_path)
    result = _purge_bash(tmp_path, root, r"""
mkdir "$OUTSIDE/child" "$OUTSIDE/runtime"
printf requested >"$OUTSIDE/runtime/sentinel"
ln -s "$OUTSIDE/child" "$ROOT/link"
mkdir "$ROOT/runtime"
printf unrelated >"$ROOT/runtime/sentinel"
requested="$ROOT/link/../runtime"
exec 8>>"$requested/.install.lock"; flock -n 8
remove_install_runtime_content "$requested"
test -f "$OUTSIDE/runtime/.install.lock"
exec 9>>"$OUTSIDE/runtime/.install.lock"
if flock -n 9; then echo 'purge replaced the held inode' >&2; exit 1; fi
test ! -e "$OUTSIDE/runtime/sentinel"
test "$(cat "$ROOT/runtime/sentinel")" = unrelated
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "runtime" / "sentinel").read_text(encoding="utf-8") == "unrelated"
    assert sorted(path.name for path in (outside / "runtime").iterdir()) == [".install.lock"]


def test_posix_purge_preserves_trailing_newlines_in_the_root(tmp_path):
    root, _ = _purge_fixture(tmp_path)
    result = _purge_bash(tmp_path, root, r"""
requested="$ROOT"$'\n\n'
mkdir "$requested"
printf requested >"$requested/sentinel"
exec 8>>"$requested/.install.lock"
remove_install_runtime_content "$requested"
test "$requested/.install.lock" -ef /dev/fd/8
test ! -e "$requested/sentinel"
test -f "$ROOT/versions/old/data"
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "versions" / "old" / "data").read_text(encoding="utf-8") == "runtime"
    assert sorted(path.name for path in root.with_name(root.name + "\n\n").iterdir()) == [".install.lock"]


def test_posix_purge_preserves_trailing_newlines_in_home(tmp_path):
    root, _ = _purge_fixture(tmp_path)
    result = _purge_bash(tmp_path, root, r"""
HOME="$ROOT"$'\n\n'
mkdir "$HOME"
printf home >"$HOME/sentinel"
exec 8>>"$ROOT/.install.lock"
remove_install_runtime_content "$ROOT"
test "$ROOT/.install.lock" -ef /dev/fd/8
test ! -e "$ROOT/versions"
test "$(cat "$HOME/sentinel")" = home
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert root.with_name(root.name + "\n\n").joinpath("sentinel").read_text(encoding="utf-8") == "home"


def test_posix_purge_ignores_cdpath_for_relative_roots(tmp_path):
    root, _ = _purge_fixture(tmp_path)
    result = _purge_bash(tmp_path, root, r"""
cd "$ROOT/.."
export CDPATH="$PWD"
HOME=home
exec 8>>"$ROOT/.install.lock"
remove_install_runtime_content runtime
test "$ROOT/.install.lock" -ef /dev/fd/8
test ! -e "$ROOT/versions"
if remove_install_runtime_content "$HOME"; then exit 1; fi
""")
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in root.iterdir()) == [
        ".install-stage", ".install.lock", ".install.lock.pid", ".install.lock.pid.reap",
    ]


@pytest.mark.parametrize("regression", [
    test_posix_purge_preserves_trailing_newlines_in_the_root,
    test_posix_purge_preserves_trailing_newlines_in_home,
    test_posix_purge_ignores_cdpath_for_relative_roots,
], ids=lambda regression: regression.__name__)
def test_posix_purge_path_resolution_negative_controls(tmp_path, monkeypatch, regression):
    source = (ENGINE / "installer-engine.sh").read_text(encoding="utf-8")
    for variable, argument, indent in (("root", "$root", 4), ("home", "$HOME", 8)):
        fixed = (
            f'{variable}="$(CDPATH= cd -P -- "{argument}" && printf \'%s.\' "$PWD")" || return 1\n'
            f'{" " * indent}{variable}="${{{variable}%.}}"'
        )
        assert source.count(fixed) == 1
        source = source.replace(
            fixed, f'{variable}="$(cd -P -- "{argument}" && pwd -P)" || return 1',
        )
    if os.name == "nt":
        pytest.skip("native POSIX Bash required")
    legacy = tmp_path / "legacy-engine"
    legacy.mkdir()
    (legacy / "installer-engine.sh").write_text(source, encoding="utf-8")
    monkeypatch.setitem(globals(), "ENGINE", legacy)
    with pytest.raises(AssertionError):
        regression(tmp_path / "probe")


def test_posix_powershell_purge_compares_physical_home_identity(tmp_path):
    if os.name == "nt" or (pwsh := shutil.which("pwsh")) is None:
        pytest.skip("native POSIX PowerShell required")
    root, _ = _purge_fixture(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / "sentinel").write_text("home", encoding="utf-8")
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    script = r"""
function Write-PurgeFixturePhase([string]$Phase) {
    [Console]::Error.WriteLine([DateTime]::UtcNow.ToString('O') + ' purge-fixture: ' + $Phase)
}
Write-PurgeFixturePhase 'script-started'
. $args[0]
Write-PurgeFixturePhase 'engine-sourced'
function Add-Type {
    Write-PurgeFixturePhase 'type-initialization-started'
    try { Microsoft.PowerShell.Utility\Add-Type @args }
    finally { Write-PurgeFixturePhase 'type-initialization-finished' }
}
$script:ReadInstallDirectoryIdentity = ${function:Get-InstallDirectoryIdentity}
function Get-InstallDirectoryIdentity {
    param([string]$Path)
    Write-PurgeFixturePhase 'identity-started'
    try { & $script:ReadInstallDirectoryIdentity -Path $Path }
    finally { Write-PurgeFixturePhase 'identity-finished' }
}
$refused = $false
Write-PurgeFixturePhase 'aliased-home-check'
try { Remove-InstallRuntimeContent -InstallRoot $args[1] }
catch {
    if ($_.Exception.Message -ne 'Refusing to purge a user home') { throw }
    $refused = $true
}
if (-not $refused) { throw 'aliased home accepted' }
Write-PurgeFixturePhase 'aliased-home-refused'
Write-PurgeFixturePhase 'runtime-purge-started'
Remove-InstallRuntimeContent -InstallRoot $args[2]
Write-PurgeFixturePhase 'runtime-purge-finished'
"""
    runner = tmp_path / "purge.ps1"
    runner.write_text(script, encoding="utf-8")
    try:
        result = subprocess.run(
            [pwsh, "-NoProfile", "-NonInteractive", "-File", str(runner),
             str(ENGINE / "installer-engine.ps1"), str(alias / "home"), str(alias / root.name)],
            capture_output=True, text=True, timeout=20,
            env={**os.environ, "HOME": str(home), "USERPROFILE": str(home)},
        )
    except subprocess.TimeoutExpired as error:
        phase_output = error.stderr or b""
        if isinstance(phase_output, bytes):
            phase_output = phase_output.decode("utf-8", errors="replace")
        print(
            "POSIX purge fixture exceeded its original 20s child deadline; native phases:\n"
            + phase_output[-16384:],
            file=sys.stderr, flush=True,
        )
        raise
    assert result.returncode == 0, result.stdout + result.stderr
    assert "purge-fixture: type-initialization-started" in result.stderr
    assert "purge-fixture: type-initialization-finished" in result.stderr
    assert "purge-fixture: runtime-purge-finished" in result.stderr
    assert (home / "sentinel").read_text(encoding="utf-8") == "home"
    assert sorted(path.name for path in root.iterdir()) == [
        ".install-stage", ".install.lock", ".install.lock.pid", ".install.lock.pid.reap",
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX physical-home fixture")
def test_posix_purge_timeout_retains_native_phase_output_and_original_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    error = subprocess.TimeoutExpired(
        ["synthetic-pwsh"], 20, output=b"primary-output",
        stderr=b"purge-fixture: identity-started\n",
    )

    def timeout(command: list[str], **kwargs: object) -> None:
        assert kwargs["timeout"] == 20
        assert "-NoProfile" in command and "-NonInteractive" in command
        script = Path(command[command.index("-File") + 1]).read_text(encoding="utf-8")
        assert "Microsoft.PowerShell.Utility\\Add-Type @args" in script
        assert "type-initialization-started" in script
        assert "type-initialization-finished" in script
        raise error

    monkeypatch.setattr(shutil, "which", lambda name: "synthetic-pwsh")
    monkeypatch.setattr(subprocess, "run", timeout)
    with pytest.raises(subprocess.TimeoutExpired) as failure:
        test_posix_powershell_purge_compares_physical_home_identity(tmp_path)
    assert failure.value is error and error.output == b"primary-output"
    assert error.stderr == b"purge-fixture: identity-started\n"
    diagnostics = capsys.readouterr().err
    assert "original 20s child deadline" in diagnostics
    assert "purge-fixture: identity-started" in diagnostics


def _uninstall_ps(tmp_path, name, *, preview=False, hold=False):
    executable = shutil.which("pwsh") if os.name == "nt" else None
    if executable is None:
        pytest.skip("native Windows PowerShell required")
    root = tmp_path / "runtime"
    home = tmp_path / "home"
    home.mkdir()
    runner = tmp_path / "uninstall.ps1"
    runner.write_text(
        "param($Installer, $Root, $Name, [int]$Preview, [int]$Hold)\n"
        "$ErrorActionPreference = 'Stop'\n"
        "function Get-ScheduledTask {}\n"
        "function Stop-ScheduledTask { throw 'unexpected service mutation' }\n"
        "function Unregister-ScheduledTask { throw 'unexpected service mutation' }\n"
        "$lock = $null\n"
        "if ($Hold) { $lock = [IO.File]::Open((Join-Path $Root '.install.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }\n"
        "try {\n"
        "    $options = @{Action='uninstall'; InstallDir=$Root}\n"
        "    if ($Preview) { $options['DryRun'] = $true }\n"
        "    if ($Name -eq 'agent-vault') { $options['Purge'] = $true }\n"
        "    & $Installer @options\n"
        "    $result = $LASTEXITCODE\n"
        "} finally { if ($lock) { $lock.Dispose() } }\n"
        "exit $result\n",
        encoding="utf-8",
    )
    return subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(runner),
         str(REPO / "plugins" / name / "scripts" / "install.ps1"),
         str(root), name, str(int(preview)), str(int(hold))],
        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
        env={
            **{key: value for key, value in os.environ.items()
               if not key.startswith("COPILOT_PLUGIN_") and key != "COPILOT_EXTENSIONS_CONTEXT"},
            "HOME": str(home), "USERPROFILE": str(home),
            "COPILOT_PLUGIN_INSTALL_LOCK_TIMEOUT_SEC": "1",
        },
        cwd=tmp_path,
    )


@pytest.mark.parametrize("name", ["agent-vault", "agent-ssh", "agent-pull-requests"])
def test_actual_uninstall_preserves_coordination_and_removes_runtime(tmp_path, name):
    root, outside = _purge_fixture(tmp_path)
    result = _uninstall_ps(tmp_path, name)
    assert result.returncode == 0, result.stdout + result.stderr
    assert sorted(path.name for path in root.iterdir()) == [
        ".install-stage", ".install.lock", ".install.lock.pid", ".install.lock.pid.reap",
    ]
    assert (outside / "sentinel").is_file()


@pytest.mark.parametrize("name", ["agent-vault", "agent-ssh"])
def test_actual_uninstall_refuses_the_existing_install_lock(tmp_path, name):
    root, _ = _purge_fixture(tmp_path)
    result = _uninstall_ps(tmp_path, name, hold=True)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "install transaction lock" in result.stdout + result.stderr
    assert (root / "versions" / "old" / "data").read_text(encoding="utf-8") == "runtime"


@pytest.mark.parametrize("name", ["agent-vault", "agent-ssh"])
def test_actual_uninstall_preview_does_not_create_a_runtime(tmp_path, name):
    result = _uninstall_ps(tmp_path, name, preview=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "dry run" in result.stdout
    assert not (tmp_path / "runtime").exists()
    assert not (tmp_path / "home" / ".local").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows FileShare.None contract")
def test_failed_construction_cleanup_releases_lock_in_same_powershell_host(tmp_path):
    source = (
        Path(__file__).resolve().parents[1] / "plugins" / "agent-pull-requests"
        / "scripts" / "install.ps1"
    ).read_text(encoding="utf-8")
    functions = source[
        source.index("function Enter-InstallLock {"):source.index("function Invoke-Stamp {")
    ]
    finalizer = source[source.index("} finally {\n    try {") + len("} finally {"):].rsplit("}", 1)[0]
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$InstallDir = '{str(tmp_path).replace(chr(39), chr(39) * 2)}'\n"
        + functions + "\n"
        "function Stop-RuntimeConstruction { throw 'construction-cleanup-failed' }\n"
        "$needsInstallLock = $true\n"
        "if (-not (Enter-InstallLock -TimeoutSec 0)) { throw 'initial acquisition failed' }\n"
        "if (Enter-InstallLock -TimeoutSec 0) { throw 'held install lock admitted a contender' }\n"
        "try { try { } finally {\n" + finalizer + "\n} } catch {\n"
        "if ($_.Exception.Message -ne 'construction-cleanup-failed') { throw }\n"
        "}\n"
        "if ($script:InstallLockHandle) { throw 'handle survived cleanup failure' }\n"
        "if (-not (Enter-InstallLock -TimeoutSec 0)) { throw 'same-host reacquisition failed' }\n"
        "Exit-InstallLock\n"
        "Write-Output 'same-host-lock-reacquired'\n"
    )
    result = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "same-host-lock-reacquired"
