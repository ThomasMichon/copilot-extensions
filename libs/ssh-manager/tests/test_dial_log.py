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
    monkeypatch.setattr(dial_log, "_approx_lines", lambda p: sum(1 for _ in p.open("rb")))
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
