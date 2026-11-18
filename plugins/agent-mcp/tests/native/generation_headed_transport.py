"""Use the existing private headed receiver as the physical MCP test transport."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import importlib
from itertools import islice
import json
import os
from pathlib import Path
import re
import subprocess
import time
from typing import TYPE_CHECKING, Any, Protocol, cast
from uuid import uuid4

from agent_procutil._daemon_instance_identity import birth_identity, identity_state

if TYPE_CHECKING:
    import pytest
    from agent_remote_driver.session_host.client import SessionHostClient


class _FixtureMux(Protocol):
    target: str

    def query(self, *arguments: str) -> subprocess.CompletedProcess[str]: ...

    def capture_original_pane(self, identity: str) -> subprocess.CompletedProcess[str]: ...


def _redact_startup(text: str, directory: Path, nonce: object) -> str:
    if len(text) > 8192:
        text = text[:8192].rsplit("\n", 1)[0] if "\n" in text[:8192] else ""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    if isinstance(nonce, str) and nonce:
        text = text.replace(nonce, "<redacted>")
    text = text.replace(str(directory), "<fixture>").replace(directory.as_posix(), "<fixture>")
    text = re.sub(r"https?://\S+", "<url>", text)
    text = re.sub(r"""[A-Za-z]:[\\/][^\s"'<>]+""", "<path>", text)
    text = re.sub(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "<email>", text)
    text = re.sub(r"\b[A-Za-z0-9_-]{32,}\b", "<identifier>", text)
    return "\n".join(
        line for line in text.splitlines()
        if not re.search(r"\b(?:token|secret|password|api[-_ ]?key|nonce)\b", line, re.I)
    )[-4096:]


class HeadedClient:
    """Adapt the real Session Host frames to the existing ACP fixture reader."""

    def __init__(
        self, directory: Path, workspace: Path, environment: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        driver_tests = Path(__file__).resolve().parents[3] / "agent-remote-driver" / "tests"
        monkeypatch.syspath_prepend(str(driver_tests))
        fixture_mux = cast(
            Callable[[str, Path, dict[str, str]], _FixtureMux],
            importlib.import_module("headed_mux_fixture").FixtureMux,
        )

        binary = os.environ.get("PSMUX_TEST_BIN")
        assert binary and Path(binary).is_file()
        self.directory, self.workspace, self.environment = directory, workspace, environment
        self.state_file = directory / "headed-host.json"
        self.session_id = str(uuid4())
        self.mux = fixture_mux(binary, Path(environment["HOME"]), {
            **environment, "PSMUX_DATA_DIR": str(directory / "private-mux"),
            "PSMUX_CONFIG_FILE": os.devnull, "PSMUX_NO_WARM": "1",
        })
        self.identity: str | None = None
        self.pid: int | None = None
        self.returncode: int | None = None
        self.state: dict[str, Any] = {}
        self.host: SessionHostClient | None = None
        self.pump: asyncio.Task[None] | None = None
        self._native_started_at: float | None = None
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdin = self
        self.buffer = bytearray()
        self.owned: list[tuple[int, str]] = []

    async def query(self, *args: str) -> subprocess.CompletedProcess[str]:
        return await asyncio.to_thread(self.mux.query, *args)

    async def state_when(
        self, predicate: Callable[[dict[str, Any]], object],
    ) -> dict[str, Any]:
        async def observe() -> dict[str, Any]:
            while True:
                if self.state_file.is_file():
                    self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
                    if predicate(self.state):
                        return self.state
                await asyncio.sleep(.02)
        return await asyncio.wait_for(observe(), 30)

    async def start(self, executable: Path, configuration: dict[str, Any]) -> dict[str, int | str]:
        from agent_mcp.owner_headed_bundle import prepare_headed_command

        home = Path(self.environment["COPILOT_HOME"])
        home.mkdir()
        (home / "settings.json").write_text(json.dumps({"screenReader": True}), encoding="utf-8")
        (home / "config.json").write_text(
            json.dumps({"trustedFolders": [str(self.workspace)]}), encoding="utf-8",
        )
        entries = self.directory / "headed-mcp.json"
        entries.write_text(json.dumps(configuration), encoding="utf-8")
        command = prepare_headed_command(
            [
                str(executable), "--session-id", self.session_id, "--screen-reader",
                "--no-auto-update", "--disable-builtin-mcps", "--no-custom-instructions",
                "--no-color", "--no-ask-user", "--allow-tool=physical-fixture(fixture_0)",
                "--log-dir", str(self.directory / "logs"),
            ],
            configuration_path=entries, names=["physical-fixture"],
            session_id=self.session_id, attempt_directory=self.directory / "headed-attempt",
            state_file=self.state_file, cwd=str(self.workspace),
        )
        assert (await self.query("has-session", "-t", self.mux.target)).returncode == 1
        launched = await self.query(
            "new-session", "-d", "-s", self.mux.target, "-c", str(self.workspace), "--", *command,
        )
        assert launched.returncode == 0, launched.stderr
        observed = await self.query(
            "display-message", "-p", "-t", self.mux.target,
            "#{session_id}:#{session_created}|#{pane_id}",
        )
        assert observed.returncode == 0 and "|" in observed.stdout
        self.identity = observed.stdout.strip()
        state = await self.state_when(lambda value: value.get("child_pid"))
        self.pid = state["host_pid"]
        self.owned = [
            (state["host_pid"], "windows:" + state["host_process_start_time"]),
            (state["child_pid"], "windows:" + state["child_process_start_time"]),
        ]
        assert self.pid != state["child_pid"]
        assert all(identity_state(*pair) == "matching" for pair in self.owned)
        self._native_started_at = time.monotonic()
        return {"pid": state["child_pid"], "birth": self.owned[1][1]}

    async def connect(self) -> None:
        from agent_remote_driver.session_host.client import SessionHostClient

        state = await self.state_when(lambda value: value["state"] == "running")
        extension = state["extension_pid"]
        self.owned.append((extension, birth_identity(extension)))
        assert all(identity_state(*pair) == "matching" for pair in self.owned)
        self.host = await SessionHostClient.connect(port=state["port"])
        hello = await self.host.attach(nonce=state["nonce"].encode())
        assert hello.child_pid == state["child_pid"]
        self.pump = asyncio.create_task(self._frames())

    async def _frames(self) -> None:
        assert self.host is not None
        try:
            async for cursor, frame in self.host.frames():
                self.stdout.feed_data(frame if frame.endswith(b"\n") else frame + b"\n")
                await self.host.ack(cursor)
        finally:
            self.stdout.feed_eof()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        assert self.host is not None
        data = bytes(self.buffer)
        self.buffer.clear()
        await self.host.write(data)

    async def completed_text(self, marker: str) -> str:
        events = (
            Path(self.environment["COPILOT_HOME"]) / "session-state"
            / self.session_id / "events.jsonl"
        )
        async def observe() -> str:
            while True:
                complete = events.read_text(encoding="utf-8").split("\n")[:-1] if events.is_file() else []
                matches: list[str] = [
                    event["data"]["content"] for event in map(json.loads, complete)
                    if event["type"] == "assistant.message"
                    and marker in event.get("data", {}).get("content", "")
                ]
                if matches:
                    assert len(matches) == 1
                    return matches[0]
                await asyncio.sleep(.02)
        return await asyncio.wait_for(observe(), 5)

    def assert_conversation(self, nonces: list[str]) -> None:
        sessions = Path(self.environment["COPILOT_HOME"]) / "session-state"
        journals = list(sessions.glob("*/events.jsonl"))
        assert [path.parent.name for path in journals] == [self.session_id]
        events = map(json.loads, journals[0].read_text(encoding="utf-8").splitlines())
        prompts = [event["data"]["content"] for event in events if event["type"] == "user.message"]
        assert prompts == ["Run the fixture tool with nonce " + nonce for nonce in nonces]

    async def startup_snapshot(self) -> dict[str, object]:
        state: dict[str, object] = {}
        try:
            with self.state_file.open("rb") as stream:
                raw = stream.read(65537)
            if len(raw) <= 65536:
                value = json.loads(raw)
                if isinstance(value, dict):
                    state = value
        except (OSError, ValueError):
            pass
        phase = state.get("state")
        snapshot: dict[str, object] = {
            "phase": phase if phase in (
                "native_starting", "running", "control_unavailable", "child_exited",
            ) else "unavailable",
            "identity_states": [identity_state(*pair) for pair in self.owned[:3]],
            "context_file_present": (self.directory / "caller-context.json").is_file(),
            "receipt_elapsed_seconds": (
                time.monotonic() - self._native_started_at
                if self._native_started_at is not None else None
            ),
        }
        exit_code = state.get("child_exit_code")
        if type(exit_code) is int:
            snapshot["child_exit_code"] = exit_code
        logs = self.directory / "logs"
        snapshot["log_directory_present"] = logs.is_dir()
        if logs.is_dir():
            observed = len(list(islice(logs.iterdir(), 33)))
            snapshot["log_entries_observed"] = observed
            snapshot["log_entries_truncated"] = observed == 33
        if self.identity is None:
            snapshot["pane_state"] = "unbound"
            return snapshot
        try:
            pane = await asyncio.to_thread(self.mux.capture_original_pane, self.identity)
        except ValueError:
            snapshot["pane_state"] = "different-owner"
            return snapshot
        except Exception as error:
            snapshot["pane_state"] = "unavailable"
            snapshot["pane_error"] = type(error).__name__
            return snapshot
        snapshot["pane_state"] = "captured" if pane.returncode == 0 else "unavailable"
        if pane.returncode == 0:
            snapshot["pane"] = _redact_startup(pane.stdout, self.directory, state.get("nonce"))
        return snapshot

    async def _wait_owned_exit(self) -> None:
        async def settled() -> None:
            while any(identity_state(*pair) != "dead" for pair in self.owned):
                await asyncio.sleep(.05)
        await asyncio.wait_for(settled(), 15)

    async def stop(self) -> None:
        settle_mux = cast(
            Callable[[_FixtureMux, str | None], Awaitable[None]],
            importlib.import_module("test_owner_headed_windows")._settle_mux,
        )

        try:
            if self.host is None:
                try:
                    startup = await self.startup_snapshot()
                except Exception as error:
                    startup = {"diagnostic_error": type(error).__name__}
                print("MCP_HEADED_STARTUP " + json.dumps(startup, sort_keys=True), flush=True)
            if self.host is not None:
                await self.host.terminate()
                await self._wait_owned_exit()
                state = await self.state_when(lambda value: value["state"] == "child_exited")
                self.returncode = state["child_exit_code"]
        finally:
            try:
                if self.host is not None:
                    await self.host.close()
                if self.pump is not None:
                    await asyncio.wait_for(self.pump, 5)
            finally:
                await settle_mux(self.mux, self.identity)
                await self._wait_owned_exit()
                if self.returncode is None:
                    self.returncode = -1
                diagnostic = "\n".join(
                    path.read_text(encoding="utf-8", errors="replace")[-6000:]
                    for path in (self.directory / "logs").glob("**/*.log")
                )
                self.stderr.feed_data(diagnostic.encode())
                self.stderr.feed_eof()
                self.stdout.feed_eof()
        assert all(identity_state(*pair) == "dead" for pair in self.owned)

    async def wait(self) -> int:
        assert self.returncode is not None
        return self.returncode
