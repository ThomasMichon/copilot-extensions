"""Mock-only Darwin exit-reference parity at Bridge's retirement boundary."""

from __future__ import annotations

import errno
import select
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from zdd import darwin_identity, process_exit, routing

from agent_bridge.app import _retire_previous_daemon

TOKEN = "1780000000:123456"


@pytest.fixture
def native(monkeypatch):
    calls = []
    state = SimpleNamespace(birth=TOKEN, events=[], registration_error=None)

    def control(changes, count, timeout):
        calls.append(("control", changes, count, timeout))
        if changes:
            if state.registration_error:
                raise state.registration_error
            return []
        events, state.events = state.events, []
        return events

    def birth(pid):
        calls.append(("birth", pid))
        if state.birth is None:
            raise OSError("native birth is unavailable")
        return state.birth, False

    monkeypatch.setattr(process_exit, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(select, "kqueue", lambda: SimpleNamespace(
        control=control, close=lambda: calls.append(("close",)),
    ), raising=False)
    monkeypatch.setattr(select, "kevent", lambda ident, **kw: SimpleNamespace(
        ident=ident, **kw,
    ), raising=False)
    for name, value in {
        "KQ_FILTER_PROC": -5, "KQ_EV_ADD": 1, "KQ_EV_ONESHOT": 16,
        "KQ_NOTE_EXIT": 0x80000000, "KQ_EV_ERROR": 0x4000,
    }.items():
        monkeypatch.setattr(select, name, value, raising=False)
    monkeypatch.setattr(darwin_identity, "native_identity", birth)
    return state, calls


def _exit(**changes):
    return SimpleNamespace(**{
        "ident": 101, "filter": -5, "fflags": 0x80000000, "flags": 0, "data": 0,
        **changes,
    })


class _Client:
    def __init__(self, *, cold=False):
        self.drains = 0
        self.shutdowns = 0
        self.undrains = 0
        self.cold = cold

    def drain(self, **kwargs):
        assert kwargs["force"] is False
        self.drains += 1
        return {"drained": True, "mode": "incompatible-contract-cold" if self.cold else "warm"}

    def shutdown(self):
        self.shutdowns += 1
        return {"shutting_down": True}

    def undrain(self):
        self.undrains += 1


def _pair(tmp_path):
    previous = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=41001, pid=101, version="old",
    )
    successor = routing.publish_active(
        tmp_path, bind="127.0.0.1", port=41002, pid=202, version="new", demote_existing=True,
    )
    return replace(previous, process_start_time=TOKEN), successor


@pytest.mark.asyncio
async def test_darwin_normal_replacement_retires_without_self_retire(
    native, tmp_path, monkeypatch,
):
    _state, calls = native
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_BRIDGE_SELF_RETIRE", "0")
    previous, successor = _pair(tmp_path)
    client = _Client()
    await _retire_previous_daemon(
        SimpleNamespace(state=SimpleNamespace(auth_token="test")),
        previous, successor, make_client=lambda _: client,
    )
    assert (client.drains, client.shutdowns, client.undrains) == (1, 1, 0)
    assert calls[-1] == ("close",)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["reuse", "unknown", "denied", "unsupported"])
async def test_darwin_unpinned_identity_cannot_drain_or_shutdown(
    native, tmp_path, monkeypatch, failure,
):
    state, calls = native
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    previous, successor = _pair(tmp_path)
    if failure == "reuse":
        state.birth = "1780000001:0"
    elif failure == "unknown":
        state.birth = None
    elif failure == "denied":
        state.registration_error = PermissionError(errno.EPERM, "denied")
    else:
        monkeypatch.delattr(select, "kqueue")
    recover = AsyncMock()
    monkeypatch.setattr("agent_bridge.app._recover_owned_cold", recover)
    client = _Client()
    await _retire_previous_daemon(
        SimpleNamespace(state=SimpleNamespace(auth_token="test")),
        previous, successor, make_client=lambda _: client,
    )
    assert (client.drains, client.shutdowns, client.undrains) == (0, 0, 0)
    if failure == "reuse":
        recover.assert_awaited_once()
        assert recover.await_args.kwargs["predecessor_exited"] is True
    else:
        recover.assert_not_awaited()
    if failure != "unsupported":
        assert calls.count(("close",)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("exited", [False, True])
async def test_darwin_cold_recovery_requires_captured_native_exit(
    native, tmp_path, monkeypatch, exited,
):
    state, calls = native
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(tmp_path))
    previous, successor = _pair(tmp_path)
    client = _Client(cold=True)
    shutdown = client.shutdown

    def request_shutdown():
        result = shutdown()
        if exited:
            state.events = [_exit()]
        return result

    client.shutdown = request_shutdown
    recover = AsyncMock()
    monkeypatch.setattr("agent_bridge.app._recover_owned_cold", recover)
    await _retire_previous_daemon(
        SimpleNamespace(state=SimpleNamespace(auth_token="test")),
        previous, successor, make_client=lambda _: client, retirement_timeout=0,
    )
    assert (client.drains, client.shutdowns) == (1, 1)
    assert recover.await_count == int(exited)
    if exited:
        assert recover.await_args.kwargs["predecessor_exited"] is True
    assert calls[-1] == ("close",)
