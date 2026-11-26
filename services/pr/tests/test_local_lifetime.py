"""Local foreground retains its writer through delayed native and disk cleanup."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
import time
import sys
from typing import TYPE_CHECKING, Any

import pytest
from agent_procutil.owned_process import OwnedCleanupError, run_caller_owned
from single_instance_lease import AlreadyRunningError, SingleInstance

from agent_pull_requests import native_command
from agent_pull_requests import watch_replay_store
from agent_pull_requests.watch_service import WatchService
from agent_pull_requests.watch_storage import _atomic_write_json
from agent_pull_requests_service import hosting
from agent_pull_requests_service.local_client import LocalClient, LocalClientError
from agent_pull_requests_service.local_config import LocalHostConfig, load_service_config

if TYPE_CHECKING:
    from services.pr.tests.test_local_repository import configuration
else:
    from test_local_repository import configuration


@pytest.mark.skipif(sys.platform != "linux", reason="Linux local ingress")
@pytest.mark.parametrize("phase", ["inventory", "snapshot", "persistence"])
def test_local_foreground_keeps_writer_until_original_native_cleanup_joins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    path, _value, _state, _log = configuration(tmp_path)
    config = load_service_config(path)
    assert isinstance(config, LocalHostConfig)
    config.provision()
    entered, release, settled = threading.Event(), threading.Event(), threading.Event()
    original = run_caller_owned
    original_write = _atomic_write_json
    loop_thread = threading.get_ident()
    services: list[Any] = []
    actual = WatchService
    def create(*args: Any, **kwargs: Any) -> Any:
        service = actual(*args, **kwargs)
        services.append(service)
        return service
    def cleanup(deadline: float) -> None:
        assert threading.get_ident() != loop_thread
        entered.set()
        if not release.wait(max(0, deadline - time.monotonic())):
            raise TimeoutError
        settled.set()
    def delayed(argv: list[str], **kwargs: Any) -> Any:
        if phase in argv and "--contract" not in argv:
            raise OwnedCleanupError(None, cleanup_retry=cleanup)
        return original(argv, **kwargs)
    def persist(target: Path, document: dict[str, Any]) -> None:
        original_write(target, document)
        if phase == "persistence":
            entered.set()
            assert release.wait(10), "fixture persistence cleanup was not released"
            settled.set()
    monkeypatch.setattr(hosting, "WatchService", create)
    monkeypatch.setattr(native_command, "run_caller_owned", delayed)
    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", persist)

    async def scenario() -> None:
        stop = asyncio.Event()
        owner = asyncio.create_task(hosting.run(config, stop=stop))
        try:
            assert await asyncio.to_thread(entered.wait, 10)
            await asyncio.wait_for(services[0].connector.connected.wait(), 5)
            client = LocalClient(config.repositories.socket_path, "test", "repo-github")
            if phase != "persistence":
                cached = await asyncio.to_thread(client.request, "query", {})
                assert not cached["pulls"] or cached["pulls"][0]["facts"] is None
            before = config.store().path.read_bytes()
            stop.set()
            deadline = time.monotonic() + 5
            while not services[0].status()["draining"]:
                assert time.monotonic() < deadline
                await asyncio.sleep(.01)
            assert not owner.done() and not settled.is_set()
            with pytest.raises(LocalClientError):
                await asyncio.to_thread(client.request, "query", {})
            with pytest.raises(AlreadyRunningError):
                SingleInstance(config.root, service="agent-pull-requests-watch").acquire()
        finally:
            stop.set()
            release.set()
            await asyncio.wait_for(asyncio.shield(owner), 10)
        assert settled.is_set() and not services[0].status()["writer_held"]
        assert not services[0].gateway._tasks and not config.repositories.socket_path.exists()
        assert config.store().path.read_bytes() == before
        with SingleInstance(config.root, service="agent-pull-requests-watch"):
            pass
    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != "linux", reason="Linux local ingress")
def test_query_recovery_requires_actual_joined_foreground_and_exclusive_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests.snapshot_batch_recovery import RecoverySelection
    from agent_pull_requests.snapshot_batch_state import BatchAdmission, scope
    from agent_pull_requests_service import snapshot_batch_transition as recovery

    path, value, _state, log = configuration(tmp_path)
    path.write_text(json.dumps(value | {"github_snapshot_batches": True}))
    config = load_service_config(path)
    assert isinstance(config, LocalHostConfig)
    config.provision()
    store = config.store()
    identity = config.observations[0].repository_identity
    assert identity is not None and identity.provider == "github"
    key = scope(identity)
    expires_at = int(time.time()) + 60
    pending = BatchAdmission(store).begin(key, expires_at=expires_at)
    assert pending is not None
    selection = RecoverySelection(key, pending, "1" * 32, "retain-unknown")
    entered, finish = threading.Event(), threading.Event()
    original = run_caller_owned

    def cleanup(deadline: float) -> None:
        entered.set()
        if not finish.wait(max(0, deadline - time.monotonic())):
            raise TimeoutError

    def delayed(argv: list[str], **kwargs: Any) -> Any:
        if "inventory" in argv and "--contract" not in argv:
            raise OwnedCleanupError(None, cleanup_retry=cleanup)
        return original(argv, **kwargs)

    monkeypatch.setattr(native_command, "run_caller_owned", delayed)

    async def scenario() -> None:
        stop = asyncio.Event()
        foreground = asyncio.create_task(hosting.run(config, stop=stop))
        joined = False

        def verify_owner() -> None:
            if not joined:
                raise PermissionError("fixture predecessor has not gracefully joined")

        try:
            assert await asyncio.to_thread(entered.wait, 10)
            stop.set()
            before = store.path.read_bytes()
            with pytest.raises(PermissionError, match="not gracefully joined"):
                recovery.recover(config, selection, owner_checkpoint=verify_owner, expires_at=expires_at)
            with pytest.raises(AlreadyRunningError):
                recovery.recover(config, selection, owner_checkpoint=lambda: None, expires_at=expires_at)
            assert store.path.read_bytes() == before and not foreground.done()
        finally:
            stop.set()
            finish.set()
            await asyncio.wait_for(asyncio.shield(foreground), 10)
        joined = True
        provider_calls = log.read_bytes()
        original_recovery = recovery.recover(
            config, selection, owner_checkpoint=verify_owner, expires_at=expires_at,
        )
        assert original_recovery == recovery.read_recovery(config, selection, expires_at=expires_at)
        assert original_recovery["disposition"] == "outcome-unknown"
        assert log.read_bytes() == provider_calls

    asyncio.run(scenario())
