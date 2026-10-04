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


def test_ready_frame_opt_in_yielded_before_any_real_event():
    """Phase 3a: a caller that passes ``ready_frame`` sees it immediately
    once its queue is registered, before any published event -- closing the
    startup/reconnect subscription-gap race (registration happens the moment
    this async generator is first iterated)."""
    async def scenario():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        gen = bus.subscribe(ready_frame={"type": "ready"})
        first = await asyncio.wait_for(gen.__anext__(), timeout=2)
        assert first == {"type": "ready"}
        bus.publish({"type": "task.created", "task": {"id": "a"}})
        second = await asyncio.wait_for(gen.__anext__(), timeout=2)
        assert second["type"] == "task.created"
        await gen.aclose()

    asyncio.run(scenario())


def test_no_ready_frame_when_not_requested():
    """Every existing caller (``ready_frame=None``, the default) sees no
    behavior change at all -- the first item is still a real event."""
    async def scenario():
        bus = EventBus()
        bus.bind_loop(asyncio.get_running_loop())
        gen = bus.subscribe()
        fut = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)
        bus.publish({"type": "task.created", "task": {"id": "a"}})
        event = await asyncio.wait_for(fut, timeout=2)
        assert event["type"] == "task.created"
        await gen.aclose()

    asyncio.run(scenario())
