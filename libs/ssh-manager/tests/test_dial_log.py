"""The bounded, structured dial log (``ssh_manager.dial_log``) and the dials that feed it."""

from __future__ import annotations

import json
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
