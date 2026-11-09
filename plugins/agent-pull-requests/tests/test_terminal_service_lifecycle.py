"""Explicit terminal lifecycle over the real service writer and admitted reads."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
import threading
import time

import pytest
from single_instance_lease import AlreadyRunningError

from test_watch_service import Connector, contender, request as replay_request, wait_for
from test_workspace_terminal_read import (
    environment as environment, integration as integration, workspace as workspace,
    terminal_reader as terminal_reader, expires, request,
)
from test_workspace_terminal_retention import terminal
from agent_pull_requests import watch_service
from agent_pull_requests.attention import Cursor
from agent_pull_requests.generation_cache import GenerationCache
from agent_pull_requests.terminal_retention import TerminalAdmissionClosed, TerminalRetentionStore
from agent_pull_requests.watch_gateway import WatchScopeBinding
from agent_pull_requests.watch_observer import NativeObservationBinding
from agent_pull_requests.watch_replay import ConsumerScope, ObservationTarget
from agent_pull_requests.watch_replay_binding import ReplayBinding
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.workspace_operations import WorkspaceOperations

pytestmark = pytest.mark.parametrize("environment", [True], indirect=True)


def service_for(operations: WorkspaceOperations, binding: ReplayBinding) -> watch_service.WatchService:
    async def access() -> str:
        return "synthetic"

    return watch_service.WatchService(
        operations.store, fleet=binding.fleet, gateway=binding.gateway, slot=binding.slot,
        bindings=(WatchScopeBinding(
            binding.principal, binding.target,
            ConsumerScope(binding.consumer, binding.account, binding.repo),
        ),),
        observations=(NativeObservationBinding(binding.account, binding.repo, sys.executable),),
        gateway_origin="https://gateway.invalid", access_token_callback=access,
        proof_key=object(), authority=expires, checkpoint=lambda: None, interval=60,
        workspaces={(binding.principal, binding.target): operations},
    )


def reopen(previous: WorkspaceOperations) -> WorkspaceOperations:
    owner = ReplayStore(
        previous.store.root, previous.store.namespace,
        service_identity=previous.store.service_identity, terminal_retention=True,
        terminal_recovery=True, terminal_cache_retirement=True,
    )
    cache = GenerationCache(
        previous.cache.owner_root, owner.namespace, service_identity=owner.service_identity,
        max_bytes=previous.cache.max_bytes, max_generations=previous.cache.max_generations,
    )
    return WorkspaceOperations(
        owner, cache, scope=previous.scope, read_owner=previous.read_owner,
        binding_ref=previous.binding_ref, checkpoint=previous.checkpoint, max_work_seconds=120,
        conversations=True, retention_target=previous.retention_target, terminal_reads=True,
    )


@pytest.mark.parametrize("drain", ["workspace", "service-timeout", "service-cancel"])
def test_explicit_lifecycle_joins_disconnected_reads_before_retirement_and_reopen(
    terminal_reader: tuple, monkeypatch: pytest.MonkeyPatch, drain: str,
) -> None:
    operations, _, _, _, evidence, backend, _, binding, initial = terminal_reader
    monkeypatch.setattr(watch_service, "ServiceConnector", Connector)
    entered, release = threading.Event(), threading.Event()
    scope = initial["scope"]
    consumer = ConsumerScope(binding.consumer, binding.account, binding.repo)
    issued = int(time.time()) - 1
    read = operations.read_owner.read

    def held(*args, **kwargs) -> dict:
        value = read(*args, **kwargs)
        entered.set()
        assert release.wait(5)
        return value

    def target(selected: dict) -> ObservationTarget:
        return next(
            (row for row in operations.store.observation_targets(expires_at=expires())
             if row.number == selected["number"]),
            ObservationTarget(binding.account, binding.repo, selected["number"], frozenset()),
        )

    operations.retention_target = target

    async def scenario() -> None:
        nonlocal operations
        service = service_for(operations, binding)
        caller = admitted = closing = None
        try:
            await service.start()
            await wait_for(lambda: service.status()["ready"])
            receipt = operations.store.execute(consumer, "register", {
                "request_id": "terminal-original", "issued_at": issued, "number": 42,
                "until": ("merged",), "deadline_at": expires() - 10,
            }, expires_at=expires())
            loaded = await service.gateway.handle(request(binding, "load", {
                "number": 42, "expected_current": initial["id"], "budget_seconds": 10,
            }))
            assert loaded.status == 200
            live = json.loads(loaded.body)["result"]
            identity = live["descriptor"]["id"]
            current = operations.cache.lookup(scope)["generation"]
            chunk = await service.gateway.handle(request(binding, "chunk", {
                "number": 42, "generation_id": identity, "offset": 0,
            }))
            assert chunk.status == 200 and json.loads(chunk.body)["result"]["data"]
            buffers = copy.deepcopy(operations._materializations)
            objects = {path: path.read_bytes() for path in operations.cache.generations.iterdir()}
            assert buffers and objects

            monkeypatch.setattr(operations.read_owner, "read", held)
            caller = asyncio.create_task(service.gateway.handle(request(binding, "load", {
                "number": 42, "expected_current": identity, "budget_seconds": 10,
            })))
            assert await asyncio.to_thread(entered.wait, 3)
            assert len(service.gateway._tasks) == 1
            admitted = next(iter(service.gateway._tasks))
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            decision, _ = terminal(operations, current)
            cursor = Cursor(**decision["cursor"])
            pending = operations.store.execute(consumer, "pending", {}, expires_at=expires())["events"]
            assert len(pending) == 1 and pending[0]["registration_id"] == receipt["registration_id"]
            before = operations.store.path.read_bytes()
            service.gateway.stop_admission()

            def exclusive() -> None:
                service._check()
                assert service.gateway._closing
                assert service.gateway.workspaces[(binding.principal, binding.target)] is operations

            with pytest.raises(TimeoutError, match="incomplete"):
                await asyncio.to_thread(
                    operations.retention.compact_recovery, scope, cursor, {},
                    expires_at=expires(), checkpoint=service._check,
                    lifecycle_checkpoint=exclusive, deadline=time.monotonic() + 0.02,
                )
            with pytest.raises(TimeoutError):
                await service.gateway.drain(timeout=0.01)
            if drain == "service-timeout":
                with pytest.raises(TimeoutError):
                    await service.close(timeout=0.02)
            elif drain == "service-cancel":
                closing = asyncio.create_task(service.close(timeout=3))
                await wait_for(lambda: service.status()["draining"])
                closing.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await closing
            assert not admitted.done()
            assert service.status()["writer_held"]
            with pytest.raises(AlreadyRunningError):
                contender(operations.store).acquire()
            refused = await service.gateway.handle(request(binding, "chunk", {
                "number": 42, "generation_id": identity, "offset": 0,
            }))
            assert refused.status == 503
            assert operations.store.path.read_bytes() == before
            assert operations._materializations == buffers
            assert all(path.read_bytes() == data for path, data in objects.items())
            retained = TerminalRetentionStore(operations.store).inspect(
                scope, cursor, expires_at=expires(),
            )
            assert retained["drain"] == "pending"

            release.set()
            response = await asyncio.wait_for(asyncio.shield(admitted), 3)
            assert response.status == 503
            await service.gateway.drain(timeout=3)
            if drain != "workspace":
                await service.close(timeout=3)
                assert service.status()["closed"] and not service.status()["writer_held"]
                # Restart the exact workspace so its retained buffers are not
                # hidden by constructing a new, empty materialization owner.
                service = service_for(operations, binding)
                await service.start()
                await wait_for(lambda: service.status()["ready"])
                service.gateway.stop_admission()
                await service.gateway.drain(timeout=3)
            await asyncio.to_thread(
                operations.retention.compact_recovery, scope, cursor, {},
                expires_at=expires(), checkpoint=service._check,
                lifecycle_checkpoint=exclusive, deadline=time.monotonic() + 3,
            )
            retired = await asyncio.to_thread(
                operations.retention.retire_cache, scope, cursor,
                expires_at=expires(), checkpoint=service._check,
                lifecycle_checkpoint=exclusive, deadline=time.monotonic() + 3,
            )
            assert retired["status"] == "complete"
            assert not retired["delivery_settled"] and not retired["recovery_settled"]
            assert not operations._materializations and identity not in operations._generation_positions
            assert all(not path.exists() for path in objects)
            assert operations.cache.lookup(scope)["generation"] is None
            assert operations.store.execute(consumer, "pending", {}, expires_at=expires())["events"] == pending
            await service.close(timeout=3)

            operations = reopen(operations)
            service = service_for(operations, binding)
            await service.start()
            await wait_for(lambda: service.status()["ready"])
            page = await service.gateway.handle(request(binding, "events", {
                "number": 42, "after": None, "limit": 16,
            }))
            assert page.status == 200
            assert json.loads(page.body)["result"]["high_water"] == decision["cursor"]
            assert operations.store.execute(consumer, "pending", {}, expires_at=expires())["events"] == pending
            backend.pr["status"] = "active"
            for section in evidence["sections"].values():
                section.update(observed_from="2026-10-30T00:00:30Z", observed_to="2026-10-30T00:00:31Z")
            refreshed = await service.gateway.handle(request(binding, "load", {
                "number": 42, "expected_current": None, "budget_seconds": 10,
            }))
            assert refreshed.status == 200
            reopened = json.loads(refreshed.body)["result"]
            assert reopened["conversation"]["cursor"] == {"epoch": 2, "sequence": 1}
            new_identity = reopened["descriptor"]["id"]
            assert new_identity != identity
            for selected, status in ((identity, 503), (new_identity, 200)):
                response = await service.gateway.handle(request(binding, "chunk", {
                    "number": 42, "generation_id": selected, "offset": 0,
                }))
                assert response.status == status
            service.gateway.stop_admission()
            await service.gateway.drain(timeout=3)
            new_bytes = {path: path.read_bytes() for path in operations.cache.generations.iterdir()}
            with pytest.raises(TerminalAdmissionClosed):
                await asyncio.to_thread(
                    operations.retention.retire_cache, scope, cursor,
                    expires_at=expires(), checkpoint=service._check,
                    lifecycle_checkpoint=exclusive, deadline=time.monotonic() + 3,
                )
            assert operations.cache.lookup(scope)["generation"]["id"] == new_identity
            assert all(path.read_bytes() == data for path, data in new_bytes.items())
            assert operations.store.execute(consumer, "pending", {}, expires_at=expires())["events"] == pending
            await service.close(timeout=3)

            service = service_for(operations, binding)
            await service.start()
            await wait_for(lambda: service.status()["ready"])
            ack = await service.gateway.handle(replay_request("acknowledge", {
                "request_id": "terminal-original", "issued_at": issued,
                "registration_id": receipt["registration_id"], "event_id": pending[0]["event_id"],
            }))
            assert ack.status == 200
            assert operations.store.execute(consumer, "pending", {}, expires_at=expires())["events"] == []
            assert not backend.calls and backend.vote is None
        finally:
            release.set()
            await asyncio.gather(
                *(task for task in (caller, admitted, closing) if task is not None),
                return_exceptions=True,
            )
            await service.close(timeout=3)
        with contender(operations.store):
            pass

    asyncio.run(scenario())
