"""POSIX installer regressions for the agent-logger binstub."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_COMMANDS = (
    "agent-logger",
    "collate-session",
    "read-session-digest",
    "prepare-session-log",
    "ramp-up-session",
    "session-sync",
)
_HARNESS_TIMEOUT_SECONDS = 20


def _isolated_install_env(home: Path) -> dict[str, str]:
    """Mirror the runner's containment roots for direct installer subprocesses."""
    env = os.environ.copy()
    roots = {
        "HOME": home,
        "USERPROFILE": home,
        "APPDATA": home / "AppData" / "Roaming",
        "LOCALAPPDATA": home / "AppData" / "Local",
        "PROGRAMDATA": home / "ProgramData",
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_DATA_HOME": home / ".local" / "share",
        "XDG_STATE_HOME": home / ".local" / "state",
        "XDG_RUNTIME_DIR": home / "run",
        "TEMP": home / "tmp",
        "TMP": home / "tmp",
        "TMPDIR": home / "tmp",
        "COPILOT_HOME": home / ".copilot",
        "AGENT_HOME": home,
        "AGENT_LOGGER_HOME": home / ".agent-logger",
    }
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    env.update({name: str(path) for name, path in roots.items()})
    env["COPILOT_PLUGIN_INSTALL_STAGED"] = "1"
    for name in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV"):
        env.pop(name, None)
    return env


def _stage_payload(tmp_path: Path) -> Path:
    payload = tmp_path / "plugins" / "agent-logger"
    payload.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _PLUGIN_ROOT,
        payload,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            "tests",
        ),
    )
    staged_libs = tmp_path / "libs"
    staged_libs.mkdir(parents=True, exist_ok=True)
    for lib in (
        "installer-engine",
        "config-migrate",
        "agent-procutil",
        "dropin-registry",
        "plugin-resolve",
        "plugin-activation",
    ):
        shutil.copytree(_PLUGIN_ROOT.parents[1] / "libs" / lib, staged_libs / lib)
    return payload


def _host_pip_index_url() -> str | None:
    """Read a configured pip index-url straight from the well-known SYSTEM
    config path, bypassing any per-process env-var sandboxing (this test's
    own containment wrapper, or a caller's, may redirect `PROGRAMDATA`/
    `APPDATA` env vars, but not the actual OS install location). Generic
    and identifier-free: any host with a governed/offline pip feed
    configured this standard way benefits, not just one particular venue.
    """
    candidates = (
        Path(r"C:\ProgramData\pip\pip.ini"),
        Path("/etc/pip.conf"),
        Path("/etc/xdg/pip/pip.conf"),
    )
    for candidate in candidates:
        try:
            text = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        match = re.search(r"(?m)^\s*index-url\s*=\s*(\S+)\s*$", text)
        if match:
            return match.group(1)
    return None


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer behavior")
# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): same class of gap -- two sequential timeout=30 subprocess
# calls mean a worst case near 60s, past the 30s blanket default.
@pytest.mark.timeout(90)
def test_stamp_replaces_dangling_legacy_binstub(tmp_path: Path) -> None:
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    binstub = local_bin / "agent-logger"
    binstub.symlink_to(home / ".agent-logger" / ".venv" / "bin" / "agent-logger")
    result = subprocess.run(
        ["bash", str(payload / "scripts" / "install.sh"), "stamp"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert binstub.is_file()
    assert not binstub.is_symlink()
    assert "agent-logger binstub -- self-provisioning" in binstub.read_text(
        encoding="utf-8"
    )
    assert "stamped-version: No such file or directory" not in result.stderr
    for command in _COMMANDS:
        command_path = local_bin / command
        assert command_path.is_file(), command
        assert os.access(command_path, os.X_OK), command
    auxiliary = (local_bin / "collate-session").read_text(encoding="utf-8")
    assert 'exec "$_shim" "$@"' in auxiliary
    assert "command -v collate-session" not in auxiliary
    payload_dir = Path(
        (home / ".agent-logger" / "payload-dir").read_text(encoding="utf-8").strip()
    )
    assert payload_dir.is_dir()
    assert payload_dir != _PLUGIN_ROOT
    assert (payload_dir / "bin" / "collate-session").is_file()
    shutil.rmtree(payload)
    shadow_bin = tmp_path / "shadow-bin"
    shadow_bin.mkdir()
    shadow = shadow_bin / "collate-session"
    shadow.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    shadow.chmod(0o755)
    env["AGENT_LOGGER_NO_SELFPROVISION"] = "1"
    env["PATH"] = f"{shadow_bin}{os.pathsep}{env.get('PATH', '')}"
    delegated = subprocess.run(
        [str(local_bin / "collate-session"), "--example-argument"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert delegated.returncode == 1
    assert "runtime not provisioned" in delegated.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows installer behavior")
# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): same class of gap -- two sequential subprocess calls
# (timeout=60, timeout=30) mean a worst case near 90s, past the 30s
# blanket default.
@pytest.mark.timeout(120)
def test_windows_stamp_publishes_complete_command_family(tmp_path: Path) -> None:
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    local_bin = home / ".local" / "bin"
    for command in _COMMANDS:
        assert (local_bin / f"{command}.ps1").is_file(), command
        assert (local_bin / f"{command}.cmd").is_file(), command
    auxiliary = (local_bin / "collate-session.ps1").read_text(encoding="utf-8")
    assert "COPILOT_PLUGIN_ROOT" in auxiliary
    assert r"bin\$($_command).ps1" in auxiliary
    shutil.rmtree(payload)
    env["AGENT_LOGGER_NO_SELFPROVISION"] = "1"
    delegated = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(local_bin / "collate-session.ps1"),
            "--example-argument",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert delegated.returncode == 1
    assert "runtime not provisioned" in delegated.stderr


def test_installers_preserve_payload_delegating_auxiliary_wrappers() -> None:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    install_sh = _INSTALL_SH.read_text(encoding="utf-8")

    assert "function Write-Binstubs" in install_ps1
    assert "Deploy-AuxiliaryCompatibilityBinstubs" in install_ps1.split(
        "function Write-Binstubs", 1
    )[1].split("function Install-Package", 1)[0]
    install_package = install_sh.split("install_package() {", 1)[1].split(
        "write_units() {", 1
    )[0]
    assert "deploy_auxiliary_compatibility_binstubs" in install_package
    assert 'ln -sf "${LINK_DIR}/bin/${name}"' not in install_package
    assert "publish_payload_snapshot" in install_package
    assert "Publish-PayloadSnapshot | Out-Null" in install_ps1.split(
        "function Install-Package", 1
    )[1].split("function Register-SyncTask", 1)[0]


def test_windows_update_rebinds_existing_sync_task_runtime() -> None:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    update_binding = install_ps1.split(
        "function Update-SyncTaskBinding", 1
    )[1].split("function Deploy-SelfProvisioningBinstub", 1)[0]
    update_action = install_ps1.split("'update' {", 1)[1].split(
        "'uninstall' {", 1
    )[0]

    assert "Set-ScheduledTask" in update_binding
    assert "$action = New-SyncTaskAction" in update_binding
    assert "-Action $action" in update_binding
    assert "no provisioned runtime" in update_binding
    assert "-Trigger" not in update_binding
    assert "Update-SyncTaskBinding" in update_action


def test_windows_sync_task_writes_wrap_both_register_and_update_branches() -> None:
    """Both Register-SyncTask branches (existing-task update AND new-task
    creation), not just Update-SyncTaskBinding, must downgrade an Access
    Denied failure to a warning instead of aborting the install (a stale
    admin-only task ACL can just as easily block *creation* on a machine that
    has no task yet, e.g. after `schtasks /Delete`)."""
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    register_sync_task = install_ps1.split(
        "function Register-SyncTask", 1
    )[1].split("function Test-IsAccessDenied", 1)[0]

    assert register_sync_task.count("try {") == 2
    assert register_sync_task.count("Write-TaskAccessDeniedWarning") == 2
    assert "Write-TaskAccessDeniedWarning $_ 'update'" in register_sync_task
    assert "Write-TaskAccessDeniedWarning $_ 'register'" in register_sync_task


def _extract_ps1_functions(*names: str) -> str:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    chunks = []
    for name in names:
        rest = install_ps1.split(f"function {name}", 1)[1]
        # Each of these functions is closed by a `}` at column 0 (the file's
        # top-level function-closing convention).
        body = rest.split("\n}\n", 1)[0]
        chunks.append(f"function {name}{body}\n}}")
    return "\n\n".join(chunks)


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_windows_task_access_denied_classifier_downgrades_and_rethrows(
    tmp_path: Path, shell: str
) -> None:
    """Regression for both catch outcomes of the stale-ACL handling: an
    Access Denied error is downgraded to a warning (install continues), while
    any other exception still propagates (install still fails loudly). Runs
    under both Windows PowerShell 5.1 (`powershell.exe`) and PowerShell 7
    (`pwsh`) since the installer must work on either."""
    exe = shutil.which(shell)
    if not exe:
        pytest.skip(f"{shell} is not installed")
    harness = tmp_path / f"harness-{shell.replace('.exe', '')}.ps1"
    harness.write_text(
        _extract_ps1_functions("Test-IsAccessDenied", "Write-TaskAccessDeniedWarning")
        + """

function Write-Warn2 { param([string]$m) Write-Host "WARN: $m" }
$TaskName = 'Test Task'

$deniedRecord = $null
try { throw [System.UnauthorizedAccessException]::new('Access is denied.') }
catch { $deniedRecord = $_ }

$otherRecord = $null
try { throw [System.IO.IOException]::new('disk full') }
catch { $otherRecord = $_ }

Write-TaskAccessDeniedWarning $deniedRecord 'update'
Write-Host 'DENIED-HANDLED-WITHOUT-THROW'

try {
    Write-TaskAccessDeniedWarning $otherRecord 'update'
    Write-Host 'SHOULD-NOT-REACH-HERE'
} catch {
    Write-Host "OTHER-RETHROWN: $($_.Exception.Message)"
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [exe, "-NoProfile", "-File", str(harness)],
        check=True,
        capture_output=True,
        text=True,
        timeout=_HARNESS_TIMEOUT_SECONDS,
    )
    assert "WARN: could not update scheduled task 'Test Task' (Access is denied)" in result.stdout
    assert "DENIED-HANDLED-WITHOUT-THROW" in result.stdout
    assert "OTHER-RETHROWN: disk full" in result.stdout
    assert "SHOULD-NOT-REACH-HERE" not in result.stdout


def test_installers_scope_sync_supervision_by_install_root() -> None:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    install_sh = _INSTALL_SH.read_text(encoding="utf-8")

    assert 'TIMER_NAME="agent-logger-sync${SERVICE_SUFFIX:+-$SERVICE_SUFFIX}"' in install_sh
    assert "Environment=AGENT_LOGGER_HOME=${INSTALL_DIR}" in install_sh
    assert "Agent Logger Session Sync - $serviceSuffix" in install_ps1
    assert "$TaskLauncher = Join-Path (Join-Path $InstallDir 'bin') 'session-sync-task.ps1'" in install_ps1
    assert "$env:AGENT_LOGGER_HOME = $_root" in install_ps1


def test_posix_snapshot_uses_self_staged_payload_not_original() -> None:
    install_sh = _INSTALL_SH.read_text(encoding="utf-8")
    publisher = install_sh.split("publish_payload_snapshot() {", 1)[1].split(
        "# Cheap 'stamp'", 1
    )[0]

    assert 'cp -a "${PLUGIN_DIR}/."' in publisher
    assert "COPILOT_PLUGIN_STAGED_FROM" not in publisher


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): this test's own `subprocess.run(..., timeout=420)` call
# already anticipates needing up to 420s "to absorb a genuinely fresh
# package resolution/build under shared-machine contention" -- but with no
# `@pytest.mark.timeout` override, `run-plugin-tests.py`'s own blanket
# 30s-per-test pytest-timeout default fires first and kills it on any real
# (non-instant, contended) run, well before that 420s internal ceiling is
# ever reached. Same class of false-positive `test_first_install_bootstrap
# .py::test_posix_lean_provision_installs_resolver_and_launchers_reenter_
# runtime` already hit and fixed the same way: give real headroom above
# the test's own internal subprocess ceiling, not a global timeout bump
# that would mask an actual hang in a lighter test elsewhere.
@pytest.mark.timeout(450)
def test_provision_publishes_durable_compatibility_wrappers(
    tmp_path: Path,
) -> None:
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    env = _isolated_install_env(home)
    # This test-runner's own pytest process runs from inside a managed venv
    # (`.test-venvs/.../agent-logger`), which can leave `PYTHONHOME`/
    # `PYTHONPATH` set to THAT venv's own paths. `install.ps1 provision`
    # here builds a genuinely fresh, independent venv via `uv` -- an
    # inherited `PYTHONHOME`/`PYTHONPATH` pointing at a different
    # interpreter's site-packages can corrupt import resolution inside a
    # nested build-isolation venv (observed: `setuptools._distutils_hack`
    # failing to import `distutils.core` while building a local path
    # dependency's wheel). Strip both so this test builds its OWN
    # standalone runtime cleanly, matching how a real end-user's
    # (non-venv-nested) shell invokes this same script.
    # This test's own isolated HOME/USERPROFILE/LOCALAPPDATA (above) is
    # layered on top of the run-plugin-tests.py containment wrapper's OWN
    # sandboxing of HOME/APPDATA/PROGRAMDATA (see
    # `tools/plugin_test_containment.py::_ROOT_ENV`) -- so the real
    # `pip config get global.index-url` / pip.ini discovery that
    # `install.ps1`'s `Ensure-UvIndex` (and `install.sh`'s POSIX
    # equivalent) normally use to bridge a governed/offline venue's pip
    # feed to uv can no longer find the real config via env vars, even
    # though it exists on the actual host. Various venues legitimately
    # block the public PyPI CDN (`files.pythonhosted.org`) while allowing
    # an internal feed proxy -- `_host_pip_index_url()` reads the standard
    # SYSTEM pip config file directly (unaffected by env-var sandboxing)
    # and is fully generic/identifier-free, so prefer its result here
    # explicitly.
    internal_index = _host_pip_index_url()
    if internal_index and not (env.get("UV_DEFAULT_INDEX") or env.get("UV_INDEX_URL")):
        env["UV_DEFAULT_INDEX"] = internal_index
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell is not None
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "provision",
        ]
        wrapper = home / ".local" / "bin" / "collate-session.ps1"
    else:
        command = ["bash", str(payload / "scripts" / "install.sh"), "provision"]
        wrapper = home / ".local" / "bin" / "collate-session"

    provision = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        # 180s comfortably covers a warm-index install; raised to 420s to
        # absorb a genuinely fresh package resolution/build under shared-
        # machine contention (competing test-runner load), which the
        # UV_DEFAULT_INDEX fix above already makes reachable but not
        # instant.
        timeout=420,
        check=False,
    )
    assert provision.returncode == 0, provision.stderr
    snapshot = Path(
        (home / ".agent-logger" / "payload-dir").read_text(encoding="utf-8").strip()
    )
    assert snapshot.is_dir()
    assert wrapper.is_file()


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): same class as `test_provision_publishes_durable_compatibility_
# wrappers` above -- two sequential subprocess.run calls (timeout=60,
# timeout=420) mean a worst case near 480s, well past the 30s blanket
# pytest-timeout default. Real headroom above the combined internal ceiling.
@pytest.mark.timeout(520)
def test_stamp_supports_first_use_provision_from_snapshot_only(tmp_path: Path) -> None:
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    version = next(
        line.split('"')[1]
        for line in (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    env = _isolated_install_env(home)
    internal_index = _host_pip_index_url()
    if internal_index and not (env.get("UV_DEFAULT_INDEX") or env.get("UV_INDEX_URL")):
        env["UV_DEFAULT_INDEX"] = internal_index

    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell is not None
        stamp = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
        ]
        wrapper = home / ".local" / "bin" / "agent-logger.ps1"
        invoke = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
            "version",
        ]
    else:
        stamp = ["bash", str(payload / "scripts" / "install.sh"), "stamp"]
        wrapper = home / ".local" / "bin" / "agent-logger"
        invoke = [str(wrapper), "version"]

    stamped = subprocess.run(
        stamp,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamped.returncode == 0, stamped.stderr

    snapshot = Path(
        (home / ".agent-logger" / "payload-dir").read_text(encoding="utf-8").strip()
    )
    assert (snapshot / "scripts" / "installer-engine.sh").is_file()
    assert (snapshot / "scripts" / "installer-engine.ps1").is_file()
    assert '. "$SCRIPT_DIR/installer-engine.sh"' in (
        snapshot / "scripts" / "install.sh"
    ).read_text(encoding="utf-8")
    assert ". (Join-Path $PSScriptRoot 'installer-engine.ps1')" in (
        snapshot / "scripts" / "install.ps1"
    ).read_text(encoding="utf-8")

    shutil.rmtree(payload)
    shutil.rmtree(tmp_path / "libs")

    provision = subprocess.run(
        invoke,
        env=env,
        capture_output=True,
        text=True,
        timeout=420,
        check=False,
    )
    assert provision.returncode == 0, provision.stderr
    assert f"agent-logger {version}" in provision.stdout


# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): same class of gap -- two sequential timeout=60 subprocess
# calls mean a worst case near 120s, past the 30s blanket default.
@pytest.mark.timeout(150)
def test_stamp_reuses_pre_adoption_same_version_snapshot(tmp_path: Path) -> None:
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    version = next(
        line.split('"')[1]
        for line in (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8").splitlines()
        if line.startswith("version = ")
    )
    legacy_snapshot = home / ".agent-logger" / "snapshots" / version
    shutil.copytree(payload, legacy_snapshot)
    legacy_bin = legacy_snapshot / "bin" / "agent-logger"
    legacy_bin.parent.mkdir(parents=True, exist_ok=True)
    legacy_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    legacy_bin.chmod(0o755)
    install_sh = legacy_snapshot / "scripts" / "install.sh"
    install_ps1 = legacy_snapshot / "scripts" / "install.ps1"
    install_sh.write_text("#!/usr/bin/env bash\n# legacy self-contained install\n", encoding="utf-8")
    install_ps1.write_text("<# legacy self-contained install #>\n", encoding="utf-8")

    env = _isolated_install_env(home)
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell is not None
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
        ]
    else:
        command = ["bash", str(payload / "scripts" / "install.sh"), "stamp"]

    result = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "incomplete" not in result.stderr.lower()

    snapshot = Path(
        (home / ".agent-logger" / "payload-dir").read_text(encoding="utf-8").strip()
    )
    assert snapshot == legacy_snapshot

    sentinel = snapshot / ".snapshot-sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    repeat = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert repeat.returncode == 0, repeat.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"


# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): same class of gap -- a single timeout=60 subprocess call,
# past the 30s blanket default.
@pytest.mark.timeout(90)
def test_scoped_stamp_avoids_global_compatibility_wrappers(tmp_path: Path) -> None:
    payload = _stage_payload(tmp_path)
    home = tmp_path / "home"
    install_dir = (
        home
        / ".copilot-extensions"
        / "marketplaces"
        / "example--1234"
        / "plugins"
        / "agent-logger"
    )
    env = _isolated_install_env(home)
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell is not None
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
            "-InstallDir",
            str(install_dir),
        ]
    else:
        command = [
            "bash",
            str(payload / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(install_dir),
        ]
    result = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (install_dir / "payload-dir").is_file()
    local_bin = home / ".local" / "bin"
    for command_name in _COMMANDS:
        assert not (local_bin / command_name).exists()
        assert not (local_bin / f"{command_name}.ps1").exists()
        assert not (local_bin / f"{command_name}.cmd").exists()


# --------------------------------------------------------------------------- #
# vendored-lib install-step regression                                        #
# --------------------------------------------------------------------------- #
#
# A dependency declared only in pyproject.toml is NOT enough for either
# installer: both install the main package with --no-deps (a bare path
# dependency name like "agent-plugin-activation" has no index entry to
# resolve against), so each vendored lib needs its own explicit install step
# BEFORE that final --no-deps install. This was missed for three new libs
# when schema v3's registered-project trust gate was added (confirmed live:
# a fresh install produced a package that raised `ModuleNotFoundError: No
# module named 'plugin_activation'` on every CLI invocation). These tests
# derive the expected lib set straight from pyproject.toml's own
# ``[tool.uv.sources]`` (the source of truth for "which packages are vendored
# path dependencies") rather than a hand-maintained list, so a future
# dependency add/rename that isn't matched by an install step fails here
# automatically -- no one has to remember to update a second, parallel list.

_PYPROJECT_TEXT = (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def _vendored_path_dependencies() -> dict[str, str]:
    """Map each ``[tool.uv.sources]`` path-pinned package name to its vendored
    ``libs/<dir>`` directory name (e.g. ``"agent-plugin-activation" ->
    "plugin-activation"``). Matches both the real-copy form
    (``path = "libs/<dir>"``) and the `uv`-editable canonical-reference form
    (``path = "../../libs/<dir>", editable = true`` --
    vendor-pointer-generalization effort, Phase 1) -- a lib converted to the
    latter still needs its own explicit install step ahead of this
    installer's final ``--no-deps`` install, exactly like a real copy does."""
    sources_match = re.search(
        r"\[tool\.uv\.sources\](.*?)(?:\n\[|\Z)", _PYPROJECT_TEXT, re.DOTALL
    )
    assert sources_match, "pyproject.toml has no [tool.uv.sources] table"
    mapping = {}
    for name, lib_dir in re.findall(
        r'^([\w-]+)\s*=\s*\{\s*path\s*=\s*"(?:\.\./)*libs/([\w-]+)"',
        sources_match.group(1),
        re.MULTILINE,
    ):
        mapping[name] = lib_dir
    assert mapping, "no path-pinned dependencies found in [tool.uv.sources]"
    return mapping


_VENDORED_LIB_DIRS = sorted(_vendored_path_dependencies().values())


def _install_sh_lib_positions() -> dict[str, int]:
    text = _INSTALL_SH.read_text(encoding="utf-8")
    no_deps_marker = text.index('--no-deps "${PLUGIN_DIR}"')
    body = text[:no_deps_marker]
    positions = {}
    for lib in _VENDORED_LIB_DIRS:
        index = body.find(f"libs/{lib}")
        assert index != -1, f"install.sh has no install step for libs/{lib}"
        positions[lib] = index
    return positions


def _install_ps1_lib_positions() -> dict[str, int]:
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    no_deps_marker = text.index("'--no-deps', \"$PluginDir\"")
    body = text[:no_deps_marker]
    positions = {}
    for lib in _VENDORED_LIB_DIRS:
        index = body.find(f"Dir = '{lib}'")
        assert index != -1, f"install.ps1 has no install step for lib dir '{lib}'"
        positions[lib] = index
    return positions


@pytest.mark.parametrize(
    "positions_fn", [_install_sh_lib_positions, _install_ps1_lib_positions]
)
def test_installers_install_every_pyproject_vendored_lib_before_no_deps(
    positions_fn,
) -> None:
    """Every ``[tool.uv.sources]`` path dependency in pyproject.toml gets an
    explicit install step, in both installers, before the final --no-deps
    main-package install (asserted by ``_install_*_positions`` itself
    raising if a lib's install step is missing entirely)."""
    positions_fn()


@pytest.mark.parametrize(
    "positions_fn", [_install_sh_lib_positions, _install_ps1_lib_positions]
)
def test_installers_install_plugin_activation_after_its_own_transitive_deps(
    positions_fn,
) -> None:
    """plugin_activation imports dropin_registry and plugin_resolve at module
    load time, so its own install step must come after both of theirs --
    installing it first would leave its own dependencies briefly
    unresolvable mid-install (uv resolves each --no-build-isolation install
    independently, so this is about avoiding an inconsistent partial state,
    not a hard uv failure)."""
    positions = positions_fn()
    assert positions["plugin-activation"] > positions["dropin-registry"]
    assert positions["plugin-activation"] > positions["plugin-resolve"]


def test_install_ps1_falls_back_to_monorepo_libs_dir() -> None:
    """Unlike a plugin-local vendored copy (always present in a deployed
    payload), a local monorepo dev checkout may stage the plugin without its
    own libs\\ copy -- install.ps1 must resolve each lib against the
    top-level libs\\ directory in that case, mirroring install.sh's own
    ${PLUGIN_DIR}/../../libs/<lib> fallback."""
    text = _INSTALL_PS1.read_text(encoding="utf-8")
    assert r"..\..\libs\$($lib.Dir)" in text
