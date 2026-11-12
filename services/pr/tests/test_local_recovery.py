"""V2 foreground recovery retains original IDs after state replacement uncertainty."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import threading
import time
import sys
from typing import TYPE_CHECKING, Any

import pytest

from agent_pull_requests import watch_replay_store
from agent_pull_requests.watch_storage import StateCommitUncertain, _atomic_write_json
from agent_pull_requests_service import hosting
from agent_pull_requests_service.local_client import LocalClient, LocalClientError
from agent_pull_requests_service.local_config import LocalHostConfig, load_service_config

if TYPE_CHECKING:
    from services.pr.tests.test_local_repository import configuration
else:
    from test_local_repository import configuration


@pytest.mark.skipif(sys.platform != "linux", reason="Linux local foreground")
@pytest.mark.parametrize("phase", ["subscription", "projection"])
def test_local_foreground_recovers_original_ids_after_replacement_uncertainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    path, _value, state, _log = configuration(tmp_path)
    config = load_service_config(path)
    assert isinstance(config, LocalHostConfig)
    config.provision()
    fired = threading.Event()
    captured: dict[str, Any] = {}
    target = config.bindings[0].scope.account
    def replace(destination: Path, document: dict[str, Any]) -> None:
        cache = document["repository_cache"]
        registrations = [row for row in cache["registrations"] if row["request_id"] == "uncertain"]
        events = [row for row in cache["events"] if row["target"] == target
                  and (row["facts"] or {}).get("head_sha") == "after"]
        interrupt = not fired.is_set() and bool(registrations if phase == "subscription" else events)
        if interrupt:
            fired.set()
        _atomic_write_json(destination, document)
        if interrupt:
            if phase == "subscription":
                captured.update(registration=dict(registrations[0]))
            else:
                captured.update(event_ids=[row["event_id"] for row in events])
            raise StateCommitUncertain("synthetic post-replace interruption")
    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", replace)

    async def request(client: LocalClient, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(client.request, operation, payload)

    async def scenario() -> None:
        owners: list[asyncio.Task[None]] = []
        stops: list[asyncio.Event] = []
        async def start() -> LocalClient:
            stop = asyncio.Event()
            owner = asyncio.create_task(hosting.run(config, stop=stop))
            owners.append(owner)
            stops.append(stop)
            client = LocalClient(config.repositories.socket_path, "test", "repo-github")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if owner.done():
                    await owner
                    raise AssertionError("foreground exited before cache admission")
                try:
                    value = await request(client, "query", {})
                    if value["pulls"] and value["pulls"][0]["facts"] is not None:
                        return client
                except LocalClientError:
                    pass
                await asyncio.sleep(.02)
            raise AssertionError("foreground cache did not populate")
        try:
            client = await start()
            intent = {"request_id": "uncertain", "ttl": 3600}
            if phase == "subscription":
                with pytest.raises(LocalClientError):
                    await request(client, "subscribe", intent)
                assert fired.is_set()
                reconciled = (await request(client, "subscribe", intent))["subscription"]
                assert reconciled["registration_id"] == captured["registration"]["registration_id"]
                assert reconciled["expires_at"] == captured["registration"]["expires_at"]
                stops[0].set()
                await asyncio.wait_for(owners[0], 10)
                client = await start()
                restored = (await request(client, "subscribe", intent))["subscription"]
                assert restored["registration_id"] == reconciled["registration_id"]
                assert restored["expires_at"] == reconciled["expires_at"]
            else:
                subscription = (await request(client, "subscribe", intent))["subscription"]
                replacement = state.with_suffix(".next")
                replacement.write_text(json.dumps({"head": "after"}))
                replacement.replace(state)
                assert await asyncio.to_thread(fired.wait, 10)
                with pytest.raises(StateCommitUncertain):
                    await asyncio.wait_for(owners[0], 10)
                client = await start()
                current = await request(client, "query", {})
                assert current["identity"]["namespace"] == "test"
                assert current["pulls"][0]["facts"]["head_sha"] == "after"
                registration = {"registration_id": subscription["registration_id"]}
                pending = await request(client, "events", registration)
                assert [row["event_id"] for row in pending["events"]] == captured["event_ids"]
                observed = current["pulls"][0]["observed_at"]
                deadline = time.monotonic() + 10
                while True:
                    refreshed = await request(client, "query", {})
                    if (
                        refreshed["inventory"]["observed_at"] > observed
                        and all(pull["observed_at"] == refreshed["inventory"]["observed_at"]
                                for pull in refreshed["pulls"])
                    ):
                        break
                    assert time.monotonic() < deadline, "restart snapshots did not reconcile"
                    await asyncio.sleep(.02)
                assert (await request(client, "events", registration))["events"] == pending["events"]
                await request(client, "acknowledge", registration | {
                    "event_id": pending["events"][-1]["event_id"],
                })
                assert not (await request(client, "events", registration))["events"]
        finally:
            for stop in stops:
                stop.set()
            if owners:
                await asyncio.wait_for(asyncio.gather(*owners, return_exceptions=True), 10)
        assert not config.repositories.socket_path.exists()
    asyncio.run(scenario())
