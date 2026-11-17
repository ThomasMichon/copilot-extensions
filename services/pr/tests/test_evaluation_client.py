"""Actual local comparison front door, without independent provider acquisition."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

from agent_pull_requests_service.__main__ import main
from agent_pull_requests_service import evaluation_client
from agent_pull_requests_service.evaluation_client import load_reference, read_comparison
from agent_pull_requests_service.local_client import LocalClient, LocalClientError

if TYPE_CHECKING:
    from services.pr.tests.test_feed_evaluation_example import fixtures, reference
else:
    from test_feed_evaluation_example import fixtures, reference


def input_file(tmp_path: Path, selected: Any, **changes: Any) -> Path:
    direct = reference(selected, **changes)
    path = tmp_path / "reference.json"
    path.write_text(json.dumps({
        "schema": evaluation_client.REFERENCE_SCHEMA, "version": 1,
        "identity": direct.identity.document(), "observed_at": direct.observed_at,
        "snapshot": direct.snapshot.native_snapshot,
    }))
    return path


@pytest.mark.skipif(sys.platform != "linux", reason="Linux private Unix ingress")
def test_actual_comparison_cli_reads_cache_without_provider_or_delivery_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    from agent_pull_requests.watch_local import LocalIngress
    from fleet_manager.gateway_service_handler import BoundedServiceResponse

    selected = fixtures.setup(tmp_path / "cache")
    fixtures.observe(selected)
    path = input_file(tmp_path, selected)
    monkeypatch.setattr(evaluation_client, "time", SimpleNamespace(
        time=lambda: 1000, monotonic=time.monotonic,
    ))
    before = selected[1].path.read_bytes()
    calls: list[str] = []

    async def scenario() -> None:
        stop = asyncio.Event()

        async def handler(request: Any) -> Any:
            operation = request.target.rsplit("/", 1)[-1]
            calls.append(operation)
            body: dict[str, Any] = {
                "protocol": "agent-pull-requests.repository-cache/v1",
                "namespace": "test", "repository_key": selected[2].key,
            }
            if operation == "capabilities":
                body.update(
                    continuing=True, storage="repository_cache_version:2",
                    forge_writes=False, task_emissions=False,
                    reviewer_actions={
                        "protocol": "agent-pull-requests.reviewer-actions/v1",
                        "storage": "repository_activity_version:1",
                        "effects": "none", "page_cursor": "projection-sha256",
                    },
                )
            else:
                envelope = json.loads(request.body)
                assert envelope["repository_key"] == selected[2].key
                assert operation in ("query", "next-actions")
                body["result"] = fixtures.request(
                    selected, operation, **envelope["request"],
                )
            return BoundedServiceResponse(200, (), json.dumps(body).encode())

        ingress: Any = LocalIngress(
            path=tmp_path / "local.sock", namespace="test",
            peers=[(int(getattr(os, "getuid", lambda: -1)()), "reader")],
            handler=handler, operation_seconds=5,
        )
        owner = asyncio.create_task(ingress.run(stop))
        try:
            await asyncio.wait_for(ingress.connected.wait(), 2)
            code = await asyncio.to_thread(main, [
                "compare", "--socket", str(ingress.path), "--namespace", "test",
                "--target", "repository", "--consumer", "consumer",
                "--reviewer", "reviewer", "--number", "7", "--reference", str(path),
            ])
            result = json.loads(capsys.readouterr().out)
            assert code == 0 and result["comparison"]["action"] == "agree"
            assert set(result["comparison"]["facts"].values()) == {"agree"}
            assert result["cache_cursor"] == result["action_event_cursor"]
            assert result["identity"] == selected[2].document()
            assert calls == ["capabilities", "query", "next-actions"]
            assert selected[1].path.read_bytes() == before
        finally:
            stop.set()
            await asyncio.wait_for(owner, 3)

    asyncio.run(scenario())


def test_mixed_cache_views_and_incomplete_first_page_remain_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = fixtures.setup(tmp_path / "cache")
    fixtures.observe(selected)
    direct = reference(selected)
    monkeypatch.setattr(evaluation_client, "time", SimpleNamespace(
        time=lambda: 1000, monotonic=lambda: 100,
    ))
    mode = ["moved"]

    def request(_self: LocalClient, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        value = fixtures.request(selected, operation, **payload)
        if operation == "next-actions":
            if mode[0] == "moved":
                value["event_cursor"] += 1
            else:
                value.update(next_actions=[], more=True)
        return cast("dict[str, Any]", value)

    monkeypatch.setattr(LocalClient, "request", request)
    client = LocalClient(tmp_path / "absent.sock", "test", "repository")
    for mode[0], reason in (("moved", "moved"), ("partial", "incomplete")):
        result = read_comparison(
            client, direct, consumer="consumer", reviewer="reviewer", number=7,
        )
        assert result["comparison"]["action"] == "unavailable"
        assert reason in result["comparison"]["reason"]
        assert client.deadline is None


def test_original_client_deadline_is_not_renewed_and_no_failure_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    selected = fixtures.setup(tmp_path / "cache")
    fixtures.observe(selected)
    client = LocalClient(tmp_path / "absent.sock", "test", "repository")
    client.deadline = 102
    monkeypatch.setattr(evaluation_client, "time", SimpleNamespace(
        time=lambda: 1000, monotonic=lambda: 100,
    ))
    calls: list[str] = []

    def request(self: LocalClient, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert self.deadline == 102
        calls.append(operation)
        if operation == "next-actions":
            raise LocalClientError("retain original selection")
        return cast("dict[str, Any]", fixtures.request(selected, operation, **payload))

    monkeypatch.setattr(LocalClient, "request", request)
    with pytest.raises(LocalClientError):
        read_comparison(
            client, reference(selected), consumer="consumer", reviewer="reviewer", number=7,
        )
    assert calls == ["query", "next-actions"] and client.deadline == 102


def test_reference_scope_and_malformed_file_refuse_before_local_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    selected = fixtures.setup(tmp_path / "cache")
    fixtures.observe(selected)
    path = input_file(tmp_path, selected)

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("invalid reference reached local transport")

    monkeypatch.setattr(LocalClient, "request", forbidden)
    direct = load_reference(path, 7)
    with pytest.raises(ValueError, match="snapshot"):
        load_reference(path, 8)
    with pytest.raises(ValueError, match="selection"):
        read_comparison(
            LocalClient(tmp_path / "absent.sock", "other", "repository"), direct,
            consumer="consumer", reviewer="reviewer", number=7,
        )
    with pytest.raises(ValueError, match="identity"):
        read_comparison(
            LocalClient(tmp_path / "absent.sock", "test", "repository"),
            replace(direct, identity=replace(direct.identity, repo="other/repo")),
            consumer="consumer", reviewer="reviewer", number=7,
        )
    for raw in ('{"secret-detail":', '{"version":1,"version":1}', " " * 1_048_577):
        path.write_text(raw)
        assert main([
            "compare", "--socket", str(tmp_path / "absent.sock"), "--namespace", "test",
            "--target", "repository", "--consumer", "consumer", "--reviewer", "reviewer",
            "--number", "7", "--reference", str(path),
        ]) == 2
        output = capsys.readouterr()
        assert not output.out and "secret-detail" not in output.err


def test_missing_comparison_core_does_not_break_existing_queries_or_fetch_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    selected = fixtures.setup(tmp_path / "cache")
    path = input_file(tmp_path, selected)
    calls: list[str] = []

    def request(_self: LocalClient, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        calls.append(operation)
        return {"result": "existing query"}

    monkeypatch.setattr(LocalClient, "request", request)
    monkeypatch.setitem(sys.modules, "agent_pull_requests_service.evaluation", None)
    assert main([
        "query", "--socket", str(tmp_path / "absent.sock"),
        "--namespace", "test", "--target", "repository",
    ]) == 0
    assert json.loads(capsys.readouterr().out) == {"result": "existing query"}
    assert main([
        "compare", "--socket", str(tmp_path / "absent.sock"), "--namespace", "test",
        "--target", "repository", "--consumer", "consumer", "--reviewer", "reviewer",
        "--number", "7", "--reference", str(path),
    ]) == 2
    assert not capsys.readouterr().out and calls == ["query"]


@pytest.mark.parametrize("field", ["endpoint", "namespace", "provider", "account", "repo"])
def test_nonstring_identity_refuses_without_traceback_or_local_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    field: str,
) -> None:
    selected = fixtures.setup(tmp_path / "cache")
    path = input_file(tmp_path, selected)
    value = json.loads(path.read_text())
    value["identity"][field] = 123
    path.write_text(json.dumps(value))

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("malformed identity reached local transport")

    monkeypatch.setattr(LocalClient, "request", forbidden)
    assert main([
        "compare", "--socket", str(tmp_path / "absent.sock"), "--namespace", "test",
        "--target", "repository", "--consumer", "consumer", "--reviewer", "reviewer",
        "--number", "7", "--reference", str(path),
    ]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert output.err == "local comparison unavailable; reference and original identities retained\n"
