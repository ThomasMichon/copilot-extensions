"""Actual standalone service/client reviewer reads, without protected write owners."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING, Any

import pytest

from agent_pull_requests.watch_service import WatchService
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service.local_client import LocalClient
from agent_pull_requests_service.local_config import LocalHostConfig, load_service_config

if TYPE_CHECKING:
    from services.pr.tests.test_filtered_foreground import selected_config
    from services.pr.tests.test_local_repository import NATIVE, configuration
else:
    from test_filtered_foreground import selected_config
    from test_local_repository import NATIVE, configuration

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux private Unix ingress")


@pytest.mark.parametrize("selection", [None, 1, 4])
@pytest.mark.parametrize("filtered", [False, True])
def test_local_host_forwards_selected_concurrency_through_actual_service_and_observer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: int | None, filtered: bool,
) -> None:
    from agent_pull_requests_service import hosting

    path, value, _state, log = selected_config(tmp_path, sponsor=False) if filtered else configuration(tmp_path)
    if selection is not None:
        value["native_read_concurrency"] = selection
    path.write_text(json.dumps(value))
    selected = load_service_config(path)
    assert isinstance(selected, LocalHostConfig)
    original = WatchService
    calls: list[dict[str, Any]] = []

    class Captured(Exception):
        pass

    def create(*args: Any, **kwargs: Any) -> WatchService:
        calls.append(kwargs)
        service = original(*args, **kwargs)
        assert service.observer.native_read_concurrency == selection
        raise Captured

    monkeypatch.setattr(hosting, "WatchService", create)
    with pytest.raises(Captured):
        asyncio.run(hosting._serve(selected, hosting.LaunchScope(selected.operation_seconds), asyncio.Event()))
    assert len(calls) == 1
    if selection is None:
        assert "native_read_concurrency" not in calls[0]
    else:
        assert calls[0]["native_read_concurrency"] == selection
    assert not selected.root.exists() and not log.exists()


@pytest.mark.parametrize("selection", [None, True, False, 0, 5, -1, 1.0, "1"])
def test_local_host_refuses_invalid_concurrency_without_state_or_native_reads(
    tmp_path: Path, selection: Any,
) -> None:
    path, value, _state, log = configuration(tmp_path)
    value["native_read_concurrency"] = selection
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="native_read_concurrency"):
        load_service_config(path)
    assert not Path(value["root"]).exists() and not log.exists()


@pytest.mark.parametrize("owner", ["watch_service", "watch_observer"])
def test_selected_concurrency_refuses_prior_executing_core_but_omission_remains_supported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str,
) -> None:
    from agent_pull_requests import watch_observer, watch_service

    def prior_service(*, local_socket: object) -> None:
        pytest.fail("qualification constructed a service")

    def prior_observer(*, bindings: object) -> None:
        pytest.fail("qualification constructed an observer")

    if owner == "watch_service":
        monkeypatch.setattr(watch_service, "WatchService", prior_service)
    else:
        monkeypatch.setattr(watch_observer, "ReplayObserver", prior_observer)
    path, value, _state, log = configuration(tmp_path)
    legacy = load_service_config(path)
    assert isinstance(legacy, LocalHostConfig)
    value["native_read_concurrency"] = 1
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="core/SDK unsupported"):
        load_service_config(path)
    assert not legacy.root.exists() and not log.exists()


def test_filtered_profile_does_not_advertise_or_adopt_unfiltered_reviewer_activity(tmp_path: Path) -> None:
    from agent_pull_requests_service.local_config import core
    from agent_pull_requests.filtered_subscriptions import contract

    path, value, _state, log = selected_config(tmp_path, sponsor=False)
    selected = load_service_config(path)
    selected.provision()
    before = selected.store().path.read_bytes()
    assert "reviewer_actions" not in contract()
    value["reviewer_actions"] = True
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="core/SDK unsupported"):
        load_service_config(path)
    with pytest.raises(ValueError, match="core/SDK unsupported"):
        core(filtered=True, reviewer_actions=True)
    assert selected.store().path.read_bytes() == before and not log.exists()


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


def test_reviewer_selection_refuses_event_only_page_cursor_support(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_pull_requests import repository_subscriptions

    prior = repository_subscriptions.contract(reviewer_actions=True)
    capability = prior["reviewer_actions"]
    assert isinstance(capability, dict)
    capability.pop("page_cursor")
    monkeypatch.setattr(repository_subscriptions, "contract", lambda **_kwargs: prior)
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
    state.write_text(json.dumps({"head": "a" * 40, "members": [7, 8]}))
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

            async def first_cycle() -> None:
                while service._last_cycle is None:
                    for task in service._tasks:
                        if task.done():
                            task.result()
                            raise AssertionError("service owner exited before its initial observation")
                    await asyncio.sleep(.02)

            await asyncio.wait_for(first_cycle(), selected.operation_seconds)
            assert service._last_cycle is not None
            assert service._last_cycle["unavailable"] == service._last_cycle["unbound"] == 0
            result = await asyncio.to_thread(client.request, "next-actions", {"reviewer": "reviewer"})
            assert result["next_actions"], {"result": result, "status": service.status()}
            assert result["next_actions"][0]["action"] == "initial_verdict"
            calls, persisted = log.read_bytes(), selected.store().path.read_bytes()
            for _ in range(3):
                result = await asyncio.to_thread(client.request, "next-actions", {"reviewer": "reviewer"})
                assert result["next_actions"][0]["candidate"]["base"] == "b" * 40
            code = await asyncio.to_thread(main, [
                "next-actions", "--socket", str(selected.repositories.socket_path),
                "--namespace", "test", "--target", "repo-github", "--reviewer", "review-app[bot]",
                "--limit", "1",
            ])
            first = json.loads(capsys.readouterr().out)
            assert code == 0 and [row["pr"] for row in first["next_actions"]] == [7]
            assert first["more"] and isinstance(first["cursor"], str)
            code = await asyncio.to_thread(main, [
                "next-actions", "--socket", str(selected.repositories.socket_path),
                "--namespace", "test", "--target", "repo-github", "--reviewer", "review-app[bot]",
                "--limit", "1", "--after", "1", "--cursor", first["cursor"],
            ])
            second = json.loads(capsys.readouterr().out)
            assert code == 0 and [row["pr"] for row in second["next_actions"]] == [8]
            assert log.read_bytes() == calls and selected.store().path.read_bytes() == persisted
            assert not service.gateway.workspaces and not service.gateway.attention
        finally:
            await service.close(timeout=10)
        assert not service.status()["writer_held"] and not selected.repositories.socket_path.exists()

    asyncio.run(scenario())
