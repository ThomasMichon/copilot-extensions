"""The actual app paints and accepts cancellation without a Manager daemon."""

from __future__ import annotations

import asyncio
import io
import json
import socket
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from zdd import routing

from worktree_manager import engine_client, process_priority, resident_picker, warm_picker_contract
from worktree_manager.production_picker import project_config
from worktree_manager.production_picker.picker_tui import engine
from worktree_manager.production_picker.picker_tui.engine_dialogs import QuitConfirmScreen


class _TerminalInput(io.StringIO):
    def isatty(self) -> bool:
        return True


@contextmanager
def _blocked_endpoint() -> Iterator[tuple[int, list[socket.socket], socket.socket]]:
    stopped = threading.Event()
    accepted = threading.Event()
    connections: list[socket.socket] = []
    errors: list[OSError] = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(.05)
        port = listener.getsockname()[1]

        def serve() -> None:
            while not stopped.is_set():
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    continue
                except OSError as error:
                    if not stopped.is_set():
                        errors.append(error)
                    return
                connections.append(connection)
                accepted.set()

        worker = threading.Thread(target=serve)
        worker.start()
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1) as canary:
                canary.settimeout(.02)
                canary.sendall(b"fixture probe\n")
                assert accepted.wait(1)
                with pytest.raises(TimeoutError):
                    canary.recv(1)
                yield port, connections, canary
        finally:
            stopped.set()
            worker.join(timeout=2)
            for connection in connections:
                connection.close()
            assert not worker.is_alive()
            assert not errors


@pytest.mark.parametrize("live", [False, True])
def test_direct_entry_paints_and_cancels_with_a_blocked_private_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, live: bool,
) -> None:
    root = tmp_path / "manager"
    root.mkdir()
    monkeypatch.setattr(sys, "stdin", _TerminalInput())
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(root))
    monkeypatch.setenv("AGENT_WORKTREES_PICKER_MOCK", "1")
    monkeypatch.setattr(resident_picker, "_binding", None)
    monkeypatch.setattr(engine_client, "_INHERITED_ENGINE_COMMAND", None)
    monkeypatch.setattr(engine_client, "_ENGINE_CMD_OVERRIDE", None)
    monkeypatch.delenv(engine_client.ENGINE_CMD_ENV, raising=False)
    monkeypatch.setenv(engine_client.ENGINE_ARGV_ENV, json.dumps(["qualified-engine"]))
    monkeypatch.setattr(process_priority, "raise_current_process_priority", lambda: None)
    monkeypatch.setattr(process_priority, "restore_normal_process_priority", lambda: None)
    monkeypatch.setattr(warm_picker_contract, "invocation_context", lambda **_kwargs: {})
    payload = {
        "ok": True, "contract_version": 1, "runtime_root": str(root),
        "bootstrap": {
            "version": 1, "project": "sample", "should_switch_cwd": False,
            "cwd": None, "workspace": None, "default_live": live, "headless": False,
            "project_config": {
                "config_dir": str(root), "repo_dir": str(root),
                "machine": "fixture-host", "install_dir": str(root),
            },
        },
    }
    monkeypatch.setenv(resident_picker.BOOTSTRAP_ENV, json.dumps(payload))
    with _blocked_endpoint() as (port, connections, canary):
        routing.publish_active(root / "mux-daemon-routing", bind="127.0.0.1", port=port)
        (root / "mux-daemon.token").write_text("a" * 64, encoding="utf-8")
        painted: list[bool] = []
        entry_connections: list[int] = []

        async def exercise(app: engine.PickerApp) -> None:
            async with app.run_test(size=(100, 30)) as pilot:
                await pilot.pause()
                assert "<svg" in app.export_screenshot()
                assert len(app.screen.query(engine.PickerScreen)) == 1
                with pytest.raises(TimeoutError):
                    canary.recv(1)
                painted.append(True)
                await pilot.press("q")
                assert isinstance(app.screen, QuitConfirmScreen)
                await pilot.press("y")
                assert app.result is None

        def run_app(app: engine.PickerApp) -> None:
            entry_connections.append(len(connections))
            assert entry_connections == [1]
            asyncio.run(exercise(app))

        monkeypatch.setattr(engine.PickerApp, "run", run_app)
        try:
            assert resident_picker.main(["--project", "sample"]) == 0
            assert painted == [True]
            assert entry_connections == [1]
        finally:
            project_config.clear_caches(reset_binding=True)
