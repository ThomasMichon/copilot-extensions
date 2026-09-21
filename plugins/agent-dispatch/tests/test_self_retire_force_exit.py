"""Tests for the self-retire force-exit backstop (#3068).

A confirmed live incident: a superseded coordinator logged "self-retiring"
(requested a graceful uvicorn shutdown via ``server.should_exit = True``) and
then never actually exited -- an unreachable-but-alive zombie that
``has_live_local_coordinator()`` correctly refuses to call live, but that
nothing reaps. ``_force_exit_after_should_exit`` is the hard backstop: if the
process is still running ``deadline_s`` seconds after the graceful request, it
force-exits.
"""

from __future__ import annotations

import asyncio

from agent_dispatch.coordinator import (
    _SELF_RETIRE_FORCE_EXIT_DEFAULT_S,
    _force_exit_after_should_exit,
    _self_retire_force_exit_seconds,
)


def test_force_exit_fires_after_deadline_with_no_graceful_shutdown():
    calls: list[float] = []
    exits: list[int] = []

    async def fake_sleep(seconds: float) -> None:
        calls.append(seconds)

    def fake_force_exit(code: int) -> None:
        exits.append(code)

    asyncio.run(
        _force_exit_after_should_exit(
            deadline_s=5.0, my_gen=3, my_pid=4242,
            sleep=fake_sleep, force_exit=fake_force_exit,
        )
    )

    assert calls == [5.0]
    assert exits == [1]


def test_force_exit_seconds_defaults_when_unset(monkeypatch):
    monkeypatch.delenv("AGENT_DISPATCH_SELF_RETIRE_FORCE_EXIT_S", raising=False)
    assert _self_retire_force_exit_seconds() == _SELF_RETIRE_FORCE_EXIT_DEFAULT_S


def test_force_exit_seconds_honors_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE_FORCE_EXIT_S", "5")
    assert _self_retire_force_exit_seconds() == 5.0


def test_force_exit_seconds_floors_at_one_second(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE_FORCE_EXIT_S", "0")
    assert _self_retire_force_exit_seconds() == 1.0


def test_force_exit_seconds_falls_back_on_unparseable_env(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE_FORCE_EXIT_S", "not-a-number")
    assert _self_retire_force_exit_seconds() == _SELF_RETIRE_FORCE_EXIT_DEFAULT_S


def test_force_exit_does_not_fire_if_cancelled_before_deadline():
    """If the loop's own task is cancelled (e.g. the coordinator shuts down
    cleanly on its own before the backstop deadline), the backstop must not
    still force-exit -- cancellation must propagate, not be swallowed."""
    exits: list[int] = []

    def fake_force_exit(code: int) -> None:
        exits.append(code)

    async def scenario() -> None:
        task = asyncio.ensure_future(
            _force_exit_after_should_exit(
                deadline_s=5.0, my_gen=1, my_pid=1,
                sleep=asyncio.sleep, force_exit=fake_force_exit,
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert exits == []
