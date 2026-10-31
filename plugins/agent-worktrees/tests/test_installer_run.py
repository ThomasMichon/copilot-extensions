"""``installer_run.run_installer``: deadline-owned installer trees, receipt-judged outcomes.

``subprocess.run(timeout=...)`` killed only the installer's own process, so a
grandchild (a staged installer copy, ``uv``, a build) kept running and could
publish a slot after the updater had labelled the run from its timer; and an
installer that exited 0 without activating the target counted as success.
These tests run real child processes.
"""

from __future__ import annotations

import os
import errno
import io
import json
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import installer_run, reconcile, update_events

pytestmark = pytest.mark.real_installer_processes

# A stub installer: optionally spawns a grandchild that writes a slot marker
# after a delay, records it, then sleeps or exits with a code.
_STUB = textwrap.dedent("""
    import subprocess, sys, time
    marker, delay, sleep, code = sys.argv[1], *map(float, sys.argv[2:4]), int(sys.argv[4])
    if delay >= 0:
        grandchild = subprocess.Popen([sys.executable, "-c",
            "import sys, time; time.sleep(float(sys.argv[2])); "
            "open(sys.argv[1], 'w').write('slot')",
            marker, str(delay)])
        open(marker + ".pid", "w").write(str(grandchild.pid))
    print("stub installer running", flush=True)
    open(marker + ".ready", "w").write("ready")
    time.sleep(sleep)
    sys.exit(code)
""")


@pytest.fixture()
def stub(tmp_path, monkeypatch):
    # Real process-tree semantics: outside contained test mode the POSIX
    # installer gets its own session, which the deadline kill targets.
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    script = tmp_path / "installer.py"
    script.write_text(_STUB, encoding="utf-8")
    original_spawn = installer_run._spawn

    def spawn(command, *args, **kwargs):
        if str(script) not in command:
            return original_spawn(command, *args, **kwargs)
        return _spawn_ready(
            original_spawn, command, *args, ready=tmp_path / "slot.ready", **kwargs,
        )

    monkeypatch.setattr(installer_run, "_spawn", spawn)

    def argv(*, delay: float = -1, sleep: float = 0, code: int = 0) -> list[str]:
        return [sys.executable, str(script), str(tmp_path / "slot"), str(delay),
                str(sleep), str(code)]

    return argv


def _spawn_ready(spawn, command, *args, ready: Path, **kwargs):
    """Bind the real child witness before exercising the unchanged command deadline."""
    ready.unlink(missing_ok=True)
    proc, job = spawn(command, *args, **kwargs)
    if not _wait_for(ready.is_file, seconds=10):
        installer_run._kill_tree(proc, job)
        pytest.fail("synthetic process did not publish its bounded readiness witness")
    return proc, job


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                             capture_output=True, text=True, check=False).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # An exited process its parent has not reaped yet still answers kill(0).
    return not _state(pid).startswith("Z")


def _state(pid: int) -> str:
    return subprocess.run(["ps", "-o", "state=", "-p", str(pid)], capture_output=True,
                          text=True, check=False).stdout.strip() or "Z"


def _wait_dead(pid: int, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.2)
    return not _alive(pid)


def test_deadline_kills_grandchild(tmp_path, stub):
    run = installer_run.run_installer(
        stub(delay=6, sleep=60), cwd=tmp_path, env=os.environ, deadline_s=2,
    )
    assert run.status == "timed out (deadline hit during build)"
    assert run.rc is None
    assert run.duration_s < 30
    grandchild = int((tmp_path / "slot.pid").read_text())
    assert _wait_dead(grandchild), "the installer's grandchild must die with the tree"
    time.sleep(6)
    assert not (tmp_path / "slot").exists(), "no slot may appear after the deadline"


def test_exit_code_alone_decides_without_a_target(tmp_path, stub):
    assert installer_run.run_installer(
        stub(code=0), cwd=tmp_path, env=os.environ, deadline_s=60).status == "OK"
    assert installer_run.run_installer(
        stub(code=3), cwd=tmp_path, env=os.environ, deadline_s=60).status == "installer exited 3"


def test_rc0_without_receipt_fails(tmp_path, stub):
    run = installer_run.run_installer(
        stub(code=0), cwd=tmp_path, env=os.environ, deadline_s=60,
        target_version="1.2.0", receipt=lambda: "1.1.0",
    )
    assert run.status.startswith("runtime not at target")
    assert "deployed 1.1.0, expected 1.2.0" in run.status
    assert run.receipt_version == "1.1.0" and not run.receipt_complete
    assert update_events.classify_status(run.status)[0] != "verified_current"

    missing = installer_run.run_installer(
        stub(code=0), cwd=tmp_path, env=os.environ, deadline_s=60,
        target_version="1.2.0", receipt=lambda: None,
    )
    assert "deployed nothing" in missing.status


def test_rc0_with_the_target_receipt_is_ok(tmp_path, stub):
    run = installer_run.run_installer(
        stub(code=0), cwd=tmp_path, env=os.environ, deadline_s=60,
        target_version="1.2.0", receipt=lambda: "1.2.0",
    )
    assert run.status == "OK" and run.receipt_complete


def test_watchdog_exit_kills_what_the_installer_left(tmp_path, stub):
    # The installers' own watchdog exits 124 after signalling only its staged
    # group; whatever survived must not publish after the reported timeout.
    run = installer_run.run_installer(
        stub(delay=4, code=124), cwd=tmp_path, env=os.environ, deadline_s=60,
    )
    assert run.status == "timed out (deadline hit during build)"
    grandchild = int((tmp_path / "slot.pid").read_text())
    assert _wait_dead(grandchild), "the installer's leftover must die with it"
    time.sleep(5)
    assert not (tmp_path / "slot").exists(), "no slot may appear after the timeout"


@pytest.mark.parametrize("published,expected", [
    (True, "timed out (slot published; deadline hit during cutover)"),
    (False, "timed out (deadline hit during build)"),
])
def test_receipt_overrides_watchdog_rc(tmp_path, stub, published, expected):
    run = installer_run.run_installer(
        stub(code=124), cwd=tmp_path, env=os.environ, deadline_s=60,
        target_version="2.0.0", receipt=lambda: "2.0.0" if published else "1.0.0",
    )
    assert run.status == expected
    assert update_events.classify_status(run.status)[0] == "timeout"


@pytest.mark.parametrize("code,outcome", [(75, "blocked_by_lock"), (76, "command_failed")])
def test_cutover_exits_keep_their_outcomes(tmp_path, stub, code, outcome):
    run = installer_run.run_installer(
        stub(code=code), cwd=tmp_path, env=os.environ, deadline_s=60,
        target_version="2.0.0", receipt=lambda: "2.0.0",
    )
    assert run.status == f"installer exited {code}"
    assert update_events.classify_status(run.status) == (outcome, f"installer_exit_{code}")


def test_spawn_failure_becomes_the_status(tmp_path):
    run = installer_run.run_installer(
        [str(tmp_path / "missing-installer")], cwd=tmp_path, env=os.environ, deadline_s=5,
    )
    assert run.rc is None and run.status and not run.status.startswith("OK")


def test_captured_output_reaches_the_log_after_a_deadline_kill(tmp_path, stub):
    lines: list[str] = []
    run = installer_run.run_installer(
        stub(sleep=60), cwd=tmp_path, env=os.environ, deadline_s=2, log=lines.append,
    )
    assert run.status.startswith("timed out")
    assert "stub installer running" in lines


def test_legacy_log_failure_occurs_after_native_settlement(tmp_path, stub, monkeypatch):
    original_spawn = installer_run._spawn
    spawned = []

    def spawn(*args, **kwargs):
        result = original_spawn(*args, **kwargs)
        spawned.append(result)
        return result

    failure = OSError("legacy log sink failed")

    def log(_line):
        proc, job = spawned[0]
        assert proc.returncode is not None
        assert job is None or job.handle is None
        assert _wait_dead(int((tmp_path / "slot.pid").read_text()))
        raise failure

    monkeypatch.setattr(installer_run, "_spawn", spawn)
    with pytest.raises(OSError) as caught:
        installer_run.run_installer(
            stub(delay=60), cwd=tmp_path, env=os.environ, deadline_s=10, log=log,
            require_tree_exit=os.name == "nt",
        )
    assert caught.value is failure


@pytest.mark.parametrize("deadline", [False, True])
def test_live_output_fd_retains_deadline_and_receipt_judgment(tmp_path, stub, deadline):
    with (tmp_path / "output").open("wb") as stream:
        run = installer_run.run_installer(
            stub(sleep=60 if deadline else 0),
            cwd=tmp_path, env=os.environ, deadline_s=2 if deadline else 10,
            target_version="2.0.0", receipt=lambda: "1.0.0",
            output_fd=stream.fileno(),
        )
    assert b"stub installer running" in (tmp_path / "output").read_bytes()
    assert run.status.startswith("timed out" if deadline else "runtime not at target")
    assert run.rc is None if deadline else run.rc == 0
    assert not run.receipt_complete and run.receipt_version == "1.0.0"


def test_live_output_fd_cannot_silently_discard_log(tmp_path):
    with pytest.raises(ValueError, match="mutually exclusive"):
        installer_run.run_installer(
            [], cwd=tmp_path, env={}, deadline_s=1, log=lambda line: None, output_fd=1,
        )


@pytest.mark.parametrize("sink", ["undrained", "closed"])
def test_output_pipe_failure_cannot_starve_installer_supervision(tmp_path, monkeypatch, sink):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    script, ready = tmp_path / "flood.py", tmp_path / "child.pid"
    script.write_text(textwrap.dedent("""
        import os, pathlib, subprocess, sys
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        pathlib.Path(sys.argv[1]).write_text(str(child.pid))
        while True:
            os.write(1, b"unbuffered-output " * 4096)
            os.write(2, b"unbuffered-diagnostic " * 4096)
    """), encoding="utf-8")
    original_spawn = installer_run._spawn
    original_capture = installer_run.SyncProcessOutput
    spawned, captures = [], []

    def spawn(command, *args, **kwargs):
        result = _spawn_ready(original_spawn, command, *args, ready=ready, **kwargs)
        spawned.append(result)
        return result

    monkeypatch.setattr(installer_run, "_spawn", spawn)
    def capture(*args, **kwargs):
        result = original_capture(*args, **kwargs)
        captures.append(result)
        return result

    monkeypatch.setattr(installer_run, "SyncProcessOutput", capture)
    reader, writer = os.pipe()
    if sink == "closed":
        os.close(reader)
        reader = -1
    try:
        started = time.monotonic()
        arguments = dict(
            cwd=tmp_path, env=os.environ, deadline_s=1,
            output_fd=writer, require_tree_exit=os.name == "nt",
        )
        if sink == "closed":
            with pytest.raises((OSError, installer_run.InstallerCleanupError)):
                installer_run.run_installer([sys.executable, str(script), str(ready)], **arguments)
        else:
            run = installer_run.run_installer([sys.executable, str(script), str(ready)], **arguments)
            assert run.status.startswith("timed out")
        assert time.monotonic() - started < 15
        assert _wait_dead(int(ready.read_text()))
        assert spawned[0][0].returncode is not None
        assert all(not thread.is_alive() for thread in captures[0]._readers)
        assert all(
            relay.process.returncode is not None and not relay._writer.is_alive()
            and not relay._reader.is_alive()
            and relay.process.stdin.closed and relay.process.stderr.closed
            for relay in captures[0]._relays.values()
        )
    finally:
        os.close(writer)
        if reader >= 0:
            os.close(reader)
        for proc, job in spawned:
            if job is not None and job.handle:
                job.terminate_and_wait(10)
            elif proc.poll() is None:
                installer_run._kill_tree(proc, job)
            proc.wait(timeout=10)


def test_slow_productive_sink_renews_delivery_after_installer_exit(tmp_path, monkeypatch):
    monkeypatch.setattr(installer_run, "_REAP_GRACE_S", 1)
    reader, writer = os.pipe()
    received = bytearray()

    def drain():
        while chunk := os.read(reader, 4096):
            received.extend(chunk)
            time.sleep(.1)

    consumer = threading.Thread(target=drain)
    consumer.start()
    try:
        started = time.monotonic()
        run = installer_run.run_installer(
            [sys.executable, "-c", "import os;os.write(1,b'x'*163840)"],
            cwd=tmp_path, env=os.environ, deadline_s=1, output_fd=writer,
            require_tree_exit=os.name == "nt",
        )
        assert run.status == "OK"
        assert time.monotonic() - started > 3
        assert os.get_blocking(writer), "a borrowed descriptor's flags must remain unchanged"
    finally:
        os.close(writer)
        consumer.join(timeout=10)
        os.close(reader)
    assert not consumer.is_alive()
    assert received == b"x" * 163840


def test_output_relays_do_not_inherit_unrelated_pipe_handles(tmp_path):
    from agent_procutil._output_relay import ByteRelay

    reader, writer = os.pipe()
    os.set_inheritable(writer, True)
    eof = threading.Event()

    def observe_eof():
        if os.read(reader, 1) == b"":
            eof.set()

    observer = threading.Thread(target=observe_eof)
    relays = []
    try:
        with (tmp_path / "stdout").open("wb") as stdout, (tmp_path / "stderr").open("wb") as stderr:
            try:
                for stream in (stdout, stderr):
                    relays.append(ByteRelay(stream.fileno()))
                os.close(writer)
                writer = -1
                observer.start()
                assert eof.wait(5), "a live relay must not retain a foreign pipe's writer"
                assert all(relay.process.poll() is None for relay in relays)
            finally:
                for relay in relays:
                    relay.abort(10)
    finally:
        if writer >= 0:
            os.close(writer)
        if observer.ident is not None:
            observer.join(timeout=10)
        os.close(reader)
    assert not observer.is_alive()


def test_unicode_relay_error_receipt_preserves_native_error():
    from agent_procutil._output_relay import ByteRelay

    error = OSError(errno.EIO, "diagnostic " + "\U0001f600" * 512)
    message = str(error)[:512]
    receipt = json.dumps({"errno": error.errno, "message": message}).encode("ascii") + b"\n"
    receiver = SimpleNamespace(
        process=SimpleNamespace(stderr=io.BytesIO(receipt)), _native_error=None,
        _errors=[], _reported=threading.Event(), last_progress=0,
    )
    ByteRelay._report(receiver)
    assert not receiver._errors
    assert receiver._native_error.errno == error.errno
    assert receiver._native_error.strerror == message


def test_output_relay_exits_when_its_caller_dies_with_an_undrained_sink(tmp_path, monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    ready = tmp_path / "relay.pid"
    program = textwrap.dedent("""
        import os, pathlib, sys, time
        from agent_procutil._output_relay import ByteRelay
        reader, writer = os.pipe()
        relay = ByteRelay(writer)
        for index in range(64):
            relay.offer(b"x" * 4096)
        pathlib.Path(sys.argv[1]).write_text(str(relay.process.pid))
        time.sleep(120)
    """)
    proc, job = _spawn_ready(
        installer_run._spawn,
        [sys.executable, "-c", program, str(ready)],
        tmp_path, os.environ, True, ready=ready,
        require_tree_exit=os.name == "nt",
    )
    try:
        relay_pid = int(ready.read_text())
        assert _alive(relay_pid)
        proc.kill()
        proc.wait(timeout=10)
        assert _wait_dead(relay_pid), "parent loss must interrupt even a blocked native output write"
    finally:
        installer_run._kill_tree(proc, job)


def test_inherited_output_keeps_native_bytes_and_channels(tmp_path, monkeypatch):
    with (tmp_path / "stdout").open("w", encoding="utf-8") as stdout, (
        tmp_path / "stderr"
    ).open("w", encoding="utf-8") as stderr, monkeypatch.context() as patched:
        patched.setattr(installer_run.sys, "stdout", stdout)
        patched.setattr(installer_run.sys, "stderr", stderr)
        run = installer_run.run_installer(
            [sys.executable, "-c", "import os;os.write(1,b'\\xffout');os.write(2,b'\\xfeerr')"],
            cwd=tmp_path, env=os.environ, deadline_s=10,
        )
    assert run.status == "OK"
    assert (tmp_path / "stdout").read_bytes() == b"\xffout"
    assert (tmp_path / "stderr").read_bytes() == b"\xfeerr"


@pytest.mark.parametrize("output", ["log", "fd"])
def test_productive_installer_renews_inactivity_timeout(tmp_path, monkeypatch, output):
    script = tmp_path / "progress.py"
    ready = tmp_path / "progress.ready"
    script.write_text(textwrap.dedent("""
        import pathlib, sys, time
        pathlib.Path(sys.argv[1]).write_text("ready")
        for index in range(20):
            stream = sys.stdout if index % 2 == 0 else sys.stderr
            stream.write(f"progress{index:02d} ")
            stream.flush()
            time.sleep(.25)
    """), encoding="utf-8")
    original_spawn = installer_run._spawn
    monkeypatch.setattr(
        installer_run, "_spawn",
        lambda command, *args, **kwargs: _spawn_ready(
            original_spawn, command, *args, ready=ready, **kwargs,
        ),
    )
    lines = []
    with (tmp_path / "output").open("wb") as stream:
        run = installer_run.run_installer(
            [sys.executable, str(script), str(ready)],
            cwd=tmp_path, env=os.environ, deadline_s=2,
            target_version="2.0.0", receipt=lambda: "2.0.0",
            **({"log": lines.append} if output == "log" else {"output_fd": stream.fileno()}),
        )
    assert run.status == "OK" and run.receipt_complete
    assert run.duration_s > 4
    text = " ".join(lines) if output == "log" else (tmp_path / "output").read_text()
    assert all(f"progress{index:02d}" in text for index in range(20))


@pytest.mark.parametrize("stall", [False, True])
def test_startup_grace_ends_when_real_output_begins(tmp_path, stall):
    ready = tmp_path / "first-output"
    program = textwrap.dedent("""
        import pathlib, sys, time
        time.sleep(1.5)
        pathlib.Path(sys.argv[1]).write_text(str(time.monotonic()))
        print("started", flush=True)
        if sys.argv[2] == "stall":
            time.sleep(60)
        else:
            for index in range(6):
                time.sleep(.3)
                print("progress", flush=True)
    """)
    proc, job = installer_run._spawn(
        [sys.executable, "-c", program, str(ready), "stall" if stall else "progress"],
        tmp_path, os.environ, True, require_tree_exit=os.name == "nt",
    )
    capture = installer_run.SyncProcessOutput(proc)
    try:
        if stall:
            with pytest.raises(subprocess.TimeoutExpired):
                capture.wait(1, startup_timeout=10)
            assert time.monotonic() - float(ready.read_text()) < 5
        else:
            capture.wait(1, startup_timeout=10)
            assert capture.output()[0].count("progress") == 6
    finally:
        installer_run._kill_tree(proc, job)
        capture.finish(10)
    assert all(not reader.is_alive() for reader in capture._readers)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_posix_group_killed(tmp_path, stub):
    run = installer_run.run_installer(
        stub(delay=30, sleep=60), cwd=tmp_path, env=os.environ, deadline_s=2,
    )
    assert run.status.startswith("timed out")
    assert _wait_dead(int((tmp_path / "slot.pid").read_text()))


# A stub installer whose staged child leaves its group the way the installers'
# self-stage watchdog does (``set -m``), and whose daemon leaves the session.
_REGROUPING_STUB = textwrap.dedent("""
    import os, subprocess, sys, time
    out = sys.argv[1]
    sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
    staged = subprocess.Popen(sleeper, preexec_fn=lambda: os.setpgid(0, 0))
    daemon = subprocess.Popen(sleeper, start_new_session=True)
    open(out, "w").write(f"{staged.pid} {daemon.pid}")
    time.sleep(120)
""")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_posix_deadline_reaches_a_regrouped_child_but_not_a_daemon(tmp_path, stub, monkeypatch):
    script = tmp_path / "regroup.py"
    script.write_text(_REGROUPING_STUB, encoding="utf-8")
    pids = tmp_path / "pids"
    original_spawn = installer_run._spawn
    monkeypatch.setattr(
        installer_run, "_spawn",
        lambda command, *args, **kwargs: _spawn_ready(
            original_spawn, command, *args, ready=pids, **kwargs,
        ),
    )
    run = installer_run.run_installer(
        [sys.executable, str(script), str(pids)], cwd=tmp_path, env=os.environ,
        deadline_s=3,
    )
    staged, daemon = map(int, pids.read_text().split())
    try:
        assert run.status.startswith("timed out")
        assert _wait_dead(staged), "a child in its own group must die with the session"
        assert _alive(daemon), "a daemon that left the session must survive"
    finally:
        for pid in (staged, daemon):
            try:
                os.kill(pid, 9)
            except OSError:
                pass


def _stopped(pid: int) -> bool:
    return _state(pid).startswith("T")


@pytest.mark.skipif(os.name == "nt", reason="POSIX zombies")
def test_an_unreaped_child_counts_as_dead():
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    try:
        assert _wait_for(lambda: _state(child.pid).startswith("Z")), "the child exits unreaped"
        assert not _alive(child.pid)
    finally:
        child.wait()


@pytest.mark.skipif(os.name == "nt", reason="POSIX sessions")
@pytest.mark.parametrize("stale", ["detached", "replaced"])
def test_session_sweep_revalidates_each_census_entry(monkeypatch, stale):
    """Between the census and the signal, an entry can leave the session
    (``setsid``) or its PID can be reused; either way it must be left running."""
    sleeper = [sys.executable, "-c", "import time; time.sleep(120)"]
    leader = subprocess.Popen(sleeper, start_new_session=True)
    other = subprocess.Popen(sleeper, start_new_session=True)
    try:
        assert _wait_for(lambda: installer_run._start_time(other.pid) is not None)
        # "detached": the census saw this very process inside the session.
        # "replaced": the census saw an earlier process that had this PID.
        started = installer_run._start_time(other.pid) if stale == "detached" else "earlier"
        censuses = iter([{other.pid: started}])
        monkeypatch.setattr(installer_run, "_session_census", lambda _sid: next(censuses, {}))
        installer_run._kill_session(leader)
        # Owned child: reap it, or os.kill(pid, 0) still sees the zombie.
        assert leader.wait(timeout=10) is not None, "the installer itself still dies"
        assert other.poll() is None and _alive(other.pid), "a stale entry is never killed"
        assert not _stopped(other.pid), "a stale entry is never left stopped"
    finally:
        for proc in (leader, other):
            proc.kill()
            proc.wait()


def _wait_for(predicate, seconds: float = 10) -> bool:
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.1)
    return predicate()

@pytest.mark.skipif(os.name != "nt", reason="Windows Job containment")
def test_windows_spawn_requires_the_job(monkeypatch, tmp_path):
    seen = {}

    def fake_spawn(argv, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("Windows descendant Job assignment failed before child resume")

    monkeypatch.setattr(installer_run, "spawn_sync_in_kill_on_close_job", fake_spawn)
    run = installer_run.run_installer(["installer"], cwd=tmp_path, env={}, deadline_s=5)
    assert seen["require_job"] is True
    assert "allow_breakaway" not in seen, "installer daemons may still break away"
    assert run.rc is None and "Job assignment failed" in run.status


@pytest.mark.skipif(os.name != "nt", reason="confirmed Windows Job exit")
@pytest.mark.parametrize("exit_mode", ["success", "deadline", "watchdog"])
def test_strict_job_exit_kills_and_reaps_every_descendant(tmp_path, stub, exit_mode):
    run = installer_run.run_installer(
        stub(delay=4, sleep=60 if exit_mode == "deadline" else 0,
             code=124 if exit_mode == "watchdog" else 0),
        cwd=tmp_path, env=os.environ, deadline_s=2, require_tree_exit=True,
    )
    assert run.status == "OK" if exit_mode == "success" else run.status.startswith("timed out")
    assert _wait_dead(int((tmp_path / "slot.pid").read_text()))
    time.sleep(4)
    assert not (tmp_path / "slot").exists(), "confirmed cleanup precedes all late writes"


@pytest.mark.skipif(os.name != "nt", reason="confirmed Windows Job exit")
@pytest.mark.parametrize("output", ["log", "fd"])
@pytest.mark.parametrize("cleanup_failure", [False, True])
def test_strict_capture_error_settles_job_and_readers(
    tmp_path, monkeypatch, output, cleanup_failure,
):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    script, ready = tmp_path / "flood.py", tmp_path / "child.pid"
    script.write_text(textwrap.dedent("""
        import os, pathlib, subprocess, sys, time
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        pathlib.Path(sys.argv[1]).write_text(str(child.pid))
        while True:
            os.write(1, b"output\\n" * 4096)
            os.write(2, b"diagnostic\\n" * 4096)
    """), encoding="utf-8")
    original_spawn, original_capture = installer_run._spawn, installer_run.SyncProcessOutput
    spawned, captures, settled, finished, terminators = [], [], [], [], []

    def spawn(command, *args, **kwargs):
        proc, job = _spawn_ready(original_spawn, command, *args, ready=ready, **kwargs)
        spawned.append((proc, job))
        terminate = job.terminate_and_wait
        terminators.append(terminate)

        def terminate_and_wait(timeout):
            if cleanup_failure:
                raise OSError("fixture Job accounting refused")
            terminate(timeout)
            settled.append(True)

        monkeypatch.setattr(job, "terminate_and_wait", terminate_and_wait)
        return proc, job

    def capture(*args, **kwargs):
        result = original_capture(*args, **kwargs)
        captures.append(result)
        finish = result.finish

        def finish_capture(*args, **kwargs):
            assert settled, "confirm the Job is empty before draining inherited pipes"
            result = finish(*args, **kwargs)
            finished.append(True)
            return result

        monkeypatch.setattr(result, "finish", finish_capture)
        wait = result.wait
        failed = False

        def fail_wait(*args, **kwargs):
            nonlocal failed
            if not failed:
                failed = True
                assert _wait_for(lambda: result._events.full())
                raise sink_error
            return wait(*args, **kwargs)

        monkeypatch.setattr(result, "wait", fail_wait)
        return result

    sink_error = OSError("output sink refused bytes")

    monkeypatch.setattr(installer_run, "_spawn", spawn)
    monkeypatch.setattr(installer_run, "SyncProcessOutput", capture)
    try:
        with (tmp_path / "output").open("wb") as stream:
            with pytest.raises(installer_run.InstallerCleanupError) as caught:
                installer_run.run_installer(
                    [sys.executable, str(script), str(ready)],
                    cwd=tmp_path, env=os.environ, deadline_s=10, require_tree_exit=True,
                    **({"log": lambda _line: None} if output == "log"
                       else {"output_fd": stream.fileno()}),
                )
        assert caught.value.__cause__ is sink_error
        if cleanup_failure:
            assert not settled and not finished
            assert caught.value.process is spawned[0][0]
            assert caught.value.job is spawned[0][1] and caught.value.job.handle
            assert "fixture Job accounting refused" in str(caught.value)
            assert any("output sink refused bytes" in note for note in caught.value.__notes__)
        else:
            assert settled and finished, "sink failure must not skip verified teardown"
            assert spawned[0][0].returncode is not None
            assert _wait_dead(int(ready.read_text()))
            assert all(not thread.is_alive() for thread in captures[0]._readers)
            assert all(pipe.closed for pipe in captures[0]._streams.values())
    finally:
        for (proc, job), terminate in zip(spawned, terminators):
            if job.handle:
                terminate(10)
            proc.wait(timeout=10)
        for result in captures:
            original_capture.finish(result, 10)


@pytest.mark.skipif(os.name != "nt", reason="verified Windows payload timeout recovery")
@pytest.mark.parametrize("refusal", ["", "HTTP 403 authentication required"])
def test_real_payload_replay_follows_empty_job_confirmation(tmp_path, monkeypatch, refusal):
    from agent_worktrees import update_acquisition

    script = tmp_path / "payload.py"
    script.write_text(textwrap.dedent("""
        import pathlib, subprocess, sys, time
        root, attempt = pathlib.Path(sys.argv[1]), int(sys.argv[2])
        if attempt == 1:
            child = subprocess.Popen(
                [sys.executable, "-c",
                 "import pathlib,sys,time; time.sleep(4); pathlib.Path(sys.argv[1]).touch()",
                 str(root / "late-write")],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            (root / "child.pid").write_text(str(child.pid))
            print(sys.argv[3], flush=True)
            (root / f"ready-{attempt}").touch()
            time.sleep(60)
        print("acquired", flush=True)
        (root / f"ready-{attempt}").touch()
    """), encoding="utf-8")
    original_spawn, calls = installer_run._spawn, []

    def spawn(argv, cwd, env, capture, **kwargs):
        calls.append(argv)
        if len(calls) == 2:
            assert _wait_dead(int((tmp_path / "child.pid").read_text()))
            assert not (tmp_path / "late-write").exists()
        return _spawn_ready(
            original_spawn,
            [sys.executable, str(script), str(tmp_path), str(len(calls)), refusal],
            cwd, env, capture, ready=tmp_path / f"ready-{len(calls)}", **kwargs,
        )

    monkeypatch.setattr(installer_run, "_spawn", spawn)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda name: tmp_path)
    monkeypatch.setattr(update_acquisition, "sleep", lambda seconds: None)
    monkeypatch.setattr(update_acquisition, "_ATTEMPT_TIMEOUT_S", 2)
    status = update_acquisition.acquire("agent-example", "m", "fixture", engine=True)
    assert status.startswith("OK") is (not refusal)
    assert len(calls) == (1 if refusal else 2)
    assert _wait_dead(int((tmp_path / "child.pid").read_text()))
    time.sleep(4)
    assert not (tmp_path / "late-write").exists()


def test_a_containment_failure_becomes_the_status(monkeypatch, tmp_path):
    def refuse(*_a, **_k):
        raise RuntimeError("failed to resume suspended process 1")

    monkeypatch.setattr(installer_run, "_spawn", refuse)
    run = installer_run.run_installer(["installer"], cwd=tmp_path, env={}, deadline_s=5)
    assert run.rc is None and run.status == "failed to resume suspended process 1"


def test_plan_step_is_bounded_and_reports_the_deadline(tmp_path, stub, monkeypatch):
    from agent_worktrees import update_runtime

    monkeypatch.setattr(update_runtime, "_installer_timeout", lambda *_a, **_k: 2)
    logged: list[str] = []
    started = time.monotonic()
    rc = reconcile._run_plan_step(stub(sleep=60), {"service": "agent-x"}, logged.append)
    assert rc == 124
    assert time.monotonic() - started < 40
    assert any("timed out" in line for line in logged)
    assert "    stub installer running" in logged


def test_plan_step_applies_the_step_environment(tmp_path, monkeypatch):
    from agent_worktrees import update_runtime

    monkeypatch.setattr(update_runtime, "_installer_timeout", lambda *_a, **_k: 60)
    monkeypatch.setenv("AW_PLAN_STEP_DROP", "present")
    logged: list[str] = []
    argv = [sys.executable, "-c",
            "import os; print(os.environ.get('AW_PLAN_STEP_SET'), "
            "os.environ.get('AW_PLAN_STEP_DROP'))"]
    rc = reconcile._run_plan_step(
        argv,
        {"service": "agent-x", "environment": {"AW_PLAN_STEP_SET": "value"},
         "unset_environment": ["AW_PLAN_STEP_DROP"]},
        logged.append,
    )
    assert rc == 0
    assert "    value None" in logged


def test_plan_step_uses_the_service_manifest_deadline(tmp_path, monkeypatch):
    for var in ("AGENT_X_INSTALL_DEADLINE_SEC", "COPILOT_PLUGIN_INSTALL_DEADLINE_SEC"):
        monkeypatch.delenv(var, raising=False)
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text('{"installerDeadlineSeconds": 7}', encoding="utf-8")
    monkeypatch.setattr(reconcile, "core_installed_payload_dir",
                        lambda name: payload if name == "agent-x" else None)
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs)
        return installer_run.InstallerRun("OK", 0, None, False, 0.0)

    monkeypatch.setattr(installer_run, "run_installer", fake_run)
    assert reconcile._run_plan_step(["installer"], {"service": "agent-x"}, lambda _l: None) == 0
    assert seen["deadline_s"] == 7 + 30

    # An unreadable manifest fails the step instead of escaping the sweep.
    (payload / "plugin.json").unlink()
    (payload / "plugin.json").mkdir()
    logged: list[str] = []
    assert reconcile._run_plan_step(["installer"], {"service": "agent-x"}, logged.append) == 1
    assert logged and "plugin.json" in logged[0]
