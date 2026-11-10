"""Client pagination and recovery diagnostics retain durable delivery meaning."""

from pathlib import Path
import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service import local_client
from agent_pull_requests_service.local_client import LocalClient, LocalClientError, LocalDeadlineExpired


@pytest.mark.parametrize("status", ["cancelled", "expired"])
def test_follow_drains_all_inactive_delivered_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str,
) -> None:
    acknowledged: list[str] = []
    def request(_self: LocalClient, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        if operation == "acknowledge":
            acknowledged.append(payload["event_id"])
            return {"status": "acknowledged"}
        position = len(acknowledged)
        return {
            "status": status, "more": position < 2,
            "events": [{"event_id": f"event-{position}", "sequence": position + 1}],
        }
    monkeypatch.setattr(LocalClient, "request", request)
    assert main([
        "follow", "--socket", str(tmp_path / "local.sock"), "--namespace", "test",
        "--target", "repository", "--registration-id", "original", "--limit", "1",
        "--timeout", "5", "--interval", "1", "--ack",
    ]) == 0
    assert acknowledged == ["event-0", "event-1", "event-2"]


def test_history_gap_cli_diagnostic_does_not_instruct_blind_identity_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    def request(_self: LocalClient, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        raise LocalClientError("repository_history_gap")
    monkeypatch.setattr(LocalClient, "request", request)
    assert main([
        "query", "--socket", str(tmp_path / "local.sock"),
        "--namespace", "test", "--target", "repository",
    ]) == 2
    error = capsys.readouterr().err
    assert "repository_history_gap" in error and "original identities retained" not in error


def test_follow_never_starts_ack_after_its_original_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock, calls = [100.0], []
    def request(_self: LocalClient, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(operation)
        clock[0] = 102
        return {"status": "active", "more": False,
                "events": [{"event_id": "original-event", "sequence": 1}]}
    monkeypatch.setattr(LocalClient, "request", request)
    monkeypatch.setattr(local_client, "time", SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda _seconds: None,
    ))
    assert main([
        "follow", "--socket", str(tmp_path / "local.sock"), "--namespace", "test",
        "--target", "repository", "--registration-id", "original", "--timeout", "1", "--ack",
    ]) == 0
    assert calls == ["events"]


@pytest.mark.skipif(sys.platform != "linux", reason="Linux local transport")
@pytest.mark.parametrize("phase", ["capabilities", "events", "acknowledge"])
def test_original_deadline_bounds_every_actual_local_http_phase(tmp_path: Path, phase: str) -> None:
    from agent_pull_requests.watch_local import LocalIngress
    from fleet_manager.gateway_service_handler import BoundedServiceResponse

    async def scenario() -> None:
        stop, release = asyncio.Event(), asyncio.Event()
        calls: list[str] = []
        async def handler(request: Any) -> Any:
            operation = request.target.rsplit("/", 1)[-1]
            calls.append(operation)
            if operation == phase:
                await release.wait()
            return BoundedServiceResponse(200, (), json.dumps({
                "protocol": local_client.PROTOCOL, "namespace": "test", "repository_key": "selected",
                "continuing": True, "storage": "repository_cache_version:2",
                "forge_writes": False, "task_emissions": False, "result": {},
            }).encode())
        ingress_type: Callable[..., Any] = LocalIngress
        ingress = ingress_type(
            path=tmp_path / "local.sock", namespace="test",
            peers={int(getattr(os, "getuid", lambda: -1)()): "reader"},
            handler=handler, operation_seconds=2,
        )
        owner = asyncio.create_task(ingress.run(stop))
        try:
            await asyncio.wait_for(ingress.connected.wait(), 2)
            client = LocalClient(ingress.path, "test", "repository")
            client.deadline = time.monotonic() + .15
            with pytest.raises(LocalDeadlineExpired):
                await asyncio.wait_for(asyncio.to_thread(client.request, phase, {}), 1)
            assert calls[-1] == phase
            with pytest.raises(LocalDeadlineExpired):
                client.request("acknowledge", {})
            assert calls[-1] == phase
        finally:
            release.set()
            stop.set()
            await asyncio.wait_for(owner, 3)
    asyncio.run(scenario())
