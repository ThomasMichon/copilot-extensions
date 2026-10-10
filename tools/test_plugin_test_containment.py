from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import plugin_test_containment
from tools.plugin_test_containment import Limits, isolated_environment


def test_tree_size_counts_nested_files_once(tmp_path):
    (tmp_path / "empty").mkdir()
    nested = tmp_path / "nested"
    nested.mkdir()
    (tmp_path / "first.bin").write_bytes(b"first")
    (nested / "second.bin").write_bytes(b"second")

    assert plugin_test_containment._tree_size(tmp_path) == 11
    assert plugin_test_containment._tree_size(tmp_path / "missing") == 0


def test_tree_size_uses_cached_entries_without_following_links(tmp_path, monkeypatch):
    calls = []

    class Entry:
        path = str(tmp_path / "file")

        def is_dir(self, *, follow_symlinks):
            assert follow_symlinks is False
            return False

        def is_file(self, *, follow_symlinks):
            assert follow_symlinks is False
            return True

        def stat(self, *, follow_symlinks):
            assert follow_symlinks is False
            calls.append("stat")
            return SimpleNamespace(st_size=7)

    class Link(Entry):
        def is_file(self, *, follow_symlinks):
            assert follow_symlinks is False
            return False

    class Removed(Entry):
        def is_dir(self, *, follow_symlinks):
            raise FileNotFoundError(self.path)

    class Entries:
        def __enter__(self):
            return iter((Link(), Removed(), Entry()))

        def __exit__(self, *args):
            pass

    monkeypatch.setattr(plugin_test_containment.os, "scandir", lambda path: Entries())

    assert plugin_test_containment._tree_size(tmp_path) == 7
    assert calls == ["stat"]


def test_exited_process_cannot_hide_an_elapsed_wall_limit(tmp_path, capfd, monkeypatch):
    class Process:
        pid = 12345

        def poll(self):
            return 0

    class Job:
        def __init__(self, limits):
            pass

        def assign(self, pid):
            pass

        def close(self):
            pass

    clock = iter((0.0, 6.0))
    monkeypatch.setattr(plugin_test_containment.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(plugin_test_containment.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(plugin_test_containment, "_WindowsJob", Job)
    monkeypatch.setattr(plugin_test_containment, "_posix_exit_code", lambda proc: 0)
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity", lambda pid: (pid, pid))
    monkeypatch.setattr(plugin_test_containment, "_terminate_posix_group", lambda *args: None)

    rc = plugin_test_containment._run_contained_process(
        ["fake"], cwd=tmp_path, env={}, sandbox=tmp_path,
        limits=Limits(wall_seconds=5),
    )

    assert rc == 124
    assert "wall-clock limit exceeded (5s)" in capfd.readouterr().err


def test_windows_job_waits_for_every_member_before_closing_handle(monkeypatch):
    events = []
    job = object.__new__(plugin_test_containment._WindowsJob)
    job._handle = 7
    counts = iter((2, 1, 0))

    def active():
        count = next(counts)
        events.append(("active", count))
        return count

    job.active_processes = active
    job._kernel32 = SimpleNamespace(
        TerminateJobObject=lambda handle, code: events.append(("terminate", handle)) or 1,
        CloseHandle=lambda handle: events.append(("close", handle)),
    )
    monkeypatch.setattr(plugin_test_containment.time, "sleep", lambda seconds: None)

    job.close()

    assert events == [
        ("active", 2), ("terminate", 7), ("active", 1), ("active", 0), ("close", 7),
    ]
    assert job._handle is None


def test_windows_job_reports_unproven_termination_instead_of_claiming_reaped(monkeypatch):
    job = object.__new__(plugin_test_containment._WindowsJob)
    job._handle = 7
    job.active_processes = lambda: 1
    closed = []
    job._kernel32 = SimpleNamespace(
        TerminateJobObject=lambda handle, code: 1,
        CloseHandle=lambda handle: closed.append(handle),
    )
    clock = iter((0.0, 6.0))
    monkeypatch.setattr(plugin_test_containment.time, "monotonic", lambda: next(clock))

    with pytest.raises(plugin_test_containment.UnreapedProcessError, match="active processes"):
        job.close()

    assert closed == [7]
    assert job._handle is None


def test_posix_group_waits_until_no_live_members_remain(monkeypatch):
    signals = []
    monkeypatch.setattr(
        plugin_test_containment.os, "killpg",
        lambda pid, signal: signals.append((pid, signal)), raising=False,
    )
    usage = iter(((2, 0), (1, 0), (0, 0)))
    monkeypatch.setattr(plugin_test_containment, "_posix_group_usage", lambda pid: next(usage))
    monkeypatch.setattr(plugin_test_containment.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity", lambda pid: (pid, pid))

    plugin_test_containment._terminate_posix_group(12345, (12345, 12345))

    assert signals == [(12345, plugin_test_containment.signal.SIGTERM)]


def test_posix_usage_does_not_treat_zombies_as_file_holding_members(monkeypatch):
    monkeypatch.setattr(plugin_test_containment.shutil, "which", lambda command: "ps")
    monkeypatch.setattr(
        plugin_test_containment.subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="7 4 S\n7 0 Z\n8 9 S\n"),
    )

    assert plugin_test_containment._ps_group_usage(7) == (1, 4096)


def test_posix_group_verifies_members_after_sigkill(monkeypatch):
    signals = []
    monkeypatch.setattr(plugin_test_containment.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(
        plugin_test_containment.os, "killpg",
        lambda pid, signal: signals.append(signal), raising=False,
    )
    usage = iter(((2, 0), (1, 0), (0, 0)))
    clock = iter((0.0, 3.0, 3.0, 3.0))
    monkeypatch.setattr(plugin_test_containment, "_posix_group_usage", lambda pid: next(usage))
    monkeypatch.setattr(plugin_test_containment.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(plugin_test_containment.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity", lambda pid: (pid, pid))

    plugin_test_containment._terminate_posix_group(12345, (12345, 12345))

    assert signals == [plugin_test_containment.signal.SIGTERM, 9]


@pytest.mark.parametrize("changed_before_kill", [False, True])
def test_posix_teardown_refuses_stale_identity_before_each_signal(monkeypatch, changed_before_kill):
    signals = []
    identities = iter(
        [(12345, 12345), (12345, 54321)] if changed_before_kill else [(12345, 54321)]
    )
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity", lambda pid: next(identities))
    monkeypatch.setattr(plugin_test_containment.os, "killpg", lambda pid, sig: signals.append(sig), raising=False)
    monkeypatch.setattr(plugin_test_containment, "_posix_group_usage", lambda pid: (1, 0))
    clock = iter((0.0, 3.0))
    monkeypatch.setattr(plugin_test_containment.time, "monotonic", lambda: next(clock))
    with pytest.raises(plugin_test_containment.UnreapedProcessError, match="stale or reused"):
        plugin_test_containment._terminate_posix_group(12345, (12345, 12345))
    assert signals == ([plugin_test_containment.signal.SIGTERM] if changed_before_kill else [])


def test_posix_exit_observation_does_not_reap_controller(monkeypatch):
    monkeypatch.setattr(plugin_test_containment.os, "P_PID", 1, raising=False)
    for name, value in (("WEXITED", 2), ("WNOHANG", 4), ("WNOWAIT", 8), ("CLD_EXITED", 1)):
        monkeypatch.setattr(plugin_test_containment.os, name, value, raising=False)
    calls = []
    monkeypatch.setattr(
        plugin_test_containment.os, "waitid",
        lambda *args: calls.append(args) or SimpleNamespace(si_status=7, si_code=1),
        raising=False,
    )
    assert plugin_test_containment._posix_exit_code(SimpleNamespace(pid=12345)) == 7
    assert calls == [(1, 12345, 14)]


@pytest.mark.parametrize("hang_controller", [False, True])
def test_file_holding_descendant_is_reaped_before_return(tmp_path, hang_controller):
    held = tmp_path / "held.txt"
    program = (
        "import os,pathlib,subprocess,sys,time;"
        "p=pathlib.Path(sys.argv[1]);"
        "child='import sys,time;f=open(sys.argv[1],\"w\");"
        "f.write(\"ready\");f.flush();time.sleep(60)';"
        "subprocess.Popen([sys.executable,'-c',child,str(p)],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
        "creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));\n"
        "if os.name != 'nt': "
        "pathlib.Path(str(p)+'.pgid').write_text(str(os.getpgrp()))\n"
        "while not p.exists(): time.sleep(0.01)\n"
        + ("time.sleep(60)\n" if hang_controller else "")
    )
    rc = plugin_test_containment.run_contained(
        [sys.executable, "-c", program, str(held)],
        cwd=tmp_path, env=isolated_environment(os.environ, tmp_path),
        sandbox=tmp_path,
        limits=Limits(wall_seconds=8, max_processes=8, max_memory_mb=256, max_temp_mb=32),
    )

    assert rc == (124 if hang_controller else 0)
    assert held.read_text() == "ready"
    if os.name != "nt":
        pgid = int(Path(str(held) + ".pgid").read_text())
        assert plugin_test_containment._posix_group_usage(pgid) == (0, 0)
    held.unlink()
    assert not held.exists()


def test_run_contained_detects_and_fails_on_persistent_environment_drift(
    tmp_path, capfd, monkeypatch
):
    before = plugin_test_containment._WindowsEnvironmentSnapshot(
        user={"Path": ("before", 1)},
        machine={},
    )
    after = plugin_test_containment._WindowsEnvironmentSnapshot(
        user={"Path": ("after", 1)},
        machine={},
    )
    snapshots = iter((before, after))
    monkeypatch.setattr(
        plugin_test_containment,
        "_read_registry_environment",
        lambda: next(snapshots),
    )
    env = isolated_environment(os.environ, tmp_path)

    rc = plugin_test_containment.run_contained(
        [sys.executable, "-c", "raise SystemExit(0)"],
        cwd=tmp_path,
        env=env,
        sandbox=tmp_path,
        limits=Limits(
            wall_seconds=10,
            max_processes=8,
            max_memory_mb=256,
            max_temp_mb=32,
        ),
    )

    assert rc == 125
    assert "detected without rollback: User:Path" in capfd.readouterr().err
