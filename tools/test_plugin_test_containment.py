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
