"""Enrollment cannot cross an already-admitted legacy handoff lifecycle."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agent_bridge.runtime_custody import RuntimeCustodyError
from test_native_turn_transport import TransportOwner, enroll
from test_native_turn_transport import transport_owner as transport_owner


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["hosting", "successor"])
@pytest.mark.parametrize("outcome", ["failure", "cancellation"])
async def test_native_turn_transport_enrollment_excluded_through_full_handoff(
    transport_owner: TransportOwner, monkeypatch: pytest.MonkeyPatch,
    boundary: str, outcome: str,
) -> None:
    manager, session, client, _, _, _ = transport_owner
    entered, release = asyncio.Event(), asyncio.Event()

    async def paused(*args: object, **kwargs: object) -> None:
        entered.set()
        await release.wait()
        raise RuntimeError("controlled handoff failure")

    if boundary == "hosting":
        monkeypatch.setattr("agent_bridge.hosting_policy.require_session_hosting", paused)
        manager.start_session = AsyncMock()
    else:
        manager.start_session = AsyncMock(side_effect=paused)
    pending = asyncio.create_task(manager.handoff_session("s", seed_text="original seed"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert session._handoff_in_progress
        with pytest.raises(RuntimeCustodyError, match="local process-owned"):
            await enroll(transport_owner)
        assert session._native_turn_transport is None and client._native_turn_transport is None
        if boundary == "hosting":
            manager.start_session.assert_not_awaited()
        if outcome == "cancellation":
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
        else:
            release.set()
            with pytest.raises(RuntimeError, match="controlled"):
                await pending
        assert not session._handoff_in_progress
        await enroll(transport_owner)
        assert session._native_turn_transport is client._native_turn_transport
    finally:
        release.set()
        if not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
