"""Replay client over the actual TLS caller, Gateway, connector and disk store."""

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from fleet_manager.gateway_service_caller import ServiceCaller

from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_client import (
    ReplayClient, ReplayClientError, ReplayOutcome, ReplayReceipt, ReplayRequest,
)
from agent_pull_requests.watch_replay_store import ReplayStore


def setup_store(tmp_path):
    store = ReplayStore((tmp_path / "replay").resolve(), "fleet/service/installation")
    store.provision()
    owner = WatchGateway(
        store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
        bindings=tuple(WatchScopeBinding(
            caller, "project", ConsumerScope(caller, caller, "example/project"),
        ) for caller in ("caller-a", "caller-b")),
    )
    return store, owner


def make_client(rig, *, principal="caller-a"):
    async def access():
        return rig.access(principal, 3)
    return ReplayClient(ServiceCaller(
        gateway_origin=rig.origin, gateway_ca=rig.ca,
        access_token_callback=access, proof_key=rig.h.keys[3].get_op_key("sign"),
    ), route_prefix="/api", target="project", repo="example/project")


def test_real_client_lost_receipt_restart_scope_and_exact_ack(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import running
    from test_gateway_service_handler import connect_handler

    async def scenario():
        store, owner = setup_store(tmp_path)
        async with running(tmp_path) as rig:
            rig.h.document["callers"].append({
                "principal": "caller-b", "key_source": "caller-a",
                "slots": ["service-a"], "methods": ["GET", "POST"],
            })
            await rig.update()
            await connect_handler(rig, owner.handle)
            selected, other = make_client(rig), make_client(rig, principal="caller-b")
            assert (await selected.capabilities()).max_batch == 16
            original = ReplayRequest("original", time.time())
            deadline = time.time() - 1
            # Lost to the consumer; it retains only the original intent, not a new ID.
            await selected.register(original, number=1, until=("merged",), deadline_at=deadline)
            receipt = await selected.inspect(original)
            assert isinstance(receipt, ReplayReceipt)
            assert await selected.register(
                original, number=1, until=("merged",), deadline_at=deadline,
            ) == receipt
            assert await other.inspect(original) == ReplayOutcome("uncertain")
            assert (await other.pending()).events == ()
            pending = await selected.pending()
            event, = pending.events
            assert event.payload()["timed_out"] is True
            assert event.registration_id == receipt.registration_id
            await owner.drain(timeout=3)
            rig.tasks[-1].cancel()
            await asyncio.gather(rig.tasks[-1], return_exceptions=True)
            owner = WatchGateway(
                store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
                bindings=tuple(WatchScopeBinding(
                    principal, target, scope,
                ) for (principal, target), scope in owner.bindings.items()),
            )
            await connect_handler(rig, owner.handle)
            assert await selected.inspect(original) == receipt
            assert await selected.pending() == pending
            assert await other.acknowledge(
                original, registration_id=event.registration_id, event_id=event.event_id,
            ) == ReplayOutcome("uncertain")
            ack = await selected.acknowledge(
                original, registration_id=event.registration_id, event_id=event.event_id,
            )
            assert ack.status == "acknowledged"
            assert await selected.acknowledge(
                original, registration_id=event.registration_id, event_id=event.event_id,
            ) == ack
            assert (await selected.pending()).events == ()
            assert await selected.cancel(
                original, registration_id=event.registration_id,
            ) == ReplayOutcome("uncertain")
            cancellable = ReplayRequest("cancel", time.time())
            registered = await selected.register(cancellable, number=2, until=("merged",))
            cancelled = await selected.cancel(
                cancellable, registration_id=registered.registration_id,
            )
            assert cancelled.status == "cancelled"
            assert await selected.inspect(cancellable) == cancelled
            assert len(json.loads(store.path.read_bytes())["records"]) == 2
            assert rig.backends[0].requests == []
            await owner.drain(timeout=3)
    asyncio.run(scenario())


@pytest.mark.parametrize("loss", ["policy", "channel", "cancel"])
@pytest.mark.parametrize("stage", ["before-lookup", "commit-started"])
def test_real_client_reconciles_original_id_after_loss(tmp_path, monkeypatch, loss, stage):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import eventually, running
    from test_gateway_service_handler import connect_handler
    from agent_pull_requests import watch_replay_store

    async def scenario():
        store, owner = setup_store(tmp_path)
        started, release = threading.Event(), threading.Event()
        decode, write = watch_replay_store.json_object, watch_replay_store._atomic_write_json

        def hold():
            if not started.is_set():
                started.set()
                assert release.wait(10)

        def lookup(raw):
            hold()
            return decode(raw)

        def commit(path, state):
            hold()
            write(path, state)

        async with running(tmp_path) as rig:
            await connect_handler(rig, owner.handle)
            selected = make_client(rig)
            original = ReplayRequest("original", time.time())
            monkeypatch.setattr(
                watch_replay_store, "json_object" if stage == "before-lookup" else "_atomic_write_json",
                lookup if stage == "before-lookup" else commit,
            )
            pending = asyncio.create_task(selected.register(
                original, number=1, until=("merged",),
            ))
            try:
                assert await asyncio.to_thread(started.wait, 3)
                callers = rig.h.document["callers"]
                if loss == "policy":
                    rig.h.document["callers"] = []
                    await rig.update()
                elif loss == "channel":
                    await rig.state.channels["service-a"].close()
                if loss == "cancel":
                    pending.cancel()
                    with pytest.raises(asyncio.CancelledError):
                        await pending
                    await eventually(lambda: rig.state.pending == 0)
                else:
                    with pytest.raises(ReplayClientError) as caught:
                        await pending
                    assert caught.value.outcome_unknown
                    assert caught.value.http_status == (403 if loss == "policy" else 502)
                    await eventually(lambda: not rig.connectors[-1].active)
                assert owner._tasks
                release.set()
                await eventually(lambda: not owner._tasks)
                if loss == "policy":
                    rig.h.document["callers"] = callers
                    await rig.update()
                elif loss == "channel":
                    await connect_handler(rig, owner.handle)
                inspected = await selected.inspect(original)
                if stage == "before-lookup":
                    assert inspected == ReplayOutcome("uncertain")
                else:
                    assert isinstance(inspected, ReplayReceipt) and inspected.status == "active"
                receipt = await selected.register(original, number=1, until=("merged",))
                assert await selected.register(original, number=1, until=("merged",)) == receipt
                if stage == "commit-started":
                    assert inspected == receipt
                assert len(json.loads(store.path.read_bytes())["records"]) == 1
            finally:
                release.set()
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                await owner.drain(timeout=3)
    asyncio.run(scenario())
