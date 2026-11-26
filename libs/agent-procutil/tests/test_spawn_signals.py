from __future__ import annotations

import sys
from collections.abc import Callable
from types import FrameType, SimpleNamespace

import pytest

from agent_procutil import _spawn_signals as signals
from agent_procutil.owned_process import OwnedCleanupError

Handler = Callable[[int, FrameType | None], object]


def install_signals(
    monkeypatch: pytest.MonkeyPatch, events: list[int],
) -> dict[int, Handler]:
    def previous(signum: int, frame: FrameType | None) -> None:
        events.append(signum)

    handlers: dict[int, Handler] = {2: previous, 15: previous}

    def replace(signum: int, handler: Handler) -> None:
        handlers[signum] = handler

    monkeypatch.setattr(signals, "signal", SimpleNamespace(
        SIGINT=2, SIGTERM=15, SIG_BLOCK=0, SIG_SETMASK=2,
        getsignal=handlers.__getitem__, signal=replace,
        pthread_sigmask=lambda how, selected: set(),
    ))
    monkeypatch.setattr(signals, "sys", SimpleNamespace(
        platform="linux", exc_info=sys.exc_info,
    ))
    return handlers


def test_distinct_termination_handlers_are_restored_and_replayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[int] = []
    handlers = install_signals(monkeypatch, events)
    original = dict(handlers)
    guard = signals.SpawnSignals()
    handlers[2](2, None)
    handlers[15](15, None)
    handlers[2](2, None)
    assert events == []
    guard.finish()
    assert events == [2, 15]
    assert handlers == original


@pytest.mark.parametrize("interrupt_restore", [False, True])
def test_unconfirmed_cleanup_is_not_replaced_by_a_pending_interrupt(
    monkeypatch: pytest.MonkeyPatch, interrupt_restore: bool,
) -> None:
    events: list[int] = []
    handlers = install_signals(monkeypatch, events)

    def interrupted(signum: int, frame: FrameType | None) -> None:
        events.append(signum)
        raise KeyboardInterrupt

    handlers[2] = interrupted
    if interrupt_restore:
        def mask(how: int, selected: set[int]) -> set[int]:
            if how == 2:
                raise KeyboardInterrupt
            return set()

        monkeypatch.setattr(getattr(signals, "signal"), "pthread_sigmask", mask)
    original = dict(handlers)
    failure = OwnedCleanupError(None)
    with pytest.raises(OwnedCleanupError) as caught:
        with signals.defer_termination():
            handlers[2](2, None)
            raise failure
    assert caught.value is failure
    assert events == []
    assert handlers == original
