"""Real subprocess regression coverage for the two-stage stamp path.

Round 2 of #3303 (full-harness-startup-reliability) split agent-machines'
sessionStart hook into a fast, synchronous `stamp-binstub` action (deploy
ONLY the self-provisioning binstub) plus a backgrounded `stamp` (the slower
full snapshot copy). This file exercises the two real regressions review
caught in that split:

1. A first-turn command run right after `stamp-binstub` (before the
   backgrounded `stamp` has finished its snapshot copy) must NOT hit the
   binstub's ":_noinst"/exit-127 path -- the `payload-dir` marker it reads
   must already resolve to something with a real `scripts\\init.ps1`.
2. Concurrent stamp-family invocations against the same install dir must
   never leave a torn/partial binstub, on both platforms.

Runs against the REAL `init.ps1`/`init.sh` (not a stub), in a genuinely
sandboxed HOME/USERPROFILE per invocation -- no shared state between test
cases, matching the effort's own "fresh state per measurement" methodology.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]
INIT_PS1 = PLUGIN / "scripts" / "init.ps1"
INIT_SH = PLUGIN / "scripts" / "init.sh"

WINDOWS_HOSTS = [
    path for path in (
        Path(os.environ.get("SystemRoot", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "PowerShell" / "7" / "pwsh.exe",
    ) if path.is_file()
] if os.name == "nt" else []

BASH = shutil.which("bash") if os.name != "nt" else None


def _sandbox_env(tmp_path: Path, *, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_MACHINES_"))
    }
    for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
        directory = tmp_path / key.lower()
        directory.mkdir(exist_ok=True)
        env[key] = str(directory)
    if extra:
        env.update(extra)
    return env


@pytest.mark.skipif(not WINDOWS_HOSTS, reason="Windows PowerShell required")
@pytest.mark.parametrize("host", WINDOWS_HOSTS, ids=lambda h: h.stem)
def test_windows_stamp_binstub_leaves_a_usable_payload_dir_marker(tmp_path, host):
    """A first-turn invocation right after `stamp-binstub` must not 127."""
    install_dir = tmp_path / "install"
    env = _sandbox_env(tmp_path)
    result = subprocess.run(
        [str(host), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INIT_PS1),
         "-Action", "stamp-binstub", "-InstallDir", str(install_dir)],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr

    marker = install_dir / "payload-dir"
    assert marker.is_file(), "stamp-binstub must publish a payload-dir marker"
    snapshot_dir = Path(marker.read_text(encoding="utf-8").strip())
    assert (snapshot_dir / "scripts" / "init.ps1").is_file(), (
        "payload-dir must resolve to something with a real scripts\\init.ps1 -- "
        "otherwise the generated binstub's self-provisioning path 127s on its "
        "very first invocation (the exact regression this test guards)"
    )

    binstub = tmp_path / "userprofile" / ".local" / "bin" / "agent-machines.cmd"
    assert binstub.is_file()


@pytest.mark.skipif(not WINDOWS_HOSTS, reason="Windows PowerShell required")
@pytest.mark.parametrize("host", WINDOWS_HOSTS, ids=lambda h: h.stem)
def test_windows_concurrent_stamp_binstub_leaves_no_torn_binstub(tmp_path, host):
    install_dir = tmp_path / "install"
    env = _sandbox_env(tmp_path)
    procs = [
        subprocess.Popen(
            [str(host), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INIT_PS1),
             "-Action", "stamp-binstub", "-InstallDir", str(install_dir)],
            env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(3)
    ]
    for proc in procs:
        _, stderr = proc.communicate(timeout=60)
        assert proc.returncode == 0, stderr

    binstub = tmp_path / "userprofile" / ".local" / "bin" / "agent-machines.cmd"
    content = binstub.read_text(encoding="utf-8")
    assert content.startswith("@echo off")
    assert content.rstrip().endswith(":eof")


@pytest.mark.skipif(not BASH, reason="bash required")
def test_posix_stamp_leaves_a_usable_payload_dir_marker(tmp_path):
    install_dir = tmp_path / "home" / ".agent-machines"
    env = _sandbox_env(tmp_path, extra={
        "COPILOT_PLUGIN_STAGED_FROM": str(PLUGIN),
    })
    result = subprocess.run(
        [BASH, str(INIT_SH), "stamp"],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr

    marker = install_dir / "payload-dir"
    assert marker.is_file()
    payload_dir = Path(marker.read_text(encoding="utf-8").strip())
    assert (payload_dir / "scripts" / "init.sh").is_file()

    binstub = tmp_path / "home" / ".local" / "bin" / "agent-machines"
    assert binstub.is_file()
    content = binstub.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash")
    assert content.rstrip().endswith('exit "$_rc"')


@pytest.mark.skipif(not BASH, reason="bash required")
def test_posix_concurrent_stamp_leaves_no_torn_binstub(tmp_path):
    install_dir = tmp_path / "home" / ".agent-machines"
    env = _sandbox_env(tmp_path, extra={
        "COPILOT_PLUGIN_STAGED_FROM": str(PLUGIN),
    })
    procs = [
        subprocess.Popen(
            [BASH, str(INIT_SH), "stamp"],
            env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(3)
    ]
    for proc in procs:
        _, stderr = proc.communicate(timeout=60)
        assert proc.returncode == 0, stderr

    binstub = tmp_path / "home" / ".local" / "bin" / "agent-machines"
    content = binstub.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash")
    assert content.rstrip().endswith('exit "$_rc"')
    assert not any(install_dir.glob(".stamp.lock.pid")), "lock symlink must not be left behind"
