"""Focused contract tests for repository test-runner admission."""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import AbstractContextManager
from pathlib import Path
from types import ModuleType, SimpleNamespace, TracebackType
from typing import TYPE_CHECKING, Any, ParamSpec, Protocol, TypeVar, cast

import pytest
from _pytest.mark.expression import Expression
from _pytest.mark import KeywordMatcher

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

P = ParamSpec("P")
T = TypeVar("T")
R = TypeVar("R")


class GroupPlan(Protocol):
    def __call__(
        self, name: str, groups: list[list[Path]], *, windows: bool | None = None,
    ) -> list[tuple[list[Path], str | None]]: ...


CodespacesPlan = tuple[Path, Path, GroupPlan]
ManagerAcceptance = tuple[ModuleType, list[bool]]


def recorded(calls: list[T], value: T, result: R) -> R:
    calls.append(value)
    return result

SCRIPT = Path(__file__).resolve().parent / "run-plugin-tests.py"
_previous_path = sys.path.copy()
sys.path.insert(0, str(SCRIPT.parent))
try:
    _spec = importlib.util.spec_from_file_location("run_plugin_tests", SCRIPT)
    assert _spec and _spec.loader
    runner: Any = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = runner
    _spec.loader.exec_module(runner)

    import plugin_test_containment as containment
    import pytest_portfolio_guard as portfolio_guard
    import _admission_protocol as admission
    import _devcontainer_network_scope as network_scope
    import plugin_test_owned_scratch as owned_scratch
    if TYPE_CHECKING:
        from plugin_test_containment import TestSandbox as _TestSandbox
    else:
        _TestSandbox = runner._TestSandbox
finally:
    sys.path[:] = _previous_path


@pytest.fixture(autouse=True)
def runner_scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(tmp_path.parent / "runner-scratch"))


@pytest.mark.parametrize("owner,code,now,expected", [
    (None, 7, 11.0, []),
    ("machine/project/worktree", 0, 9.0, [("outcome", 0)]),
    ("machine/project/worktree", 7, 10.0, [("outcome", 7)]),
    ("machine/project/worktree", 0, 11.0, [("group:0", 124), ("outcome", 124)]),
])
def test_owned_scratch_completion_keeps_owner_and_deadline_gates(
    owner: str | None, code: int, now: float, expected: list[tuple[str, int]],
) -> None:
    events: list[tuple[str, int]] = []

    class Scope(AbstractContextManager[str]):
        def __enter__(self) -> str:
            return "unused"

        def __exit__(
            self, exc_type: type[BaseException] | None, exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            pass

        def record_result(self, group: int, result: int) -> None:
            events.append((f"group:{group}", result))

        def record_outcome(self, result: int) -> None:
            events.append(("outcome", result))

    with owned_scratch.finish_owned_run(owner, Scope(), lambda: code, 10.0, lambda: now):
        assert not events
    assert events == expected


@pytest.mark.parametrize("budget,status", [(None, 0), (12.0, 0), (2.0, 0), (0.0, 0), (2.0, 7)])
def test_owned_preparation_preserves_limits_and_failure_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, budget: float | None, status: int,
) -> None:
    calls: list[tuple[list[str], containment.Limits]] = []
    limits = containment.Limits(wall_seconds=10.0, max_processes=17, max_memory_mb=256, max_temp_mb=128)

    def execute(command: list[str], **kwargs: Any) -> int:
        calls.append((command, kwargs["limits"]))
        assert kwargs["sandbox"] == tmp_path
        assert kwargs["env"] == {"FIXTURE": "only"}
        return status

    monkeypatch.setattr(owned_scratch, "run_contained", execute)

    def prepare() -> None:
        owned_scratch.prepare_owned(
            "fixture", "fixture-uv", False, retained=tmp_path / "retained",
            environment={"FIXTURE": "only"}, sandbox=str(tmp_path), limits=limits, wall_seconds=budget,
        )

    if budget == 0:
        with pytest.raises(containment.ContainmentError, match="before worker launch"):
            prepare()
        assert not calls
    else:
        if status:
            with pytest.raises(subprocess.CalledProcessError) as error:
                prepare()
            assert error.value.returncode == status
        else:
            prepare()
        command, selected = calls[0]
        assert command[2:] == ["fixture", "fixture-uv", str(tmp_path / "retained"), "reuse"]
        assert selected.wall_seconds == (10.0 if budget is None else min(10.0, budget))
        assert (selected.max_processes, selected.max_memory_mb, selected.max_temp_mb) == (17, 256, 128)


def test_unreaped_tree_retains_sandbox_without_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    plugin = tmp_path / "alpha"
    sandbox_paths = []

    def run_contained(*args: Any, **kwargs: Any) -> int:
        sandbox_paths.append(kwargs["sandbox"])
        (kwargs["sandbox"] / "still-owned.txt").write_text("preserve")
        raise runner.UnreapedProcessError("forced teardown failure")

    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(runner, "_has_suite", lambda name: True)
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: plugin)
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **k: Path("fake-python"))
    monkeypatch.setattr(runner, "_test_file_groups",
                        lambda *a, **k: [[plugin / "tests" / "test_one.py"]])
    monkeypatch.setattr(runner, "run_contained", run_contained)
    with pytest.raises(runner.UnreapedProcessError, match="forced teardown failure"):
        runner.run_plugin(
            "alpha", "uv", reinstall=False, kexpr=None, limits=runner.Limits(),
            plugin_timeout=60, test_timeout=30, max_files_per_subsuite=25,
        )
    sandbox = sandbox_paths[0]
    try:
        assert (sandbox / "still-owned.txt").read_text() == "preserve"
        assert (sandbox / "t").is_dir()
        assert (sandbox / "t" / "g1").is_dir()
        if os.name != "nt":
            assert (sandbox / "t").stat().st_mode & 0o077 == 0
        assert "[KEEP] Unreaped process tree: sandbox retained at" in capsys.readouterr().err
    finally:
        shutil.rmtree(sandbox)


@pytest.fixture
def cleanup_runner(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Callable[[], int]:
    plugin = tmp_path / "alpha"
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(runner, "_has_suite", lambda name: True)
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: plugin)
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **k: Path("fake-python"))
    monkeypatch.setattr(runner, "_test_file_groups", lambda *a, **k: [
        [plugin / "tests" / "test_one.py"], [plugin / "tests" / "test_two.py"],
    ])
    monkeypatch.setattr(runner, "run_contained", lambda *a, **k: 0)

    def run() -> int:
        return cast(int, runner.run_plugin(
            "alpha", "uv", reinstall=False, kexpr=None, limits=runner.Limits(),
            plugin_timeout=60, test_timeout=30, max_files_per_subsuite=25,
        ))

    return run


@pytest.mark.parametrize("emit_report", [False, True])
def test_junit_reports_are_distinct_per_group_and_required_on_success(
    cleanup_runner: Callable[[], int], monkeypatch: pytest.MonkeyPatch, tmp_path: Path, emit_report: bool,
) -> None:
    reports = tmp_path / "reports"
    observed = []

    def run_contained(command: list[str], **kwargs: Any) -> int:
        report = Path(next(value.split("=", 1)[1] for value in command if value.startswith("--junitxml=")))
        observed.append(report)
        assert report.parent == reports / "alpha"
        assert not report.exists()
        assert kwargs["limits"].wall_seconds <= 60
        assert command[command.index("-p") + 1] == runner.PORTFOLIO_PLUGIN
        assert "--timeout=30" in command
        if emit_report:
            report.write_text("<testsuites/>", encoding="utf-8")
        return 0

    monkeypatch.setattr(runner, "run_contained", run_contained)

    def execute() -> int:
        return cast(int, runner.run_plugin(
            "alpha", "uv", reinstall=False, kexpr=None, limits=runner.Limits(),
            plugin_timeout=60, test_timeout=30, max_files_per_subsuite=25,
            junit_dir=reports,
        ))

    if emit_report:
        assert execute() == 0
        assert [path.name for path in observed] == ["group-001.xml", "group-002.xml"]
        assert all(path.is_file() for path in observed)
    else:
        with pytest.raises(runner.ContainmentError, match="no requested JUnit report"):
            execute()


@pytest.mark.parametrize("arguments", [
    ["alpha", "--prepare-only"], ["--all", "--list"],
])
def test_junit_reporting_refuses_non_execution_modes(arguments: list[str], tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        runner.main([*arguments, "--junit-dir", str(tmp_path / "reports")])
    assert error.value.code == 2
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(os.name != "nt", reason="native Windows short scratch alias")
def test_short_temp_alias_reaches_real_daemon_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cleanup_runner: Callable[[], int],
) -> None:
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    length = getattr(ctypes, "windll").kernel32.GetShortPathNameW(str(tmp_path), buffer, len(buffer))
    if not length or length >= len(buffer) or Path(buffer.value) == tmp_path:
        pytest.skip("filesystem has no short alias for the temporary parent")
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", buffer.value)
    observed = []

    def run_contained(*args: Any, **kwargs: Any) -> int:
        from agent_procutil.daemon_instances import daemon_test_lifecycle

        sandbox = kwargs["sandbox"]
        assert sandbox == sandbox.resolve(strict=True)
        with daemon_test_lifecycle(
            owner="short-temp-regression", registry_root=sandbox / "daemon-instances",
        ):
            observed.append(sandbox)
        return 0

    monkeypatch.setattr(runner, "run_contained", run_contained)
    assert cleanup_runner() == 0
    assert observed


@pytest.mark.parametrize("noncanonical", [False, True])
def test_temp_parent_is_canonical_before_sandbox_and_native_admission(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cleanup_runner: Callable[[], int],
    noncanonical: bool,
) -> None:
    from agent_procutil import _daemon_instance_store as store

    nested = tmp_path / "nested"
    nested.mkdir()
    requested = nested / ".." if noncanonical else tmp_path
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(requested))
    parents = []

    class CanonicalSandbox(_TestSandbox):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            parent = Path(kwargs["dir"])
            assert parent == parent.resolve(strict=True)
            parents.append(parent)
            cast(Callable[..., None], super().__init__)(*args, **kwargs)

    def admitted(*args: Any, **kwargs: Any) -> int:
        root = kwargs["sandbox"] / "daemon-instances"
        assert store._root(root, create=True) == root
        return 0

    monkeypatch.setattr(runner, "_TestSandbox", CanonicalSandbox)
    monkeypatch.setattr(runner, "run_contained", admitted)
    assert cleanup_runner() == 0
    assert parents == [runner.runs_root(runner.REPO)]


def test_plugin_budget_includes_final_sandbox_cleanup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cleanup_runner: Callable[[], int],
) -> None:
    clock = [0.0]
    class Directory(_TestSandbox):
        def __exit__(self, *args: Any) -> None:
            cast(Callable[..., object], super().__exit__)(*args)
            clock[0] = 61.0

    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner, "_TestSandbox", Directory)
    assert cleanup_runner() == 124
    assert "including sandbox cleanup: 61.00s elapsed" in capsys.readouterr().err


def test_budget_exhausted_between_groups_still_reports_final_cleanup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cleanup_runner: Callable[[], int],
) -> None:
    clock = [0.0]
    calls = []
    class Directory(_TestSandbox):
        def __exit__(self, *args: Any) -> None:
            cast(Callable[..., object], super().__exit__)(*args)
            clock[0] = 65.0

    def run(*args: Any, **kwargs: Any) -> int:
        calls.append(args)
        clock[0] = 61.0
        return 0

    monkeypatch.setattr(runner, "run_contained", run)
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner, "_TestSandbox", Directory)
    assert cleanup_runner() == 124
    assert len(calls) == 1
    assert "including sandbox cleanup: 65.00s elapsed" in capsys.readouterr().err


def test_preparation_uses_the_same_aggregate_budget(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], cleanup_runner: Callable[[], int],
) -> None:
    clock = [0.0]
    calls = []

    def prepare(*args: Any, **kwargs: Any) -> Path:
        clock[0] = 61.0
        return Path("fake-python")

    monkeypatch.setattr(runner, "_ensure_venv", prepare)
    monkeypatch.setattr(runner, "run_contained", lambda *a, **k: calls.append(a))
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    assert cleanup_runner() == 124
    assert calls == []
    assert "(preparation included)" in capsys.readouterr().err


def test_reaped_group_cleanup_overlaps_the_next_group(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    cleanup_runner: Callable[[], int],
) -> None:
    started = threading.Event()
    release = threading.Event()
    removed = []
    workers = set()
    original_remove = runner.tempfile.TemporaryDirectory._rmtree
    calls = []

    def remove(name: str, *args: Any, **kwargs: Any) -> None:
        if Path(name).name in {"g1", "g2"}:
            workers.add(threading.get_ident())
        if Path(name).name == "g1":
            started.set()
            assert release.wait(5), "next group never released cleanup"
        original_remove(name, *args, **kwargs)
        removed.append(Path(name))

    def run_contained(command: list[str], **kwargs: Any) -> int:
        calls.append(command)
        group = Path(next(arg.split("=", 1)[1] for arg in command
                          if arg.startswith("--basetemp=")))
        assert group.is_dir()
        (group / "owned.txt").write_text("disposable")
        cache = kwargs["sandbox"] / "s" / "example" / "data"
        if len(calls) == 1:
            cache.parent.mkdir(parents=True)
            cache.write_bytes(b"immutable shared seed")
            assert containment._tree_size(kwargs["sandbox"]) >= cache.stat().st_size
        else:
            try:
                assert started.wait(5), "cleanup did not overlap the next group"
                assert cache.read_bytes() == b"immutable shared seed"
            finally:
                release.set()
        return 0

    monkeypatch.setattr(runner, "run_contained", run_contained)
    monkeypatch.setattr(runner.tempfile.TemporaryDirectory, "_rmtree", staticmethod(remove))
    try:
        assert cleanup_runner() == 0
    finally:
        release.set()
    assert len(calls) == 2
    assert [path.name for path in removed[:2]] == ["g1", "g2"]
    assert len(workers) == 1
    assert removed and not any(path.exists() for path in removed)
    assert "including preparation and cleanup" in capsys.readouterr().out


@pytest.mark.parametrize("interruption", [None, KeyboardInterrupt, runner.UnreapedProcessError])
def test_cleanup_failure_is_explicit_and_cannot_mask_unreaped_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    cleanup_runner: Callable[[], int], interruption: type[BaseException] | None,
) -> None:
    original_remove = runner.tempfile.TemporaryDirectory._rmtree
    roots = []
    groups = []

    def remove(name: str, *args: Any, **kwargs: Any) -> None:
        if Path(name).name == "g1":
            raise OSError("group removal refused")
        original_remove(name, *args, **kwargs)

    def run(command: list[str], **kwargs: Any) -> int:
        roots.append(kwargs["sandbox"])
        group = Path(next(arg.split("=", 1)[1] for arg in command
                          if arg.startswith("--basetemp=")))
        groups.append(group)
        (group / "owned.txt").write_text("preserve")
        if len(groups) == 2 and interruption is not None:
            raise interruption("second group interrupted")
        return 0

    monkeypatch.setattr(runner, "run_contained", run)
    monkeypatch.setattr(runner.tempfile.TemporaryDirectory, "_rmtree", staticmethod(remove))
    try:
        with pytest.raises(interruption or OSError):
            cleanup_runner()
        assert "Group sandbox cleanup failed: group removal refused" in capsys.readouterr().err
        if interruption is runner.UnreapedProcessError:
            assert (groups[1] / "owned.txt").read_text() == "preserve"
        else:
            assert not roots[0].exists()
    finally:
        if roots and roots[0].exists():
            shutil.rmtree(roots[0])


@pytest.mark.parametrize("returncode", [1, 5])
def test_failed_group_is_cleaned_without_starting_another(
    monkeypatch: pytest.MonkeyPatch, cleanup_runner: Callable[[], int], returncode: int,
) -> None:
    roots = []

    def run(*args: Any, **kwargs: Any) -> int:
        roots.append(kwargs["sandbox"])
        return returncode

    monkeypatch.setattr(runner, "run_contained", run)
    assert cleanup_runner() == returncode
    assert len(roots) == 1
    assert not roots[0].exists()


@pytest.mark.parametrize("seam", ["wait", "shutdown", "both"])
@pytest.mark.parametrize("unreaped", [False, True])
def test_cleanup_wait_interruptions_join_before_preserving_ownership_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    cleanup_runner: Callable[[], int], seam: str, unreaped: bool,
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    roots = []
    calls = []
    interruptions = []
    original_remove = runner.tempfile.TemporaryDirectory._rmtree

    def remove(name: str, *args: Any, **kwargs: Any) -> None:
        if Path(name).name == "g1":
            started.set()
            assert release.wait(5), "cleanup was not released after interruption"
        original_remove(name, *args, **kwargs)
        if Path(name).name == "g1":
            finished.set()

    class Pool(ThreadPoolExecutor):
        def submit(self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> Future[T]:
            future = super().submit(fn, *args, **kwargs)
            result = future.result

            def interrupt_once(*args: Any, **kwargs: Any) -> T:
                if seam in {"wait", "both"} and "wait" not in interruptions:
                    assert not future.done()
                    interruptions.append("wait")
                    raise KeyboardInterrupt("cleanup wait interrupted")
                release.set()
                return result(*args, **kwargs)

            monkeypatch.setattr(future, "result", interrupt_once)
            return future

        def shutdown(self, *args: Any, **kwargs: Any) -> None:
            if seam in {"shutdown", "both"} and "shutdown" not in interruptions:
                assert finished.is_set()
                interruptions.append("shutdown")
                raise KeyboardInterrupt("executor shutdown interrupted")
            return super().shutdown(*args, **kwargs)

    def run(command: list[str], **kwargs: Any) -> int:
        roots.append(kwargs["sandbox"])
        calls.append(command)
        group = Path(next(arg.split("=", 1)[1] for arg in command
                          if arg.startswith("--basetemp=")))
        (group / "owned.txt").write_text("preserve")
        if len(calls) == 2:
            assert started.wait(5)
            if unreaped:
                raise runner.UnreapedProcessError("second group still owns its files")
        return 0

    monkeypatch.setattr(runner, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(runner, "run_contained", run)
    monkeypatch.setattr(runner.tempfile.TemporaryDirectory, "_rmtree", staticmethod(remove))
    try:
        expected = runner.UnreapedProcessError if unreaped else KeyboardInterrupt
        with pytest.raises(expected):
            cleanup_runner()
        assert finished.is_set()
        assert interruptions == (["wait", "shutdown"] if seam == "both" else [seam])
        if unreaped:
            assert (roots[0] / "t" / "g2" / "owned.txt").read_text() == "preserve"
        else:
            assert not roots[0].exists()
        assert "[PASS]" not in capsys.readouterr().out
    finally:
        release.set()
        if roots and roots[0].exists():
            shutil.rmtree(roots[0])


@pytest.mark.parametrize("cancellation", [None, KeyboardInterrupt, SystemExit])
def test_final_sandbox_cleanup_failure_cannot_mask_cancellation_or_report_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    cleanup_runner: Callable[[], int], cancellation: type[BaseException] | None,
) -> None:
    original_remove = runner.tempfile.TemporaryDirectory._rmtree
    roots = set()

    def remove(name: str, *args: Any, **kwargs: Any) -> None:
        if Path(name).name.startswith(runner.run_prefix("alpha")):
            roots.add(Path(name))
            assert not kwargs.get("ignore_errors", False)
            raise OSError("final sandbox removal refused")
        original_remove(name, *args, **kwargs)

    monkeypatch.setattr(runner.tempfile.TemporaryDirectory, "_rmtree", staticmethod(remove))
    if cancellation is not None:
        def cancel(*args: Any, **kwargs: Any) -> int:
            raise cancellation("stop runner")

        monkeypatch.setattr(runner, "run_contained", cancel)
    try:
        with pytest.raises(cancellation or OSError):
            cleanup_runner()
        output = capsys.readouterr()
        assert "[PASS]" not in output.out
        if cancellation is not None:
            assert "Sandbox cleanup failed: final sandbox removal refused" in output.err
    finally:
        for root in roots:
            if root.exists():
                shutil.rmtree(root)


@pytest.mark.parametrize("seam", ["wait", "shutdown"])
@pytest.mark.parametrize("pending", [None, OSError, runner.UnreapedProcessError])
@pytest.mark.parametrize("cancellation", [KeyboardInterrupt, SystemExit])
def test_cleanup_cancellation_precedes_errors_but_not_unreaped_ownership(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cleanup_runner: Callable[[], int],
    seam: str, pending: type[BaseException] | None, cancellation: type[BaseException],
) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    roots = []
    calls = []
    interrupted: list[str] = []
    original_remove = runner.tempfile.TemporaryDirectory._rmtree
    plugin = tmp_path / "alpha"
    monkeypatch.setattr(runner, "_test_file_groups", lambda *a, **k: [
        [plugin / "tests" / f"test_{index}.py"] for index in range(3)
    ])

    def remove(name: str, *args: Any, **kwargs: Any) -> None:
        if Path(name).name == "g1":
            raise OSError("first removal refused")
        if Path(name).name == "g2":
            started.set()
            assert release.wait(5)
        original_remove(name, *args, **kwargs)
        if Path(name).name == "g2":
            finished.set()

    class Pool(ThreadPoolExecutor):
        def submit(self, fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> Future[T]:
            name = args[0]
            assert isinstance(name, (str, os.PathLike))
            future = super().submit(fn, *args, **kwargs)
            if Path(name).name == "g2":
                result = future.result

                def interrupt_once(*args: Any, **kwargs: Any) -> T:
                    if seam == "wait" and not interrupted:
                        assert not future.done()
                        interrupted.append(seam)
                        raise cancellation("cancel during wait")
                    release.set()
                    return result(*args, **kwargs)

                monkeypatch.setattr(future, "result", interrupt_once)
            return future

        def shutdown(self, *args: Any, **kwargs: Any) -> None:
            if seam == "shutdown" and not interrupted:
                assert finished.is_set()
                interrupted.append(seam)
                raise cancellation("cancel during shutdown")
            return super().shutdown(*args, **kwargs)

    def run(command: list[str], **kwargs: Any) -> int:
        roots.append(kwargs["sandbox"])
        calls.append(command)
        group = Path(next(arg.split("=", 1)[1] for arg in command
                          if arg.startswith("--basetemp=")))
        (group / "owned.txt").write_text("preserve")
        if len(calls) == 3:
            assert started.wait(5)
            if pending is not None:
                raise pending("third group failed")
        return 0

    monkeypatch.setattr(runner, "ThreadPoolExecutor", Pool)
    monkeypatch.setattr(runner, "run_contained", run)
    monkeypatch.setattr(runner.tempfile.TemporaryDirectory, "_rmtree", staticmethod(remove))
    try:
        expected = pending if pending is runner.UnreapedProcessError else cancellation
        with pytest.raises(expected):
            cleanup_runner()
        assert interrupted == [seam]
        assert finished.is_set()
        if pending is runner.UnreapedProcessError:
            assert (roots[0] / "t" / "g3" / "owned.txt").read_text() == "preserve"
        else:
            assert not roots[0].exists()
    finally:
        release.set()
        if roots and roots[0].exists():
            shutil.rmtree(roots[0])


def test_real_group_processes_release_readonly_files_before_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, cleanup_runner: Callable[[], int],
) -> None:
    plugin = tmp_path / "alpha"
    tests = plugin / "tests"
    tests.mkdir(parents=True)
    body = (
        "import stat\n"
        "def test_owned_file(tmp_path):\n"
        "    owned = tmp_path / 'readonly.txt'\n"
        "    owned.write_text('owned')\n"
        "    owned.chmod(stat.S_IREAD)\n"
    )
    for name in ("test_one.py", "test_two.py"):
        (tests / name).write_text(body, encoding="utf-8")
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **k: Path(sys.executable))
    roots = []
    groups = []

    def run(command: list[str], **kwargs: Any) -> int:
        roots.append(kwargs["sandbox"])
        group = Path(next(arg.split("=", 1)[1] for arg in command
                          if arg.startswith("--basetemp=")))
        groups.append(group)
        rc = containment.run_contained(command, **kwargs)
        assert list(group.rglob("readonly.txt"))
        return rc

    monkeypatch.setattr(runner, "run_contained", run)
    assert cleanup_runner() == 0
    assert len(groups) == 2 and groups[0] != groups[1]
    assert not roots[0].exists()


@pytest.mark.parametrize("reinstall", [False, True])
@pytest.mark.parametrize("cached", [False, True])
def test_explicit_reinstall_invalidates_uv_cached_path_dependency_wheels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reinstall: bool, cached: bool,
) -> None:
    import plugin_test_environment as preparation
    monkeypatch.setattr(preparation, "capture_preparation_auth",
                        lambda *args, **kwargs: {"UV_CREDENTIALS_DIR": str(tmp_path / "fixture-auth")})
    plugin = tmp_path / "plugin"
    plugin.mkdir()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    monkeypatch.setattr(runner, "_has_pyproject", lambda _name: True)
    monkeypatch.setattr(runner, "has_dev_extra", lambda _project: True)
    monkeypatch.setattr(runner, "_dep_fingerprint", lambda _name: "current")
    root = runner.VENV_ROOT / "example"
    root.mkdir(parents=True)
    python = root / "fixture-python"
    monkeypatch.setattr(runner, "_venv_python", lambda _name: python)
    if cached:
        python.touch()
        (root / ".dep-fingerprint").write_text("current", encoding="utf-8")
    calls = []

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if command[:2] == ["uv", "venv"]:
            python.touch()
        return subprocess.CompletedProcess(command, 0)

    def install(uv: str, arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        environment = kwargs.pop("env")
        private = Path(environment["COPILOT_EXTENSIONS_TEST_SANDBOX"])
        for key in ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME"):
            assert Path(environment[key]).is_relative_to(private)
        assert environment["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert "COPILOT_EXTENSIONS_CONTEXT" not in environment
        assert kwargs == {
            "cwd": plugin, "trusted_source_roots": (runner.REPO,), "check": True,
            "retained_resource_roots": (),
        }
        return run([uv, "pip", "install", *arguments])

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner, "run_uv_install", install)
    assert runner._ensure_venv("example", "uv", reinstall=reinstall) == python
    installs = [command for command in calls if command[:3] == ["uv", "pip", "install"]]
    assert len(installs) == int(reinstall or not cached)
    if installs:
        assert installs[0].count("--reinstall") == int(reinstall)
        assert installs[0][3:5] == ["--python", str(python)]
        assert installs[0][5:7] == ["-e", ".[dev]"]


@pytest.fixture
def selectable_tests(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    tests = tmp_path / "tests"
    (tests / "nested").mkdir(parents=True)
    for name in ("test_selected.py", "test_unrelated.py", "nested/test_child.py"):
        (tests / name).write_text("def test_example(): pass\n", encoding="utf-8")
    return tests


def test_source_tooling_selects_declared_dev_not_runtime_or_first_party_core(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    manager = tomllib.loads((runner.REPO / "worktree-manager/pyproject.toml").read_text())
    dev = manager["project"]["optional-dependencies"]["dev"]
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="tooling-fixture"\nversion="1"\n'
        'dependencies=["runtime-must-not-be-selected"]\n'
        '[project.optional-dependencies]\ndev=' + json.dumps(dev) + '\n'
        '[dependency-groups]\nfirst-party-core=["core-must-not-be-selected"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    selected = runner.declared_test_tooling_requirements("installation-context")
    assert set(dev) <= set(selected)
    assert "pydantic>=2" in selected
    assert "runtime-must-not-be-selected" not in selected
    assert "core-must-not-be-selected" not in selected


def test_declared_manager_tooling_preparation_imports_in_own_runtime() -> None:
    import json
    import os
    import subprocess

    value = os.environ.get("COPILOT_EXTENSIONS_DECLARED_TOOLING_PROOF")
    if not value:
        pytest.skip("Explicit native tooling-preparation proof was not supplied")
    proof = json.loads(Path(value).read_text(encoding="utf-8"))
    assert proof["requirements"] == list(runner.declared_test_tooling_requirements("worktree-manager"))
    assert proof["created_by_real_native_uv"] and proof["canonical_retained_install_succeeded"]
    process = subprocess.run(
        [
            proof["python"], "-I", "-B", "-c",
            "import json,sys,pydantic;from importlib.metadata import version;"
            "assert int(version('pydantic').split('.')[0])>=2;"
            "print(json.dumps({'prefix':sys.prefix,'pydantic':pydantic.__version__,"
            "'module':pydantic.__file__}))",
        ],
        capture_output=True, text=True, check=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    )
    observed = json.loads(process.stdout)
    assert Path(observed["prefix"]).resolve() == Path(proof["environment"]).resolve()
    assert Path(observed["module"]).resolve().is_relative_to(Path(proof["environment"]).resolve())


@pytest.mark.parametrize("extras", ["", '[project.optional-dependencies]\nother=["unselected"]\n'])
def test_source_tooling_falls_back_to_flat_dev_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extras: str,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="tooling-fixture"\nversion="1"\n'
        'dependencies=["runtime-must-not-be-selected"]\n' + extras
        + '[dependency-groups]\ndev=["pydantic>=2"]\n'
        'first-party-core=["core-must-not-be-selected"]\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    selected = runner.declared_test_tooling_requirements("installation-context")
    assert "pydantic>=2" in selected
    assert "runtime-must-not-be-selected" not in selected
    assert "core-must-not-be-selected" not in selected
    assert "unselected" not in selected


@pytest.mark.parametrize("metadata", [
    'project="not-a-table"\n',
    '[project]\noptional-dependencies="not-a-table"\n',
    '[project]\noptional-dependencies=[]\n',
    'dependency-groups="not-a-table"\n',
    'dependency-groups=[]\n',
    '[project.optional-dependencies]\ndev="not-a-list"\n',
    '[project.optional-dependencies]\ndev=[1]\n',
    '[dependency-groups]\ndev="not-a-list"\n',
    '[dependency-groups]\ndev=[1]\n',
    '[dependency-groups]\ndev=[{include-group="other"}]\nother=["pydantic>=2"]\n',
    'dependency-groups=[]\n[project.optional-dependencies]\ndev=["pydantic>=2"]\n',
])
def test_source_tooling_refuses_malformed_metadata_before_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, metadata: str,
) -> None:
    (tmp_path / "pyproject.toml").write_text(metadata, encoding="utf-8")
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    monkeypatch.setattr(runner, "ordinary_cache_root", lambda path: path)
    monkeypatch.setattr(runner, "run_uv_install", lambda *a, **k: pytest.fail("install attempted"))
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: pytest.fail("subprocess attempted"))
    with pytest.raises(ValueError, match="Test tooling requires"):
        runner._prepare_venv("installation-context", "unused-uv", reinstall=False, environment={})


def test_source_only_cache_invalidates_the_previous_tooling_selection() -> None:
    name = "installation-context"
    previous = runner.dependency_fingerprint(
        name, runner._plugin_dir(name), runner.REPO,
        (*runner.RUNNER_DEPENDENCIES, *runner.EXTERNAL_TEST_DEPENDENCIES.get(name, ())),
    )
    assert runner._dep_fingerprint(name) != previous


@pytest.mark.parametrize("name,extra", [
    ("agent-index", "auth"), ("agent-pull-requests", "service-gateway"),
    ("agent-dispatch", "service-gateway"), ("agent-ssh", ""), ("agent-worktrees", "auth"),
    ("agent-vault", "service-gateway"),
])
def test_preparation_selects_same_checkout_fleet_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str, extra: str,
) -> None:
    import plugin_test_environment as preparation
    monkeypatch.setattr(preparation, "capture_preparation_auth",
                        lambda *args, **kwargs: {"UV_CREDENTIALS_DIR": str(tmp_path / "fixture-auth")})
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    plugin = tmp_path / "plugins" / name
    plugin.mkdir(parents=True)
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    (plugin / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\n[project.optional-dependencies]\ndev = ["fleet-manager'
        + (f"[{extra}]" if extra else "") + '>=0.1.0.dev1"]\n',
        encoding="utf-8",
    )
    fleet = tmp_path / "fleet-manager"
    fleet.mkdir()
    metadata = fleet / "pyproject.toml"
    metadata.write_text('[project]\nname = "fleet-manager"\nversion = "0.1.0.dev1"\n', encoding="utf-8")
    for resource in runner.standalone_build_resources("fleet-manager", source_root=tmp_path):
        resource.mkdir(parents=True)
    if name == "agent-dispatch":
        replay = tmp_path / "plugins" / "agent-pull-requests"
        replay.mkdir()
        (replay / "pyproject.toml").write_text(
            '[project]\nname = "agent-pull-requests"\n', encoding="utf-8",
        )
    (runner.VENV_ROOT / name).mkdir(parents=True)
    before = runner._dep_fingerprint(name)
    metadata.write_text('[project]\nname = "fleet-manager"\nversion = "0.1.0.dev2"\n', encoding="utf-8")
    assert runner._dep_fingerprint(name) != before
    calls: list[list[str]] = []
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda command, **kwargs: recorded(calls, command, subprocess.CompletedProcess(command, 0)),
    )
    retained = []

    def prepare(uv: str, arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        retained.append(kwargs["retained_resource_roots"])
        return subprocess.run([uv, "pip", "install", *arguments])

    monkeypatch.setattr(runner, "run_uv_install", prepare)
    runner._ensure_venv(name, "fixture-uv", reinstall=False)
    install = calls[-1]
    if name == "agent-dispatch":
        assert str(tmp_path / "plugins" / "agent-pull-requests") + "[fleet]" in install
    fleet_spec = str(fleet) + (f"[{extra}]" if extra else "")
    assert ["-e", fleet_spec] in [install[index:index + 2] for index in range(len(install) - 1)]
    assert all(requirement in install for requirement in runner.RUNNER_DEPENDENCIES)
    assert not any("http" in argument for argument in install)
    assert retained == [(
        tmp_path / "libs" / "versioned-runtime",
        tmp_path / "libs" / "installer-engine",
        tmp_path / "libs" / "agent-procutil" / "src" / "agent_procutil",
    )]


def test_dispatch_preparation_fingerprints_same_checkout_replay_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: tmp_path / "plugins" / name)
    metadata = tmp_path / "plugins" / "agent-pull-requests" / "pyproject.toml"
    metadata.parent.mkdir(parents=True)
    metadata.write_text('[project]\nname = "agent-pull-requests"\nversion = "1.0"\n', encoding="utf-8")
    fleet = tmp_path / "fleet-manager"
    fleet.mkdir()
    (fleet / "pyproject.toml").write_text('[project]\nname = "fleet-manager"\n', encoding="utf-8")
    for resource in runner.standalone_build_resources("fleet-manager", source_root=tmp_path):
        resource.mkdir(parents=True)
    before = runner._dep_fingerprint("agent-dispatch")
    metadata.write_text('[project]\nname = "agent-pull-requests"\nversion = "1.1"\n', encoding="utf-8")
    assert runner._dep_fingerprint("agent-dispatch") != before


@pytest.mark.parametrize("bridge_fails", [False, True])
@pytest.mark.parametrize("cached_editable", [False, True])
def test_manager_bridge_is_prepared_only_by_the_test_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bridge_fails: bool, cached_editable: bool,
) -> None:
    import plugin_test_environment as preparation
    monkeypatch.setattr(preparation, "capture_preparation_auth",
                        lambda *args, **kwargs: {"UV_CREDENTIALS_DIR": str(tmp_path / "fixture-auth")})
    for key in ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME", "AGENT_BRIDGE_CONFIG_DIR"):
        monkeypatch.setenv(key, str(tmp_path / "host-state"))
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE"):
        monkeypatch.setenv(key, "fixture-host-participant")
    manager = tmp_path / "worktree-manager"
    manager.mkdir()
    (manager / "pyproject.toml").write_text(
        '[project]\nname = "copilot-extensions-worktree-manager"\n'
        '[project.optional-dependencies]\ndev = ["pytest>=8"]\n',
    )
    bridge = tmp_path / "plugins" / "agent-bridge"
    bridge.mkdir(parents=True)
    (bridge / "pyproject.toml").write_text('[project]\nname = "agent-bridge"\nversion = "1.0"\n')
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: manager)
    (runner.VENV_ROOT / "worktree-manager").mkdir(parents=True)
    if cached_editable:
        python = runner._venv_python("worktree-manager")
        python.parent.mkdir(exist_ok=True)
        python.touch()
        previous = runner.dependency_fingerprint(
            "worktree-manager", manager, tmp_path,
            (*runner.RUNNER_DEPENDENCIES,
             *runner.EXTERNAL_TEST_DEPENDENCIES.get("worktree-manager", ())),
        )
        (runner.VENV_ROOT / "worktree-manager" / ".dep-fingerprint").write_text(previous)
    calls = []
    monkeypatch.setattr(
        runner.subprocess, "run",
        lambda command, **kwargs: subprocess.CompletedProcess(command, 0),
    )
    def install(uv: str, arguments: list[str], **kwargs: Any) -> None:
        calls.append((arguments, kwargs))
        if kwargs["cwd"] == bridge and bridge_fails:
            raise subprocess.CalledProcessError(1, ["fixture-uv", *arguments])

    monkeypatch.setattr(runner, "run_uv_install", install)
    if bridge_fails:
        with pytest.raises(subprocess.CalledProcessError):
            runner._ensure_venv("worktree-manager", "fixture-uv", reinstall=False)
        assert not (runner.VENV_ROOT / "worktree-manager" / ".dep-fingerprint").exists()
    else:
        runner._ensure_venv("worktree-manager", "fixture-uv", reinstall=False)
    assert len(calls) == 2
    manager_arguments, manager_options = calls[0]
    assert str(bridge) not in manager_arguments
    assert "-e" not in manager_arguments
    assert ("--reinstall" in manager_arguments) is cached_editable
    assert manager_options["non_editable"] is True
    assert manager_options["cwd"] == manager
    arguments, options = calls[1]
    for installation in (manager_options, options):
        environment = installation["env"]
        private = Path(environment["COPILOT_EXTENSIONS_TEST_SANDBOX"])
        for key in ("HOME", "USERPROFILE", "APPDATA", "XDG_CONFIG_HOME", "AGENT_BRIDGE_CONFIG_DIR"):
            assert Path(environment[key]).is_relative_to(private)
        assert environment["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
        assert "COPILOT_EXTENSIONS_CONTEXT" not in environment
        assert "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE" not in environment
    assert options["env"] == manager_options["env"]
    assert arguments[arguments.index(str(bridge)) - 1] == "-e"
    assert "non_editable" not in options
    assert options["cwd"] == bridge and options["trusted_source_roots"] == (tmp_path,)
    assert "agent-bridge" not in (manager / "pyproject.toml").read_text()


def test_ssh_optional_sdk_is_not_misattributed_as_a_canonical_library() -> None:

    metadata = tomllib.loads(
        (runner.REPO / "plugins" / "agent-ssh" / "pyproject.toml").read_text(encoding="utf-8"),
    )
    assert "fleet-manager" not in metadata["tool"]["uv"]["sources"]
    assert any(item.startswith("fleet-manager>=") for item in metadata["project"]["optional-dependencies"]["fleet"])
    assert any(item.startswith("fleet-manager>=") for item in metadata["project"]["optional-dependencies"]["dev"])


def test_vault_sdk_preserves_canonical_library_attribution() -> None:
    from installer_dependency_policy import _anchor_roots

    roots = _anchor_roots()
    assert runner.REPO / "plugins" / "agent-vault" in roots
    assert runner.REPO / "fleet-manager" not in roots


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows editable staging contract")
def test_windows_editable_metadata_is_retained_near_all_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:

    policy = sys.modules[runner.run_uv_install.__module__]
    checkout = tmp_path / "checkout"
    projects = [checkout / "plugins" / "fixture", checkout / "fleet-manager"]
    for index, source in enumerate(projects):
        (source / "src").mkdir(parents=True)
        (source / "pyproject.toml").write_text(
            '[build-system]\nrequires=[]\nbuild-backend="setuptools.build_meta"\n'
            f'[project]\nname="fixture-{index}"\nversion="1.0"\ndependencies=[]\n'
            '[tool.setuptools.packages.find]\nwhere=["src"]\n', encoding="utf-8",
        )
    retained = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--show-settings" in command:
            return subprocess.CompletedProcess(command, 0, """PipInstallSettings { settings: PipSettings {
                index_locations: IndexLocations { indexes: [], flat_index: [], },
                exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },
            }, }""", "")
        root = Path(kwargs["cwd"])
        retained.append(root)
        assert root.is_relative_to(checkout.parent)
        assert all(not root.is_relative_to(source) for source in projects)
        manifests = list(root.parents[1].rglob("pyproject.toml"))
        assert len(manifests) == len(projects)
        for manifest in manifests:
            data = tomllib.loads(manifest.read_text(encoding="utf-8"))
            relative = data["tool"]["setuptools"]["package-dir"][""]
            target = (manifest.parent / relative).resolve()
            assert target in [source / "src" for source in projects]
            # Use the real metadata rename shape, with an absolute origin and
            # a lexical relative destination, rather than resolving both away.
            metadata = target / "fixture.egg-info"
            metadata.mkdir()
            temporary = metadata / "temporary"
            temporary.write_text("fixture", encoding="utf-8")
            destination = manifest.parent / relative / "fixture.egg-info" / "PKG-INFO"
            assert len(str(destination)) < 260
            temporary.replace(destination)
            assert (metadata / "PKG-INFO").read_text(encoding="utf-8") == "fixture"
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(policy.subprocess, "run", run)
    result = policy.run_uv_install(
        "fixture-uv", ["--no-config", "-e", str(projects[0]), "-e", str(projects[1])],
        cwd=projects[0], trusted_source_roots=(checkout,),
    )
    assert result.returncode == 0 and retained and not retained[0].exists()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows retained-file copy")
def test_windows_retained_file_copy_preserves_long_path_bytes(tmp_path: Path) -> None:
    policy = sys.modules[runner.run_uv_install.__module__]
    root = Path("\\\\?\\" + str(tmp_path.resolve())) / ("nested-" * 15) / ("snapshot-" * 8)
    root.mkdir(parents=True)
    source = root / "installer_dependency_policy.py"
    source.write_bytes(b"fixture retained source\n")
    destination = root / "copied_installer_dependency_policy.py"
    plain_source = Path(str(source).removeprefix("\\\\?\\"))
    plain_destination = Path(str(destination).removeprefix("\\\\?\\"))
    assert len(str(plain_destination)) > 260
    policy._copy_retained_file(plain_source, plain_destination)
    assert destination.read_bytes() == source.read_bytes()
    with policy._temporary_inputs(tmp_path) as temporary:
        owned = Path(policy._native_retained_path(temporary))
        nested = owned / ("snapshot-" * 14) / ("dependency-" * 5)
        nested.mkdir(parents=True)
        (nested / "installer_dependency_policy.py").write_bytes(b"retained fixture\n")
    assert not owned.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows configuration path normalization")
@pytest.mark.parametrize("selection", ["argument", "environment"])
def test_windows_parent_relative_configuration_keeps_native_resolver_policy(tmp_path: Path, selection: str) -> None:
    policy = sys.modules[runner.run_uv_install.__module__]
    source = tmp_path / "source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\n'
        '[project]\nname="owned-fixture"\nversion="1.0"\ndependencies=[]\n',
        encoding="utf-8",
    )
    (tmp_path / "uv.toml").write_text('[pip]\nexclude-newer = "9 days"\n', encoding="utf-8")
    environment = {key: value for key, value in os.environ.items() if not key.startswith("UV_")}
    environment["UV_OFFLINE"] = "1"
    arguments = [str(source)]
    if selection == "argument":
        arguments = ["--config-file", "../uv.toml", *arguments]
    else:
        environment["UV_CONFIG_FILE"] = "../uv.toml"
    uv = shutil.which("uv")
    assert uv, "native UV is required for configuration provenance coverage"
    admitted = policy.prepare_uv_install(
        uv, arguments, cwd=source, env=environment, trusted_source_roots=(source,),
    )
    assert "--exclude-newer" in admitted
    assert ".." not in Path(policy._native_retained_path(source / ".." / "uv.toml")).parts
    assert policy._toml(source / ".." / "uv.toml")["pip"]["exclude-newer"] == "9 days"


def test_explicit_test_files_preserve_default_grouping_and_filter_only_collection(selectable_tests: Path) -> None:
    tests = selectable_tests
    assert len(runner._test_file_groups("example", 1)) == 3
    assert len(runner._test_file_groups("example", 1, filtered=True)[0]) == 3
    assert runner._test_file_groups(
        "example", 1, filtered=True,
        test_files=("tests/test_selected.py", "tests/nested/test_child.py", "tests/test_selected.py"),
    ) == [[tests / "nested/test_child.py", tests / "test_selected.py"]]


def test_manager_default_discovery_excludes_only_its_prepared_native_lane() -> None:
    root = runner._plugin_dir("worktree-manager")
    native = set((root / "tests" / "native").glob("test_*.py"))
    assert {
        "test_manager_runtime_install.py", "test_manager_mux_callbacks.py",
        "test_manager_updater_recovery.py",
        "test_manager_runtime_uninstall.py",
        "test_launch_attempts_native.py", "test_mux_discovery_native.py",
        "test_psmux_home_directory.py", "test_warm_picker_lifetime.py",
        "test_companion_pane_native.py", "test_companion_pane_tmux.py",
        "test_manager_engine_source.py", "test_launch_retention_native.py",
        "test_manager_source_update.py", "test_launch_history_sweep_native.py",
        "test_launch_history_cli_native.py", "test_manager_source_install.py",
        "test_terminal_launch_native.py", "test_terminal_launch_mux_native.py",
        "test_pane_boundary_native.py", "test_pane_boundary_posix.py",
        "test_manager_suite_replacement.py",
    } <= {path.name for path in native}
    default = {path for group in runner._test_file_groups("worktree-manager", 25) for path in group}
    assert default == set((root / "tests").rglob("test_*.py")) - native


def test_manager_explicit_native_selection_remains_available() -> None:
    root = runner._plugin_dir("worktree-manager")
    files = (
        "tests/native/test_psmux_home_directory.py",
        "tests/native/test_warm_picker_lifetime.py",
    )
    assert runner._test_file_groups("worktree-manager", 25, test_files=files) == [
        [root / value for value in files],
    ]
    files = tuple(sorted(
        path.relative_to(root).as_posix() for path in (root / "tests" / "native").glob("test_*.py")
    ))
    groups = runner._test_file_groups("worktree-manager", 25, test_files=files)
    assert all(0 < len(group) <= 25 for group in groups)
    assert [path for group in groups for path in group] == [root / value for value in files]


@pytest.fixture
def manager_acceptance(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> ManagerAcceptance:
    script = SCRIPT.with_name("run-manager-runtime-acceptance.py")
    spec = importlib.util.spec_from_file_location("manager_acceptance_cleanup", script)
    assert spec and spec.loader
    acceptance = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acceptance)
    releases = []
    monkeypatch.setattr(acceptance, "REPO", tmp_path)
    monkeypatch.setattr(acceptance.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(
        acceptance, "acquire", lambda *a: SimpleNamespace(release=lambda: releases.append(True)),
    )
    monkeypatch.setattr(acceptance.sys, "argv", [str(script), "--root", str(tmp_path / "acceptance")])
    return acceptance, releases


@pytest.mark.portfolio_tier("T1")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("tooling_failure", [False, True])
def test_manager_provider_prepares_only_declared_tooling(
    manager_acceptance: ManagerAcceptance, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path, tooling_failure: bool,
) -> None:
    acceptance, releases = manager_acceptance
    requirements = cast(tuple[str, ...], runner.declared_test_tooling_requirements("worktree-manager"))
    selected = []
    commands = []

    def declared(name: str) -> tuple[str, ...]:
        selected.append(name)
        return requirements

    monkeypatch.setattr(acceptance.runpy, "run_path", lambda _path: {
        "declared_test_tooling_requirements": declared,
    })
    monkeypatch.setattr(acceptance.shutil, "which", lambda _name: "fixture-uv")

    def run(command: list[str], **kwargs: Any) -> int:
        commands.append(command)
        sandbox = kwargs["sandbox"]
        assert kwargs["limits"].wall_seconds <= 300
        assert not sandbox.is_relative_to(acceptance.REPO)
        if len(commands) == 2 and tooling_failure:
            return 7
        if "--prepare" in command:
            wheel = sandbox / "manager.whl"
            wheel.write_bytes(b"fixture")
            snapshot = sandbox / "complete"
            snapshot.mkdir()
            (sandbox / "fixtures.json").write_text(json.dumps({
                "provider_wheel": str(wheel), "complete_snapshot": str(snapshot),
            }), encoding="utf-8")
        return 0

    monkeypatch.setattr(acceptance, "run_contained", run)
    root = tmp_path / "provider"
    root.mkdir()
    assert acceptance._provider_acceptance(root) == (
        7 if tooling_failure else 0
    )
    assert selected == ["worktree-manager"]
    assert len(commands) == (2 if tooling_failure else 4)
    assert commands[0][:4] == ["fixture-uv", "venv", "--python", sys.executable]
    install = commands[1]
    assert install[install.index("--registry-only") + 1:] == [
        "--", "--python", str(Path(commands[0][-1]) / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )), *requirements,
    ]
    assert "-e" not in install
    assert not releases
    if tooling_failure:
        assert not (root / "provider-fixtures.json").exists()
    else:
        assert (root / "artifacts" / "manager.whl").is_file()
        assert "--timeout=30" in commands[-1]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("contract", ["runtime", "source"])
def test_manager_acceptance_retains_unsettled_sandbox(
    manager_acceptance: ManagerAcceptance, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str], contract: str,
) -> None:
    acceptance, releases = manager_acceptance
    monkeypatch.setattr(acceptance.sys, "argv", [*acceptance.sys.argv, "--contract", contract])
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    observed = []
    failure = containment.UnreapedProcessError("injected unsettled process")

    def unsettled(command: list[str], **kwargs: Any) -> int:
        sandbox = kwargs["sandbox"]
        observed.append(sandbox)
        (sandbox / "still-owned.txt").write_text("retained evidence", encoding="utf-8")
        raise failure

    monkeypatch.setattr(acceptance, "run_contained", unsettled)
    try:
        with pytest.raises(containment.UnreapedProcessError) as raised:
            acceptance.main()
        assert raised.value is failure
        assert len(observed) == 1
        sandbox = observed[0]
        assert (sandbox / "still-owned.txt").read_text(encoding="utf-8") == "retained evidence"
        assert str(sandbox) in capsys.readouterr().err
        assert releases == [True]
    finally:
        # The failure is injected: no process ever acquired these fixture files.
        for sandbox in observed:
            if sandbox.exists():
                shutil.rmtree(sandbox)


@pytest.mark.portfolio_tier("T1")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("returncode", [0, 7])
@pytest.mark.parametrize("contract", ["runtime", "callbacks", "updater", "uninstall", "source", "engine-source"])
def test_manager_acceptance_cleans_settled_sandbox(
    manager_acceptance: ManagerAcceptance, monkeypatch: pytest.MonkeyPatch, returncode: int, contract: str,
) -> None:
    acceptance, releases = manager_acceptance
    monkeypatch.setattr(acceptance.sys, "argv", [*acceptance.sys.argv, "--contract", contract])
    observed = []
    commands = []

    def settled(command: list[str], **kwargs: Any) -> int:
        commands.append(command)
        sandbox = kwargs["sandbox"]
        assert not sandbox.is_relative_to(acceptance.REPO)
        with monkeypatch.context() as nested:
            nested.setenv("AGENT_SCRATCH_ROOT", kwargs["env"]["AGENT_SCRATCH_ROOT"])
            nested.setattr(runner, "REPO", acceptance.REPO)
            assert runner._venv_python("worktree-manager").is_relative_to(sandbox)
        observed.append(sandbox)
        (sandbox / "settled.txt").write_text("disposable evidence", encoding="utf-8")
        return returncode

    monkeypatch.setattr(acceptance, "run_contained", settled)
    assert acceptance.main() == returncode
    expected = {"runtime": 1, "updater": 4, "uninstall": 3, "source": 5}.get(contract, 2) if returncode == 0 else 1
    assert len(observed) == expected
    if returncode == 0:
        python = acceptance.venv_root(acceptance.REPO) / "worktree-manager" / (
            "Scripts/python.exe" if os.name == "nt" else "bin/python"
        )
        if contract == "runtime":
            assert acceptance.__file__ is not None
            assert commands[-1] == [
                str(python), str(Path(acceptance.__file__).resolve()),
                "--runtime-group", str(observed[0]), "--runtime-python", str(python),
            ]
        else:
            assert commands[-1][:3] == [str(python), "-m", "pytest"]
            assert commands[-1][commands[-1].index("-p") + 1] == "pytest_portfolio_guard"
            assert "--timeout=30" in commands[-1] and "timeout_func_only=false" in commands[-1]
    assert not any(sandbox.exists() for sandbox in observed)
    assert releases == [True]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.effect("filesystem")
def test_manager_acceptance_cleans_sandbox_after_ordinary_exception(
    manager_acceptance: ManagerAcceptance, monkeypatch: pytest.MonkeyPatch,
) -> None:
    acceptance, releases = manager_acceptance
    observed = []
    failure = RuntimeError("injected settled failure")

    def failed(command: list[str], **kwargs: Any) -> int:
        observed.append(kwargs["sandbox"])
        raise failure

    monkeypatch.setattr(acceptance, "run_contained", failed)
    with pytest.raises(RuntimeError) as raised:
        acceptance.main()
    assert raised.value is failure
    assert len(observed) == 1
    assert not observed[0].exists()
    assert releases == [True]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.effect("filesystem")
def test_manager_acceptance_source_contains_each_construction_and_keeps_final_assertions(
    manager_acceptance: ManagerAcceptance, monkeypatch: pytest.MonkeyPatch,
) -> None:
    acceptance, releases = manager_acceptance
    monkeypatch.setattr(acceptance.sys, "argv", [*acceptance.sys.argv, "--contract", "source"])
    clock = [0]
    monkeypatch.setattr(acceptance.time, "monotonic", lambda: clock[0])
    observed = []

    def completed(command: list[str], **kwargs: Any) -> int:
        observed.append((command, kwargs["limits"].wall_seconds, kwargs["sandbox"]))
        clock[0] += 240 if "--prepare-step" in command else 10
        return 0

    monkeypatch.setattr(acceptance, "run_contained", completed)
    assert acceptance.main() == 0
    assert [command[-1] for command, _, _ in observed[:-1]] == [
        "release", "qualified", "source", "repair",
    ]
    assert all(command[-2] == "--prepare-step" for command, _, _ in observed[:-1])
    assert observed[-1][0][-1] == str(
        acceptance.REPO / "worktree-manager" / "tests" / "native" / "test_manager_source_install.py"
    )
    assert [limit for _, limit, _ in observed] == [1800, 1560, 1320, 1080, 840]
    assert len({sandbox for _, _, sandbox in observed}) == 1
    assert not observed[0][2].exists()
    assert releases == [True]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.effect("filesystem")
def test_manager_acceptance_source_does_not_reset_aggregate_deadline(
    manager_acceptance: ManagerAcceptance, monkeypatch: pytest.MonkeyPatch,
) -> None:
    acceptance, releases = manager_acceptance
    monkeypatch.setattr(acceptance.sys, "argv", [*acceptance.sys.argv, "--contract", "source"])
    clock = [0]
    monkeypatch.setattr(acceptance.time, "monotonic", lambda: clock[0])
    observed = []

    def completed(command: list[str], **kwargs: Any) -> int:
        observed.append((command, kwargs["limits"].wall_seconds, kwargs["sandbox"]))
        clock[0] += 600
        return 0

    monkeypatch.setattr(acceptance, "run_contained", completed)
    assert acceptance.main() == 124
    assert [command[-1] for command, _, _ in observed] == ["release", "qualified", "source"]
    assert [limit for _, limit, _ in observed] == [1800, 1200, 600]
    assert not observed[0][2].exists()
    assert releases == [True]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("contract,limits", [
    ("runtime", [300]), ("callbacks", [300, 290]),
    ("updater", [300, 300, 300, 290]), ("engine-source", [300, 290]),
    ("uninstall", [300, 300, 290]),
])
def test_manager_acceptance_other_contracts_keep_group_limits(
    manager_acceptance: ManagerAcceptance, monkeypatch: pytest.MonkeyPatch, contract: str, limits: list[int],
) -> None:
    acceptance, releases = manager_acceptance
    monkeypatch.setattr(acceptance.sys, "argv", [*acceptance.sys.argv, "--contract", contract])
    clock = [0]
    monkeypatch.setattr(acceptance.time, "monotonic", lambda: clock[0])
    observed = []

    def completed(command: list[str], **kwargs: Any) -> int:
        observed.append(kwargs["limits"].wall_seconds)
        clock[0] += 10
        return 0

    monkeypatch.setattr(acceptance, "run_contained", completed)
    assert acceptance.main() == 0
    assert observed == limits
    assert releases == [True]


@pytest.mark.portfolio_tier("T1")
@pytest.mark.effect("filesystem")
@pytest.mark.parametrize("unsettled", [False, True])
def test_manager_acceptance_source_stops_after_later_failed_construction(
    manager_acceptance: ManagerAcceptance, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], unsettled: bool,
) -> None:
    acceptance, releases = manager_acceptance
    monkeypatch.setattr(acceptance.sys, "argv", [*acceptance.sys.argv, "--contract", "source"])
    observed = []
    failure = containment.UnreapedProcessError("injected unsettled source construction")

    def failed(command: list[str], **kwargs: Any) -> int:
        observed.append((command, kwargs["sandbox"]))
        if command[-1] == "source":
            (kwargs["sandbox"] / "evidence.txt").write_text("construction evidence", encoding="utf-8")
            if unsettled:
                raise failure
            return 124
        return 0

    monkeypatch.setattr(acceptance, "run_contained", failed)
    try:
        if unsettled:
            with pytest.raises(containment.UnreapedProcessError) as raised:
                acceptance.main()
            assert raised.value is failure
        else:
            assert acceptance.main() == 124
        assert [command[-1] for command, _ in observed] == ["release", "qualified", "source"]
        sandbox = observed[0][1]
        assert sandbox.exists() is unsettled
        if unsettled:
            assert (sandbox / "evidence.txt").read_text(encoding="utf-8") == "construction evidence"
            assert str(sandbox) in capsys.readouterr().err
        assert releases == [True]
    finally:
        # No process owns this injected-failure fixture.
        if observed and observed[0][1].exists():
            shutil.rmtree(observed[0][1])


def test_manager_uninstall_native_groups_preserve_existing_contracts_and_bounds() -> None:
    source = SCRIPT.with_name("run-manager-runtime-acceptance.py").read_text(encoding="utf-8")
    assignment = next(
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "groups" for target in node.targets)
    )
    expression = compile(ast.Expression(assignment.value), "native-groups", "eval")
    for contract, count in (("runtime", 1), ("callbacks", 1), ("updater", 3), ("uninstall", 2)):
        groups = eval(expression, {"str": str, "Path": Path, "__file__": str(SCRIPT.with_name("run-manager-runtime-acceptance.py"))}, {
            "args": SimpleNamespace(contract=contract), "prepare_command": ["prepare"],
            "assertions": ["assert"], "python": "python", "native": "native", "sandbox": "sandbox",
        })
        assert len(groups) == count
        if contract == "runtime":
            assert groups[0][1] == [[
                "python", str(SCRIPT.with_name("run-manager-runtime-acceptance.py").resolve()),
                "--runtime-group", "sandbox", "--runtime-python", "python",
            ]]
            continue
        assert groups[0][1][0] == ["prepare"]
        assert groups[-1][1][-1] == ["assert"]
        if contract == "uninstall":
            assert groups[0][1] == [["prepare"]]
            assert groups[1][1][0] == ["python", "-I", "-B", "native", "--exercise", "sandbox"]
    assert "limits=Limits(wall_seconds=remaining)" in source


@pytest.mark.parametrize("setting", [
    '["native", "build_*", "test_default.py"]', '"native build_* test_default.py"',
])
@pytest.mark.parametrize("filtered", [False, True])
def test_default_discovery_honors_pytest_directory_patterns_but_not_file_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, setting: str, filtered: bool,
) -> None:
    root = tmp_path / "fixture"
    for name in ("test_default.py", "native/test_native.py", "nested/native/test_nested.py",
                 "build_fixture/test_build.py", "nested/test_kept.py"):
        path = root / "tests" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    (root / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\nnorecursedirs = " + setting + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: root)
    default = {path.relative_to(root).as_posix()
               for group in runner._test_file_groups("fixture", 25, filtered=filtered) for path in group}
    assert default == {"tests/test_default.py", "tests/nested/test_kept.py"}
    assert runner._test_file_groups(
        "fixture", 25, test_files=("tests/native/test_native.py",),
    ) == [[root / "tests" / "native" / "test_native.py"]]


@pytest.mark.parametrize("value", ["./tests/test_selected.py", "tests//test_selected.py"])
def test_interactive_test_file_paths_are_normalized_without_changing_manifest_policy(
    selectable_tests: Path, value: str,
) -> None:
    assert runner._test_file_groups("example", 25, test_files=(value,)) == [
        [selectable_tests / "test_selected.py"],
    ]


@pytest.mark.skipif(os.name != "nt", reason="Windows native path separators")
def test_interactive_test_file_accepts_native_windows_separators(selectable_tests: Path) -> None:
    assert runner._test_file_groups(
        "example", 25, test_files=("tests\\test_selected.py",),
    ) == [[selectable_tests / "test_selected.py"]]


@pytest.mark.parametrize("path", [
    "missing.py", "../test_selected.py", "test_selected.py", "nested",
])
def test_explicit_test_files_reject_unknown_or_nonowned_paths_before_preparation(
    selectable_tests: Path, monkeypatch: pytest.MonkeyPatch, path: str,
) -> None:
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **kw: pytest.fail("prepared invalid selection"))
    with pytest.raises(ValueError, match="existing, unlinked"):
        runner.run_plugin(
            "example", "unused", reinstall=False, kexpr=None, test_files=(path,),
            limits=runner.Limits(), plugin_timeout=60, test_timeout=10,
            max_files_per_subsuite=25,
        )


def test_explicit_test_files_reject_absolute_and_linked_escape(selectable_tests: Path, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="existing, unlinked"):
        runner._test_file_groups("example", 25, test_files=(str(selectable_tests / "test_selected.py"),))
    outside = tmp_path / "test_outside.py"
    outside.write_text("raise AssertionError('must not collect')\n", encoding="utf-8")
    try:
        (selectable_tests / "test_link.py").symlink_to(outside)
    except OSError:
        pytest.skip("file symlinks unavailable")
    with pytest.raises(ValueError, match="existing, unlinked"):
        runner._test_file_groups("example", 25, test_files=("tests/test_link.py",))


@pytest.mark.parametrize("arguments", [
    ["example", "other", "--test-file", "tests/test_selected.py"],
    ["example", "--prepare-only", "--test-file", "tests/test_selected.py"],
])
def test_explicit_test_files_require_one_executing_plugin(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        runner.main(arguments)
    assert error.value.code == 2


def test_explicit_test_files_reach_contained_pytest_without_unrelated_modules(
    selectable_tests: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **kw: Path(sys.executable))
    calls: list[list[str]] = []
    monkeypatch.setattr(runner, "run_contained", lambda command, **kw: recorded(calls, command, 0))
    assert runner.run_plugin(
        "example", "unused", reinstall=False, kexpr="example",
        test_files=("tests/test_selected.py",), limits=runner.Limits(),
        plugin_timeout=60, test_timeout=10, max_files_per_subsuite=25,
    ) == 0
    assert len(calls) == 1
    assert str(Path("tests") / "test_selected.py") in calls[0]
    assert not any("unrelated" in argument for argument in calls[0])
    assert calls[0][-3:-1] == ["-k", "example"]


@pytest.fixture
def bridge_historical_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    tests = tmp_path / "tests"
    tests.mkdir()
    target = tests / "test_http48_capture_guards.py"
    target.write_text(textwrap.dedent("""
    import pytest
    @pytest.fixture(params=["http48", "http49", "http50", "http51"])
    def capture_case(request): return request.param
    @pytest.mark.parametrize("fault", ["one", "two"])
    def test_http48_guard(capture_case, fault): pass
    def test_http48_untagged_control(): pass
    @pytest.mark.parametrize("future", [48, 49, 50, 51])
    def test_http47_future_history(future): pass
    @pytest.mark.parametrize("value", ["untagged", "http49-http50-http51"])
    def test_future_unclassified_contract(value): pass
    """), encoding="utf-8")
    (tests / "test_http47_capture_guards.py").write_text(
        "def test_shared_capture_seed_has_owned_committed_inputs(): pass\n",
        encoding="utf-8",
    )
    for name in runner._INDEPENDENT_SUBSUITE_FILES["agent-bridge"]:
        (tests / name).write_text("def test_unrelated_native(): pass\n", encoding="utf-8")
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    monkeypatch.setattr(runner, "_ensure_venv", lambda *_args, **_kwargs: Path(sys.executable))
    return tmp_path, target


@pytest.mark.parametrize("kexpr", [
    None, "http48 or shared_capture_seed_has_owned_committed_inputs",
    "http49", "never_matches",
])
@pytest.mark.parametrize("explicit", [False, True])
def test_bridge_historical_partitions_collect_every_selected_case_exactly_once(
    bridge_historical_plan: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
    kexpr: str | None, explicit: bool,
) -> None:
    plugin, target = bridge_historical_plan
    inventory = []

    class Inventory:
        def pytest_collection_finish(self, session: pytest.Session) -> None:
            inventory.extend(session.items)

    arguments = [
        "-q", "--collect-only", "--import-mode=importlib", f"--rootdir={plugin}",
        "-p", "no:cacheprovider",
    ]
    if kexpr:
        arguments += ["-k", kexpr]
    arguments.append(str(target.relative_to(plugin)) if explicit else "tests")
    with monkeypatch.context() as context:
        context.chdir(plugin)
        expected_status = pytest.main(arguments, plugins=[Inventory()])
    selections = []

    def collect(command: list[str], **kwargs: Any) -> int:
        selector = command[command.index("-k") + 1] if "-k" in command else ""
        expression = Expression.compile(selector) if selector.strip() else None
        files = {argument.replace("\\", "/") for argument in command if argument.endswith(".py")}
        selected = [
            item.nodeid for item in inventory
            if item.nodeid.split("::", 1)[0] in files
            and (expression is None or expression.evaluate(KeywordMatcher.from_item(item)))
        ]
        selections.extend(selected)
        assert "--timeout=30" in command
        limits = kwargs["limits"]
        assert 0 < limits.wall_seconds <= 300
        assert (limits.max_processes, limits.max_memory_mb, limits.max_temp_mb) == (128, 4096, 2048)
        return 0 if selected else 5

    monkeypatch.setattr(runner, "run_contained", collect)
    result = runner.run_plugin(
        "agent-bridge", "unused", reinstall=False, kexpr=kexpr,
        test_files=(str(runner._BRIDGE_HISTORICAL_FILE),) if explicit else (),
        collect_only=True, limits=runner.Limits(wall_seconds=300), plugin_timeout=900,
        test_timeout=30, max_files_per_subsuite=25,
    )
    assert result == expected_status
    assert Counter(selections) == Counter(item.nodeid for item in inventory)
    assert len(selections) == len(set(selections))


def test_bridge_historical_partitions_share_the_original_plugin_deadline(
    bridge_historical_plan: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = iter([0, 0, 0, 250, 650, 901, 901])
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    seen: list[float] = []
    monkeypatch.setattr(
        runner, "run_contained",
        lambda _command, **kwargs: recorded(seen, kwargs["limits"].wall_seconds, 0),
    )
    assert runner.run_plugin(
        "agent-bridge", "unused", reinstall=False, kexpr="http48",
        test_files=(str(runner._BRIDGE_HISTORICAL_FILE),),
        limits=runner.Limits(wall_seconds=300), plugin_timeout=900, test_timeout=30,
        max_files_per_subsuite=25,
    ) == 124
    assert seen == [300, 300, 250]


@pytest.fixture
def codespaces_native_plan(monkeypatch: pytest.MonkeyPatch) -> Iterator[CodespacesPlan]:
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        tests = root / "tests"
        tests.mkdir()
        target = tests / "test_installer_engine_adoption.py"
        target.write_text(textwrap.dedent("""
        import pytest
        @pytest.mark.parametrize("editable", [False, True])
        def test_codespaces_library_install_order_retry_and_errors(editable): pass
        @pytest.mark.parametrize("editable", [False, True])
        @pytest.mark.parametrize("case", ["healthy-source", "sre-source", "future-source"])
        def test_codespaces_real_admission_replays_captured_source(editable, case): pass
        def test_codespaces_admission_spy_refuses_incorrect_or_bypassed_calls(): pass
        @pytest.mark.parametrize("layout", [False, True])
        def test_codespaces_stamp_snapshot_is_standalone(layout): pass
        def test_native_dev_generation_dependency_failure_preserves_working_runtime(): pass
        @pytest.mark.parametrize("value", [False, True])
        def test_future_unclassified_contract(value): pass
        """), encoding="utf-8")
        (tests / "test_other.py").write_text("def test_unrelated_file(): pass\n", encoding="utf-8")
        monkeypatch.setattr(runner, "_plugin_dir", lambda _name: root)
        monkeypatch.setattr(runner, "_ensure_venv", lambda *_args, **_kwargs: Path(sys.executable))
        monkeypatch.setenv("TEMP", str(root))
        original_plan = runner._test_group_plan
        monkeypatch.setattr(
            runner, "_test_group_plan",
            lambda name, groups: original_plan(name, groups, windows=True),
        )
        yield root, target, original_plan


@pytest.mark.parametrize("kexpr", [None, " ", "True", "future", "never_matches"])
@pytest.mark.parametrize("explicit", [False, True])
def test_codespaces_normal_runner_partitions_collect_exactly_once(
    codespaces_native_plan: CodespacesPlan, monkeypatch: pytest.MonkeyPatch,
    kexpr: str | None, explicit: bool,
) -> None:
    plugin, target, _ = codespaces_native_plan
    selections = []
    commands = []
    limits = containment.Limits(
        wall_seconds=300, max_processes=19, max_memory_mb=222,
        max_temp_mb=333, poll_seconds=0.15,
    )
    inventory = []

    class Inventory:
        def pytest_collection_finish(self, session: pytest.Session) -> None:
            inventory.extend(session.items)

    baseline = [
        "-q", "--collect-only", "--import-mode=importlib", f"--rootdir={plugin}",
        "-p", "no:cacheprovider",
    ]
    if kexpr:
        baseline += ["-k", kexpr]
    baseline += [str(target.relative_to(plugin)) if explicit else "tests"]
    with monkeypatch.context() as context:
        context.chdir(plugin)
        baseline_status = pytest.main(baseline, plugins=[Inventory()])

    def collect(command: list[str], **kwargs: Any) -> int:
        commands.append((command, kwargs))
        selector = command[command.index("-k") + 1] if "-k" in command else ""
        expression = Expression.compile(selector) if selector.strip() else None
        files = {argument.replace("\\", "/") for argument in command if argument.endswith(".py")}
        selected = [
            item.nodeid for item in inventory
            if item.nodeid.split("::", 1)[0] in files
            and (expression is None or expression.evaluate(KeywordMatcher.from_item(item)))
        ]
        selections.append(selected)
        return 0 if selected else 5

    monkeypatch.setattr(runner, "run_contained", collect)
    files = (str(runner._CODESPACES_NATIVE_FILE),) if explicit else ()
    status = runner.run_plugin(
        "agent-codespaces", "unused", reinstall=False, kexpr=kexpr,
        test_files=files, collect_only=True, limits=limits,
        plugin_timeout=900, test_timeout=30, max_files_per_subsuite=25,
    )
    partitioned = [node for selection in selections for node in selection]
    expected = [item.nodeid for item in inventory]
    assert Counter(partitioned) == Counter(expected)
    assert len(partitioned) == len(set(partitioned))
    assert status == baseline_status
    assert len(commands) == (6 if explicit else 7)
    for command, kwargs in commands:
        assert "--timeout=30" in command and "--collect-only" in command
        actual = kwargs["limits"]
        assert 0 < actual.wall_seconds <= 300
        assert (
            actual.max_processes, actual.max_memory_mb, actual.max_temp_mb, actual.poll_seconds
        ) == (19, 222, 333, 0.15)
        assert kwargs["cwd"] == plugin
        assert kwargs["env"]["HOME"] != os.environ.get("HOME")
    if kexpr is None:
        offset = 0 if explicit else 1
        assert all("healthy-source" in node for node in selections[offset])
        assert any("sre-source" in node for node in selections[offset + 1])
        assert any("future-source" in node for node in selections[offset + 1])
        assert any("admission_spy_refuses" in node for node in selections[offset + 3])
        assert all("future_unclassified" not in node for node in selections[offset])
        assert any("future_unclassified" in node for node in selections[-1])
        assert any("unrelated_file" in node for node in partitioned) is not explicit


def test_codespaces_plan_preserves_other_suites_platforms_and_file_groups(codespaces_native_plan: CodespacesPlan) -> None:
    _, target, planner = codespaces_native_plan
    groups = runner._test_file_groups("example", 1)
    assert planner("example", groups, windows=True) == [(group, None) for group in groups]
    assert planner("agent-codespaces", groups, windows=False) == [
        (group, None) for group in groups
    ]
    other = [path for group in groups for path in group if path != target]
    assert planner("agent-codespaces", [other], windows=True) == [(other, None)]


@pytest.mark.parametrize("filter_mode", ["keyword", "guard", "unfiltered"])
@pytest.mark.parametrize("all_empty", [False, True])
def test_codespaces_mixed_explicit_files_preserve_filtered_empty_groups(
    monkeypatch: pytest.MonkeyPatch, filter_mode: str, all_empty: bool,
) -> None:
    with tempfile.TemporaryDirectory() as temporary:
        plugin = Path(temporary).resolve()
        tests = plugin / "tests"
        tests.mkdir()
        files = (runner._CODESPACES_NATIVE_FILE, Path("tests") / "test_other.py")
        for path in files:
            (plugin / path).write_text("def test_example(): pass\n", encoding="utf-8")
        monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
        monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **k: Path(sys.executable))
        monkeypatch.setenv("TEMP", str(plugin))
        planner = runner._test_group_plan
        monkeypatch.setattr(
            runner, "_test_group_plan",
            lambda name, groups: planner(name, groups, windows=True),
        )
        codes = iter([5, 5 if all_empty else 0, 5, 5, 5, 5, 5])
        calls = []

        def execute(command: list[str], **kwargs: Any) -> int:
            calls.append(command)
            return next(codes)

        monkeypatch.setattr(runner, "run_contained", execute)
        filtered = filter_mode != "unfiltered"
        result = runner.run_plugin(
            "agent-codespaces", "unused", reinstall=False,
            kexpr="installer" if filter_mode == "keyword" else None,
            guards=filter_mode == "guard", test_files=tuple(map(str, files)),
            limits=containment.Limits(wall_seconds=300), plugin_timeout=900,
            test_timeout=30, max_files_per_subsuite=25,
        )
        assert result == (0 if filtered and not all_empty else 5)
        assert len(calls) == (7 if filtered else 1)
        assert str(Path("tests") / "test_other.py") in calls[0]


@pytest.mark.parametrize("include_guards", [False, True])
def test_codespaces_exact_selection_preserves_manifest_admission(
    codespaces_native_plan: CodespacesPlan, monkeypatch: pytest.MonkeyPatch, include_guards: bool,
) -> None:
    plugin, _, _ = codespaces_native_plan
    payload = {
        "version": 1, "plugin": "agent-codespaces",
        "nodes": [
            "tests/test_installer_engine_adoption.py::"
            "test_codespaces_library_install_order_retry_and_errors[True]"
        ],
        "files": [], "include_guards": include_guards,
    }
    manifest = plugin / "selection.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")
    calls = []

    def execute(command: list[str], **kwargs: Any) -> int:
        calls.append(command)
        assert "-k" not in command
        assert command[command.index("-p") + 1] == runner.PORTFOLIO_PLUGIN
        selected = next(
            argument.split("=", 1)[1] for argument in command
            if argument.startswith("--exact-selection=")
        )
        assert json.loads(Path(selected).read_text(encoding="utf-8")) == payload
        return 0

    monkeypatch.setattr(runner, "run_contained", execute)
    assert runner.run_plugin(
        "agent-codespaces", "unused", reinstall=False, kexpr=None,
        selection_file=manifest, limits=containment.Limits(wall_seconds=300),
        plugin_timeout=900, test_timeout=30, max_files_per_subsuite=25,
    ) == 0
    assert len(calls) == 1


@pytest.mark.parametrize("codes,kexpr,guards,explicit,expected", [
    ([0, 1, 0, 0, 0, 0], None, False, True, 1),
    ([1, 0, 0, 0, 0, 0], None, False, True, 1),
    ([1, 5, 5, 5, 5, 5], "True", False, True, 1),
    ([1, 5], None, False, True, 5),
    ([0, 5], None, False, True, 5),
    ([5, 0, 5, 5, 5, 5], "True", False, True, 0),
    ([5, 5, 5, 5, 5, 5], "missing", False, True, 5),
    ([5, 5, 5, 5, 5, 5], None, True, True, 5),
    ([5, 5, 5, 5, 5, 5, 5], None, True, False, 0),
])
def test_codespaces_partition_failures_and_empty_selections_remain_fail_closed(
    codespaces_native_plan: CodespacesPlan, monkeypatch: pytest.MonkeyPatch, codes: list[int],
    kexpr: str | None, guards: bool, explicit: bool, expected: int,
) -> None:
    returns = iter(codes)
    calls = []
    def execute(command: list[str], **kwargs: Any) -> int:
        calls.append(command)
        return next(returns)
    monkeypatch.setattr(runner, "run_contained", execute)
    assert runner.run_plugin(
        "agent-codespaces", "unused", reinstall=False, kexpr=kexpr, guards=guards,
        test_files=(str(runner._CODESPACES_NATIVE_FILE),) if explicit else (),
        limits=containment.Limits(wall_seconds=300), plugin_timeout=900,
        test_timeout=30, max_files_per_subsuite=25,
    ) == expected
    assert len(calls) == len(codes)


@pytest.mark.parametrize("first_result", [0, 1])
def test_codespaces_partitions_share_the_unchanged_plugin_aggregate(
    codespaces_native_plan: CodespacesPlan, monkeypatch: pytest.MonkeyPatch, first_result: int,
) -> None:
    clock = iter([0, 0, 0, 250, 650, 901, 901])
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    seen: list[containment.Limits] = []
    monkeypatch.setattr(
        runner, "run_contained",
        lambda command, **kw: recorded(seen, kw["limits"], first_result),
    )
    assert runner.run_plugin(
        "agent-codespaces", "unused", reinstall=False, kexpr=None,
        test_files=(str(runner._CODESPACES_NATIVE_FILE),),
        limits=containment.Limits(wall_seconds=300), plugin_timeout=900,
        test_timeout=30, max_files_per_subsuite=25,
    ) == 124
    assert [limit.wall_seconds for limit in seen] == [300, 300, 250]


@pytest.mark.parametrize("fatal", [2, 3, 4, 5, 124, 125, 126, 130, -9])
def test_assertion_aggregation_does_not_mask_fatal_group_status(
    codespaces_native_plan: CodespacesPlan, monkeypatch: pytest.MonkeyPatch, fatal: int,
) -> None:
    returns = iter([1, fatal])
    calls: list[list[str]] = []
    monkeypatch.setattr(
        runner, "run_contained",
        lambda command, **kwargs: recorded(calls, command, next(returns)),
    )
    assert runner.run_plugin(
        "agent-codespaces", "unused", reinstall=False, kexpr=None,
        test_files=(str(runner._CODESPACES_NATIVE_FILE),),
        limits=containment.Limits(wall_seconds=300), plugin_timeout=900,
        test_timeout=30, max_files_per_subsuite=25,
    ) == fatal
    assert len(calls) == 2


def test_assertion_aggregation_does_not_swallow_containment_exceptions(
    codespaces_native_plan: CodespacesPlan, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    def execute(command: list[str], **kwargs: Any) -> int:
        calls.append(command)
        if len(calls) == 2:
            raise containment.ContainmentError("fixture containment failure")
        return 1
    monkeypatch.setattr(runner, "run_contained", execute)
    with pytest.raises(containment.ContainmentError, match="fixture containment failure"):
        runner.run_plugin(
            "agent-codespaces", "unused", reinstall=False, kexpr=None,
            test_files=(str(runner._CODESPACES_NATIVE_FILE),),
            limits=containment.Limits(wall_seconds=300), plugin_timeout=900,
            test_timeout=30, max_files_per_subsuite=25,
        )
    assert len(calls) == 2


def test_real_contained_partition_assertion_runs_later_passing_partition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    sentinel = tmp_path / "later-group-ran"
    (tests / "test_01_failure.py").write_text(
        "def test_first_group(): assert False, 'intentional assertion failure'\n", encoding="utf-8",
    )
    (tests / "test_02_pass.py").write_text(
        f"from pathlib import Path\ndef test_later_group(): Path({str(sentinel)!r}).write_text('passed')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    monkeypatch.setattr(runner, "_ensure_venv", lambda *_args, **_kwargs: Path(sys.executable))
    monkeypatch.setattr(
        runner, "_test_group_plan",
        lambda _name, groups: [(group, "test_first_group or test_later_group") for group in groups],
    )
    monkeypatch.setenv("TEMP", str(tmp_path))
    real_run = runner.run_contained
    statuses = []
    def execute(command: list[str], **kwargs: Any) -> int:
        code = cast(int, real_run(command, **kwargs))
        statuses.append(code)
        return code
    monkeypatch.setattr(runner, "run_contained", execute)
    assert runner.run_plugin(
        "example", "unused", reinstall=False, kexpr=None,
        limits=containment.Limits(wall_seconds=300), plugin_timeout=900,
        test_timeout=30, max_files_per_subsuite=1,
    ) == 1
    assert statuses == [1, 0]
    assert sentinel.read_text() == "passed"


def test_explicit_file_selection_avoids_unrelated_collection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "PLUGINS", tmp_path)
    tests = tmp_path / "example" / "tests"
    tests.mkdir(parents=True)
    for name in ("test_reconcile.py", "test_binding.py", "test_unrelated.py"):
        (tests / name).touch()
    groups = runner._test_file_groups(
        "example", 25, filtered=True,
        test_files=("tests/test_reconcile.py", "tests/test_binding.py", "tests/test_binding.py"),
    )
    assert groups == [[tests / "test_binding.py", tests / "test_reconcile.py"]]
    assert runner._test_file_groups("example", 25) == [[
        tests / "test_binding.py", tests / "test_reconcile.py", tests / "test_unrelated.py",
    ]]


@pytest.mark.parametrize("value", [
    "../test_outside.py", "tests/../test_outside.py", "tests/test_missing.py",
    "tests/helper.py", "tests/test_directory.py",
])
def test_explicit_file_selection_refuses_invalid_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, value: str,
) -> None:
    monkeypatch.setattr(runner, "PLUGINS", tmp_path)
    tests = tmp_path / "example" / "tests"
    tests.mkdir(parents=True)
    (tests / "helper.py").touch()
    (tests / "test_directory.py").mkdir()
    with pytest.raises(ValueError, match="existing, unlinked"):
        runner._test_file_groups("example", 25, test_files=(value,))


def test_explicit_file_selection_refuses_absolute_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "PLUGINS", tmp_path)
    tests = tmp_path / "example" / "tests"
    tests.mkdir(parents=True)
    target = tests / "test_absolute.py"
    target.touch()
    with pytest.raises(ValueError, match="existing, unlinked"):
        runner._test_file_groups("example", 25, test_files=(str(target),))


@pytest.mark.skipif(os.name == "nt", reason="POSIX unprivileged symlink creation")
def test_explicit_file_selection_refuses_linked_file_or_parent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "PLUGINS", tmp_path)
    tests = tmp_path / "example" / "tests"
    tests.mkdir(parents=True)
    target = tests / "test_target.py"
    target.touch()
    (tests / "test_link.py").symlink_to(target)
    (tests / "linked").symlink_to(tests, target_is_directory=True)
    for value in ("tests/test_link.py", "tests/linked/test_target.py"):
        with pytest.raises(ValueError, match="existing, unlinked"):
            runner._test_file_groups("example", 25, test_files=(value,))


@pytest.mark.parametrize("args", [
    ["--all"], ["--changed"], ["example", "other"], ["example", "--prepare-only"],
])
def test_explicit_file_selection_requires_one_execution_target(args: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        runner.main([*args, "--test-file", "tests/test_example.py"])
    assert error.value.code == 2


def test_explicit_empty_selection_is_not_a_guard_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(runner, "PLUGINS", tmp_path)
    tests = tmp_path / "example" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_empty.py").touch()
    monkeypatch.setattr(runner, "_ensure_venv", lambda *args, **kwargs: Path(sys.executable))
    calls = []
    def run(command: list[str], **kwargs: Any) -> int:
        calls.append((command, kwargs))
        return 5
    monkeypatch.setattr(runner, "run_contained", run)
    assert runner.run_plugin(
        "example", "uv", reinstall=False, kexpr="missing", guards=True,
        limits=containment.Limits(), plugin_timeout=60, test_timeout=30,
        max_files_per_subsuite=25, test_files=("tests/test_empty.py",),
    ) == 5
    assert len(calls) == 1
    assert calls[0][0][-1] == str(Path("tests") / "test_empty.py")
    assert "--timeout=30" in calls[0][0]
    assert 0 < calls[0][1]["limits"].wall_seconds <= 60


def test_worktrees_native_runtime_files_have_independent_groups() -> None:
    assert {
        "test_python_runtime_dev_live.py", "test_python_runtime_install.py",
    } <= runner._INDEPENDENT_SUBSUITE_FILES["agent-worktrees"]


def test_bridge_native_and_performance_files_have_independent_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    native = {
        "test_auth_runtime.py", "test_live_runtime.py", "test_predecessor_runtime.py",
        "test_relay_adoption.py", "test_runtime_controls.py", "test_runtime_serving.py",
        "test_runtime_transfer.py", "test_startup_imports.py", "test_startup_performance.py",
        "test_transport_owner_boundary.py",
        "test_worktree_runtime.py",
    }
    assert runner._INDEPENDENT_SUBSUITE_FILES["agent-bridge"] == native
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in native | {"test_a.py", "test_z.py"}:
        (tests / name).touch()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    groups = runner._test_file_groups("agent-bridge", 25)
    assert sorted(path.name for group in groups for path in group) == sorted(
        native | {"test_a.py", "test_z.py"},
    )
    for name in native:
        assert [tests / name] in groups


@pytest.mark.parametrize("filtered", [False, True])
def test_independent_native_groups_preserve_every_collected_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filtered: bool,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    filenames = [
        "test_a.py", "test_b.py", "test_c_native.py", "test_d.py",
        "test_e.py", "test_f_native.py", "test_g.py",
    ]
    files = [tests / name for name in filenames]
    for path in files:
        path.touch()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    monkeypatch.setattr(runner, "_INDEPENDENT_SUBSUITE_FILES", {
        "fixture": {"test_c_native.py", "test_f_native.py"},
    })
    groups = runner._test_file_groups("fixture", 2, filtered=filtered)
    assert [path for group in groups for path in group] == files
    assert len({path for group in groups for path in group}) == len(files)
    if filtered:
        assert groups == [files]
    else:
        assert groups == [files[:2], [files[2]], files[3:5], [files[5]], [files[6]]]


def test_missing_independent_native_group_fails_explicitly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    monkeypatch.setattr(runner, "_INDEPENDENT_SUBSUITE_FILES", {"fixture": {"test_missing.py"}})
    with pytest.raises(ValueError, match="Independent sub-suite files are missing"):
        runner._test_file_groups("fixture", 25)


def test_explicit_collection_does_not_require_unselected_independent_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tests = tmp_path / "tests"
    tests.mkdir()
    selected = tests / "test_selected.py"
    selected.touch()
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    monkeypatch.setattr(runner, "_INDEPENDENT_SUBSUITE_FILES", {"fixture": {"test_native.py"}})
    assert runner._test_file_groups(
        "fixture", 25, test_files=("tests/test_selected.py",),
    ) == [[selected]]


def test_worker_launch_uses_canonical_headlessness(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    ready.touch()
    calls = []
    monkeypatch.setattr(containment, "no_window_kwargs", lambda: {"creationflags": 123})

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 7)

    monkeypatch.setattr(subprocess, "run", run)
    assert containment._worker_main([str(ready), "command", "argument"]) == 7
    assert calls == [
        (
            ["command", "argument"],
            {"check": False, "creationflags": 123, **containment._windows_stdio_kwargs()},
        )
    ]


@pytest.mark.parametrize("has_project", [False, True])
def test_fresh_preparation_attributes_repository_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, has_project: bool,
) -> None:
    import agent_procutil.owned_process as owned_process
    import plugin_test_environment as preparation
    monkeypatch.setattr(owned_process, "run_caller_owned",
                        lambda *args, **kwargs: pytest.fail("pure preparation test attempted native execution"))
    monkeypatch.setattr(preparation, "capture_preparation_auth",
                        lambda *args, **kwargs: {"UV_CREDENTIALS_DIR": str(tmp_path / "fixture-auth")})
    plugin = tmp_path / "plugins" / "example"
    plugin.mkdir(parents=True)
    if has_project:
        (plugin / "pyproject.toml").write_text(
            '[build-system]\nrequires=[]\nbuild-backend="setuptools.build_meta"\n'
            '[project]\nname="example"\nversion="1.0"\ndependencies=["owned-lib"]\n'
            '[tool.uv.sources]\nowned-lib={path="../../libs/owned"}\n'
            '[tool.setuptools.packages.find]\nwhere=["src"]\n',
            encoding="utf-8",
        )
        (plugin / "src").mkdir()
        library = tmp_path / "libs" / "owned"
        library.mkdir(parents=True)
        (library / "pyproject.toml").write_text(
            '[project]\nname="owned-lib"\nversion="1.0"\n', encoding="utf-8",
        )
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "PLUGINS", tmp_path / "plugins")
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "environments")
    monkeypatch.setattr(runner, "_dep_fingerprint", lambda name: "fixture")
    environment = runner.VENV_ROOT / "example"
    environment.mkdir(parents=True)
    python = runner._venv_python("example")
    admitted = runner.run_uv_install
    installs = []
    probes = []

    def prepare(uv: str, arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        assert kwargs["trusted_source_roots"] == (tmp_path,)
        return cast(subprocess.CompletedProcess[str], admitted(uv, arguments, **kwargs))

    def command(arguments: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if arguments[1] == "venv":
            python.parent.mkdir(parents=True, exist_ok=True)
            python.touch()
        elif "--show-settings" in arguments:
            probes.append(arguments)
            return subprocess.CompletedProcess(arguments, 0, """PipInstallSettings { settings: PipSettings {
                index_locations: IndexLocations { indexes: [], flat_index: [], },
                exclude_newer: ExcludeNewer { global: None, package: ExcludeNewerPackage({}), },
            }, }""", "")
        else:
            installs.append(arguments)
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(runner, "run_uv_install", prepare)
    monkeypatch.setattr(runner.subprocess, "run", command)
    assert runner._ensure_venv("example", "uv", reinstall=False) == python
    assert len(probes) == 2 and len(installs) == 1
    assert "--exclude-newer" in installs[0] and "--reinstall" in installs[0]
    assert ("-e" in installs[0]) is has_project
    assert (environment / ".dep-fingerprint").read_text() == "fixture"


@pytest.mark.skipif(os.name == "nt", reason="POSIX creation permissions")
def test_pytest_group_parent_is_created_without_shared_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner, "_ensure_venv", lambda *_args, **_kwargs: Path(sys.executable))
    monkeypatch.setattr(runner, "_test_file_groups", lambda *_args, **_kwargs: [[]])
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    observed: list[int] = []

    def capture(command: list[str], **_kwargs: object) -> int:
        import stat

        directory = Path(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--basetemp="))).parent
        observed.append(stat.S_IMODE(directory.stat().st_mode))
        return 0

    monkeypatch.setattr(runner, "run_contained", capture)
    previous = os.umask(0o002)
    try:
        assert runner.run_plugin(
            "example", "unused", reinstall=False, kexpr=None,
            limits=containment.Limits(), plugin_timeout=60,
            test_timeout=10, max_files_per_subsuite=25,
        ) == 0
    finally:
        os.umask(previous)
    assert observed == [0o700]


def test_containment_owner_launch_preserves_process_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured = {}

    class FinishedProcess:
        pid = 123

        def poll(self) -> int:
            return 17

        def wait(self, *, timeout: float) -> int:
            return 17

    def popen(command: list[str], **kwargs: Any) -> FinishedProcess:
        captured.update(kwargs)
        assert command == containment._worker_command(
            ["command"], tmp_path / ".containment-ready"
        )
        return FinishedProcess()

    class Job:
        def __init__(self, limits: containment.Limits) -> None:
            pass

        def assign(self, pid: int) -> None:
            assert pid == 123

        def close(self) -> None:
            captured["job_closed"] = True

    monkeypatch.setattr(containment, "no_window_kwargs", lambda: {"creationflags": 123})
    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(containment, "_WindowsJob", Job)
    monkeypatch.setattr(containment, "_terminate_posix_group", lambda *args: None)
    monkeypatch.setattr(containment, "_posix_group_identity", lambda pid: (pid, pid))
    monkeypatch.setattr(containment, "_posix_exit_code", lambda fd: 17)
    assert containment._run_contained_process(
        ["command"], cwd=tmp_path, env={"TEST": "value"}, sandbox=tmp_path,
        limits=containment.Limits(),
    ) == 17
    assert captured["creationflags"] == 123
    assert captured["cwd"] == str(tmp_path)
    expected_environment = {"TEST": "value"}
    if os.name != "nt":
        assert len(captured["pass_fds"]) == 2
        expected_environment["COPILOT_EXTENSIONS_CONTAINMENT_RESULT_FD"] = str(
            captured["pass_fds"][0]
        )
        expected_environment["COPILOT_EXTENSIONS_CONTAINMENT_OWNER_FD"] = str(
            captured["pass_fds"][1]
        )
    assert captured["env"] == expected_environment
    assert {
        name: captured[name] for name in containment._windows_stdio_kwargs()
    } == containment._windows_stdio_kwargs()
    assert captured["start_new_session"] == (os.name != "nt")
    if os.name == "nt":
        assert captured["job_closed"]
    assert not (tmp_path / ".containment-ready").exists()


@pytest.mark.skipif(os.name != "nt", reason="real Windows console inheritance")
@pytest.mark.parametrize("timeout", [False, True], ids=["exit-and-streams", "timeout-tree"])
def test_windowless_runner_contains_bare_console_descendants(
    tmp_path: Path, timeout: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise pythonw -> runner -> worker -> bare Python/PowerShell/git.

    No subprocess patch is installed: intentional CREATE_NEW_CONSOLE callers
    remain untouched. Console handles/visibility are OS observations, not flag
    assertions. The opt-in desktop lane additionally observes external terminal
    hosts, visible windows and foreground changes against a pre-launch baseline.
    """
    import ctypes
    from ctypes import wintypes

    from agent_procutil import no_window_kwargs, windowless_python

    pythonw = windowless_python(sys.executable)
    if Path(pythonw).name.lower() != "pythonw.exe":
        pytest.skip("windowless base interpreter unavailable")
    powershell = shutil.which("powershell.exe")
    git = shutil.which("git.exe")
    if not powershell or not git:
        pytest.skip("PowerShell and git are required console representatives")
    receipt = tmp_path / "descendants.json"
    result = tmp_path / "result.json"
    sandbox = tmp_path / "sandbox"
    shell_timeout = 30
    child_timeout = 2 * shell_timeout + 10
    wall_seconds = 5 if timeout else child_timeout + 10
    owner_timeout = max(25, wall_seconds + 10)
    launcher_timeout = owner_timeout + 5
    console_probe = """
import ctypes, os
k = ctypes.windll.kernel32
k.GetConsoleWindow.restype = ctypes.c_void_p
window = k.GetConsoleWindow()
assert not window or not ctypes.windll.user32.IsWindowVisible(window)
"""
    ps_script = (
        'Add-Type \'using System; using System.Runtime.InteropServices; '
        'public class ConsoleProbe { [DllImport("kernel32.dll")] '
        'public static extern IntPtr GetConsoleWindow(); }\'; '
        'if ([ConsoleProbe]::GetConsoleWindow() -ne [IntPtr]::Zero) { exit 9 }; '
        f"& '{git.replace(chr(39), chr(39) * 2)}' --version; "
        '[Console]::Error.WriteLine("nested-stderr"); exit 7'
    )
    child = console_probe + f"""
import json, subprocess, sys, time
from pathlib import Path
assert os.environ['COPILOT_EXTENSIONS_TEST_CONTAINED'] == '1'
assert Path.cwd() == Path({str(tmp_path)!r})
Path({str(receipt)!r}).write_text(json.dumps({{
    'pid': os.getpid(), 'window': window,
}}))
if {timeout!r}:
    time.sleep(120)
else:
    for cycle in range(2):
        probe = subprocess.run(
            [{powershell!r}, '-NoProfile', '-NonInteractive', '-Command', {ps_script!r}],
            capture_output=True, text=True, timeout={shell_timeout},
        )
        assert probe.returncode == 7, (probe.returncode, probe.stdout, probe.stderr)
        assert 'git version' in probe.stdout
        assert 'nested-stderr' in probe.stderr
    print('nested-stdout')
    print('nested-stderr', file=sys.stderr)
    sys.exit(7)
"""
    command = console_probe + f"""
import subprocess, sys
assert sys.stdin.readline().strip() == 'nested-input'
probe = subprocess.run([sys.executable, '-c', {child!r}], timeout={child_timeout})
sys.exit(17 if probe.returncode == 7 else 18)
"""
    owner = textwrap.dedent(f"""
        import json, os, sys
        from pathlib import Path
        sys.path.insert(0, {str(SCRIPT.parent)!r})
        import plugin_test_containment as containment
        sandbox = Path({str(sandbox)!r})
        env = containment.isolated_environment(os.environ, sandbox)
        rc = containment.run_contained(
            [{sys.executable!r}, '-c', {command!r}],
            cwd=Path({str(tmp_path)!r}), env=env, sandbox=sandbox,
            limits=containment.Limits(wall_seconds={wall_seconds},
                                      poll_seconds=0.05, max_processes=32,
                                      max_memory_mb=512, max_temp_mb=32),
        )
        Path({str(result)!r}).write_text(json.dumps({{'returncode': rc}}))
    """)
    streams = tmp_path / "streams.json"
    launcher = f"""
import ctypes, json, subprocess, sys
from pathlib import Path
sys.path.insert(0, {str(SCRIPT.parent)!r})
from plugin_test_containment import no_window_kwargs
assert not ctypes.windll.kernel32.GetConsoleWindow()
probe = subprocess.run(
    [{sys.executable!r}, '-c', {owner!r}],
    input='nested-input\\n', capture_output=True, text=True,
    timeout={owner_timeout}, **no_window_kwargs(),
)
Path({str(streams)!r}).write_text(json.dumps({{
    'returncode': probe.returncode, 'stdout': probe.stdout, 'stderr': probe.stderr,
}}))
"""
    desktop = os.environ.get("MUX_CHILD_WINDOWS_E2E") == "1"
    stop = threading.Event()
    visible: set[int] = set()
    focused: set[int] = set()
    owned: set[int] = set()
    roots: set[int] = set()
    if desktop:
        session = wintypes.DWORD()
        assert getattr(ctypes, "windll").kernel32.ProcessIdToSessionId(
            os.getpid(), ctypes.byref(session),
        )
        assert session.value != 0, "desktop observation cannot run in Windows session zero"
        monkeypatch.syspath_prepend(
            str(SCRIPT.parent.parent / "worktree-manager" / "tests"),
        )
        from mux_windows_observer import descendants, processes, windows

        baseline_windows, baseline_foreground = windows()
        baseline_pids = set(processes())
        real_popen = subprocess.Popen

        def popen(argv: list[str], **kwargs: Any) -> subprocess.Popen[bytes]:
            process = real_popen(argv, **kwargs)
            roots.add(process.pid)
            return process

        monkeypatch.setattr(subprocess, "Popen", popen)

        def observe() -> None:
            while not stop.is_set():
                table = processes()
                for pid in roots.copy():
                    owned.update(descendants(pid, table))
                current_windows, foreground_pid = windows()
                terminals = {"windowsterminal.exe", "openconsole.exe", "conhost.exe"}
                for hwnd, pid in current_windows.items():
                    if pid in owned or (
                        hwnd not in baseline_windows
                        and table.get(pid, ("", 0))[0].lower() in terminals
                    ):
                        visible.add(hwnd)
                if foreground_pid in owned or (
                    foreground_pid != baseline_foreground
                    and table.get(foreground_pid, ("", 0))[0].lower() in terminals
                ):
                    focused.add(foreground_pid)
                for pid, (name, _) in table.items():
                    if pid not in baseline_pids and name.lower() == "openconsole.exe":
                        visible.add(pid)
                stop.wait(.01)

    with ThreadPoolExecutor(max_workers=1) as executor:
        monitor = executor.submit(observe) if desktop else None
        try:
            completed = subprocess.run(
                [pythonw, "-c", launcher], cwd=tmp_path, capture_output=True,
                text=True, timeout=launcher_timeout, **no_window_kwargs(),
            )
            if desktop:
                assert roots and owned, "observer never identified runner fixture processes"
                deadline = time.monotonic() + 5
                while owned & processes().keys() and time.monotonic() < deadline:
                    time.sleep(.02)
                assert not (owned & processes().keys()), "runner fixture descendants leaked"
        finally:
            stop.set()
            if monitor is not None:
                monitor.result(timeout=3)
    assert not visible, "runner child or external terminal host surfaced a window"
    assert not focused, "runner child or external terminal host stole focus"
    assert completed.returncode == 0, completed.stderr
    captured = json.loads(streams.read_text())
    assert captured["returncode"] == 0, captured["stderr"]
    assert json.loads(result.read_text())["returncode"] == (124 if timeout else 17), captured
    identity = json.loads(receipt.read_text())
    assert not identity["window"]
    if timeout:
        assert "wall-clock limit exceeded" in captured["stderr"]
        kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel32.OpenProcess(0x00100000, False, identity["pid"])
        if handle:
            try:
                assert kernel32.WaitForSingleObject(handle, 5000) == 0
            finally:
                kernel32.CloseHandle(handle)
        else:
            assert getattr(ctypes, "get_last_error")() == 87  # PID no longer exists.
    else:
        assert "nested-stdout" in captured["stdout"]
        assert "nested-stderr" in captured["stderr"]


def test_default_environment_redirects_all_mutable_roots(tmp_path: Path) -> None:
    env = containment.isolated_environment(
        {
            "PATH": os.environ.get("PATH", ""),
            "HOME": "/real/home",
            "AGENT_SCRATCH_ROOT": "/real/scratch",
            "AGENT_WORKTREES_HOME": "/real/agent-worktrees",
            "AGENT_WORKTREES_PROJECTS_YAML": "/real/projects.yaml",
            "GH_TOKEN": "not-a-real-token",
            "COPILOT_AGENT_SESSION_ID": "live-session",
            "AGENT_WORKTREES_OWNER_REF": "live-owner",
        },
        tmp_path,
    )

    assert env["PATH"] == os.environ.get("PATH", "")
    assert "GH_TOKEN" not in env
    assert "COPILOT_AGENT_SESSION_ID" not in env
    assert "AGENT_WORKTREES_OWNER_REF" not in env
    for name in containment.ROOT_ENV_NAMES:
        Path(env[name]).resolve().relative_to(tmp_path.resolve())
    assert "AGENT_CONTAINERS_CONFIG" not in env
    Path(env["AGENT_WORKTREES_PROJECTS_YAML"]).resolve().relative_to(
        tmp_path.resolve()
    )


def test_external_targets_are_explicit_and_contained_in_repository(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    driver = str(runner.REPO / "libs" / "update-driver")
    monkeypatch.setattr(runner, "REPO", tmp_path)
    assert runner._plugin_dir("worktree-manager") == tmp_path / "worktree-manager"
    assert runner._plugin_dir("payload-invocation") == tmp_path / "libs" / "payload-invocation"
    assert runner._plugin_dir("runtime-source") == tmp_path / "libs" / "versioned-runtime"
    assert runner._plugin_dir("worktree-identity") == tmp_path / "libs" / "worktree-identity"
    assert runner._plugin_dir("repository-policy") == tmp_path / "libs" / "repository-policy"
    assert runner._plugin_dir("agent-procutil") == tmp_path / "libs" / "agent-procutil"
    assert runner.EXTERNAL_TEST_DEPENDENCIES["repository-policy"] == ("pyyaml>=6.0.3",)
    assert runner.EXTERNAL_TEST_DEPENDENCIES["payload-invocation"] == (
        "pyyaml>=6.0.3", "setuptools>=84.2.1", "-e", driver,
    )
    assert runner.EXTERNAL_TEST_DEPENDENCIES["runtime-source"] == ("pyyaml>=6.0.3",)


def test_optional_file_override_is_validated_only_when_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(os.environ, tmp_path)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    for name in containment.OPTIONAL_FILE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    portfolio_guard.validate_contained_environment()

    monkeypatch.setenv("AGENT_CONTAINERS_CONFIG", str(tmp_path.parent / "config.yaml"))
    with pytest.raises(pytest.UsageError, match="AGENT_CONTAINERS_CONFIG"):
        portfolio_guard.validate_contained_environment()


def test_host_state_opt_in_preserves_credentials_not_session_affinity(
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(
        {
            "HOME": "/real/home",
            "GH_TOKEN": "not-a-real-token",
            "COPILOT_AGENT_SESSION_ID": "live-session",
            "AGENT_WORKTREES_OWNER_REF": "live-owner",
        },
        tmp_path,
        allow_explicit_tiers=True,
        allow_host_state=True,
    )

    assert env["HOME"] == "/real/home"
    assert env["GH_TOKEN"] == "not-a-real-token"
    assert env[containment.ALLOW_HOST_STATE_ENV] == "1"
    assert "COPILOT_AGENT_SESSION_ID" not in env
    assert "AGENT_WORKTREES_OWNER_REF" not in env
    for name in containment.ALWAYS_SANDBOX_ENV_NAMES:
        Path(env[name]).resolve().relative_to(tmp_path.resolve())


def test_host_state_requires_explicit_tiers_at_environment_boundary(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match="allow_host_state requires allow_explicit_tiers",
    ):
        containment.isolated_environment(
            os.environ,
            tmp_path,
            allow_host_state=True,
        )


def test_host_state_still_fails_closed_on_escaped_temp(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    env = containment.isolated_environment(
        os.environ,
        tmp_path,
        allow_explicit_tiers=True,
        allow_host_state=True,
    )
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TMPDIR", str(tmp_path.parent))

    with pytest.raises(pytest.UsageError, match="TMPDIR"):
        portfolio_guard.validate_contained_environment()


def test_admission_fails_fast_with_live_holder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "environment").mkdir()
    class BusyLease:
        def acquire(self) -> None:
            raise runner.AlreadyRunningError(tmp_path / "runner.lock", 123)

    monkeypatch.setattr(admission, "SingleInstance", lambda *_args, **_kwargs: BusyLease())

    with pytest.raises(runner.AlreadyRunningError) as exc:
        runner._acquire_admission(0, tmp_path / "environment")
    assert exc.value.holder_pid == 123


def test_admission_wait_is_bounded_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admission, "admission_dir", lambda _: Path("unused-lock"))
    class EventuallyAvailableLease:
        calls = 0

        def acquire(self) -> None:
            self.calls += 1
            if self.calls == 1:
                raise runner.AlreadyRunningError(Path("runner.lock"), 123)

    lease = EventuallyAvailableLease()
    clock = iter((10.0, 10.0))
    monkeypatch.setattr(admission, "SingleInstance", lambda *_args, **_kwargs: lease)
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)

    assert runner._acquire_admission(1.0, Path("environment")) is lease
    assert lease.calls == 2


def test_run_holds_one_environment_lease_at_a_time(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []

    class Lease:
        def release(self) -> None:
            events.append("release")

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda _wait, root: recorded(events, f"acquire:{root.name}", Lease()),
    )
    monkeypatch.setattr(
        runner,
        "run_plugin",
        lambda name, *_args, **_kwargs: recorded(events, f"run:{name}", 0),
    )

    assert runner.main(["alpha", "beta"]) == 0
    assert events == ["acquire:alpha", "run:alpha", "release", "acquire:beta", "run:beta", "release"]


def test_unexpected_runner_error_does_not_wedge_remaining_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression (coverage-guided-ci full-matrix local validation pass,
    2026-10-05): a single plugin's own contained-run failure must never
    propagate past the per-plugin loop and abort the rest of ``--all``/
    ``--changed`` -- only ``ContainmentError``/``CalledProcessError`` were
    ever caught here, so any OTHER exception type (e.g. a transient
    OS-level resource issue after many sequential heavy runs) would
    silently truncate validation of every plugin after the one that
    raised it."""
    events: list[str] = []

    class Lease:
        def release(self) -> None:
            events.append("release")

    def _run_plugin(name: str, *_args: Any, **_kwargs: Any) -> int:
        if name == "beta":
            raise MemoryError("simulated unexpected failure, not ContainmentError")
        events.append(f"run:{name}")
        return 0

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda _wait, root: recorded(events, f"acquire:{root.name}", Lease()),
    )
    monkeypatch.setattr(runner, "run_plugin", _run_plugin)

    # Exit code 1 (a real failure was recorded), but every target was
    # still attempted -- "gamma" (after the raising "beta") must have run.
    assert runner.main(["alpha", "beta", "gamma"]) == 1
    assert events == [
        "acquire:alpha", "run:alpha", "release",
        "acquire:beta", "release", "acquire:gamma", "run:gamma", "release",
    ]


def test_guards_also_take_environment_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    # `--guards` still reaches `_ensure_venv()` and so can rebuild/delete
    # the SHARED on-disk venv a concurrent bare admitted run may be
    # relying on mid-execution -- it is not exempt.
    class Lease:
        def release(self) -> None:
            pass

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    acquire_calls: list[float] = []
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda wait, root: recorded(acquire_calls, wait, Lease()),
    )
    monkeypatch.setattr(runner, "run_plugin", lambda *_args, **_kwargs: 0)

    assert runner.main(["alpha", "--guards"]) == 0
    assert acquire_calls == [0.0]


def test_collect_only_also_takes_environment_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    # `--collect-only` likewise reaches `_ensure_venv()` and so can
    # rebuild/delete the SHARED on-disk venv -- it is not exempt either.
    class Lease:
        def release(self) -> None:
            pass

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    acquire_calls: list[float] = []
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda wait, root: recorded(acquire_calls, wait, Lease()),
    )
    monkeypatch.setattr(runner, "run_plugin", lambda *_args, **_kwargs: 0)

    assert runner.main(["alpha", "--collect-only"]) == 0
    assert acquire_calls == [0.0]

@pytest.mark.parametrize("explicit", [False, True])
def test_native_owner_test_profile_is_scoped_and_explicit_limits_win(
    monkeypatch: pytest.MonkeyPatch, explicit: bool,
) -> None:
    class Lease:
        def release(self) -> None:
            pass

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(runner, "_acquire_admission", lambda wait, root: Lease())
    observed = {}

    def capture(name: str, _uv: str, **kwargs: Any) -> int:
        observed[name] = (
            kwargs["limits"].wall_seconds, kwargs["plugin_timeout"], kwargs["test_timeout"],
            kwargs["max_files_per_subsuite"],
        )
        return 0

    monkeypatch.setattr(runner, "run_plugin", capture)
    arguments = ["agent-pull-requests", "example"]
    if explicit:
        arguments += [
            "--timeout", "45", "--plugin-timeout", "75", "--test-timeout", "9",
            "--max-files-per-sub-suite", "3",
        ]
    assert runner.main(arguments) == 0
    if explicit:
        assert observed == {"agent-pull-requests": (45, 75, 9, 3), "example": (45, 75, 9, 3)}
    else:
        expected = (1200, 1800, 30) if sys.platform == "win32" else (300, 900, 30)
        assert observed == {"agent-pull-requests": (*expected, 10), "example": (300, 900, 30, 25)}


@pytest.mark.parametrize("size", [0, -1])
def test_explicit_file_group_limit_must_remain_positive(size: int) -> None:
    with pytest.raises(SystemExit) as error:
        runner.main(["agent-pull-requests", "--max-files-per-sub-suite", str(size)])
    assert error.value.code == 2


@pytest.mark.parametrize("profile_arguments", [
    ["--test-profile", "busy-host-functional"],
    ["--test-profile=busy-host-functional"],
])
def test_codespaces_functional_profile_preparation_strips_test_only_flags(
    monkeypatch: pytest.MonkeyPatch, profile_arguments: list[str],
) -> None:
    commands: list[list[str]] = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda command: recorded(commands, command, SimpleNamespace(returncode=0)),
    )
    arguments = [
        "agent-codespaces", *profile_arguments,
        "--test-file", "tests/test_snapshot_copy_contract.py",
    ]
    original = arguments.copy()
    with tempfile.TemporaryDirectory(prefix="profile-preparation-") as directory:
        root = Path(directory).resolve()
        network_scope.prepare_dependencies(
            "devcontainer", root, "container", root / "devcontainer.json",
            arguments, lambda flag: flag,
        )
    assert commands[0][-2:] == ["agent-codespaces", "--prepare-only"]
    assert arguments == original


@pytest.mark.parametrize("profile", [None, "standard", "busy-host-functional"])
@pytest.mark.parametrize("plugin,target", [
    ("agent-codespaces", "tests/test_installer_engine_adoption.py"),
    ("agent-ssh", "tests/test_snapshot_runtime.py"),
    ("agent-ssh", "tests/test_snapshot_windows_visibility.py"),
])
def test_codespaces_functional_profile_budgets_and_caps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], profile: str | None,
    tmp_path: Path, plugin: str, target: str,
) -> None:
    class Lease:
        def release(self) -> None:
            pass

    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    monkeypatch.setattr(runner, "_acquire_admission", lambda *_args: Lease())
    observed: list[dict[str, Any]] = []
    monkeypatch.setattr(runner, "run_plugin", lambda *a, **kw: recorded(observed, kw, 0))
    files = [target]
    arguments = [plugin, "--test-file", files[0], "--junit-dir", str(tmp_path / "reports")]
    if profile is not None:
        arguments += ["--test-profile", profile]
    assert runner.main(arguments) == 0
    options = observed[0]
    busy = profile == "busy-host-functional"
    assert options["limits"].wall_seconds == (900 if busy else 300)
    assert options["plugin_timeout"] == (2700 if busy else 900)
    assert options["test_timeout"] == 30
    assert options["test_files"] == tuple(files)
    assert options["kexpr"] is None and options["selection_file"] is None
    assert options["test_profile"] == (profile or "standard")
    assert options["junit_dir"] == tmp_path / "reports"
    assert (
        options["limits"].max_processes, options["limits"].max_memory_mb,
        options["limits"].max_temp_mb,
    ) == (128, 4096, 2048)
    output = capsys.readouterr().out
    assert ("functional evidence only, not original-budget/performance PASS" in output) == busy


@pytest.mark.parametrize("arguments", [
    ["agent-codespaces"],
    ["agent-bridge", "--test-file", "tests/test_installer_engine_adoption.py"],
    ["agent-codespaces", "--test-file", "tests/test_install_ps1_recovery.py"],
    ["agent-codespaces", "--test-file", "tests/test_installer_engine_adoption.py", "-k", "healthy"],
    ["agent-codespaces", "--test-file", "tests/test_installer_engine_adoption.py", "--timeout", "900"],
    ["agent-codespaces", "--test-file", "tests/test_installer_engine_adoption.py", "--plugin-timeout", "2700"],
    ["agent-codespaces", "--test-file", "tests/test_installer_engine_adoption.py", "--test-timeout", "180"],
    ["agent-codespaces", "--test-file", "tests/test_installer_engine_adoption.py", "--max-processes", "129"],
    ["agent-codespaces", "--test-file", "tests/test_installer_engine_adoption.py", "--max-memory-mb", "4097"],
    ["agent-codespaces", "--test-file", "tests/test_installer_engine_adoption.py", "--max-temp-mb", "2049"],
    ["agent-codespaces", "--test-file", "tests/test_installer_engine_adoption.py", "--collect-only"],
    ["agent-ssh", "--test-file", "tests/test_session_snapshot.py"],
    ["agent-ssh", "--test-file", "tests/test_snapshot_runtime.py", "-k", "native"],
    ["agent-ssh", "--test-file", "tests/test_snapshot_runtime.py", "--test-timeout", "300"],
    ["agent-ssh", "--test-file", "tests/test_snapshot_runtime.py", "--allow-host-state"],
    ["agent-ssh", "--test-file", "tests/test_snapshot_runtime.py", "--max-processes", "129"],
    ["agent-ssh", "agent-codespaces", "--test-file", "tests/test_snapshot_runtime.py"],
])
def test_codespaces_functional_profile_refuses_unqualified_scope_and_overrides(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str],
) -> None:
    monkeypatch.setattr(
        runner, "_acquire_admission",
        lambda *_args: pytest.fail("Invalid profile must fail before admission"),
    )
    with pytest.raises(SystemExit) as error:
        runner.main([*arguments, "--test-profile", "busy-host-functional"])
    assert error.value.code == 2


def test_codespaces_functional_profile_refuses_unknown_name() -> None:
    with pytest.raises(SystemExit) as error:
        runner.main(["agent-codespaces", "--test-profile", "busy"])
    assert error.value.code == 2


@pytest.mark.parametrize("profile", ["standard", "busy-host-functional"])
def test_codespaces_functional_profile_preserves_partitions_and_clears_ambient(
    codespaces_native_plan: CodespacesPlan, monkeypatch: pytest.MonkeyPatch, profile: str,
) -> None:
    plugin, _, _ = codespaces_native_plan
    monkeypatch.setenv(runner._CODESPACES_PROFILE_ENV, "unrequested-ambient-profile")
    monkeypatch.setenv(runner._SSH_PROFILE_ENV, "busy-host-functional")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only -k healthy")
    calls = []

    def execute(command: list[str], **kwargs: Any) -> int:
        calls.append(command)
        assert kwargs["env"].get(runner._CODESPACES_PROFILE_ENV) == (
            None if profile == "standard" else profile
        )
        assert kwargs["env"].get("PYTEST_ADDOPTS") == (
            "--collect-only -k healthy" if profile == "standard" else None
        )
        assert runner._SSH_PROFILE_ENV not in kwargs["env"]
        assert (
            kwargs["limits"].max_processes, kwargs["limits"].max_memory_mb,
            kwargs["limits"].max_temp_mb,
        ) == (128, 4096, 2048)
        return 0

    monkeypatch.setattr(runner, "run_contained", execute)
    assert runner.run_plugin(
        "agent-codespaces", "unused", reinstall=False, kexpr=None,
        test_files=(str(runner._CODESPACES_NATIVE_FILE),),
        limits=containment.Limits(wall_seconds=900 if profile != "standard" else 300),
        plugin_timeout=2700 if profile != "standard" else 900,
        test_timeout=30, max_files_per_subsuite=25, test_profile=profile,
    ) == 0
    assert len(calls) == 6
    assert [command[command.index("-k") + 1] for command in calls] == list(
        runner._CODESPACES_WINDOWS_PARTITIONS
    )
    assert all(str(runner._CODESPACES_NATIVE_FILE) in command for command in calls)
    assert plugin.is_dir()


@pytest.mark.parametrize("profile", ["standard", "busy-host-functional"])
def test_ssh_functional_profile_keeps_full_inventory_and_isolates_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, profile: str,
) -> None:
    plugin = tmp_path / "agent-ssh"
    tests = plugin / "tests"
    tests.mkdir(parents=True)
    targets = ("test_snapshot_runtime.py", "test_snapshot_windows_visibility.py")
    for target in targets:
        (tests / target).write_text("def test_native(): pass\n", encoding="utf-8")
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **k: Path(sys.executable))
    monkeypatch.setenv(runner._CODESPACES_PROFILE_ENV, "busy-host-functional")
    monkeypatch.setenv(runner._SSH_PROFILE_ENV, "busy-host-functional")
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only -k nonexistent")
    calls = []

    def execute(command: list[str], **kwargs: Any) -> int:
        calls.append(command)
        assert kwargs["env"].get(runner._SSH_PROFILE_ENV) == (
            None if profile == "standard" else profile
        )
        assert runner._CODESPACES_PROFILE_ENV not in kwargs["env"]
        assert kwargs["env"].get("PYTEST_ADDOPTS") == (
            "--collect-only -k nonexistent" if profile == "standard" else None
        )
        assert "-k" not in command and "--collect-only" not in command
        assert all(str(Path("tests") / target) in command for target in targets)
        return 0

    monkeypatch.setattr(runner, "run_contained", execute)
    assert runner.run_plugin(
        "agent-ssh", "unused", reinstall=False, kexpr=None,
        test_files=tuple(str(Path("tests") / target) for target in targets),
        test_profile=profile, limits=containment.Limits(),
        plugin_timeout=900, test_timeout=30, max_files_per_subsuite=25,
    ) == 0
    assert len(calls) == 1


@pytest.mark.portfolio_tier("T2")
@pytest.mark.parametrize("profile,status,executed", [
    ("standard", 0, False), ("busy-host-functional", 1, True),
])
def test_codespaces_functional_profile_runs_assertions_despite_ambient_collect_only(
    monkeypatch: pytest.MonkeyPatch, profile: str, status: int, executed: bool,
) -> None:
    with tempfile.TemporaryDirectory(prefix="profile-assertion-") as directory:
        root = Path(directory).resolve()
        tests = root / "tests"
        tests.mkdir()
        sentinel = root / "assertion-ran"
        target = Path("tests") / "test_installer_test_support.py"
        (root / target).write_text(
            "from pathlib import Path\n"
            "def test_assertions_execute():\n"
            f"    Path({str(sentinel)!r}).write_text('executed')\n"
            "    assert False, 'functional-profile-sentinel'\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(runner, "_plugin_dir", lambda _name: root)
        monkeypatch.setattr(runner, "_ensure_venv", lambda *a, **k: Path(sys.executable))
        monkeypatch.setenv("TEMP", str(root))
        monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
        assert runner.run_plugin(
            "agent-codespaces", "unused", reinstall=False, kexpr=None,
            test_files=(str(target),), test_profile=profile,
            limits=containment.Limits(wall_seconds=30), plugin_timeout=60,
            test_timeout=10, max_files_per_subsuite=25,
        ) == status
        assert sentinel.exists() is executed
        if executed:
            assert sentinel.read_text() == "executed"


def test_prepare_only_takes_environment_admission(monkeypatch: pytest.MonkeyPatch) -> None:
    # Like --guards/--collect-only, --prepare-only is NOT exempt: it can
    # rebuild/delete the SHARED on-disk venv a concurrent bare admitted
    # run may be relying on mid-execution.
    class Lease:
        def release(self) -> None:
            pass

    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")
    acquire_calls: list[float] = []
    monkeypatch.setattr(
        runner,
        "_acquire_admission",
        lambda wait, root: recorded(acquire_calls, wait, Lease()),
    )
    monkeypatch.setattr(runner, "run_plugin", lambda *_args, **_kwargs: 0)

    assert runner.main(["alpha", "--prepare-only"]) == 0
    assert acquire_calls == [0.0]


@pytest.mark.parametrize("collect_only", [False, True])
def test_unsafe_child_cleanup_refuses_before_preparation_or_collection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str], collect_only: bool,
) -> None:
    plugin = tmp_path / "alpha"
    tests = plugin / "tests"
    native = tests / "native"
    native.mkdir(parents=True)
    selected = tests / "test_safe.py"
    selected.write_text("def test_safe(): pass\n")
    child = native / "child.py"
    child.write_text("def main(): command('kill-server')\n")
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    monkeypatch.setattr(runner, "_test_file_groups", lambda *a, **k: [[selected]])
    monkeypatch.setattr(
        runner, "_ensure_venv",
        lambda *a, **k: pytest.fail("unsafe tests reached dependency preparation"),
    )
    monkeypatch.setattr(
        runner, "run_contained",
        lambda *a, **k: pytest.fail("unsafe tests reached execution"),
    )
    assert runner.run_plugin(
        "alpha", "uv", reinstall=False, kexpr=None, limits=runner.Limits(),
        plugin_timeout=60, test_timeout=30, max_files_per_subsuite=25,
        collect_only=collect_only, allow_host_state=True, allow_explicit_tiers=True,
    ) == 2
    assert str(child) in capsys.readouterr().err


def test_source_preflight_counts_against_plugin_budget_before_preparation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    plugin = tmp_path / "alpha"
    clock = [0.0]
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    monkeypatch.setattr(runner, "_test_file_groups", lambda *a, **k: [[plugin / "tests/test_one.py"]])
    monkeypatch.setattr(runner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runner, "check_test_sources", lambda _root: clock.__setitem__(0, 61.0))
    monkeypatch.setattr(
        runner, "_ensure_venv", lambda *a, **k: pytest.fail("expired preflight reached preparation"),
    )
    assert runner.run_plugin(
        "alpha", "uv", reinstall=False, kexpr=None, limits=runner.Limits(),
        plugin_timeout=60, test_timeout=30, max_files_per_subsuite=25,
    ) == 124
    assert "preflight exhausted" in capsys.readouterr().err


def test_run_plugin_prepare_only_never_invokes_pytest(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The whole point of `--prepare-only`: it must build/update the venv
    and return WITHOUT ever importing a single test module or
    `conftest.py` -- unlike `--collect-only`, which still runs pytest's
    own collection (and therefore executes that module-level code)."""
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    ensure_venv_calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        runner, "_ensure_venv",
        lambda name, uv, *, reinstall: recorded(ensure_venv_calls, (name, reinstall), Path("fake-py")),
    )
    monkeypatch.setattr(
        runner, "run_contained",
        lambda *_a, **_k: pytest.fail("--prepare-only must never invoke pytest"),
    )

    rc = runner.run_plugin(
        "alpha", "uv",
        reinstall=False, kexpr=None, limits=runner.Limits(
            wall_seconds=60, max_processes=10, max_memory_mb=100,
            max_temp_mb=100, poll_seconds=0.1,
        ),
        plugin_timeout=60.0, test_timeout=10.0, max_files_per_subsuite=25,
        prepare_only=True,
    )
    assert rc == 0
    assert ensure_venv_calls == [("alpha", False)]


def test_host_state_requires_explicit_tier_opt_in(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        runner.main(["--allow-host-state"])
    assert exc.value.code == 2
    assert "--allow-host-state requires --allow-explicit-tiers" in capsys.readouterr().err


def test_admission_wait_rejects_infinite_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # `float("inf")` parses cleanly and isn't `< 0`, so it would otherwise
    # slip past a bare negative check and poll forever under contention,
    # defeating the documented bounded-wait contract.
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    with pytest.raises(SystemExit) as exc:
        runner.main(["alpha", "--admission-wait", "inf"])
    assert exc.value.code == 2
    assert "admission_wait must be a non-negative, finite number" in capsys.readouterr().err


def test_admission_wait_rejects_nan_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    # `float("nan")` also parses cleanly, and `nan < 0` is False, so a
    # bare negative check alone would silently accept it too.
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    with pytest.raises(SystemExit) as exc:
        runner.main(["alpha", "--admission-wait", "nan"])
    assert exc.value.code == 2
    assert "admission_wait must be a non-negative, finite number" in capsys.readouterr().err



class _FakeCompletedProcess:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_changed_plugins_always_includes_cross_plugin_contract_testers(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PR touching only plugin 'foo' must still schedule any registered
    cross-plugin contract tester (regression: ThomasMichon/copilot-extensions
    #4166 broke copilot-extensions-maintainer's own marketplace-wide contract
    test without touching a single file under that plugin, so the PR's own
    --changed-scoped CI never ran it)."""
    calls = iter([
        _FakeCompletedProcess("plugins/foo/src/foo.py\n"),
        _FakeCompletedProcess(""),
    ])
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: next(calls))
    monkeypatch.setattr(
        runner, "_has_suite",
        lambda name: name in {"foo", "copilot-extensions-maintainer", "customizing-copilot"},
    )
    result = runner.changed_plugins("origin/main")
    assert result == sorted({"foo", "copilot-extensions-maintainer", "customizing-copilot"})


def test_changed_plugins_empty_diff_schedules_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real change (e.g. a docs-only or non-plugin diff) must not force
    the contract testers to run either -- only an actual plugin change
    should pull them in."""
    calls = iter([
        _FakeCompletedProcess(""),
        _FakeCompletedProcess(""),
    ])
    monkeypatch.setattr(runner.subprocess, "run", lambda *a, **k: next(calls))
    monkeypatch.setattr(runner, "_has_suite", lambda name: True)
    assert runner.changed_plugins("origin/main") == []
