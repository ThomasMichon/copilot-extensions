"""Required native file operations fail before later maintenance effects."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_action_native_sh import BASH, SCRIPTS, _function
from tests.test_install_sh_activation_guard import _assert_cutover_lock_released

pytestmark = pytest.mark.skipif(not BASH or os.name == "nt", reason="native POSIX bash")

FUNCTIONS = (
    "_install_service", "_supervisor_labels_configured", "_supervisor_mode",
    "_supervisor_profile_name_valid", "_supervisor_unit_for_profile",
    "_supervisor_profile_env_files", "_supervisor_action", "_supervisor_env_for_unit",
    "_write_supervisor_default_env", "_write_supervisor_launcher",
    "_remove_supervisor_unit", "_remove_all_supervisor_units",
    "_install_supervisor_unit", "_install_supervisor_profiles",
    "_reconcile_supervisor_profiles", "_retire_supervisor_profile_units",
    "_install_supervisor_service",
)


def _native_harness(
    tmp_path: Path, function: str, failure: str, *, caller: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
    root = tmp_path / "install"
    root.mkdir()
    units = root / "units"
    profiles = root / "profiles"
    effects = tmp_path / "effects"
    if failure == "env-migration":
        (root / "service.env").write_text("AGENT_DISPATCH_PORT=9847\n")
    if function == "_install_supervisor_profiles":
        units.mkdir()
        profiles.mkdir()
        (profiles / "alpha.env").write_text("AGENT_DISPATCH_SUPERVISE_LABELS=work\n")
    if function in {"_reconcile_supervisor_profiles", "_retire_supervisor_profile_units"}:
        units.mkdir()
        (units / "test-supervisor-alpha.service").write_text("unit\n")
    if failure == "coordinator-removal":
        units.mkdir()
        (units / "test-coordinator.service").write_text("unit\n")
    if failure == "launcher-removal":
        (root / "supervise.sh").write_text("launcher\n")
    if function == "_remove_all_supervisor_units":
        units.mkdir()
        (units / "test-supervisor.service").write_text("unit\n")
        (root / "supervise.sh").write_text("launcher\n")
    script = "\n".join([
        "set -eu",
        f"INSTALL_DIR={str(root)!r}; SCRIPT_DIR={str(SCRIPTS)!r}",
        f"EFFECTS={str(effects)!r}; FAILURE={failure!r}",
        'UNIT_DIR="$INSTALL_DIR/units"; ENV_FILE="$INSTALL_DIR/service.env"',
        'SUPERVISOR_PROFILE_DIR="$INSTALL_DIR/profiles"',
        'SUPERVISOR_ENV_FILE="$INSTALL_DIR/supervisor.env"',
        'SUPERVISOR_LAUNCHER="$INSTALL_DIR/supervise.sh"; SUPERVISOR_PATH="$PATH"',
        "SYSTEMD_UNIT=test-coordinator.service; SUPERVISOR_UNIT=test-supervisor.service",
        "SUPERVISOR_UNIT_BASE=test-supervisor; SUPERVISOR_RECONCILE_FAILED=0",
        f"NO_SERVICE=0; NO_SUPERVISOR={int(failure == 'launcher-removal')}; ACTION=update",
        f"VENV_PYTHON={sys.executable!r}",
        '_is_wsl() { return 1; }; _supervisor_audit() { :; }',
        '_fail() { echo "$*" >&2; }',
        '_ok() { :; }; _skip() { :; }; _step() { :; }; _warn() { :; }',
        f". {str(SCRIPTS / 'source-runtime.sh')!r}",
        *(_function(name) for name in FUNCTIONS),
        r'''
systemctl() {
    case "$*" in
        *is-enabled*) printf absent; return 1 ;;
        *is-active*) return 0 ;;
        *) printf '%s\n' "$*" >> "$EFFECTS" ;;
    esac
}
mkdir() {
    [[ "$FAILURE" != directory ]] || return 73
    command mkdir "$@"
}
cat() {
    if [[ ( "$FAILURE" == coordinator-env && /dev/fd/1 -ef "$ENV_FILE" ) ||
          ( "$FAILURE" == coordinator-unit && /dev/fd/1 -ef "$UNIT_DIR/$SYSTEMD_UNIT" ) ||
          ( "$FAILURE" == supervisor-env && /dev/fd/1 -ef "$SUPERVISOR_ENV_FILE" ) ||
          ( "$FAILURE" == supervisor-launcher && /dev/fd/1 -ef "$SUPERVISOR_LAUNCHER" ) ||
          ( "$FAILURE" == supervisor-unit && /dev/fd/1 -ef "$UNIT_DIR/$SUPERVISOR_UNIT" ) ||
          ( "$FAILURE" == profile-unit && /dev/fd/1 -ef "$UNIT_DIR/test-supervisor-alpha.service" ) ]]; then
        return 73
    fi
    command cat "$@"
}
chmod() {
    [[ "$FAILURE" != launcher-mode ]] || return 73
    command chmod "$@"
}
sed() {
    [[ "$FAILURE" != env-migration ]] || return 73
    command sed "$@"
}
rm() {
    local argument last=""
    for argument in "$@"; do last="$argument"; done
    [[ "$FAILURE" != removal || "$last" != "$UNIT_DIR/$SUPERVISOR_UNIT" ]] || return 73
    [[ "$FAILURE" != profile-removal || "$last" != "$UNIT_DIR/test-supervisor-alpha.service" ]] || return 73
    [[ "$FAILURE" != coordinator-removal || "$last" != "$UNIT_DIR/$SYSTEMD_UNIT" ]] || return 73
    [[ "$FAILURE" != launcher-removal || "$last" != "$SUPERVISOR_LAUNCHER" ]] || return 73
    command rm "$@"
}
''',
    ])
    if failure == "coordinator-removal":
        script += "\n_is_wsl() { return 0; }; _wsl_windows_client() { return 0; }\n"
    if caller:
        script += "\n".join([
            "\n_write_update_marker() { :; }; _clear_update_marker() { :; }",
            "_lifecycle_finish() { :; }; _downgrade_guard() { :; }",
            "_ensure_runtime() { :; }; _activation_superseded_now() { return 1; }",
            "_coordinator_cutover() { return 0; }",
            '_install_supervisor_service() { echo forbidden-supervisor >> "$EFFECTS"; }',
            _function(caller),
        ])
    script += f"\nresult=0\n{caller or function} || result=$?\nexit \"$result\"\n"
    result = subprocess.run(
        [BASH, "-c", script], capture_output=True, text=True, timeout=15,
    )
    return result, root, effects


@pytest.mark.parametrize("function,failure", [
    ("_install_service", "directory"),
    ("_install_service", "coordinator-env"),
    ("_install_service", "env-migration"),
    ("_install_service", "coordinator-unit"),
    ("_install_supervisor_service", "directory"),
    ("_install_supervisor_service", "supervisor-env"),
    ("_install_supervisor_service", "supervisor-launcher"),
    ("_install_supervisor_service", "launcher-mode"),
    ("_install_supervisor_service", "supervisor-unit"),
    ("_install_supervisor_profiles", "profile-unit"),
    ("_install_supervisor_profiles", "directory"),
    ("_install_supervisor_service", "launcher-removal"),
])
def test_required_native_write_failure_prevents_later_effects(
    tmp_path: Path, function: str, failure: str,
) -> None:
    result, _root, effects = _native_harness(tmp_path, function, failure)
    assert result.returncode == 73, result.stdout + result.stderr
    assert not effects.exists(), effects.read_text() if effects.exists() else ""


@pytest.mark.parametrize("function", ["_install_service", "_install_supervisor_service"])
def test_native_fixture_allows_ordinary_effects_without_injection(
    tmp_path: Path, function: str,
) -> None:
    result, _root, effects = _native_harness(tmp_path, function, "none")
    assert result.returncode == 0, result.stdout + result.stderr
    assert effects.exists()


@pytest.mark.parametrize("caller,no_flock", [
    ("do_install", False), ("do_update", False), ("do_update", True),
])
def test_native_write_failure_reaches_caller_and_releases_cutover_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caller: str, no_flock: bool,
) -> None:
    unexpected_flock = tmp_path / "unexpected-flock"
    if no_flock:
        monkeypatch.setenv("COPILOT_EXT_NO_FLOCK", "1")
        bindir = tmp_path / "no-flock"
        bindir.mkdir()
        executable = bindir / "flock"
        executable.write_text(
            f"#!/bin/sh\nprintf invoked > {str(unexpected_flock)!r}\nexit 127\n",
        )
        executable.chmod(0o755)
        monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ.get("PATH", ""))
    else:
        monkeypatch.delenv("COPILOT_EXT_NO_FLOCK", raising=False)
    result, root, effects = _native_harness(
        tmp_path, "_install_service", "coordinator-unit", caller=caller,
    )
    assert result.returncode == 73, result.stdout + result.stderr
    assert not effects.exists()
    if caller == "do_update":
        _assert_cutover_lock_released(root)
    assert not unexpected_flock.exists()


def test_pid_lock_probe_rejects_an_unreleased_dangling_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COPILOT_EXT_NO_FLOCK", "1")
    (tmp_path / ".activate.lock.pid").symlink_to("unreleased-owner")
    with pytest.raises(AssertionError):
        _assert_cutover_lock_released(tmp_path)


@pytest.mark.parametrize("function,failure", [
    ("_remove_all_supervisor_units", "removal"),
    ("_reconcile_supervisor_profiles", "profile-removal"),
    ("_retire_supervisor_profile_units", "profile-removal"),
    ("_install_service", "coordinator-removal"),
])
def test_failed_native_removal_stops_later_maintenance(
    tmp_path: Path, function: str, failure: str,
) -> None:
    result, root, effects = _native_harness(
        tmp_path, function, failure,
    )
    assert result.returncode == 73, result.stdout + result.stderr
    if failure == "removal":
        assert (root / "supervise.sh").exists()
    assert len(effects.read_text().splitlines()) == 2  # Stop and disable precede the failed unlink.
