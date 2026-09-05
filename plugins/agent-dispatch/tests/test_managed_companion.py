"""Managed-cell launch transactions, authority fencing, and restart recovery."""

from __future__ import annotations

import copy
import json
import os
import subprocess
import threading
import time
from concurrent.futures import Future
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from agent_dispatch import companion
from agent_dispatch.companion import (
    CompanionError,
    CompanionIndeterminate,
    DefaultCompanionController,
)
from agent_dispatch.managed_runtime import ManagedRuntimeError, ManagedRuntimeMaterializer
from agent_dispatch.supervisor_daemon import SupervisorDaemon
from tests.test_managed_runtime import FakeRunner, _policy, _project, _registration


class Executor:
    def __init__(self):
        self.pending = []
        self.defer = False

    def submit(self, function, *args):
        future = Future()
        if self.defer:
            future.set_running_or_notify_cancel()
            self.pending.append((future, function, args))
        else:
            self.finish(future, function, args)
        return future

    @staticmethod
    def finish(future, function, args):
        try:
            future.set_result(function(*args))
        except (RuntimeError, OSError) as exc:
            future.set_exception(exc)

    def complete(self):
        for future, function, args in self.pending:
            self.finish(future, function, args)
        self.pending.clear()


class Process:
    def __init__(self, pid, resolution, events):
        self.pid = pid
        self.resolution = resolution
        self.events = events
        self.returncode = None

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode or 0

    def release(self):
        self.events.append(("release", self.resolution.environment["MODE"]))

    def terminate(self):
        self.events.append(("terminate", self.resolution.environment["MODE"]))
        self.returncode = -1


class Harness:
    def __init__(self, tmp_path, monkeypatch):
        self.plugin = _project(tmp_path)
        scripts = self.plugin / "bin"
        scripts.mkdir()
        for name in ("service", "stop", "health", "config"):
            (scripts / f"{name}.py").write_text("pass\n", encoding="utf-8")
        self.registration = _registration(self.plugin)
        self.registration["spec"].update(
            command=["bin/service.py", "--serve"],
            stop_command=["bin/stop.py", "--stop"],
            health_probe=["bin/health.py", "--health"],
            config_provider=["bin/config.py"],
            startup_timeout_seconds=0.5,
        )
        self.registrations = [self.registration]
        self.events = []
        self.processes = []
        self.provider = {"schema_version": 1, "active": True, "environment": {"MODE": "old"}}
        self.unhealthy = set()
        self.indeterminate_health = set()
        self.time = 0.0
        self.executor = Executor()
        self.builder = FakeRunner()
        self.materializer = ManagedRuntimeMaterializer(_policy(tmp_path), runner=self.build)
        self.state = tmp_path / "state"
        monkeypatch.setenv("SNAPSHOT_AMBIENT", "initial")
        monkeypatch.setattr(companion, "_launch_gated", self.launch)
        monkeypatch.setattr(companion, "_process_exists", lambda pid: self.token(pid) is not None)
        monkeypatch.setattr(companion, "_process_group_exists", lambda pid: False)
        monkeypatch.setattr(companion, "_terminate_posix_group", self.terminate)
        monkeypatch.setattr(companion, "_terminate_windows_tree", self.terminate)
        self.controller = self.make_controller()
        self.daemon = self.make_daemon()

    def token(self, pid):
        return (
            f"token-{pid}"
            if any(process.pid == pid and process.poll() is None for process in self.processes)
            else None
        )

    def terminate(self, pid):
        for process in self.processes:
            if process.pid == pid:
                process.terminate()

    def build(self, argv, cwd, environment):
        self.events.append(("build", tuple(argv)))
        return self.builder(argv, cwd, environment)

    def advance(self, amount):
        self.time += amount

    def make_controller(self):
        return DefaultCompanionController(
            self.state,
            runner=self.run,
            token_source=self.token,
            monotonic=lambda: self.time,
            sleeper=self.advance,
        )

    def make_daemon(self):
        return SupervisorDaemon(
            self,
            "machine-a",
            companion_controller=self.controller,
            runtime_materializer=self.materializer,
            runtime_executor=self.executor,
            overrides_source=lambda: {},
            clock=lambda: self.time,
            sleep=self.advance,
        )

    def list_registrations(self, **kwargs):
        return self.registrations

    def run(self, argv, **kwargs):
        if Path(argv[1]).name == "config.py":
            if isinstance(self.provider, Exception):
                raise self.provider
            result = self.provider
        else:
            mode = kwargs["environment"]["MODE"]
            self.events.append((Path(argv[1]).stem, mode, dict(kwargs["environment"]), argv))
            if Path(argv[1]).name == "health.py":
                if mode in self.indeterminate_health:
                    raise CompanionIndeterminate("probe unavailable")
                result = {"schema_version": 1, "healthy": mode not in self.unhealthy}
            else:
                result = {}
        return subprocess.CompletedProcess(argv, 0, json.dumps(result), "")

    def launch(self, resolution):
        self.events.append(("launch", resolution.environment["MODE"]))
        process = Process(100 + len(self.processes), resolution, self.events)
        self.processes.append(process)
        return process

    def change(self, *, version="3.0.0", mode="new"):
        self.registration = copy.deepcopy(self.registration)
        self.registration["spec"]["managed_runtime"]["runtimes"][0]["version"] = version
        self.registrations = [self.registration]
        self.provider = {
            "schema_version": 1,
            "active": True,
            "arguments": ["--mode", mode],
            "environment": {"MODE": mode, "example_managed_python": "provider-cannot-select"},
        }

    @property
    def rid(self):
        return self.registration["id"]

    @property
    def unit(self):
        return self.daemon._units[self.rid]


@pytest.fixture
def harness(tmp_path, monkeypatch):
    return Harness(tmp_path, monkeypatch)


def test_first_launch_waits_for_materialization_and_records_ready_snapshot(harness):
    h = harness
    h.executor.defer = True
    assert h.daemon.reconcile_once().started == []
    assert h.processes == []
    h.executor.complete()
    summary = h.daemon.reconcile_once()
    assert summary.started == [h.rid]
    snapshot = h.unit.companion_resolution.managed_snapshot
    assert snapshot is not None
    assert h.unit.fingerprint == snapshot.fingerprint
    assert h.controller.selected_managed(h.rid) == snapshot
    assert h.unit.companion_resolution.environment["EXAMPLE_MANAGED_PYTHON"] == str(
        snapshot.runtimes[0].python
    )
    assert [event[0] for event in h.events][-3:] == ["launch", "release", "health"]


def test_launch_snapshot_is_immutable_and_includes_complete_environment(harness, monkeypatch):
    h = harness
    h.daemon.reconcile_once()
    snapshot = h.unit.companion_resolution.managed_snapshot
    before = snapshot.to_dict()
    fingerprint = snapshot.fingerprint
    h.registration["spec"]["command"].append("changed")
    h.provider["environment"]["MODE"] = "mutated"
    monkeypatch.setenv("SNAPSHOT_AMBIENT", "changed")
    returned = snapshot.resolution()
    returned.registration["spec"]["command"].append("also-changed")
    with pytest.raises(TypeError):
        returned.environment["MODE"] = "also-changed"
    with pytest.raises(FrozenInstanceError):
        snapshot._json = "{}"
    h.daemon._managed_runtime_results[h.rid] = ()
    assert snapshot.fingerprint == fingerprint
    assert snapshot.to_dict() == before
    assert snapshot.resolution().environment["SNAPSHOT_AMBIENT"] == "initial"
    assert len(snapshot.runtimes) == 1


def test_prepare_and_validate_before_stopping_healthy_predecessor(harness):
    h = harness
    h.daemon.reconcile_once()
    old = h.processes[-1]
    h.events.clear()
    h.change()
    h.executor.defer = True
    assert h.daemon.reconcile_once().restarted == []
    assert old.poll() is None
    assert not any(event[0] == "stop" for event in h.events)
    h.executor.complete()
    summary = h.daemon.reconcile_once()
    assert summary.restarted == [h.rid]
    kinds = [event[0] for event in h.events]
    assert kinds.index("build") < kinds.index("stop") < kinds.index("launch")
    assert h.unit.companion_resolution.environment["MODE"] == "new"
    assert "example_managed_python" not in h.unit.companion_resolution.environment
    assert old.poll() is not None


@pytest.mark.parametrize("failure", ["install", "validation", "snapshot"])
def test_prepare_failure_never_stops_healthy_companion(harness, failure):
    h = harness
    h.daemon.reconcile_once()
    previous = h.processes[-1]
    h.change()
    if failure == "install":
        h.builder.fail_install = True
    elif failure == "validation":
        h.builder.fail_validation_at = h.builder.validation_count + 1
    else:
        h.registration["spec"].pop("health_probe")
    summary = h.daemon.reconcile_once()
    assert summary.restarted == []
    assert h.unit.proc is previous
    assert previous.poll() is None
    assert not any(event[0] == "stop" for event in h.events)


@pytest.mark.parametrize("failure", ["unhealthy", "indeterminate", "exit"])
def test_first_launch_is_readiness_gated(harness, monkeypatch, failure):
    h = harness
    if failure == "unhealthy":
        h.unhealthy.add("old")
    elif failure == "indeterminate":
        h.indeterminate_health.add("old")
    else:
        original = h.launch

        def exited(resolution):
            process = original(resolution)
            process.returncode = 1
            return process

        monkeypatch.setattr(companion, "_launch_gated", exited)
    summary = h.daemon.reconcile_once()
    assert summary.started == summary.running == []
    assert h.controller.selected_managed(h.rid) is None
    assert all(process.poll() is not None for process in h.processes)
    count = len(h.processes)
    h.daemon.reconcile_once()
    assert len(h.processes) == count


@pytest.mark.parametrize("uncertain", [False, True])
def test_readiness_failure_rolls_back_exact_prior_launch_without_rebuild(
    harness, monkeypatch, uncertain
):
    h = harness
    h.daemon.reconcile_once()
    snapshot = h.unit.companion_resolution.managed_snapshot
    old_cell = snapshot.runtimes[0].cell
    h.change()
    monkeypatch.setenv("SNAPSHOT_AMBIENT", "changed")
    (h.indeterminate_health if uncertain else h.unhealthy).add("new")
    summary = h.daemon.reconcile_once()
    assert summary.restarted == []
    assert summary.revived == [h.rid]
    assert h.unit.companion_resolution.managed_snapshot == snapshot
    assert h.processes[-1].resolution.environment["SNAPSHOT_AMBIENT"] == "initial"
    assert h.processes[-1].resolution.command == snapshot.resolution().command
    assert h.controller.selected_managed(h.rid) == snapshot
    assert old_cell.is_dir()
    assert h.builder.install_count == 2
    assert [p.resolution.environment["MODE"] for p in h.processes] == ["old", "new", "old"]
    stopped = [event for event in h.events if event[0] == "stop"]
    assert stopped[0][2] == dict(snapshot.resolution().environment)
    assert stopped[0][3] == snapshot.resolution().stop_command
    count = len(h.processes)
    h.daemon.reconcile_once()
    assert len(h.processes) == count


def test_rollback_failure_is_reported_without_false_running_state(harness, caplog):
    h = harness
    h.daemon.reconcile_once()
    h.change()
    h.unhealthy.update({"old", "new"})
    summary = h.daemon.reconcile_once()
    assert summary.running == summary.revived == []
    assert "cannot safely reconcile" in caplog.text
    assert all(process.poll() is not None for process in h.processes)


def test_provider_uncertainty_preserves_only_live_snapshot_not_latest_desired(harness):
    h = harness
    h.daemon.reconcile_once()
    snapshot = h.unit.companion_resolution.managed_snapshot
    h.change(version="2.0.0")
    h.unhealthy.add("new")
    h.daemon.reconcile_once()
    assert h.unit.companion_resolution.managed_snapshot == snapshot
    h.provider = CompanionIndeterminate("provider unavailable")
    count = len(h.processes)
    h.daemon.reconcile_once()
    assert h.unit.companion_resolution.managed_snapshot == snapshot
    assert len(h.processes) == count
    h.unit.proc.returncode = 1
    assert h.daemon.reconcile_once().running == []
    assert len(h.processes) == count


@pytest.mark.parametrize(
    "change", ["version", "root", "scopes", "source", "command", "managed", "registration-source"]
)
def test_uncertain_changed_authority_cannot_reuse_live_runtime(harness, change):
    h = harness
    h.daemon.reconcile_once()
    selected = h.controller.selected_managed(h.rid)
    if change == "command":
        h.registration["spec"]["command"].append("--changed")
    elif change == "managed":
        h.registration["spec"]["managed_runtime"]["runtimes"][0]["profile"] = "other"
    elif change == "scopes":
        h.registration["plugin"]["activation_scopes"].append("another")
    elif change == "registration-source":
        h.registration["source"] = "direct"
    else:
        key = {"version": "version", "root": "root", "source": "source_path"}[change]
        h.registration["plugin"][key] += "-changed"
    h.provider = CompanionIndeterminate("provider unavailable")
    summary = h.daemon.reconcile_once()
    assert summary.stopped == [h.rid]
    assert summary.running == []
    assert len(h.processes) == 1
    assert h.controller.selected_managed(h.rid) == selected


def test_uncertain_changed_authority_preserves_rollback_for_later_failure(harness):
    h = harness
    h.daemon.reconcile_once()
    selected = h.controller.selected_managed(h.rid)
    h.change()
    h.provider = CompanionIndeterminate("provider unavailable")

    assert h.daemon.reconcile_once().stopped == [h.rid]
    assert h.controller.selected_managed(h.rid) == selected
    assert h.daemon.reconcile_once().running == []
    assert h.controller.selected_managed(h.rid) == selected
    h.controller = h.make_controller()
    h.daemon = h.make_daemon()
    assert h.daemon.reconcile_once().running == []
    assert h.controller.selected_managed(h.rid) == selected

    h.provider = {
        "schema_version": 1,
        "active": True,
        "arguments": ["--mode", "new"],
        "environment": {"MODE": "new"},
    }
    h.unhealthy.add("new")
    summary = h.daemon.reconcile_once()

    assert summary.revived == [h.rid]
    assert h.unit.companion_resolution.managed_snapshot == selected
    assert h.controller.selected_managed(h.rid) == selected
    assert [process.resolution.environment["MODE"] for process in h.processes] == [
        "old",
        "new",
        "old",
    ]


@pytest.mark.parametrize("disable", ["removed", "provider", "override"])
def test_disable_discards_inflight_result_and_selected_launch(harness, disable):
    h = harness
    h.daemon.reconcile_once()
    h.change()
    h.executor.defer = True
    h.daemon.reconcile_once()
    if disable == "removed":
        h.registrations = []
    elif disable == "provider":
        h.provider = {"schema_version": 1, "active": False}
    else:
        h.daemon.overrides_source = lambda: {h.rid: {"disabled": True}}
    summary = h.daemon.reconcile_once()
    assert summary.stopped == [h.rid]
    assert h.controller.selected_managed(h.rid) is None
    h.executor.complete()
    assert h.daemon.reconcile_once().running == []
    assert len(h.processes) == 1
    assert h.rid not in h.daemon._managed_runtime_results


def test_declaration_churn_discards_stale_materialization(harness):
    h = harness
    h.executor.defer = True
    h.daemon.reconcile_once()
    h.change()
    h.daemon.reconcile_once()
    h.executor.complete()
    summary = h.daemon.reconcile_once()
    assert summary.started == [h.rid]
    assert len(h.processes) == 1
    assert h.unit.companion_resolution.managed_snapshot.runtimes[0].version == "3.0.0"


def test_materializer_result_churn_cannot_alias_selected_snapshot(harness):
    h = harness
    h.daemon.reconcile_once()
    snapshot = h.unit.companion_resolution.managed_snapshot
    original = snapshot.runtimes[0]
    h.daemon._managed_runtime_results[h.rid] = (replace(original, python=h.plugin / "wrong"),)
    summary = h.daemon.reconcile_once()
    assert summary.restarted == []
    assert h.unit.companion_resolution.managed_snapshot == snapshot
    assert h.unit.proc.poll() is None


def test_materializer_result_for_another_authority_is_rejected(harness, caplog):
    h = harness
    h.daemon.reconcile_once()
    old = h.unit.proc
    prior = h.unit.companion_resolution.managed_snapshot.runtimes
    h.change()
    h.materializer.materialize = lambda registration: prior
    summary = h.daemon.reconcile_once()
    assert summary.restarted == []
    assert h.unit.proc is old
    assert "does not match" in caplog.text


def test_restart_uses_exact_selected_snapshot_before_provider_churn(harness):
    h = harness
    h.daemon.reconcile_once()
    previous = h.unit.companion_resolution.managed_snapshot
    count = len(h.processes)
    h.change(version="2.0.0", mode="after-restart")
    h.controller = h.make_controller()
    h.daemon = h.make_daemon()
    summary = h.daemon.reconcile_once()
    assert summary.running == [h.rid]
    assert h.unit.companion_resolution.managed_snapshot == previous
    assert len(h.processes) in {count, count + 1}  # Windows reacquires Job ownership by restart.


def test_restart_during_cutover_can_restore_prior_published_selection(harness):
    h = harness
    h.daemon.reconcile_once()
    previous = h.unit.companion_resolution.managed_snapshot
    h.controller.stop(h.unit.companion_resolution, h.unit.proc)
    h.controller = h.make_controller()
    h.daemon = h.make_daemon()
    summary = h.daemon.reconcile_once()
    assert summary.started == [h.rid]
    assert h.unit.companion_resolution.managed_snapshot == previous
    assert h.builder.install_count == 1


def test_restart_with_uncertain_provider_never_starts_a_new_process(harness):
    h = harness
    h.daemon.reconcile_once()
    h.unit.proc.terminate()
    h.controller = h.make_controller()
    h.daemon = h.make_daemon()
    h.provider = CompanionIndeterminate("provider unavailable")
    count = len(h.processes)
    assert h.daemon.reconcile_once().started == []
    assert len(h.processes) == count


def test_unverifiable_stop_does_not_launch_a_replacement(harness, monkeypatch):
    h = harness
    h.daemon.reconcile_once()
    previous = h.unit.proc
    h.change()

    def uncertain_stop(*args):
        raise CompanionIndeterminate("process identity unavailable")

    monkeypatch.setattr(h.controller, "stop", uncertain_stop)
    summary = h.daemon.reconcile_once()
    assert summary.restarted == []
    assert h.unit.proc is previous
    assert len(h.processes) == 1


def test_selected_cell_validation_never_rebuilds_or_replaces_missing_cell(harness):
    h = harness
    h.daemon.reconcile_once()
    snapshot = h.unit.companion_resolution.managed_snapshot
    snapshot.runtimes[0].receipt.unlink()
    with pytest.raises(ManagedRuntimeError, match="unavailable"):
        h.materializer.validate(snapshot.resolution().registration, snapshot.runtimes)
    assert h.builder.install_count == 1


def test_malformed_selected_snapshot_is_not_executable(harness):
    h = harness
    h.daemon.reconcile_once()
    path = h.controller._selection_path(h.rid)
    record = json.loads(path.read_text(encoding="utf-8"))
    record["managed_snapshot"]["environment"]["EXAMPLE_MANAGED_PYTHON"] = "foreign"
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(CompanionError, match="binding"):
        h.controller.selected_managed(h.rid)


def test_materialization_remains_nonblocking(harness, monkeypatch):
    h = harness
    started, release = threading.Event(), threading.Event()
    materialize = h.materializer.materialize

    def blocked(registration):
        started.set()
        assert release.wait(timeout=5)
        return materialize(registration)

    monkeypatch.setattr(h.materializer, "materialize", blocked)
    h.daemon._runtime_executor = None
    h.daemon._owns_runtime_executor = True
    try:
        before = time.monotonic()
        assert h.daemon.reconcile_once().started == []
        assert time.monotonic() - before < 1
        assert started.wait(timeout=2)
    finally:
        release.set()
        h.daemon._runtime_executor.shutdown(wait=True)
        h.daemon.shutdown()


def test_materialization_failure_retries_with_backoff(harness):
    h = harness
    h.builder.fail_install = True
    h.daemon.reconcile_once()
    h.daemon.reconcile_once()
    assert h.builder.install_count == 1
    h.advance(5)
    h.daemon.reconcile_once()
    assert h.builder.install_count == 2
    h.daemon.reconcile_once()
    assert h.builder.install_count == 2


def test_restart_revokes_changed_activation_authority_even_when_build_fails(harness):
    h = harness
    h.daemon.reconcile_once()
    previous = h.unit.proc
    h.registration["plugin"]["activation_scopes"].append("another")
    h.builder.fail_install = True
    h.controller = h.make_controller()
    h.daemon = h.make_daemon()
    summary = h.daemon.reconcile_once()
    assert summary.running == []
    assert previous.poll() is not None
    assert not h.controller._receipt_path(h.rid).exists()
    assert h.controller.selected_managed(h.rid) is None


def test_failed_selected_recovery_does_not_block_ready_current_configuration(harness):
    h = harness
    h.daemon.reconcile_once()
    h.unhealthy.add("old")
    h.change(version="2.0.0")
    h.controller = h.make_controller()
    h.daemon = h.make_daemon()
    summary = h.daemon.reconcile_once()
    assert summary.running == [h.rid]
    assert h.unit.companion_resolution.environment["MODE"] == "new"
    assert h.controller.selected_managed(h.rid) == h.unit.companion_resolution.managed_snapshot


@pytest.mark.skipif(os.name != "nt", reason="Windows Job ownership must be reacquired")
def test_windows_recovery_preserves_receipt_until_retirement_is_confirmed(harness, monkeypatch):
    h = harness
    h.daemon.reconcile_once()
    previous = h.unit.proc
    monkeypatch.setattr(companion, "_terminate_windows_tree", lambda pid: None)
    h.controller = h.make_controller()
    h.daemon = h.make_daemon()
    summary = h.daemon.reconcile_once()
    assert summary.running == []
    assert previous.poll() is None
    assert len(h.processes) == 1
    assert h.controller._receipt_path(h.rid).exists()


def test_crash_retirement_backoff_and_budget_preserve_managed_ownership(harness):
    h = harness
    h.daemon.max_restarts = 1
    h.daemon.reconcile_once()
    h.unit.proc.returncode = 1
    summary = h.daemon.reconcile_once()
    assert summary.revived == [h.rid]
    assert h.unit.restarts == 1
    h.unit.proc.returncode = 1
    summary = h.daemon.reconcile_once()
    assert summary.dead == [h.rid]
    assert not h.controller._receipt_path(h.rid).exists()
    assert h.unit.proc is None
    assert len(h.processes) == 2


@pytest.mark.skipif(os.name == "nt", reason="Windows cannot readopt a predecessor's Job")
def test_uncertain_restart_adopts_exact_live_posix_process(harness):
    h = harness
    h.daemon.reconcile_once()
    pid = h.unit.proc.pid
    h.provider = CompanionIndeterminate("provider unavailable")
    h.controller = h.make_controller()
    h.daemon = h.make_daemon()
    summary = h.daemon.reconcile_once()
    assert summary.recovered == [h.rid]
    assert h.unit.proc.pid == pid
    assert len(h.processes) == 1


def test_real_managed_companion_readiness_rollback_and_stop(tmp_path, monkeypatch):
    plugin = _project(tmp_path)
    script = plugin / "service.py"
    script.write_text(
        "import json, os, subprocess, sys, time\n"
        "from pathlib import Path\n"
        "from agent_procutil import no_window_kwargs\n"
        "state = Path(os.environ['EXAMPLE_STATE'])\n"
        "stop = state.with_suffix('.stop')\n"
        "mode = os.environ['MODE']\n"
        "if sys.argv[1] == 'run':\n"
        "    stop.unlink(missing_ok=True)\n"
        "    state.unlink(missing_ok=True)\n"
        "    if mode == 'bad': sys.exit(1)\n"
        "    state.write_text(json.dumps({'mode': mode, 'pid': os.getpid(),\n"
        "        'python': os.environ['EXAMPLE_MANAGED_PYTHON']}), encoding='utf-8')\n"
        "    while not stop.exists(): time.sleep(0.05)\n"
        "elif sys.argv[1] == 'health':\n"
        "    subprocess.run([sys.executable.replace('pythonw.exe', 'python.exe'), '-c', 'pass'],\n"
        "        check=True, **no_window_kwargs())\n"
        "    ready = state.exists() and json.loads(state.read_text(encoding='utf-8'))['mode'] == mode\n"
        "    print(json.dumps({'schema_version': 1, 'healthy': ready}))\n"
        "else:\n"
        "    stop.touch()\n"
        "    state.unlink(missing_ok=True)\n",
        encoding="utf-8",
    )
    registration = _registration(plugin)
    registration["spec"].update(
        command=["service.py", "run"],
        stop_command=["service.py", "stop"],
        health_probe=["service.py", "health"],
        startup_timeout_seconds=3,
        stop_timeout_seconds=3,
    )
    marker = tmp_path / "process.json"
    monkeypatch.setenv("EXAMPLE_STATE", str(marker))
    monkeypatch.setenv("MODE", "old")
    controller = DefaultCompanionController(tmp_path / "state")
    materializer = ManagedRuntimeMaterializer(_policy(tmp_path), runner=FakeRunner())

    class Client:
        def list_registrations(self, **kwargs):
            return [registration]

    daemon = SupervisorDaemon(
        Client(),
        "machine-a",
        companion_controller=controller,
        runtime_materializer=materializer,
        runtime_executor=Executor(),
        overrides_source=lambda: {},
    )
    rid = registration["id"]
    try:
        assert daemon.reconcile_once().started == [rid]
        snapshot = daemon._units[rid].companion_resolution.managed_snapshot
        first = json.loads(marker.read_text(encoding="utf-8"))
        for _ in range(2):
            time.sleep(0.1)
            assert controller.health(snapshot.resolution()) is True
        monkeypatch.setenv("MODE", "bad")
        summary = daemon.reconcile_once()
        assert summary.revived == [rid]
        restored = json.loads(marker.read_text(encoding="utf-8"))
        assert restored["mode"] == "old"
        assert restored["python"] == str(snapshot.runtimes[0].python)
        assert restored["pid"] != first["pid"]
        assert daemon._units[rid].companion_resolution.managed_snapshot == snapshot
    finally:
        daemon.shutdown()
    assert not controller._receipt_path(rid).exists()
