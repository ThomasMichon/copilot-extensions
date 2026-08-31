"""Windows accept-loop resilience for CPython issue #93821."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from agent_bridge.windows_proactor import _is_transient_accept_error


def test_transient_windows_accept_errors_are_classified():
    assert _is_transient_accept_error(SimpleNamespace(winerror=64)) is True
    assert _is_transient_accept_error(SimpleNamespace(winerror=995)) is True
    assert _is_transient_accept_error(
        SimpleNamespace(winerror=10048)
    ) is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Proactor only")
def test_proactor_closes_failed_connection_socket(monkeypatch):
    from unittest.mock import Mock

    import agent_bridge.windows_proactor as module
    from agent_bridge.windows_proactor import ResilientIocpProactor

    accepted = Mock()
    listener = Mock()
    listener.family = 2
    listener.fileno.return_value = 10
    overlapped = Mock()
    error = OSError(
        22,
        "The specified network name is no longer available",
        None,
        64,
        None,
    )
    overlapped.getresult.side_effect = error
    monkeypatch.setattr(
        module._overlapped, "Overlapped", lambda _null: overlapped
    )

    captured = {}
    proactor = object.__new__(ResilientIocpProactor)
    proactor._iocp = None
    proactor._loop = Mock()
    proactor._register_with_iocp = Mock()
    proactor._get_accept_socket = Mock(return_value=accepted)
    proactor._register = lambda _ov, _listener, finish: captured.setdefault(
        "finish", finish
    )
    monkeypatch.setattr(
        module.tasks,
        "ensure_future",
        lambda coro, **_kwargs: coro.close(),
    )

    proactor.accept(listener)
    with pytest.raises(ConnectionResetError):
        captured["finish"](None, None, overlapped)

    accepted.close.assert_called_once()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Proactor only")
def test_uvicorn_uses_resilient_loop_factory():
    import uvicorn

    from agent_bridge.windows_proactor import (
        ResilientProactorEventLoop,
        resilient_loop_factory,
    )

    config = uvicorn.Config(lambda _scope, _receive, _send: None)
    config.loop = resilient_loop_factory

    factory = config.get_loop_factory()
    assert factory is resilient_loop_factory
    loop = factory()
    try:
        assert isinstance(loop, ResilientProactorEventLoop)
    finally:
        loop.close()
