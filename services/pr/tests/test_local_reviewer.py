"""Actual standalone service/client reviewer reads, without protected write owners."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING

import pytest

from agent_pull_requests.watch_service import WatchService
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.local_client import LocalClient
from agent_pull_requests_service.local_config import LocalHostConfig, load_service_config

if TYPE_CHECKING:
    from services.pr.tests.test_local_repository import NATIVE, configuration
else:
    from test_local_repository import NATIVE, configuration

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux private Unix ingress")


def test_default_profile_retains_prior_replay_constructor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import watch_replay_store

    def prior_store(
        root: Path, namespace: str, *, repository_cache: bool, repository_continuing: bool,
    ) -> ReplayStore:
        return ReplayStore(
            root, namespace, repository_cache=repository_cache,
            repository_continuing=repository_continuing,
        )

    monkeypatch.setattr(watch_replay_store, "ReplayStore", prior_store)
    path, _value, _state, log = configuration(tmp_path)
    selected = load_service_config(path)
    assert isinstance(selected, LocalHostConfig) and not selected.reviewer_actions
    store = selected.store()
    assert not store.repository_activity and not store.root.exists() and not log.exists()


def test_reviewer_selection_refuses_prior_core_without_new_keyword_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import repository_subscriptions

    prior = repository_subscriptions.contract()
    monkeypatch.setattr(repository_subscriptions, "contract", lambda: prior)
    path, value, _state, log = configuration(tmp_path)
    value["reviewer_actions"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="core/SDK unsupported"):
        load_service_config(path)
    assert not Path(value["root"]).exists() and not log.exists()


def test_actual_local_profile_adopts_and_queries_without_query_triggered_provider_reads(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    path, value, state, log = configuration(tmp_path)
    config = load_service_config(path)
    config.provision()
    value["reviewer_actions"] = True
    value["interval"] = 300
    path.write_text(json.dumps(value))
    state.write_text(json.dumps({"head": "a" * 40}))
    script = Path(value["repository_cache"]["repositories"][0]["prefix"][1])
    script.write_text(NATIVE.replace(
        '"base_ref":"dev"',
        '"base_ref":"dev","base_sha":"' + "b" * 40 + '","author":"author",'
        '"reviews_complete":True,"updated_at":"1970-01-01T00:01:00Z"',
    ))
    selected = load_service_config(path)
    assert isinstance(selected, LocalHostConfig) and selected.reviewer_actions

    async def scenario() -> None:
        service = WatchService(
            selected.store(), fleet=None, gateway=None, slot=None,
            bindings=selected.bindings, observations=selected.observations,
            gateway_origin=None, access_token_callback=None, proof_key=None,
            authority=lambda: int(time.time()) + 30, checkpoint=lambda: None,
            interval=selected.interval, local_socket=selected.repositories.socket_path,
            local_peers=selected.repositories.peers,
        )
        await service.start()
        try:
            await asyncio.wait_for(service.connector.connected.wait(), 5)
            client = LocalClient(selected.repositories.socket_path, "test", "repo-github")
            for _ in range(100):
                result = await asyncio.to_thread(client.request, "next-actions", {"reviewer": "reviewer"})
                if result["next_actions"]:
                    break
                await asyncio.sleep(.02)
            assert result["next_actions"][0]["action"] == "initial_verdict"
            for _ in range(100):
                if service._last_cycle is not None:
                    break
                await asyncio.sleep(.02)
            assert service._last_cycle is not None
            calls, persisted = log.read_bytes(), selected.store().path.read_bytes()
            for _ in range(3):
                result = await asyncio.to_thread(client.request, "next-actions", {"reviewer": "reviewer"})
                assert result["next_actions"][0]["candidate"]["base"] == "b" * 40
            code = await asyncio.to_thread(main, [
                "next-actions", "--socket", str(selected.repositories.socket_path),
                "--namespace", "test", "--target", "repo-github", "--reviewer", "reviewer",
            ])
            assert code == 0 and json.loads(capsys.readouterr().out)["next_actions"]
            assert log.read_bytes() == calls and selected.store().path.read_bytes() == persisted
            assert not service.gateway.workspaces and not service.gateway.attention
        finally:
            await service.close(timeout=10)
        assert not service.status()["writer_held"] and not selected.repositories.socket_path.exists()

    asyncio.run(scenario())
