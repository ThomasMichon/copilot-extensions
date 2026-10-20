"""Conditional service-enabled callers must reject native unit-publication errors."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import shlex
import subprocess

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "scripts" / "install.sh"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="native POSIX service contract")


def _functions():
    text = INSTALLER.read_text(encoding="utf-8")
    bodies = []
    for name in ("_install_service", "_write_askpass", "do_install", "do_update"):
        match = re.search(rf"^{name}\(\) \{{.*?^\}}", text, re.M | re.S)
        assert match, name
        bodies.append(match.group())
    return "\n".join(bodies)


def _invoke(tmp_path, action, scenario):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("native Bash unavailable")
    root = tmp_path / "runtime"
    root.mkdir()
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    units = blocker / "units" if scenario == "mkdir" else tmp_path / "units"
    target = units / "agent-vault-fixture.service"
    if scenario != "mkdir":
        units.mkdir()
        if scenario in ("unit-directory", "unit-symlink"):
            if scenario == "unit-symlink":
                collision = tmp_path / "unit-collision"
                collision.mkdir()
                target.symlink_to(collision, target_is_directory=True)
            else:
                target.mkdir()
            (target / "previous.txt").write_text("previous", encoding="utf-8")
        else:
            target.write_bytes(b"previous healthy unit")
    calls = tmp_path / "systemctl-calls"
    local_bin = tmp_path / "local-bin"
    local_bin.mkdir()
    askpass = local_bin / "vault-askpass"
    if scenario in ("askpass-directory", "askpass-symlink"):
        if scenario == "askpass-symlink":
            collision = tmp_path / "askpass-collision"
            collision.mkdir()
            askpass.symlink_to(collision, target_is_directory=True)
        else:
            askpass.mkdir()
        (askpass / "previous.txt").write_text("previous", encoding="utf-8")
    else:
        askpass.write_bytes(b"previous askpass")
    systemctl = r"""
systemctl() {
    printf '%s\n' "$*" >> "$CALLS"
    if [[ "${2:-}" == is-active ]]; then
        active_checks=$((active_checks + 1))
        [[ "$SCENARIO" == healthy && "$active_checks" -gt 1 ]]
        return "$?"
    fi
    [[ "$SCENARIO" != unsupported-manager ]]
}
""" if scenario != "missing-systemd" else ""
    script = f"""
set -euo pipefail
INSTALL_DIR={shlex.quote(str(root))}
UNIT_DIR={shlex.quote(str(units))}
RUN_DIR="$INSTALL_DIR/run"
SYSTEMD_UNIT=agent-vault-fixture.service
SOCKET_PATH="$RUN_DIR/fixture.sock"
PIPE_PATH=fixture-pipe
PID_FILE="$RUN_DIR/fixture.pid"
LOG_FILE="$INSTALL_DIR/logs/fixture.log"
LINK_PYTHON=/fixture/runtime-not-executed
INSTALLATION_ID=""
SERVICE_SUFFIX=""
PORT_ENV_LINE=""
NO_SERVICE={1 if scenario == 'no-service' else 0}
LOCAL_BIN={shlex.quote(str(local_bin))}
ASKPASS="$LOCAL_BIN/vault-askpass"
CALLS={shlex.quote(str(calls))}
BLOCKER={shlex.quote(str(blocker))}
SCENARIO={shlex.quote(scenario)}
active_checks=0
_ensure_runtime() {{
    if [[ "$SCENARIO" == askpass-* ]]; then _write_askpass; else printf '%s\\n' 'runtime-ready'; fi
}}
_downgrade_guard() {{ :; }}
_fail() {{ printf 'FAIL: %s\\n' "$1" >&2; }}
_skip() {{ printf 'SKIP: %s\\n' "$1"; }}
_ok() {{ printf 'OK: %s\\n' "$1"; }}
_warn() {{ printf 'WARN: %s\\n' "$1"; }}
_step() {{ :; }}
cat() {{
    if [[ "$SCENARIO" == askpass-write ]]; then command cat "$@" > "$BLOCKER/child"; return "$?"; fi
    if [[ "$SCENARIO" == unit-write ]]; then command cat "$@" > "$BLOCKER/child"; else command cat "$@"; fi
}}
mktemp() {{
    if [[ "$SCENARIO" == askpass-temp ]]; then command mktemp "$BLOCKER/child.XXXXXX"; return "$?"; fi
    if [[ "$SCENARIO" == unit-temp ]]; then command mktemp "$BLOCKER/child.XXXXXX"; else command mktemp "$@"; fi
}}
mv() {{
    if [[ "$SCENARIO" == askpass-rename ]]; then command mv -f "$2" "$BLOCKER/child"; return "$?"; fi
    if [[ "$SCENARIO" == unit-rename ]]; then command mv -f "$2" "$BLOCKER/child"; else command mv "$@"; fi
}}
chmod() {{
    if [[ "$SCENARIO" == askpass-chmod ]]; then command chmod +x "$BLOCKER/child"; else command chmod "$@"; fi
}}
{systemctl}
{_functions()}
if {action}; then exit 0; else exit 19; fi
"""
    env = os.environ.copy()
    if scenario == "missing-systemd":
        empty_bin = tmp_path / "empty-bin"
        empty_bin.mkdir()
        env["PATH"] = str(empty_bin)
    result = subprocess.run(
        [bash, "-c", script], env=env, capture_output=True, text=True, timeout=10,
    )
    commands = calls.read_text().splitlines() if calls.exists() else []
    return result, target, commands


@pytest.mark.parametrize("action", ["do_install", "do_update"])
@pytest.mark.parametrize("scenario", ["mkdir", "unit-directory", "unit-symlink", "unit-temp", "unit-write", "unit-rename"])
def test_conditional_service_publication_failure_blocks_reconcile_and_success(tmp_path, action, scenario):
    result, target, commands = _invoke(tmp_path, action, scenario)
    assert result.returncode == 19, result.stdout + result.stderr
    assert "FAIL: Vault service" in result.stderr
    assert "complete ===" not in result.stdout
    assert all("--user is-active " in command for command in commands), commands
    if scenario not in ("mkdir", "unit-directory", "unit-symlink"):
        assert target.read_bytes() == b"previous healthy unit"
    if scenario in ("unit-directory", "unit-symlink"):
        assert (target / "previous.txt").read_text() == "previous"
        assert {p.name for p in target.iterdir()} == {"previous.txt"}
        assert target.is_symlink() == (scenario == "unit-symlink")


@pytest.mark.parametrize("action", ["do_install", "do_update"])
@pytest.mark.parametrize("scenario", ["no-service", "missing-systemd", "unsupported-manager", "healthy"])
def test_conditional_service_publication_preserves_documented_best_effort(tmp_path, action, scenario):
    result, target, commands = _invoke(tmp_path, action, scenario)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "complete ===" in result.stdout
    if scenario in ("no-service", "missing-systemd"):
        assert not commands
        assert "SKIP:" in result.stdout
        assert target.read_bytes() == b"previous healthy unit"
    else:
        assert "--user daemon-reload" in commands
        assert "--user enable agent-vault-fixture.service" in commands
        assert "--user restart agent-vault-fixture.service" in commands
        assert "ExecStart=/fixture/runtime-not-executed" in target.read_text()
        assert ("WARN:" if scenario == "unsupported-manager" else "OK:") in result.stdout


@pytest.mark.parametrize("action", ["do_install", "do_update"])
@pytest.mark.parametrize("scenario", ["askpass-temp", "askpass-write", "askpass-chmod", "askpass-rename", "askpass-directory", "askpass-symlink"])
def test_conditional_runtime_publication_failure_stops_service_callers(tmp_path, action, scenario):
    result, target, commands = _invoke(tmp_path, action, scenario)
    assert result.returncode == 19, result.stdout + result.stderr
    assert "complete ===" not in result.stdout
    assert not commands
    assert target.read_bytes() == b"previous healthy unit"
    askpass = tmp_path / "local-bin" / "vault-askpass"
    if scenario in ("askpass-directory", "askpass-symlink"):
        assert (askpass / "previous.txt").read_text() == "previous"
        assert {p.name for p in askpass.iterdir()} == {"previous.txt"}
        assert askpass.is_symlink() == (scenario == "askpass-symlink")
    else:
        assert askpass.read_bytes() == b"previous askpass"
