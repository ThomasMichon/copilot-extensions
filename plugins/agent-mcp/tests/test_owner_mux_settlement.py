"""Exact headed-fixture cleanup across a natural session-exit race."""

from __future__ import annotations

from collections import deque
from collections.abc import Awaitable, Callable
import importlib
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

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


@pytest.mark.asyncio
@pytest.mark.parametrize("original_pane", [True, False], ids=["owned-pane", "different-pane"])
async def test_startup_snapshot_is_redacted_and_requires_original_pane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, original_pane: bool,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "native"))
    module = importlib.import_module("generation_headed_transport")
    client = module.HeadedClient.__new__(module.HeadedClient)
    client.directory = tmp_path
    client.workspace = tmp_path / "workspace"
    client.environment = {"HOME": str(tmp_path / "home")}
    client.state_file = tmp_path / "headed-host.json"
    client.state_file.write_text(json.dumps({
        "state": "native_starting", "nonce": "synthetic-private-nonce",
        "child_pid": 12345, "session_id": "private-session",
    }), encoding="utf-8")
    client.identity = "$1:123|%1"
    client.owned = [(10, "host-birth"), (20, "native-birth")]
    client._native_started_at = time.monotonic() - 10
    calls: list[tuple[str, ...]] = []

    def query(*arguments: str) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if arguments[0] == "display-message":
            output = client.identity if original_pane else "$9:999|%9"
        else:
            output = (
                "Startup waiting\nsynthetic-private-nonce\n"
                + str(tmp_path / "workspace" / "private.txt")
                + "\nhttps://example.test/private\n"
            )
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    client.mux = SimpleNamespace(target="fixture", query=query)
    monkeypatch.setattr(module, "identity_state", lambda *_: "matching")
    snapshot = await client.startup_snapshot()
    assert snapshot["phase"] == "native_starting"
    assert snapshot["identity_states"] == ["matching", "matching"]
    serialized = json.dumps(snapshot)
    assert not {"nonce", "child_pid", "session_id"} & snapshot.keys()
    assert "synthetic-private-nonce" not in serialized
    assert str(tmp_path) not in serialized
    assert "https://example.test" not in serialized
    if original_pane:
        assert snapshot["pane_state"] == "captured"
        assert "Startup waiting" in snapshot["pane"]
        assert str(tmp_path) not in snapshot["pane"]
        assert [call[0] for call in calls] == ["display-message", "capture-pane"]
        assert calls[-1][-1] == "%1"
    else:
        assert snapshot["pane_state"] == "different-owner"
        assert [call[0] for call in calls] == ["display-message"]


@pytest.mark.asyncio
async def test_startup_diagnostics_do_not_mask_original_exception_or_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).parent / "native"))
    module = importlib.import_module("generation_headed_transport")
    settlement = importlib.import_module("test_owner_headed_windows")
    cleanup = AsyncMock()
    monkeypatch.setattr(settlement, "_settle_mux", cleanup)
    client = module.HeadedClient.__new__(module.HeadedClient)
    client.directory = tmp_path
    client.host = client.pump = None
    client.mux = SimpleNamespace()
    client.identity = None
    client.owned = []
    client.returncode = None
    client.stdout, client.stderr = module.asyncio.StreamReader(), module.asyncio.StreamReader()
    client.startup_snapshot = AsyncMock(side_effect=RuntimeError("diagnostic failed"))
    with pytest.raises(ValueError, match="original failure"):
        try:
            raise ValueError("original failure")
        finally:
            await client.stop()
    cleanup.assert_awaited_once_with(client.mux, client.identity)
