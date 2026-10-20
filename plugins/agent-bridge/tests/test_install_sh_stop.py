"""Run the real POSIX stop action with isolated listener and signal fixtures."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

_PLUGIN = Path(__file__).resolve().parents[1]
_GIT_BASH = Path(r"C:\Program Files\Git\bin\bash.exe")
_BASH = (
    str(_GIT_BASH) if _GIT_BASH.is_file()
    else shutil.which("bash") if os.name != "nt" else None
)

pytestmark = pytest.mark.skipif(_BASH is None, reason="requires a native POSIX shell or Git Bash")


def _posix(path: Path) -> str:
    value = path.as_posix()
    if os.name == "nt":
        return "/" + value[0].lower() + value[2:]
    return value


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(0o755)


@pytest.fixture
def stop_action(tmp_path: Path):
    home = tmp_path / "home"
    install = home / ".agent-bridge"
    tools = tmp_path / "tools"
    install.mkdir(parents=True)
    tools.mkdir()
    (install / "config.yaml").write_text("port: 9280\n", encoding="utf-8")
    signals = tmp_path / "signals"
    primary_stopped = tmp_path / "primary-stopped"
    relay_stopped = tmp_path / "relay-stopped"
    startup = tmp_path / "startup.sh"
    startup.write_text(
        """kill() {
    if [[ "${1:-}" == -0 ]]; then
        [[ "${2:-}" == 424242 && ! -f "$PRIMARY_STOPPED" ]]
        return
    fi
    printf '%s\\n' "$*" >> "$SIGNALS"
    case "$*" in
        424242) : > "$PRIMARY_STOPPED" ;;
        424243) : > "$RELAY_STOPPED" ;;
        *) printf 'unexpected fixture signal\\n' >&2; return 99 ;;
    esac
}
""",
        encoding="utf-8",
        newline="\n",
    )
    # Restrict PATH so the missing-ss case cannot find a host listener tool.
    for name in ("dirname", "grep", "sed", "head", "cat", "rm", "seq", "awk", "tr"):
        executable = (
            _GIT_BASH.parent.parent / "usr" / "bin" / f"{name}.exe"
            if os.name == "nt" else Path(shutil.which(name) or "")
        )
        assert executable.is_file(), f"missing shell fixture prerequisite: {name}"
        _executable(
            tools / name,
            f"#!/bin/sh\nexec {shlex.quote(_posix(executable))} \"$@\"\n",
        )
    _executable(tools / "systemctl", "#!/bin/sh\nexit 1\n")
    _executable(tools / "curl", "#!/bin/sh\nexit 1\n")

    def run(
        *, primary: bool = True, relay: bool = False, inspection: str = "ok",
        pid_file: bool = True, systemd: bool = False,
    ):
        if primary and pid_file:
            (install / "agent-bridge.pid").write_text("424242\n", encoding="utf-8")
        if systemd:
            _executable(
                tools / "systemctl",
                "#!/bin/sh\n"
                'if [ "$2" = stop ]; then\n'
                '  echo systemd-stop >> "$SIGNALS"\n'
                '  : > "$PRIMARY_STOPPED"\n'
                "fi\nexit 0\n",
            )
        if inspection != "missing":
            _executable(
                tools / "ss",
                "#!/bin/sh\n"
                + (
                    "printf 'fixture listener inspection failed\\n' >&2\nexit 23\n"
                    if inspection == "failed" else
                    'if [ ! -f "$PRIMARY_STOPPED" ] && [ "$HAS_PRIMARY" = 1 ]; then\n'
                    '  echo \'LISTEN 0 128 127.0.0.1:9280 0.0.0.0:* users:(("bridge",pid=424242,fd=3))\'\n'
                    "fi\n"
                    'if [ ! -f "$RELAY_STOPPED" ] && [ "$HAS_RELAY" = 1 ]; then\n'
                    '  echo \'LISTEN 0 128 127.0.0.1:9857 0.0.0.0:* users:(("relay",pid=424243,fd=4))\'\n'
                    "fi\nexit 0\n"
                ),
            )
        env = {
            key: value for key, value in os.environ.items()
            if not key.startswith(("AGENT_BRIDGE_", "COPILOT_PLUGIN_", "COPILOT_EXTENSIONS_"))
        }
        env.update(
            HOME=_posix(home),
            USERPROFILE=str(home),
            PATH=_posix(tools),
            BASH_ENV=_posix(startup),
            SIGNALS=_posix(signals),
            PRIMARY_STOPPED=_posix(primary_stopped),
            RELAY_STOPPED=_posix(relay_stopped),
            HAS_PRIMARY="1" if primary else "0",
            HAS_RELAY="1" if relay else "0",
        )
        result = subprocess.run(
            [_BASH, _posix(_PLUGIN / "scripts" / "install.sh"), "stop"],
            cwd=tmp_path, env=env, capture_output=True, text=True,
            timeout=20, check=False, **no_window_kwargs(),
        )
        return result, signals.read_text().splitlines() if signals.exists() else []

    return run


@pytest.mark.parametrize("primary", [True, False])
def test_stop_without_relay_succeeds(stop_action, primary):
    result, signals = stop_action(primary=primary)
    assert result.returncode == 0, result.stdout + result.stderr
    assert signals == (["424242"] if primary else [])
    assert (
        "agent-bridge stopped" if primary else "agent-bridge is not running"
    ) in result.stdout


@pytest.mark.parametrize("inspection", ["missing", "failed"])
@pytest.mark.parametrize("primary", [True, False])
def test_stop_listener_inspection_failure_is_not_success(stop_action, inspection, primary):
    result, signals = stop_action(inspection=inspection, primary=primary)
    assert result.returncode != 0
    assert signals == (["424242"] if primary else [])
    assert "agent-bridge stopped" not in result.stdout
    assert "Cannot inspect TCP listeners" in result.stderr


def test_stop_retires_present_relay(stop_action):
    result, signals = stop_action(relay=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert signals == ["424242", "424243"]
    assert "Credential relay port 9857 still in use" in result.stderr
    assert "agent-bridge stopped" in result.stdout


def test_stop_finds_orphan_when_pid_file_is_absent(stop_action):
    result, signals = stop_action(pid_file=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert signals == ["424242"]
    assert "Stopping orphaned agent-bridge" in result.stdout
    assert "agent-bridge stopped" in result.stdout


def test_stop_preserves_systemd_owner_path(stop_action):
    result, signals = stop_action(systemd=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert signals == ["systemd-stop"]
    assert "agent-bridge stopped (systemd)" in result.stdout
