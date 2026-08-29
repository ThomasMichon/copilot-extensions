"""Regression coverage for the POSIX installer's systemd start fallback."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import sys
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_BASH = shutil.which("bash")


def _plugin_version() -> str:
    text = (_PLUGIN_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, "could not read the plugin version from pyproject.toml"
    return match.group(1)


def _executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_failed_systemd_start_reaches_direct_fallback(tmp_path: Path) -> None:
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    install_dir = home / ".agent-bridge"
    unit_dir = home / ".config" / "systemd" / "user"
    fake_bin.mkdir(parents=True)
    unit_dir.mkdir(parents=True)
    (unit_dir / "agent-bridge.service").write_text("[Service]\n", encoding="utf-8")

    # Versioned-slot layout, as `activate --no-link` leaves it: a
    # `current-version` marker and a completed slot, and NO `venv` link. The
    # installer must resolve the interpreter through the deployed resolver.
    version = _plugin_version()
    slot_bin = install_dir / "versions" / version / "bin"
    slot_bin.mkdir(parents=True)
    (install_dir / "versions" / version / ".install-complete.json").write_text(
        json.dumps(
            {"version": version, "completed_at": "1970-01-01T00:00:00Z", "pid": 1},
            separators=(", ", ": "),
        )
        + "\n",
        encoding="utf-8",
    )
    (install_dir / "current-version").write_text(version + "\n", encoding="utf-8")
    resolver_dir = install_dir / "bin"
    resolver_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        _PLUGIN_ROOT / "scripts" / "resolve-runtime.sh",
        resolver_dir / "resolve-runtime.sh",
    )

    _executable(
        fake_bin / "systemctl",
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        "  *' start '*) exit 1 ;;\n"
        "  *' is-active '*) exit 1 ;;\n"
        "esac\n"
        "exit 0\n",
    )
    _executable(fake_bin / "curl", "#!/bin/sh\nexit 0\n")
    # Stands in for the slot interpreter; the installer invokes it as
    # `python -m agent_bridge start`.
    _executable(
        slot_bin / "python",
        "#!/bin/sh\n"
        "trap 'exit 0' TERM INT\n"
        "sleep 30\n",
    )

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        [_BASH, str(_INSTALL_SH), "start"],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )

    pid_file = home / ".agent-bridge" / "agent-bridge.pid"
    try:
        assert result.returncode == 0, result.stderr
        assert "falling back to direct start" in result.stderr
        assert "agent-bridge started" in result.stdout
        assert pid_file.is_file()
    finally:
        if pid_file.is_file():
            os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_healthy_daemon_without_pid_file_is_not_restarted(tmp_path: Path) -> None:
    """A daemon started by the binstub/hook leaves no PID file.

    Without a liveness probe the installer spawns a duplicate, the singleton
    guard refuses it, and a routine update reports failure over a healthy
    bridge. `start` must see the running daemon and succeed instead.
    """
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    install_dir = home / ".agent-bridge"
    fake_bin.mkdir(parents=True)

    version = _plugin_version()
    slot_bin = install_dir / "versions" / version / "bin"
    slot_bin.mkdir(parents=True)
    (install_dir / "versions" / version / ".install-complete.json").write_text(
        json.dumps(
            {"version": version, "completed_at": "1970-01-01T00:00:00Z", "pid": 1},
            separators=(", ", ": "),
        )
        + "\n",
        encoding="utf-8",
    )
    (install_dir / "current-version").write_text(version + "\n", encoding="utf-8")
    resolver_dir = install_dir / "bin"
    resolver_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        _PLUGIN_ROOT / "scripts" / "resolve-runtime.sh",
        resolver_dir / "resolve-runtime.sh",
    )
    # A live daemon advertises its port here; there is deliberately NO PID file.
    (install_dir / "active.json").write_text(
        json.dumps(
            {"active": {"bind": "127.0.0.1", "port": 8765, "pid": 4242}},
        ),
        encoding="utf-8",
    )

    # Healthy probe; the interpreter must never be invoked to START a duplicate.
    # It is still the interpreter the installer uses to READ active.json, so
    # delegate everything except the start invocation to the real python.
    _executable(
        fake_bin / "curl",
        '#!/bin/sh\necho \'{"status":"ok","service":"agent-bridge"}\'\n',
    )
    sentinel = tmp_path / "spawned"
    _executable(
        slot_bin / "python",
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        f"  *'-m agent_bridge start'*) touch '{sentinel}'; sleep 30 ;;\n"
        f"  *) exec '{sys.executable}' \"$@\" ;;\n"
        "esac\n",
    )

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        [_BASH, str(_INSTALL_SH), "start"],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "already running" in result.stdout
    assert not sentinel.exists(), "spawned a duplicate daemon"
    assert not (install_dir / "agent-bridge.pid").exists()


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_foreign_health_responder_does_not_suppress_the_start(tmp_path: Path) -> None:
    """A stale active.json port can be reused by an unrelated service.

    If any /health responder counted, `start` would skip the spawn and leave no
    daemon running at all, so the response must identify itself as ours.
    """
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    install_dir = home / ".agent-bridge"
    fake_bin.mkdir(parents=True)

    version = _plugin_version()
    slot_bin = install_dir / "versions" / version / "bin"
    slot_bin.mkdir(parents=True)
    (install_dir / "versions" / version / ".install-complete.json").write_text(
        json.dumps(
            {"version": version, "completed_at": "1970-01-01T00:00:00Z", "pid": 1},
            separators=(", ", ": "),
        )
        + "\n",
        encoding="utf-8",
    )
    (install_dir / "current-version").write_text(version + "\n", encoding="utf-8")
    resolver_dir = install_dir / "bin"
    resolver_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        _PLUGIN_ROOT / "scripts" / "resolve-runtime.sh",
        resolver_dir / "resolve-runtime.sh",
    )
    (install_dir / "active.json").write_text(
        json.dumps({"active": {"bind": "127.0.0.1", "port": 8765, "pid": 4242}}),
        encoding="utf-8",
    )

    # Someone else is on that port and answers /health.
    _executable(
        fake_bin / "curl",
        '#!/bin/sh\necho \'{"status":"ok","service":"some-other-service"}\'\n',
    )
    sentinel = tmp_path / "spawned"
    _executable(
        slot_bin / "python",
        "#!/bin/sh\n"
        "case \"$*\" in\n"
        f"  *'-m agent_bridge start'*) touch '{sentinel}'; sleep 30 ;;\n"
        f"  *) exec '{sys.executable}' \"$@\" ;;\n"
        "esac\n",
    )

    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        [_BASH, str(_INSTALL_SH), "start"],
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
        check=False,
    )
    pid_file = install_dir / "agent-bridge.pid"
    try:
        assert result.returncode == 0, result.stderr
        assert sentinel.exists(), "did not start the daemon despite a foreign responder"
        assert "already running" not in result.stdout
    finally:
        if pid_file.is_file():
            os.kill(int(pid_file.read_text().strip()), signal.SIGTERM)


@pytest.mark.skipif(
    _BASH is None or os.name == "nt",
    reason="a POSIX bash environment is not available",
)
def test_probe_uses_the_recorded_bind_address(tmp_path: Path) -> None:
    """active.json records bind as well as port.

    A daemon bound to a specific non-loopback address is unreachable on
    127.0.0.1, so a loopback-only probe would miss it and start a duplicate.
    """
    home = tmp_path / "home"
    fake_bin = tmp_path / "bin"
    install_dir = home / ".agent-bridge"
    fake_bin.mkdir(parents=True)

    version = _plugin_version()
    slot_bin = install_dir / "versions" / version / "bin"
    slot_bin.mkdir(parents=True)
    (install_dir / "versions" / version / ".install-complete.json").write_text(
        json.dumps(
            {"version": version, "completed_at": "1970-01-01T00:00:00Z", "pid": 1},
            separators=(", ", ": "),
        )
        + "\n",
        encoding="utf-8",
    )
    (install_dir / "current-version").write_text(version + "\n", encoding="utf-8")
    resolver_dir = install_dir / "bin"
    resolver_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(
        _PLUGIN_ROOT / "scripts" / "resolve-runtime.sh",
        resolver_dir / "resolve-runtime.sh",
    )
    (install_dir / "active.json").write_text(
        json.dumps(
            {"active": {"bind": "10.1.2.3", "port": 8765, "pid": 4242}},
        ),
        encoding="utf-8",
    )

    # Only answers for the recorded bind; a loopback probe records the miss.
    probed = tmp_path / "probed-url"
    _executable(
        fake_bin / "curl",
        "#!/bin/sh\n"
        f"for a in \"$@\"; do echo \"$a\" >> '{probed}'; done\n"
        'case "$*" in\n'
        "  *10.1.2.3:8765*) "
        "echo '{\"status\":\"ok\",\"service\":\"agent-bridge\"}' ;;\n"
        "  *) exit 7 ;;\n"
        "esac\n",
    )
    sentinel = tmp_path / "spawned"
    _executable(
        slot_bin / "python",
        "#!/bin/sh\n"
        'case "$*" in\n'
        f"  *'-m agent_bridge start'*) touch '{sentinel}'; sleep 30 ;;\n"
        f"  *) exec '{sys.executable}' \"$@\" ;;\n"
        "esac\n",
    )

    result = subprocess.run(
        [_BASH, str(_INSTALL_SH), "start"],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "HOME": str(home),
            "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        },
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "already running" in result.stdout
    assert not sentinel.exists(), "spawned a duplicate against a bound daemon"
    assert "10.1.2.3:8765" in probed.read_text(encoding="utf-8")


def test_preflight_probe_is_bounded_by_timeouts() -> None:
    """A half-open listener must not hang the installer before it can start.

    The pre-flight runs before the normal start path, so an unbounded probe
    would strand `start` with no fallback.
    """
    # Join shell line-continuations so a wrapped invocation reads as one line.
    install_sh = _INSTALL_SH.read_text(encoding="utf-8").replace("\\\n", " ")
    preflight = [
        line
        for line in install_sh.splitlines()
        if "curl" in line and "/health" in line and "${live_host}" in line
    ]
    assert preflight, "the pre-flight probe should target the resolved endpoint"
    for line in preflight:
        assert "--connect-timeout" in line, line
        assert "--max-time" in line, line


def _bind_resolver_source() -> str:
    """The embedded python that turns active.json's `bind` into a URL host."""
    text = _INSTALL_SH.read_text(encoding="utf-8")
    start = text.index("import json, re, sys")
    return text[start : text.index("PYEOF", start)]


@pytest.mark.parametrize(
    ("bind", "expected"),
    [
        ("127.0.0.1", "127.0.0.1"),
        ("10.1.2.3", "10.1.2.3"),
        # Wildcards are not connectable.
        ("0.0.0.0", "127.0.0.1"),
        ("::", "127.0.0.1"),
        ("", "127.0.0.1"),
        # IPv6 literals need brackets to form a valid URL authority.
        ("::1", "[::1]"),
        ("[::1]", "[::1]"),
        ("fe80::1", "[fe80::1]"),
        # A corrupt/hostile value must never reach the URL verbatim.
        ('evil" ; touch /tmp/pwned ; #', "127.0.0.1"),
        ("$(touch /tmp/pwned)", "127.0.0.1"),
        ("host;rm -rf /", "127.0.0.1"),
    ],
)
def test_bind_is_sanitised_for_the_probe_url(
    tmp_path: Path, bind: str, expected: str
) -> None:
    active = tmp_path / "active.json"
    active.write_text(
        json.dumps({"active": {"bind": bind, "port": 8765}}), encoding="utf-8"
    )
    result = subprocess.run(
        [sys.executable, "-", str(active)],
        input=_bind_resolver_source(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected
