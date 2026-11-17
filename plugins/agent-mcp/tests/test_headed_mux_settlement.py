"""Keep native fixture cleanup strict without racing a completed pane exit."""

import importlib
from pathlib import Path
import subprocess

import pytest


class FixtureMux:
    target = "fixture"

    def __init__(self, replies: list[subprocess.CompletedProcess[str]]) -> None:
        self.replies = replies
        self.calls: list[tuple[str, ...]] = []

    def query(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return self.replies.pop(0)


def reply(code: int, output: str = "", error: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, output, error)


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [1, 0, 2], ids=["already-exited", "still-present", "unknown"])
async def test_failed_mux_kill_requires_fresh_absence(
    monkeypatch: pytest.MonkeyPatch, remaining: int,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "native"))
    owner = importlib.import_module("test_owner_headed_windows")
    mux = FixtureMux([
        reply(0), reply(0, "$1:1234|%2"), reply(1, error="kill failed"),
        reply(remaining, error="presence unknown"),
    ])
    if remaining == 1:
        await owner._settle_mux(mux, "$1:1234|%2")
    else:
        with pytest.raises(AssertionError, match="kill failed" if remaining == 0 else "presence unknown"):
            await owner._settle_mux(mux, "$1:1234|%2")
    assert not mux.replies
    assert mux.calls[-1] == ("has-session", "-t", "fixture")
    assert sum(call[0] == "kill-session" for call in mux.calls) == 1


@pytest.mark.asyncio
async def test_changed_mux_identity_never_reaches_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "native"))
    owner = importlib.import_module("test_owner_headed_windows")
    mux = FixtureMux([reply(0), reply(0, "$2:5678|%3")])
    with pytest.raises(AssertionError):
        await owner._settle_mux(mux, "$1:1234|%2")
    assert all(call[0] != "kill-session" for call in mux.calls)
