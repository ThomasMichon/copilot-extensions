from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import plugin_test_containment
from tools.plugin_test_containment import Limits, isolated_environment


def test_interactive_daemons_require_an_explicit_runner_policy(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin_test_containment, "_read_registry_environment", lambda: None)
    monkeypatch.setattr(plugin_test_containment, "_registry_environment_drift", lambda _: [])
    monkeypatch.setattr(
        plugin_test_containment, "_run_contained_process",
        lambda *args, **kwargs: pytest.fail("must refuse before starting a test"),
    )
    with pytest.raises(ValueError, match="explicit"):
        plugin_test_containment.run_contained(
            ["unused"], cwd=tmp_path, env={}, sandbox=tmp_path,
            limits=Limits(), daemon_purpose="interactive-test",
        )


def test_runner_passes_pause_tolerant_policy_and_keeps_renewal_custody(tmp_path, monkeypatch):
    from contextlib import contextmanager

    events = []
    policy = plugin_test_containment.DaemonLeasePolicy(90000, 172800)

    @contextmanager
    def lifecycle(**kwargs):
        events.append(("enter", kwargs))
        yield {"DAEMON_TEST_CONTEXT": "owned"}
        events.append(("exit", None))

    def run(command, **kwargs):
        events.append(("run", kwargs["env"]))
        return 0

    monkeypatch.setattr(plugin_test_containment, "daemon_test_lifecycle", lifecycle)
    monkeypatch.setattr(plugin_test_containment, "_run_contained_process", run)
    monkeypatch.setattr(plugin_test_containment, "_read_registry_environment", lambda: None)
    monkeypatch.setattr(plugin_test_containment, "_registry_environment_drift", lambda _: [])
    assert plugin_test_containment.run_contained(
        ["unused"], cwd=tmp_path, env={"KEEP": "yes"}, sandbox=tmp_path,
        limits=Limits(), daemon_purpose="interactive-test", daemon_lease=policy,
    ) == 0
    assert [event for event, _ in events] == ["enter", "run", "exit"]
    assert events[0][1]["purpose"] == "interactive-test"
    assert events[0][1]["lease"] is policy
    assert events[0][1]["registry_root"] == tmp_path.resolve() / "daemon-instances"
    assert events[1][1] == {"KEEP": "yes", "DAEMON_TEST_CONTEXT": "owned"}


@pytest.mark.parametrize("alias", [
    "parent-reference",
    "missing-child",
    pytest.param("short-name", marks=pytest.mark.skipif(os.name != "nt", reason="Windows short path")),
])
def test_runner_canonicalizes_its_owned_sandbox_before_daemon_admission(tmp_path, monkeypatch, alias):
    from contextlib import contextmanager

    parent = tmp_path / "parent"
    parent.mkdir()
    sandbox = parent / ".." / "sandbox"
    if alias == "missing-child":
        sandbox = parent / "pending" / "sandbox"
        assert not sandbox.exists()
    else:
        sandbox.mkdir()
    expected = sandbox.resolve() / "daemon-instances"
    if alias == "short-name":
        import ctypes

        buffer = ctypes.create_unicode_buffer(32768)
        length = ctypes.windll.kernel32.GetShortPathNameW(str(sandbox.resolve()), buffer, len(buffer))
        assert 0 < length < len(buffer)
        sandbox = Path(buffer.value)
        if sandbox == sandbox.resolve():
            pytest.skip("volume does not expose an alternate short path")
    admitted = []

    @contextmanager
    def lifecycle(**kwargs):
        from agent_procutil._daemon_instance_store import _root

        root = kwargs["registry_root"]
        admitted.append(_root(root, create=True))
        yield {}

    monkeypatch.setattr(plugin_test_containment, "daemon_test_lifecycle", lifecycle)
    monkeypatch.setattr(plugin_test_containment, "_run_contained_process", lambda *args, **kwargs: 0)
    monkeypatch.setattr(plugin_test_containment, "_read_registry_environment", lambda: None)
    monkeypatch.setattr(plugin_test_containment, "_registry_environment_drift", lambda _: [])
    assert plugin_test_containment.run_contained(
        ["unused"], cwd=tmp_path, env={}, sandbox=sandbox, limits=Limits(),
    ) == 0
    assert admitted == [expected]


def test_tree_size_counts_nested_files_without_repeated_path_stats(tmp_path, monkeypatch):
    (tmp_path / "nested").mkdir()
    (tmp_path / "a").write_bytes(b"abc")
    (tmp_path / "nested" / ".hidden").write_bytes(b"12345")

    def repeated_walk(*args, **kwargs):
        raise AssertionError("The monitor must retain scandir's cached file metadata")

    monkeypatch.setattr(Path, "rglob", repeated_walk)
    assert plugin_test_containment._tree_size(tmp_path) == 8
    assert plugin_test_containment._tree_size(tmp_path / "absent") == 0


def test_tree_size_does_not_follow_file_or_directory_links(tmp_path):
    root = tmp_path / "sandbox"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "data").write_bytes(b"not-counted")
    (root / "data").write_bytes(b"counted")
    try:
        (root / "linked-file").symlink_to(outside / "data")
        (root / "linked-directory").symlink_to(outside, target_is_directory=True)
        (root / "loop").symlink_to(root, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"Symbolic link creation is unavailable: {error}")
    assert plugin_test_containment._tree_size(root) == len(b"counted")


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

        def is_symlink(self):
            return False

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
        def is_symlink(self):
            return True

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


def test_parallel_tree_size_counts_across_multiple_batches(tmp_path, monkeypatch):
    for index in range(80):
        directory = tmp_path / str(index)
        directory.mkdir()
        (directory / "data.bin").write_bytes(b"x" * (index + 1))
    read = plugin_test_containment._directory_sizes
    batches = []

    def bounded_read(directories):
        batches.append(tuple(directories))
        return read(directories)

    monkeypatch.setattr(plugin_test_containment, "_directory_sizes", bounded_read)
    assert plugin_test_containment._tree_size_parallel(tmp_path) == sum(range(1, 81))
    assert all(1 <= len(batch) <= 32 for batch in batches)
    assert sorted(str(path) for batch in batches for path in batch) == sorted(
        [str(tmp_path), *(str(tmp_path / str(index)) for index in range(80))]
    )


def test_parallel_tree_size_propagates_unexpected_read_failure(tmp_path, monkeypatch):
    def read(directories):
        raise RuntimeError("unexpected scan failure")

    monkeypatch.setattr(plugin_test_containment, "_directory_sizes", read)
    with pytest.raises(RuntimeError, match="unexpected scan failure"):
        plugin_test_containment._tree_size_parallel(tmp_path)


@pytest.mark.parametrize("platform", ["nt", "posix"])
def test_tree_size_uses_parallel_readers_only_on_windows(tmp_path, monkeypatch, platform):
    calls = []

    def parallel(path):
        calls.append(path)
        return 17

    monkeypatch.setattr(plugin_test_containment, "_tree_size_parallel", parallel)
    monkeypatch.setattr(plugin_test_containment, "_directory_sizes", lambda directories: (11, []))
    monkeypatch.setattr(plugin_test_containment, "os", SimpleNamespace(name=platform))
    assert plugin_test_containment._tree_size(tmp_path) == (17 if platform == "nt" else 11)
    assert calls == ([tmp_path] if platform == "nt" else [])


@pytest.mark.parametrize("fault", ["reader-failure", "cancel"])
def test_parallel_tree_size_joins_file_holding_readers_before_return(
    tmp_path, monkeypatch, fault,
):
    root = tmp_path / "scan"
    root.mkdir()
    held_file = root / "held"
    held_file.write_bytes(b"data")
    opened = threading.Event()
    release = threading.Event()
    shutdown = threading.Event()
    returned = threading.Event()
    observations = []
    errors = []
    executor = plugin_test_containment.ThreadPoolExecutor
    wait = plugin_test_containment.wait

    class ObservedExecutor(executor):
        def shutdown(self, *args, **kwargs):
            shutdown.set()
            return super().shutdown(*args, **kwargs)

    def read(directories):
        if directories == [root]:
            return 0, ["held-reader", "failing-reader"]
        if directories == ["failing-reader"]:
            assert opened.wait(3)
            if fault == "reader-failure":
                raise RuntimeError("unexpected scan failure")
            return 0, []
        assert directories == ["held-reader"]
        with held_file.open("rb") as stream:
            assert stream.read() == b"data"
            opened.set()
            assert release.wait(3)
        observations.append("reader-closed")
        return 4, []

    def cancellable_wait(active, **kwargs):
        if fault == "cancel" and opened.is_set():
            raise KeyboardInterrupt("cancel scan")
        return wait(active, **kwargs)

    def scan():
        try:
            plugin_test_containment._tree_size_parallel(root)
        except (RuntimeError, KeyboardInterrupt) as error:
            errors.append(error)
        finally:
            observations.append("caller-returned")
            returned.set()

    monkeypatch.setattr(plugin_test_containment, "ThreadPoolExecutor", ObservedExecutor)
    monkeypatch.setattr(plugin_test_containment, "_directory_sizes", read)
    monkeypatch.setattr(plugin_test_containment, "wait", cancellable_wait)
    caller = threading.Thread(target=scan)
    caller.start()
    try:
        assert opened.wait(3)
        assert shutdown.wait(3)
        assert not returned.is_set()
    finally:
        release.set()
        caller.join(timeout=3)
    assert not caller.is_alive()
    assert len(errors) == 1
    expected = RuntimeError if fault == "reader-failure" else KeyboardInterrupt
    assert isinstance(errors[0], expected)
    assert observations == ["reader-closed", "caller-returned"]
    held_file.unlink()
    root.rmdir()


def test_native_temp_budget_still_terminates_the_process_tree(tmp_path, capfd):
    environment = isolated_environment(os.environ, tmp_path)
    code = (
        "from pathlib import Path; import time; "
        "Path('allocation').write_bytes(b'x' * (2 * 1024 * 1024)); "
        "time.sleep(60)"
    )
    result = plugin_test_containment.run_contained(
        [sys.executable, "-c", code], cwd=tmp_path, env=environment,
        sandbox=tmp_path, limits=Limits(
            wall_seconds=10, max_processes=8, max_memory_mb=256, max_temp_mb=1,
        ),
    )
    assert result == 124
    assert "temporary-storage limit exceeded" in capfd.readouterr().err


@pytest.mark.parametrize("module_path", [None, "host-modules"])
@pytest.mark.parametrize(
    ("allow_explicit_tiers", "allow_host_state"),
    [(False, False), (True, False), (True, True)],
)
def test_powershell_module_path_requires_explicit_host_state(
    tmp_path, module_path, allow_explicit_tiers, allow_host_state,
):
    base = {"PATH": "native-applications"}
    if module_path is not None:
        base["PSModulePath"] = module_path
    before = base.copy()
    env = isolated_environment(
        base, tmp_path,
        allow_explicit_tiers=allow_explicit_tiers, allow_host_state=allow_host_state,
    )
    assert base == before
    assert env["PATH"] == before["PATH"]
    if allow_host_state:
        assert env.get("PSModulePath") == module_path
    else:
        assert env["PSModulePath"] == str(tmp_path / "powershell-modules")
        assert Path(env["PSModulePath"]).is_dir()


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
    parent_module_path = os.environ.get("PSModulePath")
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
        "if (-not (Get-Command $args[1] -CommandType Application)) { throw 'native-app-missing' }\n"
        "$probe = Get-Command Get-ContainmentProbe -ErrorAction SilentlyContinue\n"
        "if ($probe) { & $probe } else { Write-Output 'host-module-unavailable' }\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-File", str(runner),
         str(marker), Path(executable).name],
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
    assert os.environ.get("PSModulePath") == parent_module_path


def test_exited_process_cannot_hide_an_elapsed_wall_limit(tmp_path, capfd, monkeypatch):
    class Process:
        pid = 12345

        def poll(self):
            return 0

        def wait(self, *, timeout):
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
    monkeypatch.setattr(plugin_test_containment, "_posix_exit_code", lambda fd: 0)
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


@pytest.mark.parametrize("fault", ["timeout", "terminate", "query", "cancel"])
def test_windows_job_reports_unproven_termination_instead_of_claiming_reaped(monkeypatch, fault):
    job = object.__new__(plugin_test_containment._WindowsJob)
    job._handle = 7

    def active():
        if fault == "query":
            raise plugin_test_containment.ContainmentError("query failed")
        if fault == "cancel":
            raise KeyboardInterrupt
        return 1

    job.active_processes = active
    closed = []
    job._kernel32 = SimpleNamespace(
        TerminateJobObject=lambda handle, code: int(fault != "terminate"),
        CloseHandle=lambda handle: closed.append(handle),
    )
    clock = iter((0.0, 6.0))
    monkeypatch.setattr(plugin_test_containment.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(plugin_test_containment.ctypes, "get_last_error", lambda: 5, raising=False)
    with pytest.raises(plugin_test_containment.UnreapedProcessError):
        job.close()
    assert closed == [7]
    assert job._handle is None


def test_posix_group_waits_until_no_live_members_remain(monkeypatch):
    signals = []
    monkeypatch.setattr(plugin_test_containment.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(
        plugin_test_containment.os, "killpg",
        lambda pid, sig: signals.append((pid, sig)), raising=False,
    )
    usage = iter(((2, 0), (1, 0), (0, 0)))
    monkeypatch.setattr(plugin_test_containment, "_posix_live_group_usage", lambda pid: next(usage))
    monkeypatch.setattr(plugin_test_containment.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity", lambda pid: (pid, pid))
    plugin_test_containment._terminate_posix_group(12345, (12345, 12345))
    assert signals == [(12345, plugin_test_containment.signal.SIGTERM), (12345, 9)]


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


def test_partial_control_pipe_allocation_closes_owned_descriptors(tmp_path, monkeypatch):
    calls = []

    def pipe():
        calls.append("pipe")
        if len(calls) == 1:
            return 10, 11
        raise OSError("owner pipe allocation failed")

    monkeypatch.setattr(plugin_test_containment, "os", SimpleNamespace(
        name="posix", pipe=pipe, close=lambda fd: calls.append(("close", fd)),
    ))
    with pytest.raises(OSError, match="owner pipe allocation failed"):
        plugin_test_containment._run_contained_process(
            ["fake"], cwd=tmp_path, env={}, sandbox=tmp_path, limits=Limits(),
        )
    assert calls == ["pipe", "pipe", ("close", 10), ("close", 11)]


def test_failed_teardown_releases_owner_before_reaping_controller(tmp_path, monkeypatch):
    events = []
    pipes = iter(((10, 11), (12, 13)))
    monkeypatch.setattr(plugin_test_containment, "os", SimpleNamespace(
        name="posix", pipe=lambda: next(pipes), set_blocking=lambda *args: None,
        close=lambda fd: events.append(("close", fd)),
    ))
    monkeypatch.setattr(plugin_test_containment.subprocess, "Popen",
                        lambda *a, **k: SimpleNamespace(pid=12345))
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity", lambda pid: (pid, pid))
    monkeypatch.setattr(plugin_test_containment, "_posix_exit_code", lambda fd: 0)

    def unavailable(*args):
        raise plugin_test_containment.UnreapedProcessError("membership unavailable")

    def reap(proc):
        assert ("close", 13) in events
        events.append("reap")

    monkeypatch.setattr(plugin_test_containment, "_terminate_posix_group", unavailable)
    monkeypatch.setattr(plugin_test_containment, "_reap_controller", reap)
    with pytest.raises(plugin_test_containment.UnreapedProcessError, match="membership unavailable"):
        plugin_test_containment._run_contained_process(
            ["fake"], cwd=tmp_path, env={}, sandbox=tmp_path, limits=Limits(),
        )
    assert events[-3:] == [("close", 13), ("close", 10), "reap"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-budget accounting")
def test_zombie_heavy_group_exceeds_process_budget(tmp_path, capfd, monkeypatch):
    class Process:
        pid = 12345

        def wait(self, *, timeout):
            return 0

    monkeypatch.setattr(plugin_test_containment.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(plugin_test_containment, "_posix_exit_code", lambda fd: None)
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
        lambda pid, sig: signals.append(sig), raising=False,
    )
    usage = iter(((2, 0), (1, 0), (0, 0)))
    clock = iter((0.0, 3.0, 3.0, 3.0))
    monkeypatch.setattr(plugin_test_containment, "_posix_live_group_usage", lambda pid: next(usage))
    monkeypatch.setattr(plugin_test_containment.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(plugin_test_containment.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity", lambda pid: (pid, pid))
    plugin_test_containment._terminate_posix_group(12345, (12345, 12345))
    assert signals == [plugin_test_containment.signal.SIGTERM, 9]


def test_posix_teardown_kills_children_missed_by_term_enumeration(monkeypatch):
    events = []
    monkeypatch.setattr(plugin_test_containment.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity", lambda pid: (pid, pid))
    monkeypatch.setattr(plugin_test_containment.os, "killpg",
                        lambda pid, sig: events.append(("signal", sig)), raising=False)
    # TERM enumeration saw only the controller: a parent forked after the
    # directory snapshot and exited before its stat read. KILL is still required.
    observations = iter((1, 2, 0))

    def usage(pid):
        count = next(observations)
        events.append(("members", count))
        return count, 0

    monkeypatch.setattr(plugin_test_containment, "_posix_live_group_usage", usage)
    monkeypatch.setattr(plugin_test_containment.time, "sleep", lambda seconds: None)
    plugin_test_containment._terminate_posix_group(12345, (12345, 12345))
    assert events == [
        ("signal", plugin_test_containment.signal.SIGTERM), ("members", 1),
        ("signal", 9), ("members", 2), ("members", 0),
    ]


@pytest.mark.parametrize("changed_before_kill", [False, True])
def test_posix_teardown_refuses_stale_identity_before_each_signal(monkeypatch, changed_before_kill):
    signals = []
    identities = iter(
        [(12345, 12345), (12345, 54321)] if changed_before_kill else [(12345, 54321)]
    )
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity", lambda pid: next(identities))
    monkeypatch.setattr(plugin_test_containment.os, "killpg",
                        lambda pid, sig: signals.append(sig), raising=False)
    monkeypatch.setattr(plugin_test_containment, "_posix_live_group_usage", lambda pid: (1, 0))
    clock = iter((0.0, 3.0))
    monkeypatch.setattr(plugin_test_containment.time, "monotonic", lambda: next(clock))
    with pytest.raises(plugin_test_containment.UnreapedProcessError, match="stale or reused"):
        plugin_test_containment._terminate_posix_group(12345, (12345, 12345))
    assert signals == ([plugin_test_containment.signal.SIGTERM] if changed_before_kill else [])


def test_posix_exit_observation_does_not_reap_controller(monkeypatch):
    calls = []
    monkeypatch.setattr(plugin_test_containment.os, "read",
                        lambda *args: calls.append(args) or b"7\n", raising=False)
    assert plugin_test_containment._posix_exit_code(12) == 7
    assert calls == [(12, 64)]


@pytest.mark.parametrize("result", [b"", b"not-an-exit-code\n"])
def test_posix_control_pipe_failure_is_explicit(monkeypatch, result):
    monkeypatch.setattr(plugin_test_containment.os, "read", lambda *args: result)
    with pytest.raises(plugin_test_containment.ContainmentError):
        plugin_test_containment._posix_exit_code(12)


@pytest.mark.parametrize("failure", [None, PermissionError("unreadable process")])
def test_posix_teardown_refuses_unknown_membership(monkeypatch, failure):
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity", lambda pid: (pid, pid))
    monkeypatch.setattr(plugin_test_containment.os, "killpg", lambda *args: None, raising=False)

    def usage(pid):
        if failure is not None:
            raise failure
        return None

    monkeypatch.setattr(plugin_test_containment, "_posix_live_group_usage", usage)
    with pytest.raises(plugin_test_containment.UnreapedProcessError, match="Cannot verify"):
        plugin_test_containment._terminate_posix_group(12345, (12345, 12345))


@pytest.mark.parametrize("row", ["unparseable", "wrong 0 S"])
def test_posix_teardown_rejects_malformed_ps_membership(monkeypatch, row):
    monkeypatch.setattr(plugin_test_containment.shutil, "which", lambda command: "ps")
    monkeypatch.setattr(plugin_test_containment.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(returncode=0, stdout=row))
    with pytest.raises(ValueError):
        plugin_test_containment._ps_group_usage(7, live_only=True)


def test_controller_reports_status_then_stays_alive_without_leaking_control_env(
    monkeypatch, tmp_path,
):
    ready = tmp_path / "ready"
    ready.touch()
    events = []
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTAINMENT_RESULT_FD", "12")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTAINMENT_OWNER_FD", "13")

    def command(*args, **kwargs):
        assert "COPILOT_EXTENSIONS_CONTAINMENT_RESULT_FD" not in os.environ
        assert "COPILOT_EXTENSIONS_CONTAINMENT_OWNER_FD" not in os.environ
        assert "pass_fds" not in kwargs
        events.append("command")
        return SimpleNamespace(poll=lambda: 7)

    monkeypatch.setattr(plugin_test_containment.subprocess, "Popen", command)
    monkeypatch.setattr(plugin_test_containment.signal, "signal", lambda *args: None)
    monkeypatch.setattr(plugin_test_containment.os, "set_blocking", lambda *args: None,
                        raising=False)

    def owner_alive(*args):
        raise BlockingIOError

    monkeypatch.setattr(plugin_test_containment.os, "read", owner_alive)
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


@pytest.mark.parametrize("during_report", [False, True])
def test_posix_worker_reaps_abandoned_group_even_if_status_write_races_owner_exit(
    tmp_path, monkeypatch, during_report,
):
    ready = tmp_path / "ready"
    ready.touch()
    monkeypatch.setattr(plugin_test_containment.os, "set_blocking", lambda *args: None,
                        raising=False)
    monkeypatch.setattr(plugin_test_containment.signal, "signal", lambda *args: None)

    def owner(*args):
        if during_report:
            raise BlockingIOError
        return b""

    def report(*args):
        raise BrokenPipeError

    def abandoned():
        raise RuntimeError("abandoned group reaped")

    monkeypatch.setattr(plugin_test_containment.os, "read", owner)
    monkeypatch.setattr(plugin_test_containment.os, "write", report)
    monkeypatch.setattr(plugin_test_containment.subprocess, "Popen",
                        lambda *a, **k: SimpleNamespace(poll=lambda: 0))
    monkeypatch.setattr(plugin_test_containment, "_terminate_abandoned_posix_group", abandoned)
    with pytest.raises(RuntimeError, match="abandoned group reaped"):
        plugin_test_containment._posix_worker(["command"], ready, 12, 13)


def test_abandoned_controller_keeps_its_identity_through_kill_escalation(monkeypatch):
    events = []
    monkeypatch.setattr(plugin_test_containment.os, "getpid", lambda: 12345)
    monkeypatch.setattr(plugin_test_containment, "_posix_group_identity",
                        lambda pid: events.append("identity") or (pid, pid))
    monkeypatch.setattr(plugin_test_containment.signal, "signal",
                        lambda sig, handler: events.append("ignore-term"))
    monkeypatch.setattr(plugin_test_containment.os, "killpg",
                        lambda pid, sig: events.append(sig), raising=False)
    monkeypatch.setattr(plugin_test_containment.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(plugin_test_containment.time, "sleep",
                        lambda seconds: events.append(("grace", seconds)))
    plugin_test_containment._terminate_abandoned_posix_group()
    assert events == [
        "identity", "ignore-term", plugin_test_containment.signal.SIGTERM,
        ("grace", 2), "identity", 9,
    ]


@pytest.mark.parametrize("name", [b"caf\xc3\xa9", b"invalid\xff"])
def test_linux_membership_ignores_unrelated_process_name_encoding(tmp_path, monkeypatch, name):
    process = tmp_path / "99"
    process.mkdir()
    (process / "stat").write_bytes(b"99 (" + name + b") S 1 42 42 0\n")
    path = Path
    monkeypatch.setattr(plugin_test_containment, "Path",
                        lambda value: tmp_path if value == "/proc" else path(value))
    monkeypatch.setattr(plugin_test_containment.os, "sysconf", lambda name: 4096, raising=False)
    assert plugin_test_containment._linux_group_usage(7, live_only=True) == (0, 0)


def _assert_posix_owner_death_reaps_group(root, command, *, boundary):
    root.mkdir()
    owner_code = """
import os,sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from tools import plugin_test_containment as containment
root=Path(sys.argv[2])
identity=containment._posix_group_identity
def record(pid):
    result=identity(pid)
    (root/'controller').write_text(str(pid))
    return result
containment._posix_group_identity=record
observe=containment._posix_exit_code
def status(fd):
    result=observe(fd)
    if result is not None and sys.argv[4]=='reported':
        (root/'reported').touch()
        time.sleep(60)
    return result
containment._posix_exit_code=status
killpg=os.killpg
def signal_group(pid,sig):
    killpg(pid,sig)
    if sig==containment.signal.SIGTERM and sys.argv[4]=='term':
        (root/'termed').touch()
        time.sleep(60)
os.killpg=signal_group
if sys.argv[4]=='membership-failure':
    def unavailable(pid):
        raise PermissionError('controlled membership observation failure')
    containment._posix_live_group_usage=unavailable
try:
    containment.run_contained(
        [sys.executable,'-c',sys.argv[3],str(root/'held')],
        cwd=root,env=containment.isolated_environment(os.environ,root),sandbox=root,
        limits=containment.Limits(wall_seconds=20,max_processes=8,max_memory_mb=256,max_temp_mb=32),
    )
except containment.UnreapedProcessError:
    (root/'unreaped').touch()
    raise
"""
    owner = subprocess.Popen(
        [sys.executable, "-c", owner_code, str(Path(__file__).resolve().parents[1]),
         str(root), command, boundary],
        **plugin_test_containment.no_window_kwargs(),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    controller = None
    try:
        marker = root / {
            "reported": "reported", "term": "termed", "executing": "held",
            "membership-failure": "unreaped",
        }[boundary]
        deadline = time.monotonic() + 8
        while not marker.exists():
            assert owner.poll() is None or marker.exists(), "owner exited before the test boundary"
            assert time.monotonic() < deadline, "controller did not reach the test boundary"
            time.sleep(0.01)
        controller = int((root / "controller").read_text())
        if boundary == "membership-failure":
            assert owner.wait(timeout=3) != 0
        else:
            owner.kill()
            owner.wait(timeout=3)
        deadline = time.monotonic() + 8
        while plugin_test_containment._posix_live_group_usage(controller)[0]:
            assert time.monotonic() < deadline, "abandoned group remained live"
            time.sleep(0.05)
        (root / "held").unlink()
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.wait(timeout=3)
        if controller is not None:
            usage = plugin_test_containment._posix_live_group_usage(controller)
            if usage and usage[0]:
                if plugin_test_containment._posix_group_identity(controller) == (
                    controller, controller,
                ):
                    os.killpg(controller, plugin_test_containment.signal.SIGKILL)


@pytest.mark.parametrize("hang_controller", [False, True])
def test_file_holding_descendant_is_reaped_before_return(tmp_path, hang_controller):
    held = tmp_path / "held.txt"
    program = (
        "import os,pathlib,subprocess,sys,time;"
        "p=pathlib.Path(sys.argv[1]);"
        "child='import signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);"
        "f=open(sys.argv[1],\"w\");"
        "f.write(\"ready\");f.flush();time.sleep(60)';"
        "subprocess.Popen([sys.executable,'-c',child,str(p)],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,"
        "creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));\n"
        "if os.name != 'nt': "
        "pathlib.Path(str(p)+'.pgid').write_text(str(os.getpgrp()))\n"
        "while not p.exists() or p.stat().st_size < 5: time.sleep(0.01)\n"
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
        _assert_posix_owner_death_reaps_group(
            tmp_path / "owner-death", program,
            boundary="executing" if hang_controller else "reported",
        )
        if not hang_controller:
            _assert_posix_owner_death_reaps_group(
                tmp_path / "term-owner-death", program, boundary="term",
            )
            _assert_posix_owner_death_reaps_group(
                tmp_path / "membership-failure", program, boundary="membership-failure",
            )
    held.unlink()
    assert not held.exists()


@pytest.mark.parametrize("fault", ["wait", "kill", "cancel"])
def test_controller_reaping_failure_is_explicit(fault):
    class Process:
        def wait(self, *, timeout):
            if fault == "cancel":
                raise KeyboardInterrupt
            raise subprocess.TimeoutExpired("controller", timeout)

        def kill(self):
            if fault == "kill":
                raise OSError("kill failed")

    with pytest.raises(plugin_test_containment.UnreapedProcessError, match="could not be reaped"):
        plugin_test_containment._reap_controller(Process())


@pytest.mark.parametrize("failure", ["assignment", "terminate", "wait"])
def test_windows_assignment_failure_cannot_claim_unreaped_controller_is_safe(
    tmp_path, monkeypatch, failure,
):
    events = []

    class Process:
        pid = 12345

        def terminate(self):
            events.append("terminate")
            if failure == "terminate":
                raise OSError("terminate failed")

        def wait(self, *, timeout):
            events.append("wait")
            if failure == "wait":
                raise subprocess.TimeoutExpired("controller", timeout)
            return 1

        def kill(self):
            events.append("kill")

    class Job:
        def __init__(self, limits):
            pass

        def assign(self, pid):
            raise plugin_test_containment.ContainmentError("assignment failed")

        def close(self):
            events.append("job-close")

    monkeypatch.setattr(plugin_test_containment, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(plugin_test_containment, "_windows_stdio_kwargs", lambda: {})
    monkeypatch.setattr(plugin_test_containment.subprocess, "Popen", lambda *a, **k: Process())
    monkeypatch.setattr(plugin_test_containment, "_WindowsJob", Job)
    expected = (
        plugin_test_containment.ContainmentError if failure == "assignment"
        else plugin_test_containment.UnreapedProcessError
    )
    with pytest.raises(expected) as raised:
        plugin_test_containment._run_contained_process(
            ["fake"], cwd=tmp_path, env={}, sandbox=tmp_path, limits=Limits(),
        )
    assert type(raised.value) is expected
    assert "terminate" in events
    assert "job-close" in events
    assert "wait" in events
    assert not (tmp_path / ".containment-ready").exists()


def test_native_cancellation_reaps_file_holding_descendant(tmp_path, monkeypatch):
    held = tmp_path / "cancel-held.txt"
    original_sleep = plugin_test_containment.time.sleep

    def cancel_after_child_ready(seconds):
        if held.exists() and held.read_text() == "ready":
            raise KeyboardInterrupt
        original_sleep(seconds)

    monkeypatch.setattr(plugin_test_containment.time, "sleep", cancel_after_child_ready)
    # Restore ordinary sleeps for teardown after the injected cancellation.
    terminate_posix = plugin_test_containment._terminate_posix_group

    def terminate(*args):
        monkeypatch.setattr(plugin_test_containment.time, "sleep", original_sleep)
        return terminate_posix(*args)

    monkeypatch.setattr(plugin_test_containment, "_terminate_posix_group", terminate)
    job_close = plugin_test_containment._WindowsJob.close

    def close(job):
        monkeypatch.setattr(plugin_test_containment.time, "sleep", original_sleep)
        return job_close(job)

    monkeypatch.setattr(plugin_test_containment._WindowsJob, "close", close)
    result = plugin_test_containment.run_contained(
        [sys.executable, "-c",
         "import sys,time; f=open(sys.argv[1],'w'); f.write('ready'); "
         "f.flush(); time.sleep(60)", str(held)],
        cwd=tmp_path, env=isolated_environment(os.environ, tmp_path), sandbox=tmp_path,
        limits=Limits(wall_seconds=10, max_processes=8, max_memory_mb=256, max_temp_mb=32),
    )
    assert result == 130
    assert held.read_text() == "ready"
    held.unlink()


@pytest.mark.parametrize("unreaped", [False, True])
@pytest.mark.parametrize("audit_error", [
    PermissionError("registry unreadable"), RuntimeError("audit failed"),
    KeyboardInterrupt("audit interrupted"),
])
def test_environment_audit_failure_cannot_mask_unreaped_retention(
    tmp_path, monkeypatch, unreaped, audit_error,
):
    original = plugin_test_containment.UnreapedProcessError("tree remains owned")
    sandbox = plugin_test_containment.TestSandbox(dir=tmp_path)

    def command(*args, **kwargs):
        (kwargs["sandbox"] / "still-owned").write_text("preserve")
        if unreaped:
            raise original
        return 0

    def audit(snapshot):
        raise audit_error

    monkeypatch.setattr(plugin_test_containment, "_read_registry_environment", lambda: None)
    monkeypatch.setattr(plugin_test_containment, "_registry_environment_drift", audit)
    monkeypatch.setattr(plugin_test_containment, "_run_contained_process", command)
    expected = plugin_test_containment.UnreapedProcessError if unreaped else type(audit_error)
    with pytest.raises(expected) as raised:
        with sandbox as directory:
            plugin_test_containment.run_contained(
                ["fake"], cwd=tmp_path, env={}, sandbox=Path(directory), limits=Limits(),
            )
    try:
        assert sandbox.retained is unreaped
        if unreaped:
            assert (Path(sandbox.name) / "still-owned").read_text() == "preserve"
            assert raised.value.__cause__ is original
            assert "environment audit also failed" in str(raised.value)
            assert type(audit_error).__name__ in str(raised.value)
        else:
            assert raised.value is audit_error
            assert not Path(sandbox.name).exists()
    finally:
        if Path(sandbox.name).exists():
            shutil.rmtree(sandbox.name)


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
