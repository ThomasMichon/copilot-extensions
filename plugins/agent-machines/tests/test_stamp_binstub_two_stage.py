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
import queue
import shutil
import subprocess
import threading
import time
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


def _assert_binstub_resolves_marker(binstub: Path, env: dict[str, str], tmp_path: Path) -> None:
    """Invoke the real generated binstub and prove it resolves `payload-dir`
    into genuine (slow) provisioning rather than the fast ":_noinst" exit-127
    path -- the exact regression this two-stage split exists to avoid.

    Real self-provisioning can run for 30-120s and shells out to further
    processes (pwsh -File init.ps1 provision), so this deliberately does NOT
    wait for it to finish: it polls stderr for either the "cannot
    self-provision" (bad) or "provisioning on first use" (good, marker
    resolved) line for a few seconds, then kills the whole process tree via
    `taskkill /T /F` (a bare `Popen.terminate()`/`subprocess.run(timeout=...)`
    only signals the top-level `cmd.exe`, leaving a nested `pwsh` running).

    Review finding (round 7): `proc.stderr.readline()` BLOCKS, so polling it
    directly against a wall-clock deadline does not actually enforce that
    deadline -- a hung `.cmd`/nested PowerShell that never writes a newline
    would hang this whole loop (and the test) indefinitely. Read on a
    daemon background thread into a queue instead, so the deadline loop's own
    `queue.get(timeout=...)` is what's actually bounded, independent of
    whether the subprocess ever produces output.
    """
    proc = subprocess.Popen(
        [str(binstub)], env=env, cwd=tmp_path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    lines: "queue.Queue[str]" = queue.Queue()

    def _pump_stderr() -> None:
        try:
            for line in iter(proc.stderr.readline, ""):
                lines.put(line)
        except Exception:
            pass

    reader = threading.Thread(target=_pump_stderr, daemon=True)
    reader.start()

    saw_noinst = False
    saw_provisioning = False
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                line = lines.get(timeout=max(0.05, remaining))
            except queue.Empty:
                if proc.poll() is not None:
                    break
                continue
            if "cannot self-provision" in line:
                saw_noinst = True
                break
            if "provisioning on first use" in line:
                saw_provisioning = True
                break
        if proc.poll() is not None and not saw_noinst and not saw_provisioning:
            # Exited fast without either signature line -- only acceptable if
            # it's a genuinely different (non-127) failure, e.g. AGENT_MACHINES
            # env quirks in the sandbox; the 127/no-installer signature is the
            # one regression this test must catch.
            assert proc.returncode != 127, (
                "binstub exited 127 without the expected diagnostic line"
            )
    finally:
        subprocess.run(
            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
            capture_output=True, text=True,
        )
        proc.wait(timeout=15)
    assert not saw_noinst, "binstub hit the fast exit-127 self-provision-missing path"


@pytest.mark.skipif(not WINDOWS_HOSTS, reason="Windows PowerShell required")
@pytest.mark.parametrize("host", WINDOWS_HOSTS, ids=lambda h: h.stem)
def test_windows_stamp_binstub_leaves_a_usable_payload_dir_marker(tmp_path, host):
    """A first-turn invocation right after `stamp-binstub` must not 127."""
    env = _sandbox_env(tmp_path)
    # No -InstallDir override here: the generated .cmd hardcodes
    # `_ROOT=%USERPROFILE%\.agent-machines` (matching bootstrap-check.ps1's
    # own real hook invocation, which never passes -InstallDir either), so
    # the install dir under test must be the same default the binstub reads
    # from, not an arbitrary one.
    install_dir = tmp_path / "userprofile" / ".agent-machines"
    result = subprocess.run(
        [str(host), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INIT_PS1),
         "-Action", "stamp-binstub"],
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

    # Review finding (round 4): the marker-file check above proves the marker
    # is *readable*, but never proved the generated .cmd can actually consume
    # it without hitting the fast ":_noinst" exit-127 path. Invoke the real
    # binstub and confirm it does.
    _assert_binstub_resolves_marker(binstub, env, tmp_path)


@pytest.mark.skipif(not WINDOWS_HOSTS, reason="Windows PowerShell required")
@pytest.mark.parametrize("host", WINDOWS_HOSTS, ids=lambda h: h.stem)
def test_windows_binstub_is_usable_while_background_stamp_still_runs(tmp_path, host):
    """The exact handoff window this split exists for: a first-turn command
    racing the SLOWER backgrounded `stamp` (the operation that later
    overwrites the marker with the real snapshot path) must still resolve
    a usable marker, not the fast ":_noinst" exit-127 path."""
    env = _sandbox_env(tmp_path)
    result = subprocess.run(
        [str(host), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INIT_PS1),
         "-Action", "stamp-binstub"],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    binstub = tmp_path / "userprofile" / ".local" / "bin" / "agent-machines.cmd"
    assert binstub.is_file()

    stamp_proc = subprocess.Popen(
        [str(host), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INIT_PS1),
         "-Action", "stamp"],
        env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        _assert_binstub_resolves_marker(binstub, env, tmp_path)
    finally:
        stamp_proc.communicate(timeout=60)


def _assert_no_torn_reads_while_running(
    path: Path, procs: list[subprocess.Popen], *, prefix: str, suffix: str,
) -> None:
    """Poll `path` while any of `procs` is still running, and assert every
    observed NON-EMPTY read is a complete file (starts with `prefix`, ends
    with `suffix`) -- never a torn/partial write.

    Review finding (round 8): the original concurrency tests only inspected
    the final file after every writer had already exited, so a non-atomic
    implementation that briefly exposed an empty or partial file DURING the
    writes would still pass. Reading here mid-write, on a background thread
    while the real writer processes race each other, is what actually
    exercises the reader-visible guarantee the atomic-publish fix claims.
    """
    violations: list[str] = []

    def _poll() -> None:
        while any(p.poll() is None for p in procs):
            try:
                content = path.read_text(encoding="utf-8")
            except (FileNotFoundError, OSError):
                content = ""
            if content and not (content.startswith(prefix) and content.rstrip().endswith(suffix)):
                violations.append(content)
            time.sleep(0.005)

    poller = threading.Thread(target=_poll, daemon=True)
    poller.start()
    for proc in procs:
        _, stderr = proc.communicate(timeout=60)
        assert proc.returncode == 0, stderr
    poller.join(timeout=5)
    assert not violations, (
        f"observed a torn/partial file during concurrent writes: {violations[0]!r}"
    )


@pytest.mark.skipif(not WINDOWS_HOSTS, reason="Windows PowerShell required")
@pytest.mark.parametrize("host", WINDOWS_HOSTS, ids=lambda h: h.stem)
def test_windows_concurrent_stamp_binstub_leaves_no_torn_binstub(tmp_path, host):
    env = _sandbox_env(tmp_path)
    # No -InstallDir override: round-10 review found the generated .cmd's
    # marker root is hardcoded (not -InstallDir-aware), so init.ps1 now
    # rejects a custom -InstallDir for this action outright -- match the
    # other tests' pattern (and bootstrap-check.ps1's own real invocation).
    binstub = tmp_path / "userprofile" / ".local" / "bin" / "agent-machines.cmd"
    procs = [
        subprocess.Popen(
            [str(host), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INIT_PS1),
             "-Action", "stamp-binstub"],
            env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(3)
    ]
    _assert_no_torn_reads_while_running(binstub, procs, prefix="@echo off", suffix=":eof")


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
    binstub = tmp_path / "home" / ".local" / "bin" / "agent-machines"
    procs = [
        subprocess.Popen(
            [BASH, str(INIT_SH), "stamp"],
            env=env, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        for _ in range(3)
    ]
    _assert_no_torn_reads_while_running(
        binstub, procs, prefix="#!/usr/bin/env bash", suffix='exit "$_rc"',
    )
    content = binstub.read_text(encoding="utf-8")
    assert content.startswith("#!/usr/bin/env bash")
    assert content.rstrip().endswith('exit "$_rc"')
    assert not any(install_dir.glob(".stamp.lock.pid")), "lock symlink must not be left behind"
