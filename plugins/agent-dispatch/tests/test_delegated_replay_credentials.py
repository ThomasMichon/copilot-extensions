"""Actual watcher-to-Dispatch replay through renewable native forge credentials."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from agent_dispatch.delegated_replay_consumer import create_replay_consumer
from agent_pull_requests.watch_gateway import WatchScopeBinding
from agent_pull_requests.watch_observer import NativeObservationBinding
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_client import ReplayClientError
from agent_pull_requests.watch_replay_store import ReplayServiceIdentity, ReplayStore
from agent_pull_requests.watch_service import WatchService
from fleet_manager.gateway_server import build_server
from fleet_manager.gateway_service_caller import ServiceCaller

from tests._delegation_helpers import identity, task
from tests._helpers import RepoDefaultingQueue as TaskQueue

pytestmark = [
    pytest.mark.contract("agent_dispatch.delegated_replay.credentials"),
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
    pytest.mark.effect("network"),
]


@pytest.mark.timeout(180 if sys.platform == "win32" else 120)
def test_watch_service_preserves_dispatch_waits_across_native_credential_loss(
    tmp_path, monkeypatch, capsys, caplog,
):
    repository = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repository / "fleet-manager" / "tests"))
    monkeypatch.syspath_prepend(str(repository / "plugins" / "agent-pull-requests" / "tests"))
    from gateway_service_transport_fixtures import Rig, eventually
    from test_gateway_pr_credentials import ScopedProvider, configured, forge, responding, worker_policy
    from test_gateway_runtime import running as credential_gateway
    from watch_credential_transport_fixtures import native_reader

    denied = threading.Event()
    source = ScopedProvider(lambda material: None if denied.is_set() else material)
    scope = replace(
        forge("github"), api_base="https://api.github.com", repository="example/project",
    )
    credential_root = tmp_path / "credentials"
    credential_root.mkdir()
    fixture, parameters = configured(credential_root, scope)
    prefix, requests, snapshot = native_reader(tmp_path / "native", repository, scope)
    monkeypatch.setenv("GH_TOKEN", "ambient-credential-must-not-win")
    monkeypatch.setenv("GITHUB_TOKEN", "ambient-credential-must-not-win")

    def renew_fixture_access():
        access = credential_root / "typed-access.jwt"
        replacement = access.with_suffix(".pending")
        replacement.write_text(fixture.access("worker"), encoding="utf-8")
        replacement.replace(access)

    class WallClock:
        @property
        def value(self):
            return int(time.time())

        def __call__(self):
            return self.value

    @asynccontextmanager
    async def serving():
        rig = Rig(tmp_path / "serving")
        rig.h.clock = WallClock()
        try:
            await rig.start()
            yield rig
        finally:
            await rig.close()

    async def scenario(credential_command):
        store = ReplayStore(
            (tmp_path / "replay").resolve(), "fleet/service/installation",
            service_identity=ReplayServiceIdentity("fleet-a", "gateway-a", "service-a"),
        )
        store.provision()
        queue = TaskQueue(tmp_path / "tasks.db")
        task_ids = [task(queue), task(queue)]
        binding = {
            "fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
            "namespace": store.namespace, "principal": "caller-a", "target": "project",
            "consumer": "caller-a", "account": parameters["account"], "repo": scope.repository,
        }

        def records():
            return [json.loads(line) for line in requests.read_text().splitlines()] if requests.exists() else []

        async def cycle(consumer, predicate):
            result = None
            for _ in range(100):
                result = await consumer.cycle()
                if not result["blocked"] and predicate():
                    return result
                await asyncio.sleep(0.05)
            raise AssertionError((
                "native credential-backed replay did not settle", result,
                current_owner._last_cycle, current_owner.status(),
                len(source.calls), len(records()),
                [len(queue.list_run_waiter_wakes(task_id)) for task_id in task_ids],
                [queue.get_active_run_waiter(task_id) for task_id in task_ids],
            ))

        async with serving() as rig:
            async def service_access():
                return rig.access("service-a", 1)

            async def caller_access():
                return rig.access("caller-a", 3)

            def service():
                return WatchService(
                    store, fleet="fleet-a", gateway="gateway-a", slot="service-a",
                    bindings=(WatchScopeBinding(
                        "caller-a", "project",
                        ConsumerScope("caller-a", parameters["account"], scope.repository),
                    ),),
                    observations=(NativeObservationBinding(
                        parameters["account"], scope.repository, sys.executable,
                        prefix=prefix, credential_command=credential_command,
                    ),),
                    gateway_origin=rig.origin, gateway_ca=rig.ca,
                    access_token_callback=service_access,
                    proof_key=rig.h.keys[1].get_op_key("sign"),
                    authority=lambda: int(time.time()) + 300, checkpoint=lambda: None,
                    interval=0.1,
                )

            def consumer():
                return create_replay_consumer(
                    queue, ServiceCaller(
                        gateway_origin=rig.origin, gateway_ca=rig.ca,
                        access_token_callback=caller_access,
                        proof_key=rig.h.keys[3].get_op_key("sign"),
                    ),
                    route_prefix="/api", target="project", repo=scope.repository,
                    caller_scope="caller-a:" + parameters["account"], service_binding=binding,
                )

            owner = service()
            current_owner = owner
            await owner.start()
            try:
                await eventually(lambda: owner.status()["ready"], timeout=10)
                selected = consumer()
                original = {}
                for index, task_id in enumerate(task_ids):
                    spec = identity(queue, suffix=str(index))
                    issued = time.time()
                    spec.update(until=["merged"], replay={
                        "binding": selected.binding, "request_id": f"request-{index}",
                        "issued_at": issued, "deadline_at": issued + spec["timeout"],
                    })
                    for _ in range(100):
                        try:
                            await selected.prepare(
                                task_id, delegation=spec, worker_id="worker",
                                resume_worktree="host/wt", host="host", reason="waiting",
                            )
                            break
                        except ReplayClientError as error:
                            if error.http_status != 503 or error.outcome_unknown:
                                raise
                            assert queue.get_active_run_waiter(task_id) is None
                            await asyncio.sleep(0.05)
                    else:
                        raise AssertionError("watch service did not admit preparation")
                    original[task_id] = spec["replay"]
                await cycle(selected, lambda: all(
                    (queue.get_active_run_waiter(task_id) or {}).get("state") == "active"
                    for task_id in task_ids
                ))
                await eventually(
                    lambda: owner._last_cycle is not None and owner._last_cycle["observed"] == 1,
                    timeout=30,
                )
                assert all(not queue.list_run_waiter_wakes(task_id) for task_id in task_ids)
                registrations = {
                    task_id: queue.get_active_run_waiter(task_id)["delegation"]["registration_id"]
                    for task_id in task_ids
                }
            finally:
                await owner.close(timeout=10)

            before = len(records())
            assert before >= 4 and [r["credential"] for r in records()] == list(range(1, before + 1))
            assert len(source.calls) == before
            denied.set()
            renew_fixture_access()
            replacement = snapshot.with_suffix(".pending")
            replacement.write_text(json.dumps({
                "state": "closed", "merged": True, "head": {"sha": "head"}, "title": "Fixture",
            }), encoding="utf-8")
            replacement.replace(snapshot)
            successor = service()
            current_owner = successor
            await successor.start()
            try:
                await eventually(
                    lambda: successor._last_cycle is not None and successor._last_cycle["unavailable"] == 1,
                    timeout=30,
                )
                assert not successor.status()["ready"]
                assert len(source.calls) > before and len(records()) == before
                selected = consumer()
                await cycle(selected, lambda: all(
                    queue.get_active_run_waiter(task_id)["state"] == "active" for task_id in task_ids
                ))
                for task_id in task_ids:
                    waiter = queue.get_active_run_waiter(task_id)
                    assert waiter["delegation"]["replay"] == original[task_id]
                    assert waiter["delegation"]["registration_id"] == registrations[task_id]
                    assert not queue.list_run_waiter_wakes(task_id)
                denied.clear()
                renew_fixture_access()
                await eventually(
                    lambda: successor._last_cycle is not None and successor._last_cycle["observed"] == 1,
                    timeout=30,
                )
                await cycle(selected, lambda: all(
                    len(queue.list_run_waiter_wakes(task_id)) == 1 for task_id in task_ids
                ))
                await cycle(selected, lambda: not queue.list_replay_waiters(selected.binding))
                assert (await cycle(selected, lambda: True))["events"] == 0
                assert all(len(queue.list_run_waiter_wakes(task_id)) == 1 for task_id in task_ids)
                assert rig.backends[0].requests == []
            finally:
                denied.clear()
                await successor.close(timeout=10)
        assert len(records()) >= before + 4
        assert len({r["credential"] for r in records()}) == len(records())
        assert all(call == parameters for call in source.calls)
        persisted = repr([queue.list_run_waiter_wakes(task_id) for task_id in task_ids]).encode()
        persisted += b"".join(path.read_bytes() for path in store.root.rglob("*.json"))
        persisted += b"".join(path.read_bytes() for path in tmp_path.glob("tasks.db*") if path.is_file())
        assert b"synthetic-forge-secret-" not in persisted
        assert b"ambient-credential-must-not-win" not in persisted

    (tmp_path / "serving").mkdir()
    with credential_gateway(build_server(fixture.config, fixture.signer)) as gateway:
        policy = worker_policy(fixture, parameters, gateway.server_port)
        credential_command = (
            sys.executable, "-m", "fleet_manager", "credential", "--config", str(policy),
            "--source", parameters["source"], "--account", parameters["account"],
            "--forge", json.dumps(scope.to_dict()), "--timeout", "5",
        )
        with responding(fixture, gateway, source):
            asyncio.run(scenario(credential_command))
            assert all(item.delivery is None for item in gateway.gateway.broker._items.values())
    captured = capsys.readouterr()
    assert "synthetic-forge-secret-" not in captured.out + captured.err + caplog.text
