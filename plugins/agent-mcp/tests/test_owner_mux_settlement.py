"""Exact headed-fixture cleanup across a natural session-exit race."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
import importlib
from pathlib import Path
import subprocess
from typing import cast

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "final_presence", [1, 0, 2], ids=["confirmed-absent", "still-present", "probe-error"],
)
async def test_failed_mux_kill_requires_subsequent_confirmed_absence(
    monkeypatch: pytest.MonkeyPatch, final_presence: int,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "native"))
    identity = "$1:123|%1"
    replies = deque([
        (0, ""), (0, identity), (1, ""), (final_presence, ""),
    ])
    calls: list[tuple[str, ...]] = []

    class FixtureMux:
        target = "fixture"

        def query(self, *arguments: str) -> subprocess.CompletedProcess[str]:
            calls.append(arguments)
            code, output = replies.popleft()
            return subprocess.CompletedProcess(
                arguments, code, stdout=output, stderr="fixture session settlement",
            )

    mux = FixtureMux()
    settle = cast(
        Callable[[FixtureMux, str | None], Awaitable[None]],
        importlib.import_module("test_owner_headed_windows")._settle_mux,
    )
    if final_presence == 1:
        await settle(mux, identity)
        assert calls[-1] == ("has-session", "-t", mux.target)
        assert not replies
    else:
        with pytest.raises(AssertionError):
            await settle(mux, identity)
    assert calls[:3] == [
        ("has-session", "-t", mux.target),
        ("display-message", "-p", "-t", mux.target, "#{session_id}:#{session_created}|#{pane_id}"),
        ("kill-session", "-t", mux.target),
    ]
