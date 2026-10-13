"""Receipt-aware requests retain their protocol floor across real transport retries."""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.protocol import (
    LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION,
    REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION,
)
from agent_bridge.session_targeting_cli import _hold_protocol_floor


class _Response(io.BytesIO):
    status = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(json.dumps(payload).encode())


@pytest.mark.parametrize("cli_floor", [0, 20, 35])
@pytest.mark.parametrize("replacement", ["new-url", "same-url", "pinned"])
@pytest.mark.parametrize("refusal", ["connection-refused", "draining", "initializing"])
def test_reply_wait_reprobes_the_maximum_floor_before_retry(
    monkeypatch: pytest.MonkeyPatch, cli_floor: int, replacement: str, refusal: str,
) -> None:
    old_base = "http://127.0.0.1:57585"
    new_base = "http://127.0.0.1:47000" if replacement == "new-url" else old_base
    floor = max(33, cli_floor)
    client = BridgeClient(
        old_base, "test", connect_grace=2,
        reresolve=None if replacement == "pinned" else lambda: new_base,
    )
    posted: list[str] = []
    retired = False

    def transport(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        nonlocal retired
        if req.full_url.endswith("/health"):
            return _Response({"protocol_version": floor - 1 if retired else floor,
                              "min_protocol_version": 0})
        posted.append(req.full_url)
        if not retired:
            retired = True
            if refusal == "connection-refused":
                raise urllib.error.URLError(ConnectionRefusedError("retired"))
            raise urllib.error.HTTPError(
                req.full_url, 503, "unavailable", {},
                io.BytesIO(json.dumps({"detail": refusal}).encode()),
            )
        if json.loads(req.data).get("wait"):
            pytest.fail("must not send a reply-wait POST below the negotiated floor")
        return _Response({"message_id": 7, "replied": False})

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    monkeypatch.setattr("agent_bridge.client_protocol_floor.time.sleep", lambda _seconds: None)
    if cli_floor:
        _hold_protocol_floor(client, cli_floor)
    original_request = client._request
    with pytest.raises(BridgeClientError, match=f"predates protocol {floor}") as error:
        client.send_live_message("live-1", sender="peer", body="update", wait=True, wait_timeout=0)
    assert error.value.status == 426
    assert posted == [f"{old_base}/api/v1/live-sessions/live-1/messages"]
    assert client._request == original_request
    assert client._connect_grace == 2
    if cli_floor < 33:
        result = client.send_live_message("live-1", sender="peer", body="legacy", wait=False)
        assert result["message_id"] == 7
        assert posted[-1] == f"{new_base}/api/v1/live-sessions/live-1/messages"
    else:
        with pytest.raises(BridgeClientError, match=f"predates protocol {cli_floor}"):
            client.send_live_message("live-1", sender="peer", body="legacy", wait=False)
        assert len(posted) == 1


def test_reply_wait_reprobes_before_the_first_post_after_cached_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = BridgeClient("http://127.0.0.1:57585", "test")
    healths: list[str] = []

    def transport(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        assert req.full_url.endswith("/health"), "must not send to the downgraded daemon"
        healths.append(req.full_url)
        return _Response({"protocol_version": 33 if len(healths) == 1 else 32})

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    with pytest.raises(BridgeClientError) as error:
        client.send_live_message("live-1", sender="peer", body="update", wait=True, wait_timeout=0)
    assert error.value.status == 426 and len(healths) == 2


@pytest.mark.parametrize("wait", [False, True])
def test_reply_wait_can_retry_a_supported_replacement_without_gating_legacy_sends(
    monkeypatch: pytest.MonkeyPatch, wait: bool,
) -> None:
    old_base, new_base = "http://127.0.0.1:57585", "http://127.0.0.1:47000"
    client = BridgeClient(old_base, "test", connect_grace=2, reresolve=lambda: new_base)
    posted: list[str] = []
    healths: list[str] = []

    def transport(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        if req.full_url.endswith("/health"):
            healths.append(req.full_url)
            return _Response({"protocol_version": 33})
        posted.append(req.full_url)
        payload = json.loads(req.data)
        assert bool(payload.get("wait")) is wait
        if req.full_url.startswith(old_base):
            raise urllib.error.URLError(ConnectionRefusedError("retired"))
        return _Response({"message_id": 7, "replied": False})

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    monkeypatch.setattr("agent_bridge.client_protocol_floor.time.sleep", lambda _seconds: None)
    result = client.send_live_message("live-1", sender="peer", body="update", wait=wait, wait_timeout=0)
    assert result["message_id"] == 7
    assert posted == [f"{base}/api/v1/live-sessions/live-1/messages" for base in (old_base, new_base)]
    assert len(healths) == (3 if wait else 0)


@pytest.mark.parametrize("failure", [
    urllib.error.URLError(TimeoutError("timed out")),
    urllib.error.URLError(BrokenPipeError("broken pipe")),
    ConnectionResetError("reset"),
])
def test_reply_wait_never_retries_an_ambiguously_delivered_post(
    monkeypatch: pytest.MonkeyPatch, failure: Exception,
) -> None:
    client = BridgeClient(
        "http://127.0.0.1:57585", "test", connect_grace=2,
        reresolve=lambda: "http://127.0.0.1:47000",
    )
    posted: list[str] = []

    def transport(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        if req.full_url.endswith("/health"):
            return _Response({"protocol_version": 33})
        posted.append(req.full_url)
        raise failure

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    with pytest.raises(BridgeConnectionError):
        client.send_live_message("live-1", sender="peer", body="update", wait=True, wait_timeout=0)
    assert posted == ["http://127.0.0.1:57585/api/v1/live-sessions/live-1/messages"]


@pytest.mark.parametrize("read,floor", [
    ("receipt", LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION),
    ("snapshot", REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION),
    ("detail", REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION),
])
def test_versioned_receipt_and_result_reads_reprobe_their_floor_after_cutover(
    monkeypatch: pytest.MonkeyPatch, read: str, floor: int,
) -> None:
    old_base, new_base = "http://127.0.0.1:57585", "http://127.0.0.1:47000"
    client = BridgeClient(old_base, "test", connect_grace=2, reresolve=lambda: new_base)
    resources: list[str] = []

    def transport(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        if req.full_url.endswith("/health"):
            return _Response({"protocol_version": floor - 1 if req.full_url.startswith(new_base) else floor})
        resources.append(req.full_url)
        if req.full_url.startswith(old_base):
            raise urllib.error.URLError(ConnectionRefusedError("retired"))
        pytest.fail("must not read the versioned resource below its protocol floor")

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    monkeypatch.setattr("agent_bridge.client_protocol_floor.time.sleep", lambda _seconds: None)
    with pytest.raises(BridgeClientError) as error:
        if read == "receipt":
            client.get_live_message_receipt("live-1", 7)
        elif read == "snapshot":
            client.get_live_result_snapshot("live-1", max_items=2)
        else:
            client.expand_live_result_ref("live-1", "opaque-ref")
    assert error.value.status == 426
    assert len(resources) == 1 and resources[0].startswith(old_base)
