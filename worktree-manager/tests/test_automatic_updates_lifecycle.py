"""Scheduling-custody and draining contracts, separate from native qualification."""

from __future__ import annotations

import asyncio
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from agent_procutil import DaemonSpawnError
from zdd.cutover_lock import CutoverLock

from worktree_manager import automatic_updates_autostart as autostart
from worktree_manager import automatic_updates_policy as policy
from worktree_manager import automatic_updates_process as process
from worktree_manager import automatic_updates_runtime as runtime


def _enabled(root: Path) -> None:
    policy.configure(root, owner="manager", provenance="user", project="example")


def test_worker_stays_responsive_and_drains_overdue_update_before_disable(
    tmp_path: Path, monkeypatch,
) -> None:
    async def exercise() -> None:
        _enabled(tmp_path)
        worker = runtime.Worker(tmp_path)
        released, started = asyncio.Event(), asyncio.Event()

        async def spawn(*args, **kwargs):
            async def wait() -> int:
                started.set()
                await released.wait()
                return 0

            return SimpleNamespace(wait=wait)

        monkeypatch.setattr(process, "selected_slot", lambda root: Path(worker.snapshot["runtime"]))
        monkeypatch.setattr(process, "selected_command", lambda root, args: ["canonical", *args])
        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(runtime, "POLL_SECONDS", 0.01)
        monkeypatch.setattr(runtime, "CYCLE_BUDGET_SECONDS", 0.02)
        task = asyncio.create_task(worker.run())
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.sleep(0.04)
        observed = await asyncio.to_thread(process.worker_status, tmp_path)
        assert observed["running"]
        assert observed["state"] == "draining-overdue-update"
        policy.atomic_json(tmp_path / policy.POLICY_NAME, asdict(policy.Policy(
            provenance="user", user_disabled=True,
        )))
        await asyncio.sleep(0.02)
        assert not task.done()
        released.set()
        assert await asyncio.wait_for(task, timeout=2) == 0
        assert worker.snapshot["state"] == "disabled"
        assert not worker.lease.held
        assert process.worker_status(tmp_path)["running"] is False

    asyncio.run(exercise())


def test_native_supervisor_restarts_only_after_nonowning_worker_qualification(tmp_path: Path, monkeypatch) -> None:
    async def exercise() -> None:
        _enabled(tmp_path)
        worker = runtime.Worker(tmp_path)
        worker.lease.acquire()
        monkeypatch.setattr(process, "selected_slot", lambda root: tmp_path / "new")
        monkeypatch.setattr(autostart, "native_supervisor_state", lambda root: "systemd")
        monkeypatch.setattr(process, "probe", lambda snapshot: {"runtime": str(tmp_path / "new")})
        calls = []

        def qualify(*, timeout):
            assert worker.lease.held
            calls.append(timeout)
            return 0

        monkeypatch.setattr(process, "spawn_worker", lambda *a, **k: SimpleNamespace(
            poll=lambda: 0, wait=qualify,
        ))
        assert await worker.replace_if_stale()
        assert calls == [25]
        assert worker.snapshot["state"] == "native-restart-requested"
        assert not worker.lease.held
        with CutoverLock(policy.state_dir(tmp_path) / "worker-lease"):
            pass

    asyncio.run(exercise())


def test_native_failed_successor_preserves_owner(tmp_path: Path, monkeypatch) -> None:
    async def exercise() -> None:
        _enabled(tmp_path)
        worker = runtime.Worker(tmp_path)
        worker.lease.acquire()
        monkeypatch.setattr(process, "selected_slot", lambda root: tmp_path / "new")
        monkeypatch.setattr(autostart, "native_supervisor_state", lambda root: "systemd")
        monkeypatch.setattr(process, "spawn_worker", lambda *a, **k: SimpleNamespace(poll=lambda: 23))
        assert not await worker.replace_if_stale()
        assert worker.lease.held and worker.snapshot["state"] == "replacement-deferred"
        worker.lease.release()

    asyncio.run(exercise())


def test_native_qualification_timeout_does_not_spawn_another_live_candidate(tmp_path: Path, monkeypatch) -> None:
    async def exercise() -> None:
        _enabled(tmp_path)
        worker = runtime.Worker(tmp_path)
        worker.lease.acquire()
        monkeypatch.setattr(process, "selected_slot", lambda root: tmp_path / "new")
        monkeypatch.setattr(autostart, "native_supervisor_state", lambda root: "systemd")
        monkeypatch.setattr(process, "probe", lambda snapshot: {"runtime": str(tmp_path / "new")})
        calls = []

        def waiting(*, timeout):
            assert worker.lease.held
            raise subprocess.TimeoutExpired("native candidate", timeout)

        def spawn(*args, **kwargs):
            calls.append(True)
            return SimpleNamespace(poll=lambda: None, wait=waiting)

        monkeypatch.setattr(process, "spawn_worker", spawn)
        assert not await worker.replace_if_stale()
        worker.restart_after = 0
        assert not await worker.replace_if_stale()
        assert len(calls) == 1 and worker.lease.held
        assert "unsettled" in worker.snapshot["reason"]
        worker.lease.release()

    asyncio.run(exercise())


def test_native_selection_change_during_qualification_keeps_original_owner(tmp_path: Path, monkeypatch) -> None:
    async def exercise() -> None:
        _enabled(tmp_path)
        worker = runtime.Worker(tmp_path)
        worker.lease.acquire()
        selection = [tmp_path / "new"]
        monkeypatch.setattr(process, "selected_slot", lambda root: selection[0])
        monkeypatch.setattr(autostart, "native_supervisor_state", lambda root: "systemd")
        monkeypatch.setattr(process, "probe", lambda snapshot: {"runtime": str(tmp_path / "new")})

        def qualify(*, timeout):
            selection[0] = tmp_path / "newer"
            return 0

        monkeypatch.setattr(process, "spawn_worker", lambda *a, **k: SimpleNamespace(poll=lambda: 0, wait=qualify))
        assert not await worker.replace_if_stale()
        assert worker.lease.held and "selection changed" in worker.snapshot["reason"]
        worker.lease.release()

    asyncio.run(exercise())


def test_native_changed_bootstrap_interpreter_preserves_owner(tmp_path: Path, monkeypatch) -> None:
    async def exercise() -> None:
        _enabled(tmp_path)
        old = runtime.Worker(tmp_path)
        old.lease.acquire()
        loop = asyncio.get_running_loop()
        candidates = []
        monkeypatch.setattr(process, "selected_slot", lambda root: tmp_path / "new")
        monkeypatch.setattr(process, "runtime_identity", lambda: str(tmp_path / "new"))
        monkeypatch.setattr(autostart, "native_supervisor_state", lambda root: "systemd")
        monkeypatch.setattr(autostart, "inspect_autostart_refresh", lambda root: {
            "registered": True, "activation": "blocked",
            "blockers": ["active service replacement requires separate stop/handover approval"],
        })

        async def launch(standby):
            candidate = runtime.Worker(tmp_path, standby=standby)
            task = asyncio.create_task(candidate.run())
            candidates.append((candidate, task))

            async def outcome():
                try:
                    return await task
                except policy.PolicyError:
                    return 1

            return SimpleNamespace(
                poll=lambda: None if not task.done() else (1 if task.exception() else task.result()),
                wait=lambda timeout: asyncio.run_coroutine_threadsafe(outcome(), loop).result(timeout),
            )

        def spawn(root, *, standby):
            return asyncio.run_coroutine_threadsafe(launch(standby), loop).result(timeout=2)

        monkeypatch.setattr(process, "spawn_worker", spawn)
        assert not await asyncio.wait_for(old.replace_if_stale(), timeout=3)
        assert old.lease.held and old.snapshot["state"] == "replacement-deferred"
        for candidate, task in candidates:
            assert isinstance(task.exception(), policy.PolicyError)
            assert candidate.snapshot["state"] == "starting"
            assert not candidate.lease.held
        assert not list(policy.state_dir(tmp_path).glob("candidate-*.json"))
        old.lease.release()

    asyncio.run(exercise())


def test_standby_validates_saved_schedule_before_publishing_readiness(tmp_path: Path, monkeypatch) -> None:
    async def exercise() -> None:
        monkeypatch.delenv(autostart.UNIT_ENV, raising=False)
        _enabled(tmp_path)
        old = CutoverLock(policy.state_dir(tmp_path) / "worker-lease")
        old.acquire()
        candidate = runtime.Worker(tmp_path, standby="a" * 32)
        monkeypatch.setattr(autostart, "inspect_autostart_refresh", lambda root: {"registered": False})
        monkeypatch.setattr(process, "read_snapshot", lambda path: {"next_due": float("nan")})
        with pytest.raises(policy.PolicyError, match="saved maintenance schedule"):
            await candidate.run()
        assert old.held and not candidate.lease.held
        assert not list(policy.state_dir(tmp_path).glob("candidate-*.json"))
        old.release()

    asyncio.run(exercise())


def test_failed_successor_preparation_keeps_current_owner(tmp_path: Path, monkeypatch) -> None:
    async def exercise() -> None:
        worker = runtime.Worker(tmp_path)
        worker.lease.acquire()
        monkeypatch.setattr(
            process, "spawn_worker", lambda *args, **kwargs: SimpleNamespace(poll=lambda: 1),
        )
        assert not await worker.handover()
        assert worker.lease.held
        assert worker.snapshot["state"] == "replacement-deferred"
        assert worker.restart_after > time.time()
        worker.lease.release()

    asyncio.run(exercise())


def test_daemon_broker_failure_does_not_retire_a_healthy_controller(
    tmp_path: Path, monkeypatch,
) -> None:
    async def exercise() -> None:
        worker = runtime.Worker(tmp_path)
        worker.lease.acquire()
        due = worker.snapshot["next_due"]

        def denied(*args, **kwargs):
            raise DaemonSpawnError("fixture lifetime broker denied launch", cleanup_confirmed=True)

        monkeypatch.setattr(process, "spawn_worker", denied)
        assert not await worker.handover()
        assert worker.lease.held
        assert worker.snapshot["state"] == "replacement-deferred"
        assert worker.snapshot["next_due"] == due
        assert "lifetime broker denied" in worker.snapshot["reason"]
        worker.lease.release()

    asyncio.run(exercise())


def test_failed_activation_reacquires_old_custody(tmp_path: Path, monkeypatch) -> None:
    async def exercise() -> None:
        worker = runtime.Worker(tmp_path)
        worker.lease.acquire()
        monkeypatch.setattr(process, "selected_slot", lambda root: tmp_path / "new")
        monkeypatch.setattr(
            process, "spawn_worker", lambda *args, **kwargs: SimpleNamespace(poll=lambda: 1),
        )
        monkeypatch.setattr(process, "probe", lambda snapshot: {"runtime": str(tmp_path / "new")})
        monkeypatch.setattr(process, "worker_status", lambda root: {"running": False})
        assert not await worker.handover()
        assert worker.lease.held
        assert worker.snapshot["state"] == "replacement-recovered"
        worker.lease.release()

    asyncio.run(exercise())


def test_real_standby_activation_preserves_due_time_and_single_owner(
    tmp_path: Path, monkeypatch,
) -> None:
    async def exercise() -> None:
        monkeypatch.delenv(autostart.UNIT_ENV, raising=False)
        _enabled(tmp_path)
        old = runtime.Worker(tmp_path)
        old.lease.acquire()
        old.snapshot["next_due"] = time.time() + 3600
        old.snapshot["state"] = "waiting"
        old.snapshot["port"] = 1
        old.publish()
        loop = asyncio.get_running_loop()
        spawned = []
        monkeypatch.setattr(process, "selected_slot", lambda root: tmp_path / "new")
        monkeypatch.setattr(process, "runtime_identity", lambda: str(tmp_path / "new"))
        monkeypatch.setattr(runtime, "POLL_SECONDS", 0.01)

        async def launch(standby: str):
            successor = runtime.Worker(tmp_path, standby=standby)
            task = asyncio.create_task(successor.run())
            spawned.append((successor, task))
            return SimpleNamespace(poll=lambda: task.result() if task.done() else None)

        def spawn(root: Path, *, standby: str):
            return asyncio.run_coroutine_threadsafe(launch(standby), loop).result(timeout=2)

        monkeypatch.setattr(process, "spawn_worker", spawn)
        assert await asyncio.wait_for(old.handover(), timeout=3)
        successor, task = spawned[0]
        assert not old.lease.held
        assert successor.lease.held
        assert successor.snapshot["next_due"] == old.snapshot["next_due"]
        assert process.read_snapshot(
            policy.state_dir(tmp_path) / process.STATUS_NAME,
        )["worker_id"] == successor.snapshot["worker_id"]
        policy.configure(tmp_path, owner="disabled", provenance="user")
        assert await asyncio.wait_for(task, timeout=2) == 0
        assert not successor.lease.held

    asyncio.run(exercise())
