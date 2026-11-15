"""Use the existing private headed receiver as the physical MCP test transport."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from agent_procutil._daemon_instance_identity import birth_identity, identity_state


class HeadedClient:
    """Adapt the real Session Host frames to the existing ACP fixture reader."""

    def __init__(self, directory, workspace, environment, monkeypatch):
        driver_tests = Path(__file__).resolve().parents[3] / "agent-remote-driver" / "tests"
        monkeypatch.syspath_prepend(str(driver_tests))
        from headed_mux_fixture import FixtureMux

        binary = os.environ.get("PSMUX_TEST_BIN")
        assert binary and Path(binary).is_file()
        self.directory, self.workspace, self.environment = directory, workspace, environment
        self.state_file = directory / "headed-host.json"
        self.session_id = str(uuid4())
        self.mux = FixtureMux(binary, Path(environment["HOME"]), {
            **environment, "PSMUX_DATA_DIR": str(directory / "private-mux"),
            "PSMUX_CONFIG_FILE": os.devnull, "PSMUX_NO_WARM": "1",
        })
        self.identity = self.pid = self.returncode = None
        self.state = {}
        self.host = self.pump = None
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdin = self
        self.buffer = bytearray()
        self.owned = []

    async def query(self, *args):
        return await asyncio.to_thread(self.mux.query, *args)

    async def state_when(self, predicate):
        async with asyncio.timeout(30):
            while True:
                if self.state_file.is_file():
                    self.state = json.loads(self.state_file.read_text(encoding="utf-8"))
                    if predicate(self.state):
                        return self.state
                await asyncio.sleep(.02)

    async def start(self, executable, configuration):
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
        return {"pid": state["child_pid"], "birth": self.owned[1][1]}

    async def connect(self):
        from agent_remote_driver.session_host.client import SessionHostClient

        state = await self.state_when(lambda value: value["state"] == "running")
        extension = state["extension_pid"]
        self.owned.append((extension, birth_identity(extension)))
        assert all(identity_state(*pair) == "matching" for pair in self.owned)
        self.host = await SessionHostClient.connect(port=state["port"])
        hello = await self.host.attach(nonce=state["nonce"].encode())
        assert hello.child_pid == state["child_pid"]
        self.pump = asyncio.create_task(self._frames())

    async def _frames(self):
        try:
            async for cursor, frame in self.host.frames():
                self.stdout.feed_data(frame if frame.endswith(b"\n") else frame + b"\n")
                await self.host.ack(cursor)
        finally:
            self.stdout.feed_eof()

    def write(self, data):
        self.buffer.extend(data)

    async def drain(self):
        assert self.host is not None
        data = bytes(self.buffer)
        self.buffer.clear()
        await self.host.write(data)

    async def completed_text(self, marker):
        events = (
            Path(self.environment["COPILOT_HOME"]) / "session-state"
            / self.session_id / "events.jsonl"
        )
        async with asyncio.timeout(5):
            while True:
                complete = events.read_text(encoding="utf-8").split("\n")[:-1] if events.is_file() else []
                matches = [
                    event["data"]["content"] for event in map(json.loads, complete)
                    if event["type"] == "assistant.message"
                    and marker in event.get("data", {}).get("content", "")
                ]
                if matches:
                    assert len(matches) == 1
                    return matches[0]
                await asyncio.sleep(.02)

    def assert_conversation(self, nonces):
        sessions = Path(self.environment["COPILOT_HOME"]) / "session-state"
        journals = list(sessions.glob("*/events.jsonl"))
        assert [path.parent.name for path in journals] == [self.session_id]
        events = map(json.loads, journals[0].read_text(encoding="utf-8").splitlines())
        prompts = [event["data"]["content"] for event in events if event["type"] == "user.message"]
        assert prompts == ["Run the fixture tool with nonce " + nonce for nonce in nonces]

    async def stop(self):
        from test_owner_headed_windows import _settle_mux

        try:
            if self.host is not None:
                await self.host.terminate()
                async with asyncio.timeout(15):
                    while any(identity_state(*pair) != "dead" for pair in self.owned):
                        await asyncio.sleep(.05)
                state = await self.state_when(lambda value: value["state"] == "child_exited")
                self.returncode = state["child_exit_code"]
        finally:
            try:
                if self.host is not None:
                    await self.host.close()
                if self.pump is not None:
                    await asyncio.wait_for(self.pump, 5)
            finally:
                await _settle_mux(self.mux, self.identity)
                async with asyncio.timeout(15):
                    while any(identity_state(*pair) != "dead" for pair in self.owned):
                        await asyncio.sleep(.05)
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

    async def wait(self):
        assert self.returncode is not None
        return self.returncode
