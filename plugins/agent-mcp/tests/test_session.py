"""Tests for :class:`agent_mcp.session.BridgeSession`."""

from __future__ import annotations

import asyncio
import sys
import time

import pytest

from agent_mcp.config import parse_config
from agent_mcp.session import BridgeSession
from agent_mcp.transports.base import Transport

SILENT_CHILD = "import sys\nfor _ in sys.stdin:\n    pass\n"


def _cfg(extra: dict | None = None):
    data = {
        "server": {"type": "stdio", "command": [sys.executable, "-c", SILENT_CHILD]},
        "auth": {"kind": "none"},
    }
    if extra:
        data.update(extra)
    return parse_config(data)


async def test_bridge_session_bounds_a_transport_start_that_never_returns(monkeypatch):
    """Regression test for aperture-labs#6673 (session.py side of the fix).

    A ``transport.start()`` that never returns (a hung/slow upstream spawn)
    previously left :meth:`BridgeSession.start` suspended forever -- with no
    global lock involved here, that "only" hangs the one caller (an
    ``agent-mcp bridge`` process, or one ``serve`` attach connection) rather
    than wedging every bridge caller the way the ``OneShotSession``/WarmPool
    gap did, but it's the same missing bound and deserves the same fix.
    """

    class _NeverStartsTransport(Transport):
        def __init__(self, cfg, injector) -> None:
            super().__init__(cfg, injector)
            self.closed = False

        async def start(self) -> None:
            await asyncio.Event().wait()  # never returns

        async def send(self, msg: dict) -> None:
            pass

        async def end_input(self) -> None:
            pass

        async def aclose(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "agent_mcp.session.build_transport",
        lambda cfg, injector: _NeverStartsTransport(cfg, injector),
    )

    cfg = _cfg({"timeout": 0.2})
    session = BridgeSession(cfg, lambda _msg: None)
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="did not start"):
        await session.start()
    elapsed = time.monotonic() - start
    assert elapsed < 5.0


async def test_bridge_session_starts_normally_within_timeout():
    """Sanity check: a real, fast-starting transport is unaffected."""
    cfg = _cfg({"timeout": 5.0})
    session = BridgeSession(cfg, lambda _msg: None)
    await session.start()
    try:
        assert session._transport is not None
    finally:
        await session.aclose()
