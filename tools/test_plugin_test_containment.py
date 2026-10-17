from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import plugin_test_containment
from tools.plugin_test_containment import Limits, isolated_environment


@pytest.mark.skipif(os.name != "nt", reason="native Windows PowerShell module discovery")
@pytest.mark.parametrize("host", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("allow_host_state", [False, True])
def test_native_powershell_module_discovery_is_host_detached(tmp_path, host, allow_host_state):
    executable = shutil.which(host)
    if executable is None:
        pytest.skip(f"{host} unavailable")
    host_modules = tmp_path / "host-modules"
    module = host_modules / "ContainmentProbe"
    module.mkdir(parents=True)
    (module / "ContainmentProbe.psd1").write_text(
        "@{ RootModule='ContainmentProbe.psm1'; ModuleVersion='1.0'; "
        "FunctionsToExport=@('Get-ContainmentProbe') }\n", encoding="utf-8",
    )
    (module / "ContainmentProbe.psm1").write_text(
        "function Get-ContainmentProbe { 'host-module' }\n", encoding="utf-8",
    )
    base = {**os.environ, "PSModulePath": str(host_modules)}
    sandbox = tmp_path / "sandbox"
    env = isolated_environment(
        base, sandbox, allow_explicit_tiers=allow_host_state, allow_host_state=allow_host_state,
    )
    marker = sandbox / "core-command-marker"
    runner = tmp_path / "modules.ps1"
    runner.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        "$PSModuleAutoLoadingPreference = 'All'\n"
        "Set-Content -LiteralPath $args[0] -Value 'core-commands-available'\n"
        "if (-not (Test-Path -LiteralPath $args[0])) { throw 'core-command-failed' }\n"
        "$probe = Get-Command Get-ContainmentProbe -ErrorAction SilentlyContinue\n"
        "if ($probe) { & $probe } else { Write-Output 'host-module-unavailable' }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(runner), str(marker)],
        env=env, capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.read_text().strip() == "core-commands-available"
    assert result.stdout.strip() == (
        "host-module" if allow_host_state else "host-module-unavailable"
    )
    assert env["PSModulePath"] == str(host_modules if allow_host_state else sandbox / "powershell-modules")
    assert base["PSModulePath"] == str(host_modules)


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


def test_parallel_tree_size_overlaps_reads_with_two_threads(tmp_path, monkeypatch):
    children = [str(tmp_path / name) for name in ("one", "two")]
    barrier = threading.Barrier(2)
    lock = threading.Lock()
    counts = {"active": 0, "peak": 0}

    def read(directories):
        if directories == [tmp_path]:
            return 1, children
        assert len(directories) == 1
        with lock:
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
        try:
            barrier.wait(timeout=2)
            return (7 if directories[0] == children[0] else 11), []
        finally:
            with lock:
                counts["active"] -= 1

    monkeypatch.setattr(plugin_test_containment, "_directory_sizes", read)
    assert plugin_test_containment._tree_size_parallel(tmp_path) == 19
    assert counts == {"active": 0, "peak": 2}


def test_parallel_tree_size_rechecks_growth_and_removal(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    first, second = tmp_path / "first.bin", nested / "second.bin"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    assert plugin_test_containment._tree_size_parallel(tmp_path) == 11
    first.write_bytes(b"x" * 31)
    second.unlink()
    assert plugin_test_containment._tree_size_parallel(tmp_path) == 31


def test_parallel_tree_size_counts_across_multiple_batches(tmp_path):
    for index in range(80):
        directory = tmp_path / str(index)
        directory.mkdir()
        (directory / "data.bin").write_bytes(b"x" * (index + 1))
    assert plugin_test_containment._tree_size_parallel(tmp_path) == sum(range(1, 81))


def test_parallel_tree_size_propagates_unexpected_read_failure(tmp_path, monkeypatch):
    def read(directories):
        raise RuntimeError("unexpected scan failure")

    monkeypatch.setattr(plugin_test_containment, "_directory_sizes", read)
    with pytest.raises(RuntimeError, match="unexpected scan failure"):
        plugin_test_containment._tree_size_parallel(tmp_path)


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
    monkeypatch.setattr(plugin_test_containment, "_posix_live_group_usage", lambda pid: next(usage))
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

    assert plugin_test_containment._ps_group_usage(7) == (2, 4096)
    assert plugin_test_containment._ps_group_usage(7, live_only=True) == (1, 4096)

def test_linux_empty_group_does_not_require_ps(monkeypatch):
    monkeypatch.setattr(plugin_test_containment, "_linux_group_usage", lambda pid: (0, 0))
    monkeypatch.setattr(
        plugin_test_containment, "_ps_group_usage",
        lambda pid: pytest.fail("native empty-group measurement is authoritative"),
    )
    assert plugin_test_containment._posix_group_usage(7) == (0, 0)

@pytest.mark.skipif(os.name == "nt", reason="POSIX process-budget accounting")
def test_zombie_heavy_group_exceeds_process_budget(tmp_path, capfd, monkeypatch):
    class Process:
        pid = 12345

        def poll(self):
            return 0

    monkeypatch.setattr(plugin_test_containment.subprocess, "Popen", lambda *args, **kwargs: Process())
    monkeypatch.setattr(plugin_test_containment, "_posix_exit_code", lambda proc: None)
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity", lambda pid: (pid, pid))
    monkeypatch.setattr(plugin_test_containment, "_terminate_posix_group", lambda *args: None)
    monkeypatch.setattr(plugin_test_containment, "_linux_group_usage", lambda pid: (130, 0))
    monkeypatch.setattr(plugin_test_containment, "_tree_size", lambda path: 0)
    rc = plugin_test_containment._run_contained_process(
        ["fake"], cwd=tmp_path, env={}, sandbox=tmp_path,
        limits=Limits(wall_seconds=5, max_processes=128),
    )
    assert rc == 124
    assert "process-count limit exceeded (128)" in capfd.readouterr().err


def test_posix_group_verifies_members_after_sigkill(monkeypatch):
    signals = []
    monkeypatch.setattr(plugin_test_containment.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(
        plugin_test_containment.os, "killpg",
        lambda pid, signal: signals.append(signal), raising=False,
    )
    usage = iter(((2, 0), (1, 0), (0, 0)))
    clock = iter((0.0, 3.0, 3.0, 3.0))
    monkeypatch.setattr(plugin_test_containment, "_posix_live_group_usage", lambda pid: next(usage))
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
    monkeypatch.setattr(plugin_test_containment, "_posix_live_group_usage", lambda pid: (1, 0))
    clock = iter((0.0, 3.0))
    monkeypatch.setattr(plugin_test_containment.time, "monotonic", lambda: next(clock))
    with pytest.raises(plugin_test_containment.UnreapedProcessError, match="stale or reused"):
        plugin_test_containment._terminate_posix_group(12345, (12345, 12345))
    assert signals == ([plugin_test_containment.signal.SIGTERM] if changed_before_kill else [])


def test_posix_exit_observation_does_not_reap_controller(monkeypatch):
    calls = []
    monkeypatch.setattr(
        plugin_test_containment.os, "read",
        lambda *args: calls.append(args) or b"7\n",
        raising=False,
    )
    assert plugin_test_containment._posix_exit_code(12) == 7
    assert calls == [(12, 64)]


@pytest.mark.parametrize("result", [b"", b"not-an-exit-code\n"])
def test_posix_control_pipe_failure_is_explicit(monkeypatch, result):
    monkeypatch.setattr(plugin_test_containment.os, "read", lambda *args: result)
    with pytest.raises(plugin_test_containment.ContainmentError):
        plugin_test_containment._posix_exit_code(12)


def test_controller_reports_status_then_stays_alive_without_leaking_control_env(
    monkeypatch, tmp_path,
):
    ready = tmp_path / "ready"
    ready.touch()
    events = []
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTAINMENT_RESULT_FD", "12")

    def command(*args, **kwargs):
        assert "COPILOT_EXTENSIONS_CONTAINMENT_RESULT_FD" not in os.environ
        assert "pass_fds" not in kwargs
        events.append("command")
        return SimpleNamespace(returncode=7)

    monkeypatch.setattr(plugin_test_containment.subprocess, "run", command)
    monkeypatch.setattr(plugin_test_containment.os, "write",
                        lambda fd, data: events.append(("status", fd, data)))
    monkeypatch.setattr(plugin_test_containment.os, "close",
                        lambda fd: events.append(("close", fd)))

    def waiting(seconds):
        events.append("waiting-for-owner")
        raise RuntimeError("owner teardown")

    monkeypatch.setattr(plugin_test_containment.time, "sleep", waiting)
    with pytest.raises(RuntimeError, match="owner teardown"):
        plugin_test_containment._worker_main([str(ready), "command"])
    assert events == [
        "command", ("status", 12, b"7\n"), ("close", 12), "waiting-for-owner",
    ]


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
        assert plugin_test_containment._posix_live_group_usage(pgid) == (0, 0)
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
