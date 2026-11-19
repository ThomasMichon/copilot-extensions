"""Actual admitted cache reads produce bounded, body-free diagnostics."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, cast

import pytest

from agent_pull_requests import repository_read_trace as trace
from agent_pull_requests.repository_contract import PROTOCOL
from agent_pull_requests.repository_gateway import RepositoryGatewayOwner, handle
from agent_pull_requests.repository_stream import LocalStreamResponse
from fleet_manager.local_service_handler import LocalCallerContext, LocalServiceRequest
from single_instance_lease import OperationLockedError
from test_repository_consumer_binding import call, gateway_for
from test_repository_reviewer import BASE, HEAD, observe, setup


def records(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(row.getMessage()) for row in caplog.records if row.name == trace.LOGGER]


@pytest.mark.parametrize("selection", ["actions", "empty", "stale", "failed"])
def test_admitted_read_traces_actual_candidates_empty_stale_and_failed_results(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, selection: str,
) -> None:
    caplog.set_level(logging.INFO, logger=trace.LOGGER)

    async def scenario() -> None:
        selected = setup(tmp_path)
        observe(selected, draft=selection == "empty", title="private title must not be logged")
        if selection == "stale":
            selected[0][0] += 2000
        gateway = gateway_for(selected)
        before = selected[1].path.read_bytes()
        payload = {"reviewer": "reviewer", **({"cursor": "0" * 64} if selection == "failed" else {})}
        try:
            response = await call(gateway, selected, "next-actions", {"request": payload})
            row, = records(caplog)
            assert row["target"] == "repository" and row["repository_key"] == selected[2].key
            assert row["reviewer"] == "reviewer" and row["view"] == "next-actions"
            if selection == "failed":
                assert response.status == 400
                assert row["outcome"] == "failed" and row["error"] == "read-unavailable"
                assert row["requested_cursor"] == "0" * 64
                assert row["projection_cursor"] is None and row["inventory_age_seconds"] is None
            else:
                page = json.loads(response.body)["result"]
                assert response.status == 200 and row["outcome"] == "projected"
                assert row["projection_cursor"] == page["cursor"]
                assert row["event_cursor"] == page["event_cursor"]
                assert row["inventory_age_seconds"] == (2000 if selection == "stale" else 0)
                if selection == "actions":
                    assert row["actions"] == [{
                        "pr": 7, "action": "initial_verdict", "reason": "author_create",
                        "candidate": {"head": HEAD, "base": BASE},
                    }]
                elif selection == "empty":
                    assert row["actions"] == row["unavailable"] == []
                else:
                    assert row["unavailable"] == [{
                        "pr": 7, "reason": "evidence-incomplete-or-stale",
                    }]
            assert selected[1].path.read_bytes() == before
            assert "private title" not in caplog.text
        finally:
            gateway.stop_admission()
            await gateway.drain(timeout=2)
    asyncio.run(scenario())


def test_transaction_lock_retries_emit_one_read_and_no_extra_cache_query(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.INFO, logger=trace.LOGGER)

    async def scenario() -> None:
        selected = setup(tmp_path)
        observe(selected)
        gateway = gateway_for(selected)
        original = selected[1]._review_transaction
        attempts = 0

        def delayed(*args: Any, **kwargs: Any) -> Any:
            nonlocal attempts
            attempts += 1
            if attempts <= 2:
                raise OperationLockedError("opaque lock details must not be logged")
            return original(*args, **kwargs)

        monkeypatch.setattr(selected[1], "_review_transaction", delayed)
        try:
            response = await call(gateway, selected, "next-actions", {"request": {"reviewer": "reviewer"}})
            assert response.status == 200 and attempts == 3
            assert len(records(caplog)) == 1 and "opaque lock details" not in caplog.text
        finally:
            gateway.stop_admission()
            await gateway.drain(timeout=2)
    asyncio.run(scenario())


def test_stream_validation_is_not_a_read_trace_but_each_existing_sample_is(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=trace.LOGGER)

    async def scenario() -> None:
        selected = setup(tmp_path)
        observe(selected)
        gateway = gateway_for(selected)
        try:
            registered = await call(gateway, selected, "subscribe")
            registration = json.loads(registered.body)["result"]["subscription"]["registration_id"]
            response = await handle(cast(RepositoryGatewayOwner, gateway), LocalServiceRequest(
                LocalCallerContext("test", "reader", 10000), "POST",
                "/v1/repositories/repository/stream", (("content-type", "application/json"),),
                json.dumps({
                    "protocol": PROTOCOL, "namespace": "test", "repository_key": selected[2].key,
                    "request": {"registration_id": registration, "reviewer": "reviewer", "seconds": 1},
                }).encode(),
            ))
            assert isinstance(response, LocalStreamResponse)
            row, = records(caplog)
            assert row["view"] == "stream" and row["action_count"] == 1
            assert registration not in caplog.text
            await response.chunks.aclose()
        finally:
            gateway.stop_admission()
            await gateway.drain(timeout=2)
    asyncio.run(scenario())


def test_diagnostics_cannot_replace_the_original_result_or_error(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    caplog.set_level(logging.INFO, logger=trace.LOGGER)
    marker = "opaque private failure details"

    def failed() -> dict[str, Any]:
        raise ValueError(marker)

    with pytest.raises(ValueError, match=marker):
        trace.read(failed, target="repository", repository_key="a" * 64, payload={"reviewer": "\ninvalid"})
    row, = records(caplog)
    assert row["reviewer"] is None and marker not in caplog.text
    caplog.clear()
    result: dict[str, Any] = {"frames": []}
    assert trace.read(
        lambda: result, target="repository", repository_key="a" * 64,
        payload={"reviewer": "reviewer"}, stream=True,
    ) is result
    assert records(caplog)[0]["error"] == "trace-summary-unavailable"

    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError(marker)

    monkeypatch.setattr(trace._LOG, "info", unavailable)
    assert trace.read(
        lambda: result, target="repository", repository_key="a" * 64, payload={},
    ) is result
    with pytest.raises(ValueError, match=marker):
        trace.read(failed, target="repository", repository_key="a" * 64, payload={})


def test_read_trace_caps_summaries_and_never_serializes_opaque_fields(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=trace.LOGGER)
    selected = setup(tmp_path)
    observe(selected)
    result = selected[-1].execute(
        selected[2].key, "consumer", "next-actions", {"reviewer": "reviewer"}, expires_at=10000,
    )
    marker = "opaque body header and credential details"
    result["inventory"]["error"] = marker
    result["identity"]["endpoint"] = marker
    result["next_actions"][0]["review"] = {"body": marker}
    result["unavailable"] = [{"pr": number, "reason": "review-dismissed"} for number in range(1, 41)]
    returned = trace.read(
        lambda: result, target="repository", repository_key=selected[2].key,
        payload={"reviewer": "reviewer", "authorization": marker, "body": marker},
    )
    assert returned is result
    row, = records(caplog)
    assert row["unavailable_count"] == 40 and len(row["unavailable"]) == 8 and row["truncated"]
    assert marker not in caplog.text and len(caplog.records[0].getMessage().encode()) < 8192
