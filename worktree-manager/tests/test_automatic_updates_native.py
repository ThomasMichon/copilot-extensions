"""Real contained lifecycle qualification; native startup remains an explicit venue run."""

from __future__ import annotations

import ast
import json
import importlib
import importlib.util
import hashlib
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.skipif(os.name == "nt", reason="contained Linux fixture; native Windows gates are separate")
@pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="explicit real-process qualification; use --allow-explicit-tiers",
)
def test_contained_real_worker_qualification(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    probe = repository / "tools/clean-room/scenarios/manager-automatic-updates/probe.py"
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    # Reuse only the package manager's dependency-preparation cache, not user state.
    cache_environment = dict(environment)
    for name in ("HOME", "USERPROFILE", "XDG_CACHE_HOME"):
        cache_environment.pop(name, None)
    cache = subprocess.run(
        [str(shutil.which("uv")), "cache", "dir"], env=cache_environment,
        capture_output=True, text=True, timeout=15, check=True,
    ).stdout.strip()
    environment["UV_CACHE_DIR"] = cache
    environment["UV_OFFLINE"] = "1"
    completed = subprocess.run(
        [sys.executable, str(probe), "qualify", "--source", str(repository),
         "--workdir", str(tmp_path / "qualification"), "--cycles", "1"],
        env=environment, capture_output=True, text=True, timeout=220, check=False,
    )
    if (
        completed.returncode == 1
        and "canonical fixture preparation failed" in completed.stdout
        and "Packages were unavailable because the network was disabled" in completed.stdout
    ):
        pytest.skip(
            "runner cache lacks canonical UV project-resolution metadata; real-process "
            "qualification is blocked, not passed; run the credential-free Tier-P scenario"
        )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    report = json.loads(completed.stdout.splitlines()[-1])
    assert report["passed"] is True
    assert report["real_installation_cycles"] is False
    assert report["handover"]["health_responsive_during_update"] is True
    assert report["handover"]["old_process_exit_verified"] is True
    assert report["handover"]["concurrent_update_exit"] == 0
    assert report["handover"]["concurrent_request_state"] == "pending"
    assert report["handover"]["concurrent_ownership_exit"] == 3
    assert report["handover"]["no_concurrent_installation"] is True
    assert report["handover"]["cadence_observed"] is False
    assert report["failed_successor"]["failed_candidate_preserved_owner"] is True
    assert all(report[key]["cleanup_verified"] for key in ("handover", "failed_successor"))


def test_prepare_selfcontained_candidate_without_source_runtime_changes(tmp_path: Path) -> None:
    repository = Path(__file__).resolve().parents[2]
    scenario = repository / "tools/clean-room/scenarios/manager-automatic-updates"
    output = tmp_path / "prepared"
    completed = subprocess.run(
        [sys.executable, str(scenario / "probe.py"), "prepare", "--source", str(repository),
         "--output", str(output)],
        capture_output=True, text=True, timeout=45, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    prepared = json.loads((output / "prepared.json").read_text(encoding="utf-8"))
    assert prepared["runtime_hashes"]
    assert (output / "payload/.install-complete").is_file()
    assert (output / "payload/libs/agent-procutil/src/agent_procutil/__init__.py").is_file()
    assert (output / "probe.py").is_file()


@pytest.fixture
def windows_probe(monkeypatch: pytest.MonkeyPatch) -> tuple:
    scenario = Path(__file__).resolve().parents[2] / "tools/clean-room/scenarios/manager-automatic-updates"
    monkeypatch.syspath_prepend(str(scenario))
    api = importlib.import_module("windows_api")
    adapter = importlib.import_module("windows_native")
    assert Path(api.__file__).parent == scenario
    return api, adapter


def desktop_facts(module) -> object:
    return module.DesktopFacts(
        elevated=False, shell_present=True, foreground_visible=True, station_visible=True,
        input_desktop_matches=True, same_session=True, ssh=False,
    )


@pytest.mark.parametrize(("field", "value", "code"), [
    ("elevated", True, "elevated-token"),
    ("ssh", True, "ssh-session"),
    ("shell_present", False, "shell-unavailable"),
    ("foreground_visible", False, "foreground-unavailable"),
    ("station_visible", False, "station-not-interactive"),
    ("input_desktop_matches", False, "desktop-not-interactive"),
    ("same_session", False, "desktop-session-mismatch"),
])
def test_windows_preflight_blocks_before_registry_write(
    windows_probe: tuple, tmp_path: Path, field: str, value: bool, code: str,
) -> None:
    api_module, adapter = windows_probe
    facts = replace(desktop_facts(api_module), **{field: value})
    api = SimpleNamespace(facts=lambda: facts)
    startup, process = Mock(), Mock()
    session = adapter.StartupSession(
        tmp_path, {}, SimpleNamespace(api=api), api=api, autostart=startup, procutil=process,
    )
    with pytest.raises(api_module.NativeBlocked, match=code):
        session.start()
    assert startup.mock_calls == []
    assert process.mock_calls == []
    assert not (tmp_path / "probe/job-spec.json").exists()


def test_windows_valid_desktop_preflight_is_read_only(windows_probe: tuple) -> None:
    module, adapter = windows_probe
    api = SimpleNamespace(facts=lambda: desktop_facts(module))
    result = adapter.preflight(api)
    assert result["elevated"] is False and result["same_session"] is True


def test_windows_missing_inspection_api_blocks_before_registry(
    windows_probe: tuple, tmp_path: Path,
) -> None:
    module, adapter = windows_probe
    api = SimpleNamespace(
        facts=Mock(side_effect=module.NativeBlocked("required-win32-api-unavailable", "GetShellWindow")),
    )
    startup = Mock()
    session = adapter.StartupSession(
        tmp_path, {}, SimpleNamespace(api=api), api=api, autostart=startup,
    )
    with pytest.raises(module.NativeBlocked, match="GetShellWindow"):
        session.start()
    assert startup.mock_calls == []


@pytest.mark.skipif(os.name == "nt", reason="Linux-compatible import/API-unavailability contract")
def test_windows_adapter_imports_without_win32_side_effects(windows_probe: tuple) -> None:
    module, adapter = windows_probe
    with pytest.raises(module.NativeBlocked, match="win32-unavailable"):
        adapter.preflight()


class DesktopStub:
    def __init__(self, module) -> None:
        self.module = module
        self.root = module.ProcessInfo(os.getpid(), 1, 0, "python.exe", 1)
        self.records = {self.root.pid: self.root}
        self.visible = {10: module.WindowInfo(10, self.root, (self.root.key,))}
        self.front = 10
        self.handles = {self.root.pid: self.root.pid}

    def refresh_processes(self) -> None:
        pass

    def windows(self) -> dict:
        return dict(self.visible)

    def foreground(self) -> int:
        return self.front

    def process(self, pid: int) -> object:
        return self.records[pid]

    def session(self, pid: int) -> int:
        return 1

    def elevated(self, handle: int) -> bool:
        return False

    def facts(self) -> object:
        return desktop_facts(self.module)


def test_windows_baseline_console_is_not_a_new_fixture_window(windows_probe: tuple) -> None:
    module, _ = windows_probe
    api = DesktopStub(module)
    observer = module.WindowObserver(api)
    observer.sample()
    assert observer.packet()["violations"] == []


def test_get_foreground_window_null_does_not_read_stale_last_error(windows_probe: tuple) -> None:
    module, _ = windows_probe
    api = module.WinAPI.__new__(module.WinAPI)
    api.user = SimpleNamespace(GetForegroundWindow=lambda: None)
    api._check = Mock(side_effect=AssertionError("NULL has no GetLastError contract"))
    assert api.foreground() == 0
    api._check.assert_not_called()


def test_concurrent_process_snapshots_retain_one_ctypes_signature(
    windows_probe: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from concurrent.futures import ThreadPoolExecutor

    module, _ = windows_probe
    signature = [module.wt.HANDLE, module.ct.POINTER(module.ProcessEntry)]

    def first(_handle, pointer):
        assert isinstance(pointer._obj, module.ProcessEntry)
        return False

    first_call = Mock(side_effect=first)
    next_call = Mock(side_effect=AssertionError("empty snapshot has no next entry"))
    first_call.argtypes = next_call.argtypes = signature
    api = module.WinAPI.__new__(module.WinAPI)
    api.kernel = SimpleNamespace(
        CreateToolhelp32Snapshot=Mock(return_value=1),
        Process32FirstW=first_call, Process32NextW=next_call, CloseHandle=Mock(),
    )
    monkeypatch.setattr(module.ct, "get_last_error", lambda: 18, raising=False)
    with ThreadPoolExecutor(max_workers=2) as callers:
        list(callers.map(lambda _: api.refresh_processes(), range(100)))
    assert first_call.argtypes is signature and next_call.argtypes is signature
    assert first_call.call_count == api.kernel.CloseHandle.call_count == 100
    assert api.parents == {}


def test_native_case_retains_ctypes_startup_error_when_cleanup_also_fails(
    windows_probe: tuple, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    api_module, adapter = windows_probe
    lifecycle = importlib.import_module("lifecycle")
    root = tmp_path / "fixture"
    (root / "probe").mkdir(parents=True)
    monkeypatch.setattr(lifecycle.fixtures, "build", lambda *a, **k: {
        "runtime_hashes": {}, "resolution_environment": "test",
    })
    monkeypatch.setattr(lifecycle.fixtures, "environment", lambda *a, **k: {})
    monkeypatch.setattr(lifecycle.fixtures, "warm", lambda *a: None)
    monkeypatch.setattr(lifecycle, "cleanup", lambda *a: None)
    monkeypatch.setattr(lifecycle, "os", SimpleNamespace(name="nt"))

    class Session:
        def __init__(self, *args):
            pass

        def start(self):
            raise api_module.ct.ArgumentError("controlled pointer signature conflict")

        def close_job(self):
            pass

        def retire(self):
            raise api_module.NativeBlocked("unattested-startup-cleanup", "controlled cleanup hold")

    monkeypatch.setattr(adapter, "StartupSession", Session)
    with pytest.raises(api_module.NativeBlocked, match="cleanup hold"):
        lifecycle.case(
            root, root, {"automatic_updates_process": object()}, cycles=2,
            native_startup=True, windows_observer=object(),
        )
    result = json.loads((root / "probe/result.json").read_text())
    assert result["error"] == "ArgumentError: controlled pointer signature conflict"
    assert "cleanup hold" in result["cleanup_error"]
    assert result["passed"] is False and result["cleanup_verified"] is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX immutable worker command identity")
@pytest.mark.parametrize("leased", [False, True])
def test_worker_identity_accepts_only_retained_invocation_and_canonical_lease_shim(
    windows_probe: tuple, tmp_path: Path, leased: bool,
) -> None:
    from agent_procutil._daemon_instance_child import leased_python_command

    lifecycle = importlib.import_module("lifecycle")
    slot = tmp_path / "versions" / lifecycle.fixtures.OLD
    command = [
        sys.executable, "-I", "-S", "-B", "-X", "utf8",
        str(slot / ".runtime-bootstrap/runtime_launcher.py"),
        "-m", "worktree_manager", "automatic-updates", "run", "--root", str(tmp_path),
    ]
    if leased:
        command, _environment = leased_python_command(command)
    assert lifecycle.worker_command_matches(tmp_path, slot, tuple(command))
    assert lifecycle.worker_command_matches(
        tmp_path, slot, tuple([*command, "--standby", "a" * 32]),
    )
    assert not lifecycle.worker_command_matches(tmp_path, slot, tuple([*command, "--unexpected"]))
    wrong_root = [*command[:-1], str(tmp_path / "foreign")]
    assert not lifecycle.worker_command_matches(tmp_path, slot, tuple(wrong_root))
    if leased:
        command[command.index("-c") + 2] = str(tmp_path / "foreign-helper")
        assert not lifecycle.worker_command_matches(tmp_path, slot, tuple(command))


def test_initial_null_foreground_does_not_create_a_passing_observer(windows_probe: tuple) -> None:
    module, _ = windows_probe
    api = DesktopStub(module)
    api.front = 0
    with pytest.raises(module.NativeBlocked, match="foreground-unavailable"):
        module.WindowObserver(api)


def test_null_foreground_is_retained_without_stopping_later_observation(windows_probe: tuple) -> None:
    module, _ = windows_probe
    api = DesktopStub(module)
    observer = module.WindowObserver(api)
    api.front = 0
    observer.sample()
    child = module.ProcessInfo(123456, 2, api.root.pid, "python.exe", 1)
    api.visible[20] = module.WindowInfo(20, child, (child.key, api.root.key))
    api.front = 20
    observer.sample()
    packet = observer.packet()
    assert packet["samples"] == 2 and packet["observer_error"] is None
    assert packet["passed"] is False
    assert {item["kind"] for item in packet["violations"]} == {
        "foreground-unobservable", "visible", "foreground",
    }


def test_windows_new_fixture_window_and_focus_are_observed(windows_probe: tuple) -> None:
    module, _ = windows_probe
    api = DesktopStub(module)
    observer = module.WindowObserver(api)
    child = module.ProcessInfo(123456, 2, api.root.pid, "python.exe", 1)
    api.visible[20] = module.WindowInfo(20, child, (child.key, api.root.key))
    api.front = 20
    observer.sample()
    packet = observer.packet()
    assert packet["passed"] is False
    assert {item["kind"] for item in packet["violations"]} == {"visible", "foreground"}
    assert all(item["attribution"] == "fixture" for item in packet["violations"])


def test_windows_unattributed_console_broker_never_silently_passes(windows_probe: tuple) -> None:
    module, _ = windows_probe
    api = DesktopStub(module)
    observer = module.WindowObserver(api)
    broker = module.ProcessInfo(222222, 3, 4, "openconsole.exe", 1)
    api.visible[30] = module.WindowInfo(30, broker, (broker.key,))
    observer.sample()
    assert observer.packet()["violations"][0]["attribution"] == "ambiguous-console-host"


def test_windows_late_worker_identity_retroactively_attributes_event(windows_probe: tuple) -> None:
    module, _ = windows_probe
    api = DesktopStub(module)
    observer = module.WindowObserver(api)
    brokered = module.ProcessInfo(333333, 4, 4, "python.exe", 1)
    api.records[brokered.pid] = brokered
    api.handles[brokered.pid] = brokered.pid
    api.visible[40] = module.WindowInfo(40, brokered, (brokered.key,))
    observer.sample()
    observer.add_pid(brokered.pid)
    assert observer.packet()["violations"][0]["attribution"] == "fixture"


def test_windows_monitoring_gap_is_a_failed_observation(windows_probe: tuple) -> None:
    module, _ = windows_probe
    observer = module.WindowObserver(DesktopStub(module))
    observer.sample()
    observer.max_gap = 0.6
    assert observer.packet()["passed"] is False


def test_windows_background_observer_failure_cannot_mint_zero_window_pass(
    windows_probe: tuple,
) -> None:
    module, _ = windows_probe
    api = DesktopStub(module)
    observer = module.WindowObserver(api)
    observer.stop = SimpleNamespace(wait=lambda _: False)
    api.windows = Mock(side_effect=TypeError("native signature mismatch"))
    observer._run()
    assert "TypeError" in observer.packet()["observer_error"]
    assert observer.packet()["passed"] is False


@pytest.mark.parametrize(("cycles", "duration", "spacing", "control_exit", "expected"), [
    (1, 400, 300, True, False),
    (2, 299, 300, True, False),
    (2, 400, 299, True, False),
    (2, 400, 300, False, False),
    (2, 400, 300, True, True),
])
def test_windows_proof_requires_two_real_cycles_and_job_control(
    windows_probe: tuple, cycles: int, duration: int, spacing: int,
    control_exit: bool, expected: bool,
) -> None:
    _, adapter = windows_probe
    packet = {"passed": True, "observed_seconds": duration}
    handover = {
        "observed_cycles": cycles, "observed_cycle_spacing_seconds": spacing,
        "windows_job_lifetime": {
            "inner_job_closed": True,
            "contained_control_exit_verified": control_exit,
            "daemon_kernel_handle_live_after_job_close": True,
            "authenticated_health_after_job_close": True,
            "registered_command_executed": True,
            "registered_command_sha256": "a" * 64,
            "executed_command_sha256": "a" * 64,
        },
    }
    assert adapter.observation_passed(packet, handover) is expected


def test_windows_job_helper_executes_exact_command_and_contains_control(
    windows_probe: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    helper = importlib.import_module("windows_job")
    monkeypatch.setattr(sys, "path", list(sys.path))
    root = tmp_path
    (root / "probe").mkdir()
    (root / "current-version").write_text("0.0.0.dev1", encoding="utf-8")
    library = root / "versions/0.0.0.dev1/payload/libs/agent-procutil/src"
    pu = SimpleNamespace(
        __file__=str(library / "agent_procutil/__init__.py"),
        direct_python=Mock(return_value=("base-python.exe", {})),
        spawn_daemon=Mock(return_value=SimpleNamespace(pid=101)),
        no_window_kwargs=lambda: {"creationflags": 0x08000000},
    )
    monkeypatch.setitem(sys.modules, "agent_procutil", pu)
    gui = SimpleNamespace(pid=102, wait=Mock(return_value=0))
    popen = Mock(return_value=gui)
    monkeypatch.setattr(helper.subprocess, "Popen", popen)
    exact = '"C:\\Python\\pythonw.exe" -I -S "C:\\fixture space\\startup.py" --root "C:\\fixture space"'
    result = helper.execute({
        "root": str(root), "registered_command": exact,
        "environment": {"WORKTREE_MANAGER_ROOT": str(root)},
    })
    assert popen.call_args.args[0] == exact
    assert popen.call_args.kwargs["shell"] is False
    assert pu.spawn_daemon.call_args.kwargs["breakaway"] is False
    assert pu.spawn_daemon.call_args.kwargs["env"]["COPILOT_EXTENSIONS_TEST_CONTAINED"] == "1"
    assert "COPILOT_EXTENSIONS_TEST_CONTAINED" not in popen.call_args.kwargs["env"]
    assert result["executed_command_sha256"] == hashlib.sha256(exact.encode()).hexdigest()


@pytest.mark.parametrize("long_command", [False, True])
def test_windows_session_uses_strict_canonical_job_and_health_after_close(
    windows_probe: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, long_command: bool,
) -> None:
    module, adapter = windows_probe
    root = tmp_path
    (root / "probe").mkdir()
    (root / "automatic-updates").mkdir()
    exact = '"C:\\Python\\pythonw.exe" -I -S "C:\\fixture\\startup.py" --root "C:\\fixture"'
    if long_command:
        exact += "x" * 260
    (root / "automatic-updates/autostart.json").write_text(
        json.dumps({"entry": exact}), encoding="utf-8",
    )
    (root / "probe/job-launch.json").write_text(json.dumps({
        "control_pid": 101, "gui_pid": 102, "gui_returncode": 0,
        "registered_command_executed": True,
        "executed_command_sha256": hashlib.sha256(exact.encode()).hexdigest(),
    }), encoding="utf-8")
    startup = Mock()
    startup.inspect_autostart.return_value = {"registered": False, "activation": "not-registered"}
    startup.register_autostart.return_value = {"registered": True, "activation": "pending", "name": "fixture"}
    startup._registry_read.return_value = exact
    startup.remove_autostart.return_value = {"registered": False, "activation": "not-registered"}
    control = SimpleNamespace(dead=False)
    caller = SimpleNamespace(pid=100, wait=Mock(return_value=0))
    job = SimpleNamespace(close=Mock(side_effect=lambda: setattr(control, "dead", True)))
    pu = SimpleNamespace(
        direct_python=Mock(return_value=("base-python.exe", {})),
        no_window_kwargs=lambda: {},
        spawn_sync_in_kill_on_close_job=Mock(return_value=(caller, job)),
    )

    class Pin:
        def __init__(self, pid: int, **kwargs: object) -> None:
            self.pid = pid

        def exited(self) -> bool:
            return self.pid == 101 and control.dead

        def close(self) -> None:
            pass

    monkeypatch.setattr(adapter, "ProcessPin", Pin)
    api = SimpleNamespace(facts=lambda: desktop_facts(module))
    observer = SimpleNamespace(api=api, add_pid=Mock())
    session = adapter.StartupSession(
        root, {"COPILOT_EXTENSIONS_TEST_CONTAINED": "1"}, observer,
        api=api, autostart=startup, procutil=pu,
    )
    try:
        if long_command:
            with pytest.raises(module.NativeBlocked, match="autorun-command-too-long"):
                session.start()
            pu.spawn_sync_in_kill_on_close_job.assert_not_called()
            return
        session.start()
        kwargs = pu.spawn_sync_in_kill_on_close_job.call_args.kwargs
        assert kwargs["require_job"] is True and kwargs["allow_breakaway"] is True
        assert "COPILOT_EXTENSIONS_TEST_CONTAINED" not in kwargs["env"]
        health = SimpleNamespace(worker_status=lambda _: {"running": True, "worker_id": "fixture-worker"})
        proof = session.verify_job_close({"pid": 103, "worker_id": "fixture-worker"}, health)
        assert proof["contained_control_exit_verified"] is True
        assert proof["authenticated_health_after_job_close"] is True
        job.close.assert_called_once()
    finally:
        session.close_job()
        session.retire()
        startup.remove_autostart.assert_called_once_with(root)
@pytest.mark.parametrize("defect", ["none", "foreign-slot", "wrong-launcher"])
def test_native_mainpid_checks_the_actual_retained_worker_command(
    windows_probe: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    from worktree_manager import automatic_updates_startup as startup

    fixtures = importlib.import_module("fixtures")
    native = importlib.import_module("native")
    root = tmp_path
    slot = root / "versions" / fixtures.OLD
    unit = "manager-maintenance-fixture.service"
    group = "/user.slice/" + unit
    values = {"MainPID": "123", "ControlGroup": group}
    monkeypatch.setattr(native, "fields", lambda name: values)
    disposable = Mock()
    monkeypatch.setattr(native, "assert_disposable_instance", disposable)
    argv = fixtures.command(
        root, ["automatic-updates", "run", "--root", str(root)], version=fixtures.OLD,
    )
    if defect == "wrong-launcher":
        argv[6] = str(root / "foreign-launcher.py")
    monkeypatch.setattr(startup, "process_snapshot", lambda pid: (pid, 1, tuple(argv), (group,)))
    status = {
        "pid": 123, "runtime": str(slot if defect != "foreign-slot" else root / "foreign"),
    }
    if defect == "none":
        assert native.assert_mainpid(unit, status, root) == values
        disposable.assert_called_once_with(root, 123)
    else:
        with pytest.raises(AssertionError, match="retained"):
            native.assert_mainpid(unit, status, root)


def test_native_registration_binds_only_its_fixture_lifecycle(
    windows_probe: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import _daemon_instance_store as store
    from agent_procutil.daemon_instances import TEST_CONTEXT_ENV
    from worktree_manager import automatic_updates_autostart as startup

    native = importlib.import_module("native")
    (tmp_path / "probe").mkdir()
    overlay = {TEST_CONTEXT_ENV: "controlled-disposable-declaration"}
    monkeypatch.setattr(store, "_test_context", lambda environment: {"purpose": "unattended-test"})
    register, activate = Mock(return_value={"registered": True}), Mock(return_value={"activation": "started"})
    monkeypatch.setattr(startup, "register_autostart", register)
    monkeypatch.setattr(startup, "activate_autostart", activate)
    before = dict(os.environ)
    native.registration(tmp_path, instance_environment=overlay)
    assert json.loads((tmp_path / "probe/native-lifecycle.json").read_text()) == overlay
    assert dict(os.environ) == before
    register.assert_called_once_with(tmp_path)
    activate.assert_called_once_with(tmp_path)


@pytest.mark.parametrize("defect", ["none", "production", "foreign-run", "foreign-owner", "no-expiry", "foreign-pid"])
def test_native_mainpid_requires_this_run_and_disposable_expiry(
    windows_probe: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    from agent_procutil import _daemon_instance_store as store, daemon_instances

    native = importlib.import_module("native")
    (tmp_path / "probe").mkdir()
    (tmp_path / "probe/native-lifecycle.json").write_text("{}")
    context = {
        "registry_root": str(tmp_path / "registry"), "purpose": "unattended-test",
        "run_id": "current-run", "owner": "current-owner",
    }
    monkeypatch.setattr(store, "_test_context", lambda environment: context)
    record = {
        "pid": "123", "runtime_root": str(tmp_path),
        "service": "worktree-manager-automatic-updates", "purpose": "unattended-test",
        "run_id": "current-run", "owner": "current-owner",
        "deadline": "1", "hard_deadline": "2",
    }
    if defect != "none":
        key, value = {
            "production": ("purpose", "production"), "foreign-run": ("run_id", "foreign"),
            "foreign-owner": ("owner", "foreign"), "no-expiry": ("deadline", "0"),
            "foreign-pid": ("pid", "999"),
        }[defect]
        record[key] = value
    monkeypatch.setattr(daemon_instances, "inspect_instances", lambda registry: [record])
    if defect == "none":
        native.assert_disposable_instance(tmp_path, 123)
    else:
        with pytest.raises(AssertionError, match="exact leased instance"):
            native.assert_disposable_instance(tmp_path, 123)


@pytest.mark.parametrize("known_entry", [True, False])
def test_failed_successor_fixture_uses_only_the_late_console_entry(
    windows_probe: tuple, tmp_path: Path, known_entry: bool,
) -> None:
    fixtures = importlib.import_module("fixtures")
    repository = Path(__file__).resolve().parents[2]
    source = repository / "worktree-manager/src/worktree_manager/__main__.py"
    candidate = tmp_path / "main.py"
    original = source.read_text(encoding="utf-8")
    if not known_entry:
        assert original.count("    console_entry()") == 1
        original = original.replace("    console_entry()", "    unknown_entry()")
    candidate.write_text(original, encoding="utf-8")
    if not known_entry:
        with pytest.raises(ValueError, match="qualified late"):
            fixtures.fixture_main(candidate, failing_standby=True)
        return
    early = ast.dump(next(node for node in ast.parse(original).body if isinstance(node, ast.If)))
    fixtures.fixture_main(candidate, failing_standby=True)
    syntax = ast.parse(candidate.read_text(encoding="utf-8"))
    assert ast.dump(next(node for node in syntax.body if isinstance(node, ast.If))) == early
    imported_sys = next(
        node for node in syntax.body if isinstance(node, ast.Import)
        and any(alias.name == "sys" and alias.asname is None for alias in node.names)
    )
    failure = syntax.body[-2]
    assert isinstance(failure, ast.If) and "automatic-updates" in ast.unparse(failure.test)
    assert "run" in ast.unparse(failure.test)
    assert failure.lineno > imported_sys.lineno
    assert ast.unparse(syntax.body[-1].body[0]) == "console_entry()"


def test_cycle_witness_uses_loaded_package_and_retained_runtime_not_file_depth(
    windows_probe: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import worktree_manager

    fixtures = importlib.import_module("fixtures")
    root = tmp_path / "fixture"
    slot = root / "versions" / fixtures.NEW
    installed = slot / "lib/python3.12/site-packages/worktree_manager/_qualification_cycle.py"
    installed.parent.mkdir(parents=True)
    shutil.copy2(fixtures.HERE / "fixture_cycle.py", installed)
    (root / "probe").mkdir()
    (root / "probe/release").touch()
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.setenv("COPILOT_EXTENSIONS_UPDATE_HOME", str(root / "original-telemetry"))
    monkeypatch.setattr(sys, "prefix", str(slot))
    monkeypatch.setattr(worktree_manager, "__version__", fixtures.NEW)
    spec = importlib.util.spec_from_file_location("worktree_manager._qualification_cycle_test", installed)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.execute() == 0
    observed = json.loads((root / "probe/cycle-001-begin.json").read_text())
    assert observed["version"] == fixtures.NEW
    assert observed["runtime"] == str(slot.resolve())


@pytest.mark.parametrize("valid", [True, False])
def test_fixture_only_native_binding_reaches_startup_before_worker_admission(
    windows_probe: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, valid: bool,
) -> None:
    from agent_procutil.daemon_instances import TEST_CONTEXT_ENV

    fixtures = importlib.import_module("fixtures")
    (tmp_path / "probe").mkdir()
    overlay = {TEST_CONTEXT_ENV: "owned-test-declaration"} if valid else {"HOME": "foreign"}
    (tmp_path / "probe/native-lifecycle.json").write_text(json.dumps(overlay))
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKTREE_MANAGER_AUTOMATIC_UPDATES_UNIT", "fixture.service")
    monkeypatch.delenv(TEST_CONTEXT_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["worktree-manager", "automatic-updates", "run"])
    spec = importlib.util.spec_from_file_location(
        "worktree_manager._native_lifecycle_fixture", fixtures.HERE / "fixture_cycle.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    if valid:
        spec.loader.exec_module(module)
        assert os.environ[TEST_CONTEXT_ENV] == overlay[TEST_CONTEXT_ENV]
    else:
        with pytest.raises(RuntimeError, match="exact disposable lifecycle"):
            spec.loader.exec_module(module)
        assert TEST_CONTEXT_ENV not in os.environ


def test_native_update_execution_uses_private_driver_root(
    windows_probe: tuple, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd,
) -> None:
    import update_driver as driver
    from worktree_manager import update_driver_cli

    fixtures = importlib.import_module("fixtures")
    root = tmp_path / "fixture"
    root.mkdir()
    native_home = tmp_path / "native-home"
    native_home.mkdir()
    monkeypatch.setenv("HOME", str(native_home))
    monkeypatch.setenv("USERPROFILE", str(native_home))
    monkeypatch.setenv("UPDATE_DRIVER_WITNESS", "must-not-borrow-native-custody")
    environment = fixtures.environment(root, native=True)
    assert environment["HOME"] == str(native_home)
    assert "UPDATE_DRIVER_WITNESS" not in environment
    monkeypatch.setattr(os, "environ", environment)
    monkeypatch.setattr(sys, "argv", ["worktree-manager", "update", "--detach", "--json"])
    helper = fixtures.HERE / "fixture_cycle.py"
    spec = importlib.util.spec_from_file_location("worktree_manager._private_update_fixture", helper)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    private_home = root / "home"
    assert driver.default_root().is_relative_to(private_home)
    assert driver.borrowed_custody() is None
    monkeypatch.setattr(update_driver_cli, "source_identity", lambda: {
        "root": str(root), "repo": "https://github.com/example/project", "ref": "main",
    })
    monkeypatch.setattr(driver, "runtime_command", lambda name, args: (
        (sys.executable, "-m", name, *args), {},
    ))
    starts = []

    def ensure_worker(argv, *, env):
        assert driver.default_root().is_relative_to(private_home)
        assert env["HOME"] == env["USERPROFILE"] == str(private_home)
        starts.append(argv)
        return None

    monkeypatch.setattr(driver, "ensure_worker", ensure_worker)
    assert update_driver_cli.cmd_update_request(["--detach", "--json"]) == 0
    observed = json.loads(capfd.readouterr().out)
    request = driver.status(observed["request_id"])
    assert request.state == "pending"
    assert request.log_path.is_relative_to(private_home)
    assert len(starts) == 1
    assert not (native_home / ".copilot-extensions/update-driver").exists()
