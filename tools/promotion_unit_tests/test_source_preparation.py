"""Portable source-owner boundaries; process qualification stays in its own lane."""
from __future__ import annotations

import importlib.metadata
from contextlib import contextmanager, nullcontext
from collections.abc import Iterator
from pathlib import Path
import sys
import subprocess
import shutil
import time
from types import SimpleNamespace, ModuleType
from typing import Any

import pytest

from devcontainer_source_identity import check_snapshot_ancestors
import plugin_test_cache as cache
import run_python_with
from test_run_plugin_tests import runner
import promotion_source_archive as source_io
import plugin_test_environment
import plugin_test_owned_scratch as owner
import plugin_test_source_versions as versions
from test_run_plugin_tests import preparation_logic_only as preparation_logic_only
from plugin_test_containment import ContainmentError, Limits
import plugin_test_containment as containment
import plugin_test_lifecycle
import plugin_test_cleanup
from native_test_paths import NativeTemporaryDirectory


def test_direct_runner_dispatches_missing_tooling_through_existing_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[list[str]] = []
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0")
    monkeypatch.setattr(sys, "argv", ["runner", "example", "--prepare-only"])

    def prepare(arguments: list[str]) -> int:
        requests.append(arguments)
        return 17

    monkeypatch.setattr(run_python_with, "main", prepare)
    assert cache.run_with_test_tooling(lambda: pytest.fail("unprepared entrypoint ran")) == 17
    assert requests[0][:3] == ["--with", "packaging>=23", "--"]
    assert Path(requests[0][3]).name == "run-plugin-tests.py"
    assert requests[0][4:] == ["example", "--prepare-only"]


def test_snapshot_source_paths_reject_external_ancestors(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "owned.py").write_text("SOURCE = True\n")
    check_snapshot_ancestors(repo, ["owned.py"])
    external = tmp_path / "external"
    external.mkdir()
    (external / "private.py").write_text("NOT_SNAPSHOT_INPUT = True\n")
    try:
        (repo / "indirect").symlink_to(external, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"native source-path symlink fixture unavailable: {error}")
    with pytest.raises(SystemExit, match="outside"):
        check_snapshot_ancestors(repo, ["indirect/private.py"])


def test_ordinary_and_owned_preparation_share_process_tree_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    private = tmp_path / "private"
    private.mkdir()
    @contextmanager
    def scope(*args: object, **kwargs: object) -> Iterator[str]:
        yield str(private)
    calls: list[object] = []
    monkeypatch.setattr(runner, "preparation_scope", scope)
    monkeypatch.setattr(runner, "_venv_root", lambda: tmp_path / "retained")
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: tmp_path)
    monkeypatch.setattr(runner, "record_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(runner, "runs_root", lambda repo: tmp_path)
    monkeypatch.setattr(
        plugin_test_environment, "preparation_environment",
        lambda environment, *args, **kwargs: dict(environment),
    )
    expected = tmp_path / "fixture-python"
    selected = runner.Limits(
        wall_seconds=3, max_processes=4, max_memory_mb=100, max_temp_mb=100,
    )
    monkeypatch.setattr(runner.time, "monotonic", lambda: 100.0)
    def prepare(*args: Any, **kwargs: Any) -> Path:
        calls.append(kwargs)
        assert kwargs["deadline"] == 103.0
        assert kwargs["limits"] is selected
        assert kwargs["sandbox"] == private
        return expected
    monkeypatch.setattr(runner, "_prepare_venv", prepare)
    monkeypatch.setitem(sys.modules, runner.__name__, ModuleType(runner.__name__))
    for scratch_owner in (None, "fixture/source/owner"):
        monkeypatch.setattr(runner, "OWNED_SCRATCH_OWNER", scratch_owner)
        assert runner._ensure_venv("fixture", "uv", reinstall=False, wall_seconds=10, limits=selected) == expected
    assert len(calls) == 2


def test_snapshot_index_is_explicit_without_trusting_inherited_selectors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    environments: list[dict[str, str]] = []
    def run(arguments: list[str], **options: Any) -> subprocess.CompletedProcess[bytes]:
        environments.append(options["env"])
        return subprocess.CompletedProcess(arguments, 0, b"tree\n", b"")
    monkeypatch.setattr(source_io, "run_composition_child", run)
    monkeypatch.setenv("GIT_INDEX_FILE", "foreign-index")
    monkeypatch.setenv("GIT_WORK_TREE", "foreign-tree")
    selected = tmp_path / "test-source-index"
    assert source_io.git_text(tmp_path, ["write-tree"], index_file=selected) == "tree"
    assert source_io.git_text(tmp_path, ["write-tree"]) == "tree"
    assert environments[0]["GIT_INDEX_FILE"] == str(selected)
    assert "GIT_WORK_TREE" not in environments[0]
    assert "GIT_INDEX_FILE" not in environments[1]


def test_settled_cleanup_deadline_failure_invalidates_pending_and_allows_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    venv, private = tmp_path / "venv", tmp_path / "private"
    private.mkdir()
    with pytest.raises(ValueError, match="deadline"):
        with source_io.composition_budget(2.0), versions.preparation_ready(venv) as cleanup:
            versions.bind_preparation_owner(venv, str(private))
            versions.stage_ready(venv, "not reusable")
            private.rmdir()
            cleanup["confirmed"] = True
            clock[0] = 3.0
    assert not list(venv.glob(".dep-fingerprint*"))
    with versions.preparation_ready(venv) as cleanup:
        versions.bind_preparation_owner(venv, str(private))
        versions.stage_ready(venv, "ready")
        cleanup["confirmed"] = True
    assert (venv / ".dep-fingerprint").read_text() == "ready"


@pytest.mark.parametrize("during_allocation", [False, True])
def test_preallocation_refusal_allows_retry_but_uncertain_allocation_retains_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, during_allocation: bool,
) -> None:
    venv, private = tmp_path / "venv", tmp_path / "private"
    refusal = ContainmentError("readiness refused")
    attempts: list[str] = []
    def initialize(self: Any, *args: Any, **kwargs: Any) -> None:
        attempts.append("readiness")
        if not during_allocation:
            raise refusal
    def enter(self: Any) -> str:
        attempts.append("allocation")
        raise refusal
    monkeypatch.setattr(owner.OwnedTestSandbox, "__init__", initialize)
    monkeypatch.setattr(owner.OwnedTestSandbox, "__enter__", enter)
    with pytest.raises(ContainmentError, match="readiness refused"):
        with owner.preparation_scope("fixture/source/owner", tmp_path, runner._TestSandbox,
                                     venv, deadline=None):
            pytest.fail("refused sandbox yielded")
    assert (venv / ".dep-fingerprint.pending").exists() is during_allocation
    assert attempts == (["readiness", "allocation"] if during_allocation else ["readiness"])
    if during_allocation:
        with pytest.raises(ValueError, match="unsettled"):
            with owner.preparation_scope("fixture/source/owner", tmp_path, runner._TestSandbox,
                                         venv, deadline=None):
                pytest.fail("uncertain allocation retried")
        return
    monkeypatch.setattr(owner.OwnedTestSandbox, "__init__", lambda self, *args, **kwargs: None)
    def admitted(self: Any) -> str:
        self.cleanup_settled = False
        private.mkdir()
        return str(private)
    def settled(self: Any, *args: Any) -> bool:
        private.rmdir()
        self.cleanup_settled = True
        return False
    monkeypatch.setattr(owner.OwnedTestSandbox, "__enter__", admitted)
    monkeypatch.setattr(owner.OwnedTestSandbox, "__exit__", settled)
    with owner.preparation_scope("fixture/source/owner", tmp_path, runner._TestSandbox,
                                 venv, deadline=None):
        versions.stage_ready(venv, "ready after refusal")
    assert (venv / ".dep-fingerprint").read_text() == "ready after refusal"
    assert not (venv / ".dep-fingerprint.pending").exists()


def test_installed_packaging_needs_no_tooling_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from packaging.version import Version

    assert Version(importlib.metadata.version("packaging")) >= Version("23")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "empty-cache"))
    monkeypatch.setattr(run_python_with, "main", lambda arguments: pytest.fail("offline cache preparation"))
    assert cache.run_with_test_tooling(lambda: 19) == 19
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("delay", ["startup", "settled-teardown", "unsettled-teardown"])
def test_absolute_preparation_deadline_includes_startup_and_settlement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delay: str,
) -> None:
    clock = [0.0]
    waits: list[float] = []
    deadlines: list[float] = []
    class Process:
        pid = 123
        def wait(self, *, timeout: float) -> int:
            waits.append(timeout)
            if delay == "unsettled-teardown":
                raise subprocess.TimeoutExpired("controller", timeout)
            return 0
        def kill(self) -> None:
            pass
    def spawn(*args: Any, **kwargs: Any) -> Process:
        if delay == "startup":
            clock[0] = 7.0
        return Process()
    def terminate(*args: Any, deadline: float) -> None:
        deadlines.append(deadline)
        if delay != "startup":
            clock[0] = 11.0
    @contextmanager
    def lifecycle(**kwargs: Any) -> Iterator[dict[str, str]]:
        assert kwargs["deadline"] == 10.0
        yield {}
    monkeypatch.setattr(containment, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(containment, "os", SimpleNamespace(
        name="posix", pipe=lambda: (1, 2), set_blocking=lambda *args: None,
        close=lambda fd: None, getpid=lambda: 456,
    ))
    monkeypatch.setattr(containment.subprocess, "Popen", spawn)
    monkeypatch.setattr(containment, "_posix_group_identity", lambda pid: (pid, pid))
    monkeypatch.setattr(containment, "_posix_exit_code", lambda fd: 0)
    monkeypatch.setattr(containment, "_terminate_posix_group", terminate)
    monkeypatch.setattr(containment, "daemon_test_lifecycle", lifecycle)
    monkeypatch.setattr(containment, "_read_registry_environment", lambda: None)
    monkeypatch.setattr(containment, "_registry_environment_drift", lambda _: [])
    monkeypatch.setattr(plugin_test_lifecycle, "_native_instance_pins", lambda *args: nullcontext(None))
    limits = Limits(wall_seconds=10, absolute_deadline=10)
    def run() -> int:
        return containment.run_contained(["fixture"], cwd=tmp_path, env={}, sandbox=tmp_path, limits=limits)
    if delay == "unsettled-teardown":
        with pytest.raises(containment.UnreapedProcessError, match="reaped"):
            run()
    else:
        assert run() == 124
    assert deadlines == [10.0]
    assert waits == ([3.0] if delay == "startup" else [0.0, 0.0] if delay == "unsettled-teardown" else [0.0])
    if delay != "unsettled-teardown":
        assert not (tmp_path / ".containment-ready").exists()


@pytest.mark.parametrize("outcome", ["expired-before", "retry-expired", "settled-late"])
def test_sandbox_cleanup_keeps_the_original_preparation_deadline_and_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str,
) -> None:
    clock = [2.0 if outcome == "expired-before" else 0.0]
    private, venv = tmp_path / "private", tmp_path / "venv"
    private.mkdir()
    scope: Any = object.__new__(containment.TestSandbox)
    scope.name, scope.retained, scope.cleanup_deadline = str(private), False, 1.0
    scope._finalizer = SimpleNamespace(detach=lambda: None)
    calls: list[bool] = []
    def cleanup(*args: Any) -> None:
        calls.append(True)
        if outcome == "settled-late":
            private.rmdir()
            clock[0] = 2.0
            return
        error = PermissionError("controlled sharing violation")
        setattr(error, "winerror", 32)
        raise error
    def sleep(seconds: float) -> None:
        assert clock[0] + seconds <= 1.0
        clock[0] += seconds
    monkeypatch.setattr(plugin_test_cleanup, "sys", SimpleNamespace(platform="win32", stderr=sys.stderr))
    monkeypatch.setattr(plugin_test_cleanup, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    monkeypatch.setattr(NativeTemporaryDirectory, "__exit__", cleanup)
    expected = TimeoutError if outcome == "settled-late" else plugin_test_cleanup.SandboxCleanupError
    with pytest.raises(expected):
        with versions.preparation_ready(venv) as state:
            versions.bind_preparation_owner(venv, str(private))
            try:
                scope.__exit__(None, None, None)
            finally:
                state["confirmed"] = not scope.retained and not private.exists()
    assert scope.retained is (outcome != "settled-late")
    assert (venv / ".dep-fingerprint.pending").exists() is (outcome != "settled-late")
    assert bool(calls) is (outcome != "expired-before")
    if outcome == "retry-expired":
        assert clock[0] == 1.0


@pytest.mark.parametrize("bounded", [False, True])
def test_darwin_cleanup_census_receives_the_original_deadline(
    monkeypatch: pytest.MonkeyPatch, bounded: bool,
) -> None:
    observations: list[dict[str, Any]] = []
    def census(pgid: int, **options: Any) -> tuple[int, int]:
        observations.append(options)
        if bounded:
            assert options == {"live_only": True, "deadline": 1.0}
            raise TimeoutError("controlled census deadline exhausted")
        assert options == {"live_only": True}
        return (0, 0)
    monkeypatch.setattr(containment, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(containment, "_darwin_group_usage", census)
    monkeypatch.setattr(containment, "_posix_group_identity", lambda pid: (pid, pid))
    monkeypatch.setattr(containment, "os", SimpleNamespace(killpg=lambda *args: None))
    monkeypatch.setattr(containment, "time", SimpleNamespace(monotonic=lambda: 0.0))
    if bounded:
        with pytest.raises(containment.UnreapedProcessError, match="verify POSIX"):
            containment._terminate_posix_group(7, (7, 7), deadline=1.0)
    else:
        assert containment._posix_live_group_usage(7) == (0, 0)
    assert len(observations) == 1


def test_owned_settled_failure_keeps_diagnostics_without_blocking_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    allocations: list[Path] = []
    def allocate(*args: Any, **kwargs: Any) -> dict[str, str]:
        directory = tmp_path / f"diagnostic-{len(allocations)}"
        directory.mkdir()
        allocations.append(directory)
        return {"path": str(directory), "token": str(len(allocations) - 1)}
    def settle(ledger: Path, subject: str, token: str, **options: Any) -> dict[str, Any]:
        directory = allocations[int(token)]
        for child in directory.iterdir():
            child.unlink()
        if options["diagnostic"] is not None:
            (directory / "diagnostic").write_bytes(options["diagnostic"])
        return {}
    def dispose(ledger: Path, subject: str, token: str, **options: Any) -> dict[str, str]:
        shutil.rmtree(allocations[int(token)])
        return {"outcome": "removed"}
    def scope(*args: Any, **kwargs: Any) -> owner.OwnedTestSandbox:
        result = object.__new__(owner.OwnedTestSandbox)
        result.owner, result.ledger = "fixture/source/owner", tmp_path / "ledger"
        result.stage, result.operation_id = "preparation", "fixture"
        result.policy = {"scratch_root": str(tmp_path), "failure_evidence": {}}
        result.forbidden, result.results = (), []
        result.group_count, result.failed, result.retained, result.cleanup_settled = 0, False, False, False
        result.allocation = None
        result.provider = SimpleNamespace(allocate=allocate, settle_after_cleanup=settle, dispose_consumed=dispose)
        return result
    monkeypatch.setattr(owner, "test_sandbox", scope)
    venv = tmp_path / "venv"
    with pytest.raises(RuntimeError, match="resolver failure"):
        with owner.preparation_scope("fixture/source/owner", tmp_path, scope, venv, deadline=None):
            raise RuntimeError("resolver failure")
    diagnostic = (allocations[0] / "diagnostic").read_bytes()
    assert not (venv / ".dep-fingerprint.pending").exists()
    with owner.preparation_scope("fixture/source/owner", tmp_path, scope, venv, deadline=None):
        versions.stage_ready(venv, "ready")
    assert (venv / ".dep-fingerprint").read_text() == "ready"
    assert (allocations[0] / "diagnostic").read_bytes() == diagnostic


def test_build_command_preserves_original_resource_caps_and_remaining_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = Limits(wall_seconds=3, max_processes=4, max_memory_mb=100, max_temp_mb=200)
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    def contained(arguments: list[str], **options: Any) -> int:
        applied = options["limits"]
        assert applied.wall_seconds == 2
        assert (applied.max_processes, applied.max_memory_mb, applied.max_temp_mb) == (4, 100, 200)
        assert options["sandbox"] == tmp_path
        return 0
    monkeypatch.setattr(owner, "run_contained", contained)
    assert owner.run_preparation_command(
        ["fixture-uv"], deadline=102, limits=selected, sandbox=tmp_path, env={}, cwd=tmp_path,
    ).returncode == 0


def test_auth_metadata_uses_the_same_preparation_deadline_without_widening_stream_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agent_procutil.owned_process as native
    import installer_dependency_policy
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    home, sandbox, project = (tmp_path / name for name in ("home", "sandbox", "project"))
    for path in (home, sandbox, project):
        path.mkdir()
    credential_directory = tmp_path / "credentials"
    def metadata(arguments: list[str], **options: Any) -> subprocess.CompletedProcess[bytes]:
        assert options["timeout"] == 3
        assert options["budget"].deadline == 103
        assert (options["max_stdout_bytes"], options["max_stderr_bytes"]) == (32768, 16384)
        return subprocess.CompletedProcess(arguments, 0, (str(credential_directory) + "\n").encode(), b"")
    monkeypatch.setattr(native, "run_caller_owned", metadata)
    monkeypatch.setattr(installer_dependency_policy, "_config_inputs", lambda *args: [])
    result = plugin_test_environment.preparation_environment(
        {"HOME": str(home), "USERPROFILE": str(home)}, sandbox, project,
        uv=sys.executable, deadline=103,
    )
    assert result["UV_CREDENTIALS_DIR"] == str(credential_directory)


def test_build_failure_emits_captured_diagnostic_before_reraising_and_closes_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import installer_dependency_policy
    closed: list[bool] = []
    @contextmanager
    def retain(*args: Any, **kwargs: Any) -> Iterator[SimpleNamespace]:
        try:
            yield SimpleNamespace(argv=("fixture-uv",), environment={}, cwd=tmp_path)
        finally:
            closed.append(True)
    failure = subprocess.CalledProcessError(1, ["fixture-uv"], stderr=b"resolver refused a bound\n")
    def execute(*args: Any, **kwargs: Any) -> None:
        raise failure
    monkeypatch.setattr(installer_dependency_policy, "retain_uv_install", retain)
    monkeypatch.setattr(owner, "run_preparation_command", execute)
    with pytest.raises(subprocess.CalledProcessError) as caught:
        owner.run_preparation_install(
            "fixture-uv", [], deadline=time.monotonic() + 10, limits=Limits(), sandbox=tmp_path,
        )
    assert caught.value is failure
    assert capsys.readouterr().err == "resolver refused a bound\n"
    assert closed == [True]


def test_manager_routes_both_installs_through_the_prepared_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, preparation_logic_only: None,
) -> None:
    """Guard routing; actual allocation and resolution use the real UV controls."""
    raw, prepared = tmp_path / "raw", tmp_path / "prepared"
    manager, bridge = prepared / "worktree-manager", prepared / "plugins/agent-bridge"
    for path in (raw, manager, bridge):
        path.mkdir(parents=True)
    constraint = prepared / "requirements.constraints"
    constraint.write_text("shared>=1\n")
    monkeypatch.setenv("AGENT_SCRATCH_ROOT", str(tmp_path / "scratch"))
    monkeypatch.setattr(runner, "REPO", raw)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    monkeypatch.setattr(runner, "_plugin_dir", lambda name: raw)
    monkeypatch.setattr(runner, "_dep_fingerprint", lambda name: "fixture")
    monkeypatch.setattr(runner, "_has_pyproject", lambda name: True)
    monkeypatch.setattr(runner, "has_dev_extra", lambda path: False)
    monkeypatch.setattr(runner, "retained_build_resources", lambda *args: ())
    monkeypatch.setattr(runner, "prepare_version_sources", lambda *args, **kwargs: (
        prepared, manager, {"opaque_validated_source_record": True},
    ))
    monkeypatch.setattr(plugin_test_environment, "preparation_environment",
                        lambda environment, *args, **kwargs: dict(environment))
    monkeypatch.setattr(runner.subprocess, "run", lambda arguments, **options: subprocess.CompletedProcess(arguments, 0))
    def sources(name: str, plugin: Path, repository: Path, venv: Path) -> list[str]:
        assert (name, plugin, repository) == ("worktree-manager", manager, prepared)
        return ["--constraint", str(constraint)]
    monkeypatch.setattr(runner, "source_resolution_arguments", sources)
    installs: list[tuple[list[str], dict[str, Any]]] = []
    def install(uv: str, arguments: list[str], **options: Any) -> None:
        installs.append((arguments, options))
        if len(installs) == 2:
            raise subprocess.CalledProcessError(1, arguments)
    monkeypatch.setattr(runner, "run_uv_install", install)
    with pytest.raises(subprocess.CalledProcessError):
        runner._ensure_venv("worktree-manager", "fixture-uv", reinstall=False)
    assert [options["cwd"] for _, options in installs] == [manager, bridge]
    assert all(options["trusted_source_roots"] == (prepared,) for _, options in installs)
    assert all(arguments[arguments.index("--constraint") + 1] == str(constraint) for arguments, _ in installs)


@pytest.mark.parametrize("marker,extra,expected", [
    ("extra == 'feature' or python_version < '3'", "feature", ""),
    ("extra == 'feature' and python_version < '3'", "other", None),
    ("(extra == 'feature' or os_name == 'nt') and python_version >= '3'", "other",
     "os_name == 'nt' and python_version >= '3'"),
    ("'feature' == extra and (os_name == 'nt' or python_version >= '3')", "feature",
     "os_name == 'nt' or python_version >= '3'"),
])
def test_extra_binding_folds_only_extra_predicates(
    marker: str, extra: str, expected: str | None,
) -> None:
    from packaging.markers import Marker

    bound = cache._bind_extra(marker, extra)
    if not expected:
        assert bound == expected
        return
    assert bound
    actual, target = Marker(bound), Marker(expected)
    for os_name in ("nt", "posix"):
        for python_version in ("2.7", "3.12"):
            environment = {"os_name": os_name, "python_version": python_version}
            assert actual.evaluate(environment) == target.evaluate(environment)
