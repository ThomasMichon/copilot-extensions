"""Actual PR operations through synthetic issuer, TLS, PoP and the SDK channel."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

import pytest
from fleet_manager.gateway_service_handler import RequestDisconnected
from single_instance_lease import OperationLock, OperationLockedError

from agent_pull_requests.watch_gateway import WatchGateway, WatchScopeBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore


def test_real_gateway_scopes_reconcile_restart_ack_and_policy_withdrawal(tmp_path, monkeypatch):
    # Reuse Fleet's transport fixtures, not a second authentication/test server.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import running
    from test_gateway_service_handler import connect_handler

    async def scenario():
        store = ReplayStore((tmp_path / "replay").resolve(), "fleet/service/installation")
        store.provision()
        bindings = (
            WatchScopeBinding("caller-a", "project",
                              ConsumerScope("coordinator-a", "account-a", "example/project")),
            WatchScopeBinding("caller-b", "project",
                              ConsumerScope("coordinator-b", "account-b", "example/project")),
        )

        def adapter():
            return WatchGateway(store, fleet="fleet-a", gateway="gateway-a",
                                slot="service-a", bindings=bindings)

        owner = adapter()
        async with running(tmp_path) as rig:
            rig.h.document["callers"].append({
                "principal": "caller-b", "key_source": "caller-a",
                "slots": ["service-a"], "methods": ["GET", "POST"],
            })
            await rig.update()
            await connect_handler(rig, owner.handler_binding().handle)

            async def send(operation, payload=None, *, caller="caller-a", expected=200):
                method = "GET" if operation == "capabilities" else "POST"
                path = "/api/v1/watch/project/" + operation
                headers = await rig.auth(path, method, subject=caller)
                headers["X-Identity"] = "caller-b"
                options = {"json": payload} if payload is not None else {}
                async with rig.session.request(
                    method, rig.origin + path, headers=headers, **options,
                ) as response:
                    assert response.status == expected
                    assert response.headers["Cache-Control"] == "no-store"
                    return await response.json()

            assert (await send("capabilities"))["protocol"] == "agent-pull-requests.watch/v1"
            original = {
                "request_id": "original", "issued_at": time.time(), "number": 1,
                "until": ["merged"], "deadline_at": time.time() - 1,
            }
            rejected = await send("register", dict(original, account="foreign"), expected=400)
            assert rejected == {"error": "invalid_watch_request"}
            before = store.path.read_bytes()
            assert await send(
                "register", dict(original, deadline_at=2**53 + 1), expected=400,
            ) == {"error": "invalid_watch_request"}
            assert store.path.read_bytes() == before
            assert await send("pending", caller="caller-b") == {"events": [], "more": False}
            # Deliberately discard the initial success and reconcile its original request.
            await send("register", original)
            receipt = await send("register", original)
            assert receipt["status"] == "active"
            inspect = {key: original[key] for key in ("request_id", "issued_at")}
            assert await send("inspect", inspect, caller="caller-b") == {"status": "uncertain"}
            assert await send("pending", caller="caller-b") == {"events": [], "more": False}
            pending = await send("pending")
            event = pending["events"][0]
            assert event["timed_out"] is True
            # Replace the adapter and TLS connector; only disk carries replay state.
            await owner.drain(timeout=3)
            rig.tasks[-1].cancel()
            await asyncio.gather(rig.tasks[-1], return_exceptions=True)
            owner = adapter()
            await connect_handler(rig, owner.handle)
            assert await send("inspect", inspect) == receipt
            assert await send("pending") == pending
            completion = dict(inspect, registration_id=receipt["registration_id"],
                              event_id=event["event_id"])
            assert await send("acknowledge", completion, caller="caller-b") == {
                "status": "uncertain",
            }
            ack = await send("acknowledge", completion)
            assert ack["status"] == "acknowledged"
            assert await send("acknowledge", completion) == ack
            assert await send("pending") == {"events": [], "more": False}
            assert rig.backends[0].requests == []
            before = store.path.read_bytes()
            rig.h.document["callers"] = []
            await rig.update()
            await send("register", dict(original, request_id="after-withdrawal"), expected=403)
            assert store.path.read_bytes() == before
            assert len(json.loads(before)["records"]) == 1
            await owner.drain(timeout=3)
    asyncio.run(scenario())


@pytest.mark.parametrize("loss", ["policy", "channel"])
@pytest.mark.parametrize("stage", ["before-lookup", "commit-started"])
def test_real_revocation_and_disconnect_preserve_uncertainty_and_original_identity(
    tmp_path, monkeypatch, loss, stage,
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import eventually, running
    from test_gateway_service_handler import connect_handler

    from agent_pull_requests import watch_replay_store

    async def scenario():
        store = ReplayStore((tmp_path / "replay").resolve(), "fleet/service/installation")
        store.provision()
        owner = WatchGateway(
            store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
            bindings=(WatchScopeBinding(
                "caller-a", "project", ConsumerScope("coordinator-a", "account-a", "example/project"),
            ),),
        )
        started, release = threading.Event(), threading.Event()
        received = []
        decode, write = watch_replay_store.json_object, watch_replay_store._atomic_write_json

        def hold():
            if not started.is_set():
                started.set()
                assert release.wait(10)

        def held_lookup(raw):
            hold()
            return decode(raw)

        def held_commit(path, state):
            hold()
            write(path, state)

        async def handle(request):
            received.append(request)
            return await owner.handle(request)

        async with running(tmp_path) as rig:
            await connect_handler(rig, handle)
            original = {
                "request_id": "original", "issued_at": time.time(), "number": 1, "until": ["merged"],
            }
            path = "/api/v1/watch/project/register"
            pending = asyncio.create_task(rig.session.post(
                rig.origin + path, headers=await rig.auth(path, "POST"), json=original,
            ))
            # Pause only inside the real ReplayStore's operation lock.
            monkeypatch.setattr(
                watch_replay_store, "json_object" if stage == "before-lookup" else "_atomic_write_json",
                held_lookup if stage == "before-lookup" else held_commit,
            )
            try:
                assert await asyncio.to_thread(started.wait, 3)
                callers = rig.h.document["callers"]
                if loss == "policy":
                    rig.h.document["callers"] = []
                    await rig.update()
                    # Policy publication does not wait for the remote disk effect.
                    assert rig.state.authority.status().revision == rig.h.document["manifest"]["revision"]
                else:
                    await rig.state.channels["service-a"].close()
                async with await pending as response:
                    assert response.status != 200
                    assert await response.json() == {"error": "outcome-unknown"}
                await eventually(lambda: not rig.connectors[-1].active)
                with pytest.raises(RequestDisconnected):
                    received[0].lifetime.check()
                assert owner._tasks
                with pytest.raises(OperationLockedError):
                    with OperationLock(store.root / "watch-replay.lock"):
                        pass
                release.set()
                await eventually(lambda: not owner._tasks)
                if loss == "policy":
                    rig.h.document["callers"] = callers
                    await rig.update()
                else:
                    await connect_handler(rig, handle)

                async def send(operation, payload):
                    selected = "/api/v1/watch/project/" + operation
                    async with rig.session.post(
                        rig.origin + selected, headers=await rig.auth(selected, "POST"), json=payload,
                    ) as response:
                        assert response.status == 200
                        return await response.json()

                inspect = {key: original[key] for key in ("request_id", "issued_at")}
                reconciled = await send("inspect", inspect)
                if stage == "before-lookup":
                    assert reconciled == {"status": "uncertain"}
                    assert json.loads(store.path.read_bytes())["records"] == []
                else:
                    assert reconciled["status"] == "active"
                receipt = await send("register", original)
                assert await send("register", original) == receipt
                if stage == "commit-started":
                    assert receipt == reconciled
                assert len(json.loads(store.path.read_bytes())["records"]) == 1
            finally:
                release.set()
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
                await owner.drain(timeout=3)
    asyncio.run(scenario())
