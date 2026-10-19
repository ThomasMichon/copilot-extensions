"""Private wire boundaries never stand in for SDK or database completion."""

import json
from dataclasses import replace

import pytest
from zdd.private_protocol import PrivateIPCError

from agent_bridge.session_host import acp_boundary as module
from agent_bridge.session_host.acp_boundary import AcpWireBoundary


def wire(**fields):
    return json.dumps({"jsonrpc": "2.0", **fields}, ensure_ascii=False).encode() + b"\n"


def test_fragmented_coalesced_bidirectional_requests_retain_id_namespaces():
    boundary = AcpWireBoundary()
    request = wire(id=7, method="session/prompt", params={"sessionId": "synthetic"})
    boundary.observe(request[:13], to_child=True)
    with pytest.raises(PrivateIPCError):
        boundary.capture(0, 0)
    boundary.observe(request[13:] + wire(method="session/cancel", params={}), to_child=True)
    boundary.observe(
        wire(id=7, method="session/request_permission", params={})
        + wire(id="7", method="elicitation/create", params={}),
        to_child=False,
    )
    boundary.observe(wire(id=7, result={"outcome": "allowed"}), to_child=True)
    with pytest.raises(PrivateIPCError):
        boundary.capture(2, 2)
    boundary.observe(wire(id="7", error={"code": -32601, "message": "unsupported"}), to_child=True)
    boundary.observe(wire(id=7, result={"stopReason": "end_turn"}), to_child=False)
    with pytest.raises(PrivateIPCError):
        boundary.capture(2, 3)
    stamp = boundary.capture(3, 3)
    assert stamp.next_client_request_id == 8
    assert stamp.client_messages == 4 and stamp.child_messages == 3
    boundary.require_unchanged(stamp, 3, 3)
    with pytest.raises(PrivateIPCError):
        boundary.require_unchanged(replace(stamp, next_client_request_id=0), 3, 3)


def test_later_idle_does_not_validate_capture_before_new_rpc():
    boundary = AcpWireBoundary()
    stamp = boundary.capture(0, 0)
    boundary.observe(wire(id=0, method="session/set_config_option"), to_child=True)
    boundary.observe(wire(id=0, result={}), to_child=False)
    assert boundary.capture(1, 1).next_client_request_id == 1
    with pytest.raises(PrivateIPCError):
        boundary.require_unchanged(stamp, 1, 1)


@pytest.mark.parametrize("raw", [
    b"not-json\n", b'{"jsonrpc":"2.0","method":"secret","method":"other"}\n',
    b'{"jsonrpc":"2.0","method":"m","params":{"value":NaN}}\n',
    '{"jsonrpc":"2.0","method":"m"}\n'.encode("utf-16"),
    b'{"jsonrpc":"2.0","method":"\xff"}\n',
    wire(id=True, method="m"), wire(id=None, method="m"),
    wire(id=1.0, method="m"), wire(id="", method="m"),
    wire(id=0, method="m", result={}), wire(method="m", params="secret"),
    wire(id=0, result={}), wire(id=0, result={}, error={}),
    wire(id=0, error={"code": True, "message": "secret"}),
    wire(id=0, method="m") * 2,
    wire(id=0, method="m", params={}) + wire(id="0", result={}),
    b'[]\n', b"\n",
])
def test_unsupported_wire_poison_is_permanent_bounded_and_redacted(raw, caplog):
    boundary = AcpWireBoundary()
    boundary.observe(raw, to_child=True)
    boundary.observe(wire(method="session/update"), to_child=False)
    for _ in range(2):
        with pytest.raises(PrivateIPCError):
            boundary.capture(0, 0)
    assert caplog.messages == ["Private ACP wire continuation boundary unavailable"]
    assert boundary._buffers == {True: b"", False: b""}
    assert boundary._pending == {True: {}, False: {}}


def test_pending_and_partial_capacity_fail_without_retaining_payload(monkeypatch):
    monkeypatch.setattr(module, "_MAX_PENDING", 1)
    boundary = AcpWireBoundary()
    boundary.observe(wire(id=0, method="m") + wire(id=1, method="m"), to_child=True)
    with pytest.raises(PrivateIPCError):
        boundary.capture(0, 0)
    monkeypatch.setattr(module, "MAX_MESSAGE_BYTES", 8)
    boundary = AcpWireBoundary()
    boundary.observe(b"12345678", to_child=True)
    boundary.observe(b"9", to_child=True)
    with pytest.raises(PrivateIPCError):
        boundary.capture(0, 0)
    assert not boundary._buffers[True]


@pytest.mark.parametrize("cursor,maximum", [(True, True), (-1, -1), (1, 0), (0, 1)])
def test_delivery_cursor_requires_actual_bounded_equal_integers(cursor, maximum):
    with pytest.raises(PrivateIPCError):
        AcpWireBoundary().capture(cursor, maximum)
