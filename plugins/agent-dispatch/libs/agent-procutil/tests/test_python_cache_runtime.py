"""Runtime-owner custody and finite aggregate collection controls."""
from contextlib import contextmanager
import importlib.util
import os
import sys
import venv
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator

import pytest


@pytest.fixture
def owner() -> Any:
    source = Path(__file__).resolve().parents[3] / "libs/versioned-runtime/versioned_runtime_cache.py"
    spec = importlib.util.spec_from_file_location("_cache_runtime_test", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def report(*, size: int = 0, completed: int = 0, name: str = "unsealed-one", status: str = "held") -> SimpleNamespace:
    return SimpleNamespace(
        bytes=size, files=bool(size), completed=completed, pending=not completed,
        namespaces=1, namespace_total=1, next_cursor=name, liveness="process-scan",
        inventory_complete=True, namespace_names=(name,),
        candidates=[{"name": name, "generation": [1, 2, "3"], "bytes": 100,
                     "status": status}], reasons={}, errors={},
    )


def run(
    owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *,
    provider: Callable[..., Any], custody: Any = None, protected: Any = None, **options: Any,
) -> dict[str, Any]:
    @contextmanager
    def available(root: Path, slots: list[str]) -> Iterator[None]:
        yield

    monkeypatch.setattr(owner, "_collector", lambda **kwargs: SimpleNamespace(collect_prefixes=provider))
    result: dict[str, Any] = owner.collect_prefixes(
        tmp_path, custody=custody or available,
        protected=protected or (lambda **kwargs: set()),
        version_dir=lambda root, slot: root / "versions" / slot,
        busy_errors=(TimeoutError,), **options,
    )
    return result


@pytest.mark.parametrize("hold", ["protected", "present", "busy", "none"])
def test_actual_retirement_callback_keeps_authority_inside_custody(
    owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, hold: str,
) -> None:
    held: list[bool] = []

    @contextmanager
    def custody(root: Path, slots: list[str]) -> Iterator[None]:
        assert slots == ["1.0"]
        if hold == "busy":
            raise TimeoutError("busy")
        held.append(True)
        try:
            yield
        finally:
            held.pop()

    def protected(**kwargs: Any) -> set[str]:
        assert held
        return {"1.0"} if hold == "protected" else set()

    if hold == "present":
        (tmp_path / "versions" / "1.0").mkdir(parents=True)

    def collect(root: Path, *, retirement: Any, **kwargs: Any) -> SimpleNamespace:
        with retirement(["1.0"], check=lambda: None) as reason:
            assert reason == {
                "protected": "runtime-protected", "present": "runtime-present",
                "busy": "runtime-busy", "none": "",
            }[hold]
            assert bool(held) == (hold != "busy")
        return report()

    result = run(owner, monkeypatch, tmp_path, provider=collect, custody=custody, protected=protected)
    assert result["summary"]["held_bytes"] == 100
    assert not held


def test_body_timeout_is_not_recategorized_as_acquisition_contention(
    owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def collect(root: Path, *, retirement: Any, **kwargs: Any) -> SimpleNamespace:
        with retirement(["1.0"], check=lambda: None):
            raise TimeoutError("body deadline")

    with pytest.raises(TimeoutError, match="body deadline"):
        run(owner, monkeypatch, tmp_path, provider=collect)


def test_drain_stops_after_a_full_idle_cycle(owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[dict[str, Any]] = []

    def collect(root: Path, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return report()

    result = run(owner, monkeypatch, tmp_path, provider=collect, drain=True, details=True)
    assert len(calls) == 1
    assert result["scan_complete"] and result["pending"]
    assert result["summary"]["measured_bytes"] == 100
    assert result["summary"]["removed_bytes"] == 0


def test_drain_accumulates_effects_not_repeated_observations(
    owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    calls: list[dict[str, Any]] = []

    def collect(root: Path, **kwargs: Any) -> SimpleNamespace:
        calls.append(kwargs)
        return report(size=40, completed=int(len(calls) == 2), status="partial")

    result = run(owner, monkeypatch, tmp_path, provider=collect, drain=True)
    assert len(calls) == 2
    assert result["summary"]["removed_bytes"] == 80
    assert result["summary"]["measured_bytes"] == 100
    assert calls[1]["after"] == "unsealed-one"


@pytest.mark.parametrize("options", [
    {"seconds": 0}, {"seconds": float("nan")}, {"seconds": 3601},
    {"byte_limit": 0}, {"entries": True}, {"namespaces": 4097},
])
def test_invalid_aggregate_budget_refuses_before_loading_provider(
    owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, options: dict[str, Any],
) -> None:
    def unexpected(root: Path, **kwargs: Any) -> Any:
        pytest.fail("invalid options reached provider")

    with pytest.raises(ValueError):
        run(owner, monkeypatch, tmp_path, provider=unexpected, **options)


def test_maintenance_failure_does_not_change_runtime_gc_result(
    owner: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(owner, "maintain", lambda *args, **kwargs: None)

    def automatic() -> None:
        raise OSError("private failure text")

    result = owner.gc(
        tmp_path, candidates=lambda root: [], cleanup_lease=None, protected=None,
        version_dir=None, is_link=None, slot_age_days=None, remove_slot=None,
        min_age_days=0, automatic=automatic,
    )
    assert result == []
    assert "catch-up unavailable (OSError)" in capsys.readouterr().err


def test_dry_run_does_not_take_writing_maintenance_guard(
    owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def unexpected() -> Any:
        pytest.fail("dry run acquired writing custody")

    result = run(owner, monkeypatch, tmp_path, provider=lambda *args, **kwargs: report(),
                 maintenance=unexpected)
    assert not result["apply"]


def test_budget_stop_counts_unvisited_prefix_after_prior_removals(
    owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from agent_procutil import python_cache_status

    calls = 0
    monkeypatch.setattr(python_cache_status, "write", lambda *args: None)

    def collect(root: Path, **options: Any) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        value = report(size=100, completed=1, name=str(calls), status="removed")
        value.namespace_names = ("1", "2", "3") if calls == 1 else ("2", "3")
        value.namespace_total = len(value.namespace_names)
        value.pending = True
        return value

    value = run(owner, monkeypatch, tmp_path, provider=collect, apply=True,
                drain=True, byte_limit=200)
    assert calls == 2 and value["summary"]["removed_bytes"] == 200
    assert value["summary"]["unmeasured_prefixes"] == 1
    assert not value["scan_complete"] and value["pending"]


def test_failed_top_level_inventory_remains_unknown_not_empty(
    owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    def collect(root: Path, **options: Any) -> SimpleNamespace:
        value = report()
        value.namespaces, value.namespace_total = 0, 0
        value.namespace_names, value.candidates = (), []
        value.inventory_complete = False
        value.reasons = {"legacy-inventory-unavailable": 1}
        return value

    value = run(owner, monkeypatch, tmp_path, provider=collect, drain=True)
    assert value["summary"]["unmeasured_prefixes"] is None
    assert value["pending"] and not value["scan_complete"]
    assert not value["inventory_complete"]


@pytest.mark.parametrize("apply", [False, True])
def test_common_collection_api_persists_terminal_apply_only(
    owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, apply: bool,
) -> None:
    from agent_procutil import python_cache_status

    saved: list[tuple[Path, dict[str, Any]]] = []
    monkeypatch.setattr(python_cache_status, "write", lambda root, value: saved.append((root, value)))
    result = run(owner, monkeypatch, tmp_path, apply=apply, provider=lambda *args, **kwargs: report(
        size=100 if apply else 0, completed=int(apply), status="removed" if apply else "eligible",
    ))
    assert len(saved) == int(apply)
    if apply:
        assert saved[0][0] == tmp_path
        assert saved[0][1]["summary"] == result["summary"]
        assert saved[0][1]["last_worker_state"] == "finished"


@pytest.mark.skipif(os.name != "nt", reason="automatic legacy catch-up is Windows-only")
@pytest.mark.parametrize("kind", ["selected", "old-owner", "recent"])
def test_automatic_work_uses_selected_retained_finite_owner_only(
    owner: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, kind: str,
) -> None:
    from agent_procutil import daemon_instances, python_cache_status, python_cache_store

    root = tmp_path / "runtime"
    slot = root / "versions" / "1.0"
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir(parents=True)
    script = bootstrap / "versioned_runtime.py"
    script.write_text("" if kind == "old-owner" else "PYTHON_CACHE_RECLAMATION_CAPABILITY = 'v1'")
    (bootstrap / "runtime_launcher.py").write_text("retained launcher fixture")
    sdk = slot / ".venv" / "Lib" / "site-packages"
    (sdk / "agent_procutil").mkdir(parents=True)
    (sdk / "agent_procutil" / "_daemon_instance_child.py").write_text("fixture SDK")
    runtime = SimpleNamespace(
        current_version=lambda root: "1.0", is_complete=lambda root, version: True,
        version_dir=lambda root, version: slot,
        slot_python=lambda root, version: slot / ".venv" / "Scripts" / "python.exe",
    )
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def spawn(argv: list[str], **kwargs: Any) -> None:
        calls.append((argv, kwargs))

    monkeypatch.setattr(daemon_instances, "spawn_finite_worker", spawn)
    monkeypatch.setattr(python_cache_store, "user_cache", lambda: tmp_path / "os-cache")
    monkeypatch.setattr(python_cache_status, "describe", lambda *args, **kwargs: {
        "observation_age_seconds": 10 if kind == "recent" else 7200,
    })
    owner.schedule(root, runtime)
    if kind != "selected":
        assert not calls
        return
    argv, options = calls[0]
    assert str(bootstrap / "runtime_launcher.py") in argv
    assert str(script) in argv
    assert argv[-2:] == ["--apply", "--automatic"]
    assert options["instance"].runtime_root == root
    assert options["instance"].source == script
    assert options["instance"].lease is None
    assert options["cwd"] == root
    assert options["log_path"].name == "legacy-prefix-maintenance.log"
    assert options["python_bootstrap_root"] == sdk


@pytest.mark.skipif(os.name != "nt", reason="native retained Windows finite worker")
def test_finite_worker_runs_retained_cli_and_persists_terminal_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import daemon_instances, python_cache_status, python_cache_store

    source = Path(__file__).resolve().parents[3] / "libs/update-driver/scripts/versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("_native_cache_runtime_fixture", source)
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    process: Any = None
    try:
        spec.loader.exec_module(runtime)
        root = tmp_path / "runtime"
        slot = runtime.slot(root, "1.0")
        venv.EnvBuilder(with_pip=False).create(slot / ".venv")
        runtime._runtime_component("versioned_runtime_construction").retain_helpers(slot, complete=False)
        sdk = slot / ".venv" / "Lib" / "site-packages"
        with zipfile.ZipFile(slot / ".runtime-bootstrap" / "daemon_instance_runtime.pyz") as archive:
            assert all(".." not in Path(name).parts and not Path(name).is_absolute()
                       for name in archive.namelist())
            archive.extractall(sdk)
        runtime.mark_complete(root, "1.0", payload_hash="a" * 64)
        python = runtime.slot_python(root, "1.0")
        assert python is not None
        bootstrap = slot / ".runtime-bootstrap"
        script = bootstrap / "versioned_runtime.py"
        cache = tmp_path / "os-cache"
        monkeypatch.setattr(python_cache_store, "user_cache", lambda: cache)
        log = tmp_path / "finite-worker.log"
        process = daemon_instances.spawn_finite_worker(
            [str(python), "-I", "-S", "-B", str(bootstrap / "runtime_launcher.py"), str(script),
             "--root", str(root), "--json", "python-cache", "--apply", "--drain",
             "--automatic", "--max-seconds", "2"],
            cwd=root, log_path=log, breakaway=False,
            python_bootstrap_root=sdk,
            env_overrides={"LOCALAPPDATA": str(cache)},
            instance=daemon_instances.DaemonInstanceSpec(
                service="legacy-bytecode-maintenance", runtime_root=root, source=script,
                purpose="unattended-test",
            ),
        )
        assert process.wait(timeout=45) == 0, log.read_text(errors="replace")
        result = python_cache_status.read(root)
        assert result is not None
        assert result["last_worker_state"] == "finished"
        assert result["summary"]["removed_bytes"] == 0
        assert not result["pending"]
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        sys.modules.pop(spec.name, None)


@pytest.mark.skipif(os.name != "nt", reason="native Windows owner-to-planner pipeline")
@pytest.mark.parametrize("opaque", [False, True])
def test_actual_owner_observes_recorded_foreign_consumer_with_absent_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, opaque: bool,
) -> None:
    from agent_procutil import python_cache_processes, python_cache_store
    from test_python_cache_prefixes import namespace
    from test_python_cache_processes import Reader

    source = Path(__file__).resolve().parents[3] / "libs/versioned-runtime/versioned_runtime.py"
    spec = importlib.util.spec_from_file_location("_recorded_consumer_runtime", source)
    assert spec is not None and spec.loader is not None
    runtime = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = runtime
    try:
        spec.loader.exec_module(runtime)
        root = tmp_path / "runtime"
        target = namespace(root)
        assert not (root / "versions" / "1.0").exists()
        (root / runtime.RUNNING_VERSION_FILE).write_text('{"pid":20,"version":"1.0"}')
        monkeypatch.setattr(python_cache_store, "user_cache", lambda: tmp_path / "os-cache")

        class ForeignReader(Reader):
            @contextmanager
            def pin(self, pid: int, birth: int) -> Iterator[int]:
                if opaque and pid == 20:
                    raise PermissionError("foreign consumer unavailable")
                with super().pin(pid, birth) as handle:
                    yield handle

            def texts(self, pid: int) -> Iterator[str]:
                yield target.name if pid == 20 else "unrelated"

        reader = ForeignReader()
        original = python_cache_processes.observe
        monkeypatch.setattr(python_cache_processes, "observe",
                            lambda **options: original(reader=reader, **options))
        monkeypatch.setattr(runtime, "_gc_protected_versions",
                            lambda *args, **kwargs: pytest.fail("reference refusal must precede retirement"))
        result = runtime.collect_python_cache(root, min_age_seconds=0, details=True)
        expected = "process-reference-unavailable" if opaque else "referenced-by-live-process"
        assert result["summary"]["eligible_bytes"] == 0
        assert result["reasons"][expected] == 1
        assert target.is_dir() and not reader.open
    finally:
        sys.modules.pop(spec.name, None)


@pytest.mark.parametrize("data", ['{"pid":true}', '{"pid":10,"pid":20}', 'not-json'])
def test_ambiguous_consumer_record_is_unavailable_not_empty(
    owner: Any, tmp_path: Path, data: str,
) -> None:
    from agent_procutil.python_cache_processes import Unavailable

    (tmp_path / "running-version.json").write_text(data)
    with pytest.raises(Unavailable, match="runtime-consumers-unavailable"):
        owner.recorded_consumers(tmp_path, filename="running-version.json", check=lambda: None)
