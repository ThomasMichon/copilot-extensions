"""The bounded, structured dial log (``ssh_manager.dial_log``) and the dials that feed it."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from ssh_manager import dial_log
from ssh_manager.codespace_source import CodespaceConfigSource
from ssh_manager.config_sources import SSHProfileSource
from ssh_manager.platform import MultiplexMode, PlatformInfo


@pytest.fixture
def win_platform(tmp_path):
    return PlatformInfo(mode=MultiplexMode.DIRECT, socket_dir=tmp_path / "sockets", max_socket_path=260)


@pytest.fixture
def source():
    return SSHProfileSource(host_alias="test-host")


def _kinds(target):
    return [(e["kind"], e["outcome"]) for e in dial_log.read(target)]


def test_a_dial_is_one_structured_redacted_line():
    dial_log.record("cs-1", kind="config_fetch", outcome="error", elapsed_s=1.23456, attempt=2,
                    reason="rc=1", account=dial_log.account_of({"GH_TOKEN": "x"}),
                    stderr="auth failed: token=ghp_abcdefghijklmnopqrstuvwx Authorization: Bearer abc.def")
    (entry,) = dial_log.read("cs-1")
    assert (entry["kind"], entry["outcome"], entry["attempt"], entry["account"]) == (
        "config_fetch", "error", 2, "pinned")
    assert entry["elapsed_s"] == 1.235
    assert "ghp_" not in entry["stderr"] and "abc.def" not in entry["stderr"]
    assert entry["stderr"].count("[REDACTED]") >= 2
    assert dial_log.account_of(None) == "ambient"


def test_the_log_stays_bounded_and_keeps_the_newest(monkeypatch):
    monkeypatch.setattr(dial_log, "MAX_LINES", 50)
    monkeypatch.setattr(dial_log, "KEEP_LINES", 30)
    for i in range(120):
        dial_log.record("cs-2", kind="direct_exec", outcome="ok", elapsed_s=0, attempt=i)
    entries = dial_log.read("cs-2", last=1000)
    assert len(entries) <= 50
    assert entries[-1]["attempt"] == 119
    assert [e["attempt"] for e in entries] == sorted(e["attempt"] for e in entries)


def test_concurrent_writers_never_interleave_a_line():
    def write(n):
        for i in range(40):
            dial_log.record("cs-3", kind="reconnect", outcome="ok", elapsed_s=0, attempt=n * 100 + i)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    raw = dial_log._file_for("cs-3").read_text(encoding="utf-8").splitlines()
    assert len(raw) == 240
    assert all(json.loads(line)["kind"] == "reconnect" for line in raw)


def test_recording_never_raises(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv(dial_log.DIAL_LOG_ENV, str(blocker))
    dial_log.record("cs-4", kind="config_fetch", outcome="ok", elapsed_s=0)  # no exception
    assert dial_log.read("cs-4") == []


def test_summary_counts_dials_by_window():
    now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    path = dial_log._file_for("cs-5")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [(5, "ok"), (30, "timeout"), (2000, "transient"), (9000, "ok")]
    with path.open("w", encoding="utf-8") as fh:
        for minutes_ago_s, outcome in rows:
            at = (now - timedelta(seconds=minutes_ago_s)).isoformat()
            fh.write(json.dumps({"at": at, "kind": "config_fetch", "outcome": outcome}) + "\n")
    s = dial_log.summary("cs-5", now=now)
    assert s["last_10m"] == {"dials": 2, "by_outcome": {"ok": 1, "timeout": 1}}
    assert s["last_1h"]["dials"] == 3
    assert s["last_failure"]["outcome"] == "transient"


def test_each_config_fetch_attempt_is_logged(monkeypatch, tmp_path):
    """A cold start: the first attempt times out, the second answers."""
    answers = iter([subprocess.TimeoutExpired(["gh"], 30),
                    subprocess.CompletedProcess(["gh"], 0, "Host cs\n", "")])

    def run(*_a, **_k):
        item = next(answers)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(subprocess, "run", run)
    src = CodespaceConfigSource("cs-cold", config_dir=tmp_path, gh_env={"GH_TOKEN": "t"})
    assert src._fetch_gh_config() == "Host cs\n"
    assert _kinds("cs-cold") == [("config_fetch", "timeout"), ("config_fetch", "ok")]
    assert {e["account"] for e in dial_log.read("cs-cold")} == {"pinned"}


def test_a_failed_config_fetch_is_logged_before_it_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess(
        ["gh"], 1, "", "HTTP 404: not found"))
    src = CodespaceConfigSource("cs-gone", config_dir=tmp_path)
    with pytest.raises(RuntimeError):
        src._fetch_gh_config()
    (entry,) = dial_log.read("cs-gone")
    assert (entry["outcome"], entry["reason"], entry["account"]) == ("error", "rc=1", "ambient")
    assert "404" in entry["stderr"]


@pytest.mark.asyncio
async def test_a_direct_mode_exec_is_a_logged_dial(win_platform, source):
    from ssh_manager import ConnectionManager

    manager = ConnectionManager(platform=win_platform)
    await manager.ensure_connected("direct-host", source)
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(b"", b"kex_exchange_identification: connection reset"))
    proc.returncode = 255
    with patch("ssh_manager.proxy.spawn_in_kill_on_close_job", return_value=(proc, None)):
        await manager.exec_command("direct-host", "true")
    proc.communicate = AsyncMock(return_value=(b"ok", b""))
    proc.returncode = 0
    with patch("ssh_manager.proxy.spawn_in_kill_on_close_job", return_value=(proc, None)):
        await manager.exec_command("direct-host", "true")
    assert _kinds("direct-host") == [("direct_exec", "transient"), ("direct_exec", "ok")]
    # ssh can't tell a remote command's own 255 from a dropped link: the reason says so.
    assert "transport failure" in dial_log.read("direct-host")[0]["reason"]


@pytest.mark.asyncio
async def test_each_reconnect_attempt_is_logged():
    """A dead master: the first reconnect leaves it unhealthy, the second heals it."""
    from unittest.mock import MagicMock

    from ssh_manager import health
    from ssh_manager.health import HealthStatus

    statuses = iter([HealthStatus(ok=False, reason="process_dead"),
                     HealthStatus(ok=False, reason="stale_socket", stderr="socket gone"),
                     HealthStatus(ok=True, reason="ok")])
    manager = AsyncMock()
    src = MagicMock()
    src.gh_env = {"GH_TOKEN": "t"}
    with patch.object(health, "check_health", new=AsyncMock(side_effect=lambda *_a: next(statuses))), \
            patch.object(health.asyncio, "sleep", new=AsyncMock()):
        final = await health.ensure_healthy(manager, "cs-heal", src)
    assert final.ok
    entries = dial_log.read("cs-heal")
    assert [(e["kind"], e["outcome"], e["attempt"]) for e in entries] == [
        ("reconnect", "unhealthy", 1), ("reconnect", "ok", 2)]
    assert entries[0]["reason"] == "process_dead" and entries[0]["stderr"] == "socket gone"
    assert {e["account"] for e in entries} == {"pinned"}


def test_trimming_always_progresses_even_for_a_few_huge_lines(monkeypatch):
    """Lines larger than the byte budget: the trim drops them rather than looping."""
    monkeypatch.setattr(dial_log, "MAX_BYTES", 600)
    for i in range(3):
        dial_log.record("cs-big", kind="config_fetch", outcome="error", elapsed_s=0, attempt=i,
                        reason="r" * 500, stderr="e" * 500)
    assert dial_log._file_for("cs-big").stat().st_size <= 600


def test_targets_that_sanitize_alike_keep_separate_logs():
    dial_log.record("container:foo", kind="direct_exec", outcome="ok", elapsed_s=0)
    dial_log.record("container_foo", kind="direct_exec", outcome="timeout", elapsed_s=0)
    assert [e["outcome"] for e in dial_log.read("container:foo")] == ["ok"]
    assert [e["outcome"] for e in dial_log.read("container_foo")] == ["timeout"]


def test_reading_zero_entries_reads_none():
    dial_log.record("cs-z", kind="direct_exec", outcome="ok", elapsed_s=0)
    assert dial_log.read("cs-z", last=0) == [] and dial_log.read("cs-z", last=-3) == []
    assert len(dial_log.read("cs-z", last=1)) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_the_log_is_user_only():
    dial_log.record("cs-perm", kind="direct_exec", outcome="ok", elapsed_s=0)
    path = dial_log._file_for("cs-perm")
    assert (path.stat().st_mode & 0o777, path.parent.stat().st_mode & 0o777) == (0o600, 0o700)


@pytest.mark.asyncio
async def test_a_reconnect_whose_config_refresh_fails_is_still_logged():
    from unittest.mock import MagicMock

    from ssh_manager import health
    from ssh_manager.health import HealthStatus

    src = MagicMock()
    src.gh_env = None
    src.refresh.side_effect = RuntimeError("gh codespace ssh --config failed (rc=1)")
    with patch.object(health, "check_health", new=AsyncMock(
            return_value=HealthStatus(ok=False, reason="process_dead"))):
        with pytest.raises(RuntimeError):
            await health.ensure_healthy(AsyncMock(), "cs-refresh", src)
    (entry,) = dial_log.read("cs-refresh")
    assert (entry["kind"], entry["outcome"], entry["attempt"]) == ("reconnect", "error", 1)
    assert "config failed" in entry["reason"]


@pytest.mark.asyncio
async def test_a_direct_mode_spawn_that_raises_is_still_logged(win_platform, source):
    from ssh_manager import ConnectionManager

    manager = ConnectionManager(platform=win_platform)
    await manager.ensure_connected("spawn-fails", source)
    with patch("ssh_manager.proxy.spawn_in_kill_on_close_job", side_effect=OSError("no ssh.exe")):
        with pytest.raises(OSError):
            await manager.exec_command("spawn-fails", "true")
    (entry,) = dial_log.read("spawn-fails")
    assert (entry["kind"], entry["outcome"]) == ("direct_exec", "error")
    assert "no ssh.exe" in entry["reason"]


@pytest.mark.asyncio
async def test_a_reconnect_error_keeps_its_message():
    from unittest.mock import MagicMock

    from ssh_manager import health
    from ssh_manager.health import HealthStatus

    manager = AsyncMock()
    manager.ensure_connected.side_effect = ConnectionError("ControlMaster failed: kex reset")
    src = MagicMock()
    src.gh_env = None
    with patch.object(health, "check_health", new=AsyncMock(
            return_value=HealthStatus(ok=False, reason="process_dead"))), \
            patch.object(health.asyncio, "sleep", new=AsyncMock()):
        await health.ensure_healthy(manager, "cs-err", src, max_retries=1)
    (entry,) = dial_log.read("cs-err")
    assert entry["outcome"] == "error"
    assert "kex reset" in entry["reason"] and "kex reset" in entry["stderr"]


def test_writers_in_separate_processes_never_interleave_a_line():
    """The production writers are separate plugin processes: the per-file lock must
    serialize them across process boundaries."""
    import sys

    here = dial_log.Path(dial_log.__file__).resolve()
    paths = [str(here.parents[1]), str(here.parents[3] / "agent-procutil" / "src")]
    script = (f"import sys; sys.path[:0] = {paths!r}\n"
              "from ssh_manager import dial_log\n"
              "n = int(sys.argv[1])\n"
              "for i in range(40):\n"
              "    dial_log.record('cs-procs', kind='reconnect', outcome='ok', elapsed_s=0,"
              " attempt=n * 100 + i, stderr='x' * 200)\n")
    procs = [subprocess.Popen([sys.executable, "-c", script, str(n)]) for n in range(6)]
    assert all(p.wait(timeout=120) == 0 for p in procs)
    raw = dial_log._file_for("cs-procs").read_text(encoding="utf-8").splitlines()
    assert len(raw) == 240
    assert sorted(json.loads(line)["attempt"] for line in raw) == sorted(
        n * 100 + i for n in range(6) for i in range(40))


def test_on_an_event_loop_recording_returns_at_once_and_the_line_still_lands():
    """From a coroutine, record() hands the line off and returns at once even while the
    file is locked; the background writer appends it once the lock frees."""
    import asyncio
    import time as _time

    path = dial_log._file_for("cs-busy")
    path.parent.mkdir(parents=True, exist_ok=True)

    async def record_while_locked():
        with dial_log._locked(path) as held:
            assert held
            started = _time.monotonic()
            dial_log.record("cs-busy", kind="direct_exec", outcome="ok", elapsed_s=0)
            took = _time.monotonic() - started
            await asyncio.sleep(0.3)  # the writer waits for the lock meanwhile
            return took

    assert asyncio.run(record_while_locked()) < 0.1
    assert [e["kind"] for e in dial_log.read("cs-busy")] == ["direct_exec"]


def test_the_event_loop_thread_never_writes_the_file(monkeypatch):
    import asyncio
    import threading

    writers = []
    real = dial_log._write
    monkeypatch.setattr(dial_log, "_write", lambda p, line: (writers.append(threading.current_thread().name),
                                                             real(p, line)))

    async def dial():
        dial_log.record("cs-thread", kind="reconnect", outcome="ok", elapsed_s=0)

    asyncio.run(dial())
    assert dial_log.flush()
    assert writers == ["dial-log-writer"] and len(dial_log.read("cs-thread")) == 1


def test_a_saturated_writer_drops_lines_instead_of_growing(monkeypatch):
    import asyncio
    import threading

    monkeypatch.setattr(dial_log, "QUEUE_MAX", 2)
    dial_log._reset_writer()
    gate, written = threading.Event(), []
    monkeypatch.setattr(dial_log, "_write", lambda p, line: (gate.wait(5), written.append(line)))

    async def burst():
        for i in range(6):
            dial_log.record("cs-burst", kind="reconnect", outcome="ok", elapsed_s=0, attempt=i)

    try:
        asyncio.run(burst())
    finally:
        gate.set()
    assert dial_log.flush()
    assert 1 <= len(written) <= 3  # one in the writer's hands, at most QUEUE_MAX queued
    dial_log._reset_writer()


@pytest.mark.asyncio
async def test_a_direct_mode_stdio_channel_is_a_logged_dial(win_platform, source):
    from ssh_manager import ConnectionManager

    manager = ConnectionManager(platform=win_platform)
    await manager.ensure_connected("stdio-host", source)
    proc = AsyncMock()
    with patch("ssh_manager.proxy.spawn_in_kill_on_close_job", return_value=(proc, None)):
        await manager.open_stdio_channel("stdio-host", "copilot --acp")
    with patch("ssh_manager.proxy.spawn_in_kill_on_close_job", side_effect=OSError("spawn failed")):
        with pytest.raises(OSError):
            await manager.open_stdio_channel("stdio-host", "copilot --acp")
    assert [(e["kind"], e["outcome"]) for e in dial_log.read("stdio-host")] == [
        ("stdio_channel", "spawned"), ("stdio_channel", "error")]


def test_a_spawned_stdio_channel_is_neither_ok_nor_a_failure():
    """Only the spawn is known for a direct-mode stdio channel: counted as ``spawned``,
    never as a connection success, and never reported as the last failure."""
    dial_log.record("cs-sp", kind="stdio_channel", outcome="error", elapsed_s=0)
    dial_log.record("cs-sp", kind="stdio_channel", outcome="spawned", elapsed_s=0)
    s = dial_log.summary("cs-sp")
    assert s["last_10m"]["by_outcome"] == {"error": 1, "spawned": 1}
    assert s["last_failure"]["outcome"] == "error"


@pytest.mark.asyncio
async def test_each_control_master_start_attempt_is_logged(win_platform, monkeypatch):
    """A tunnel reset then a start: one line per attempt, with its attempt number and
    account; a start that never comes logs every attempt before raising."""
    from types import SimpleNamespace

    from ssh_manager import ConnectionManager

    manager = ConnectionManager(platform=win_platform)
    monkeypatch.setattr("ssh_manager.manager.asyncio.sleep", AsyncMock())
    starts = iter([ConnectionError("kex_exchange_identification: reset"), "master-proc"])

    async def start(*_a, **_k):
        item = next(starts)
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(manager, "_start_control_master", start)
    config = SimpleNamespace(ssh_target="cs-cm.example")
    assert await manager._connect_with_retry(config, None, [], env={"GH_TOKEN": "t"},
                                             target="cs-cm") == "master-proc"
    entries = dial_log.read("cs-cm")
    assert [(e["kind"], e["outcome"], e["attempt"], e["account"]) for e in entries] == [
        ("control_master", "error", 1, "pinned"), ("control_master", "ok", 2, "pinned")]
    assert "reset" in entries[0]["reason"]

    async def never(*_a, **_k):
        raise ConnectionError("tunnel down")

    monkeypatch.setattr(manager, "_start_control_master", never)
    with pytest.raises(ConnectionError):
        await manager._connect_with_retry(config, None, [], target="cs-cm-down", attempts=3)
    assert [(e["outcome"], e["attempt"], e["account"]) for e in dial_log.read("cs-cm-down")] == [
        ("error", 1, "ambient"), ("error", 2, "ambient"), ("error", 3, "ambient")]


def test_a_steady_state_append_never_rescans_the_log(monkeypatch):
    """The line count is kept beside the log under the writer lock: an append reads
    no part of the log back, however large it is."""
    from pathlib import Path

    monkeypatch.setattr(dial_log, "MAX_LINES", 10)  # a log past any "big enough to count" cutoff
    for _ in range(3):
        dial_log.record("cs-o1", kind="direct_exec", outcome="ok", elapsed_s=0)
    log = dial_log._file_for("cs-o1")
    reads, real = [], Path.open

    def spy(self, mode="r", *a, **k):
        if self == log and "r" in mode:
            reads.append(mode)
        return real(self, mode, *a, **k)

    monkeypatch.setattr(Path, "open", spy)
    dial_log.record("cs-o1", kind="direct_exec", outcome="ok", elapsed_s=0)
    assert reads == []
    assert dial_log._count_file(log).read_text() == f"{log.stat().st_size} 4"


def test_a_stale_line_count_is_recounted(monkeypatch):
    """A count that doesn't describe the log (a crash between the two writes, an
    edit) is recounted once, so the line bound still holds."""
    monkeypatch.setattr(dial_log, "MAX_LINES", 5)
    monkeypatch.setattr(dial_log, "KEEP_LINES", 3)
    for _ in range(4):
        dial_log.record("cs-o2", kind="reconnect", outcome="ok", elapsed_s=0)
    log = dial_log._file_for("cs-o2")
    dial_log._count_file(log).write_text("0 0")  # stale: claims an empty log
    dial_log.record("cs-o2", kind="reconnect", outcome="ok", elapsed_s=0)
    dial_log.record("cs-o2", kind="reconnect", outcome="ok", elapsed_s=0)  # the 6th line trims
    assert len(dial_log.read("cs-o2", last=100)) <= 5


def test_only_pinned_or_ambient_is_ever_stored_as_the_account():
    """The log's guarantee holds at the write, not by trusting each caller."""
    for given, stored in (("pinned", "pinned"), ("ambient", "ambient"),
                          ("ghp_abcdefghijklmnopqrstuvwxyz", "other"), ("someone@corp", "other"), ("", "")):
        dial_log.record("cs-acct", kind="reconnect", outcome="ok", elapsed_s=0, account=given)
        assert dial_log.read("cs-acct", last=1)[0]["account"] == stored

