"""Receipt-aware requests retain their protocol floor across real transport retries."""

from __future__ import annotations

import argparse
import io
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.client_protocol_floor import protocol_floored_request
from agent_bridge.protocol import (
    HTTP_PROTOCOL_VERSION,
    LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION,
    REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION,
)
from agent_bridge.session_targeting_cli import _hold_protocol_floor


class _Response(io.BytesIO):
    status = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(json.dumps(payload).encode())


@pytest.mark.parametrize("cutover,minimum", [
    (False, HTTP_PROTOCOL_VERSION + 1),
    (True, HTTP_PROTOCOL_VERSION + 1),
    (True, 1),
])
def test_protocol_fence_rechecks_range_before_each_attempt(monkeypatch, cutover, minimum):
    base, replacement = "http://127.0.0.1:57585", "http://127.0.0.1:47000"
    client = BridgeClient(base, "test", connect_grace=2, reresolve=lambda: replacement)
    replaced = False
    posted = []

    def transport(req, timeout=None):
        nonlocal replaced
        if req.full_url.endswith("/health"):
            return _Response({
                "protocol_version": HTTP_PROTOCOL_VERSION + 2,
                "min_protocol_version": minimum if replaced or not cutover else 1,
            })
        posted.append(req.full_url)
        if cutover and not replaced:
            replaced = True
            raise urllib.error.URLError(ConnectionRefusedError("retired"))
        return _Response({"session_id": "created"})

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    monkeypatch.setattr("agent_bridge.client_protocol_floor.time.sleep", lambda _: None)
    request = protocol_floored_request(client, LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION)
    if minimum > HTTP_PROTOCOL_VERSION:
        with pytest.raises(BridgeClientError) as error:
            request("POST", "/api/v1/sessions", {"agent": "worker"})
        assert error.value.status == 426
        assert posted == ([base + "/api/v1/sessions"] if cutover else [])
    else:
        assert request("POST", "/api/v1/sessions", {}) == {"session_id": "created"}
        assert posted == [base + "/api/v1/sessions", replacement + "/api/v1/sessions"]


@pytest.mark.parametrize("cli_floor", [0, 20, LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION + 1])
@pytest.mark.parametrize("replacement", ["new-url", "same-url", "pinned"])
@pytest.mark.parametrize("refusal", ["connection-refused", "draining", "initializing"])
def test_reply_wait_reprobes_the_maximum_floor_before_retry(
    monkeypatch: pytest.MonkeyPatch, cli_floor: int, replacement: str, refusal: str,
) -> None:
    old_base = "http://127.0.0.1:57585"
    new_base = "http://127.0.0.1:47000" if replacement == "new-url" else old_base
    floor = max(LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION, cli_floor)
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
    if cli_floor < LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION:
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
        return _Response({"protocol_version": LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION
                          if len(healths) == 1 else LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION - 1})

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
            return _Response({"protocol_version": LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION})
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
            return _Response({"protocol_version": LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION})
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
    ("resolver", REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION),
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
        elif read == "detail":
            client.expand_live_result_ref("live-1", "opaque-ref")
        else:
            client.resolve_live_result_target("live-1")
    assert error.value.status == 426
    assert len(resources) == 1 and resources[0].startswith(old_base)


@pytest.mark.parametrize("read", ["snapshot", "detail", "resolver"])
@pytest.mark.parametrize("replacement_protocol", [6, 7])
def test_result_history_merging_retries_only_after_a_fresh_floor(
    monkeypatch: pytest.MonkeyPatch, read: str, replacement_protocol: int,
) -> None:
    client = BridgeClient("http://127.0.0.1:57585", "test", connect_grace=2)
    resources: list[str] = []
    healths: list[str] = []

    def transport(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        if req.full_url.endswith("/health"):
            healths.append(req.full_url)
            return _Response({"protocol_version": replacement_protocol if resources else 7})
        resources.append(req.full_url)
        if len(resources) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 503, "unavailable", {},
                io.BytesIO(json.dumps({"detail": "represented history is merging"}).encode()),
            )
        assert replacement_protocol == 7
        return _Response({"session_id": "live-1"})

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    monkeypatch.setattr("agent_bridge.client_protocol_floor.time.sleep", lambda _seconds: None)
    action = {
        "snapshot": lambda: client.get_live_result_snapshot("live-1"),
        "detail": lambda: client.expand_live_result_ref("live-1", "opaque"),
        "resolver": lambda: client.resolve_live_result_target("live-1"),
    }[read]
    if replacement_protocol < 7:
        with pytest.raises(BridgeClientError) as error:
            action()
        assert error.value.status == 426 and len(resources) == 1
    else:
        assert action()["session_id"] == "live-1"
        assert len(resources) == 2
    assert len(healths) == 3


@pytest.mark.parametrize("replacement", ["new-url", "same-url", "pinned"])
@pytest.mark.parametrize("command", ["client", "read", "result"])
def test_result_target_downgrade_cannot_turn_a_missing_endpoint_into_acp_fallback(
    monkeypatch: pytest.MonkeyPatch, replacement: str, command: str,
) -> None:
    from agent_bridge import __main__ as cli

    old_base = "http://127.0.0.1:57585"
    new_base = "http://127.0.0.1:47000" if replacement == "new-url" else old_base
    client = BridgeClient(
        old_base, "test", connect_grace=2,
        reresolve=None if replacement == "pinned" else lambda: new_base,
    )
    resources: list[str] = []
    healths: list[str] = []

    def transport(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        if req.full_url.endswith("/health"):
            healths.append(req.full_url)
            return _Response({"protocol_version": 7 if len(healths) == 1 else 6})
        resources.append(req.full_url)
        raise urllib.error.HTTPError(req.full_url, 404, "missing", {}, io.BytesIO(b"{}"))

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    monkeypatch.setattr(cli, "_get_client", lambda **_kwargs: client)
    monkeypatch.setattr(cli, "_caller_id_for", lambda _args: "peer")
    monkeypatch.setattr(cli, "_resolve_read_target", lambda *_args: pytest.fail("no ACP read fallback"))
    monkeypatch.setattr(client, "get_result_snapshot", lambda *_args, **_kwargs: pytest.fail("no ACP result fallback"))
    if command == "result":
        with pytest.raises(SystemExit) as error:
            cli._cmd_result(argparse.Namespace(session_ref="live-1", expand=None))
        assert error.value.code == 1
    else:
        with pytest.raises(BridgeClientError) as error:
            if command == "read":
                cli._cmd_read(argparse.Namespace(session_id="live-1"))
            else:
                client.resolve_live_result_target("live-1")
        assert error.value.status == 426
    assert resources == [] and len(healths) == 2


@pytest.mark.parametrize("method", ["GET", "HEAD", "POST"])
def test_history_merging_retry_is_bounded_and_never_repeats_a_post(
    monkeypatch: pytest.MonkeyPatch, method: str,
) -> None:
    client = BridgeClient("http://127.0.0.1:57585", "test", connect_grace=1)
    now = [0.0]
    resources: list[str] = []
    healths: list[str] = []

    def transport(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        if req.full_url.endswith("/health"):
            healths.append(req.full_url)
            return _Response({"protocol_version": 7})
        resources.append(req.full_url)
        raise urllib.error.HTTPError(
            req.full_url, 503, "unavailable", {},
            io.BytesIO(json.dumps({"detail": "represented history is merging"}).encode()),
        )

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    monkeypatch.setattr("agent_bridge.client_protocol_floor.time.monotonic", lambda: now[0])
    monkeypatch.setattr("agent_bridge.client_protocol_floor.time.sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    with pytest.raises(BridgeClientError) as error:
        protocol_floored_request(client, 7)(method, "/api/v1/live-sessions/live-1/result")
    assert error.value.status == 503
    assert len(resources) == (1 if method == "POST" else 2)
    assert len(healths) == (1 if method == "POST" else 3)
    assert now[0] <= 1


@pytest.mark.parametrize("replacement", ["new-url", "same-url", "pinned"])
@pytest.mark.parametrize("refusal", ["connection-refused", "draining", "initializing", "merging"])
@pytest.mark.parametrize("predecessor,successor", [(34, 33), (33, 34)])
def test_supported_result_retry_invalidates_cached_capabilities_before_discovery(
    monkeypatch: pytest.MonkeyPatch, replacement: str, refusal: str,
    predecessor: int, successor: int,
) -> None:
    old_base = "http://127.0.0.1:57585"
    new_base = "http://127.0.0.1:47000" if replacement == "new-url" else old_base

    def resolve() -> str:
        assert client._daemon_proto is None
        return new_base

    client = BridgeClient(
        old_base, "test", connect_grace=2,
        reresolve=None if replacement == "pinned" else resolve,
    )
    retired = False
    resources: list[str] = []
    healths: list[str] = []

    def transport(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        nonlocal retired
        if req.full_url.endswith("/health"):
            healths.append(req.full_url)
            return _Response({"protocol_version": successor if retired else predecessor})
        resources.append(req.full_url)
        if not retired:
            retired = True
            if refusal == "connection-refused":
                raise urllib.error.URLError(ConnectionRefusedError("retired"))
            raise urllib.error.HTTPError(
                req.full_url, 503, "unavailable", {},
                io.BytesIO(json.dumps({"detail": refusal}).encode()),
            )
        return _Response({"session_id": "live-1"})

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    monkeypatch.setattr("agent_bridge.client_protocol_floor.time.sleep", lambda _seconds: None)
    assert client.get_live_result_snapshot("live-1")["session_id"] == "live-1"
    assert client.daemon_supports(LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION) is (
        successor >= LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION
    )
    assert client.daemon_protocol()[0] == successor
    assert resources == [f"{base}/api/v1/live-sessions/live-1/result" for base in (old_base, new_base)]
    assert len(healths) == 4


@pytest.mark.parametrize("surface,floor", [
    ("receipt", LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION),
    ("wait", LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION),
    ("snapshot", REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION),
    ("detail", REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION),
    ("resolver", REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION),
    ("read", REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION),
    ("result", REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION),
])
@pytest.mark.parametrize("cached", ["unversioned", "previous"])
def test_cached_negative_capability_is_refreshed_before_rejecting_an_upgrade(
    monkeypatch: pytest.MonkeyPatch, surface: str, floor: int, cached: str,
) -> None:
    from agent_bridge import __main__ as cli

    client = BridgeClient("http://127.0.0.1:57585", "test")
    current = [0 if cached == "unversioned" else floor - 1]
    resources: list[str] = []
    healths: list[int] = []

    def transport(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        path = req.full_url.split("?")[0]
        if path.endswith("/health"):
            healths.append(current[0])
            return _Response({"protocol_version": current[0]})
        resources.append(path)
        assert current[0] >= floor
        if path.endswith("/result-target"):
            return _Response({"session_id": "live-1"})
        if path.endswith("/messages"):
            assert json.loads(req.data)["wait"] is True
            return _Response({"message_id": 7})
        return _Response({"message_id": 7, "identity": {"snapshot_session_id": "live-1"}})

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    monkeypatch.setattr(cli, "_get_client", lambda **_kwargs: client)
    monkeypatch.setattr(cli, "_caller_id_for", lambda _args: "peer")
    monkeypatch.setattr(cli, "_resolve_read_target", lambda *_args: pytest.fail("no ACP read fallback"))
    monkeypatch.setattr(client, "get_result_snapshot", lambda *_args, **_kwargs: pytest.fail("no ACP result fallback"))
    assert client.daemon_supports(floor) is False
    current[0] = floor
    actions = {
        "receipt": lambda: client.get_live_message_receipt("live-1", 7),
        "wait": lambda: client.send_live_message("live-1", sender="peer", body="continue",
                                               wait=True, wait_timeout=0),
        "snapshot": lambda: client.get_live_result_snapshot("live-1"),
        "detail": lambda: client.expand_live_result_ref("live-1", "opaque"),
        "resolver": lambda: client.resolve_live_result_target("live-1"),
        "read": lambda: cli._cmd_read(argparse.Namespace(session_id="live-1", json=True)),
        "result": lambda: cli._cmd_result(argparse.Namespace(
            session_ref="live-1", expand=None, position=None,
            max_items=None, max_text_chars=None, json=True,
        )),
    }
    actions[surface]()
    assert resources
    assert client.daemon_protocol()[0] == floor
    assert healths[0] < floor and all(value == floor for value in healths[1:])


@pytest.mark.parametrize("predecessor,successor", [(8, 7), (7, 8)])
def test_first_floor_probe_clears_preflight_cache_without_a_transport_retry(
    monkeypatch: pytest.MonkeyPatch, predecessor: int, successor: int,
) -> None:
    client = BridgeClient(
        "http://127.0.0.1:57585", "test",
        reresolve=lambda: pytest.fail("a successful first attempt must not retry discovery"),
    )
    healths: list[str] = []
    resources: list[str] = []

    def transport(req: urllib.request.Request, timeout: float | None = None) -> _Response:
        if req.full_url.endswith("/health"):
            healths.append(req.full_url)
            return _Response({"protocol_version": predecessor if len(healths) == 1 else successor})
        resources.append(req.full_url)
        assert client._daemon_proto is None
        return _Response({"session_id": "live-1"})

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", transport)
    assert client.get_live_result_snapshot("live-1")["session_id"] == "live-1"
    assert client.daemon_supports(8) is (successor >= 8)
    assert client.daemon_protocol()[0] == successor
    assert len(resources) == 1 and len(healths) == 3
