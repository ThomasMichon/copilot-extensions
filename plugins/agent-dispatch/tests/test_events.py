"""Tests for the coordinator EventBus."""

from __future__ import annotations

import asyncio

from agent_dispatch.events import EventBus, sse_format


def test_publish_before_loop_bound_is_noop():
    EventBus().publish({"type": "x"})  # no loop bound -> silently ignored


def test_fanout_to_subscriber():
    async def scenario():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        gen = bus.subscribe()
        fut = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)  # let subscribe() register its queue
        assert bus.subscriber_count == 1
        bus.publish({"type": "task.created", "task": {"id": "a"}})
        event = await asyncio.wait_for(fut, timeout=2)
        assert event["type"] == "task.created"
        await gen.aclose()
        assert bus.subscriber_count == 0

    asyncio.run(scenario())


def test_sse_format():
    frame = sse_format({"type": "task.claimed", "task": {"id": "z"}})
    assert frame.startswith("event: task.claimed\n")
    assert "data: " in frame
    assert frame.endswith("\n\n")
