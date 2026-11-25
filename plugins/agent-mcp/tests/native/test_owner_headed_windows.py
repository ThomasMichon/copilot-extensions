"""Opt-in actual copied headed receiver and actual native CLI overlay reader."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any, Callable, Protocol, cast
from uuid import uuid4

import pytest
from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

from agent_mcp.owner_overlay_pipe import OverlayPipe
from agent_mcp.owner_headed_bundle import prepare_headed_command
from zdd.private_protocol import PrivateIPCError, Refusal
from zdd.private_windows_native import Native

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.skipif(sys.platform != "win32", reason="native Windows receiving owner"),
]


class _Mux(Protocol):
    target: str

    def query(self, *args: str) -> subprocess.CompletedProcess[str]: ...


class _PaneMux(_Mux, Protocol):
    def capture_original_pane(self, identity: str) -> subprocess.CompletedProcess[str]: ...


class _StartupInfo(Protocol):
    lpAttributeList: dict[str, list[int]]


async def _settle_mux(mux: _Mux, identity: str | None) -> None:
    for _ in range(100):
        present = await asyncio.to_thread(mux.query, "has-session", "-t", mux.target)
        if present.returncode == 1:
            return
        assert present.returncode == 0, present.stderr
        observation = await asyncio.to_thread(
            mux.query, "display-message", "-p", "-t", mux.target,
            "#{session_id}:#{session_created}|#{pane_id}",
        )
        if not observation.stdout.strip():
            await asyncio.sleep(0.05)
            continue
        assert identity and observation.returncode == 0 and observation.stdout.strip() == identity
        stopped = await asyncio.to_thread(mux.query, "kill-session", "-t", mux.target)
        if stopped.returncode:
            remaining = await asyncio.to_thread(mux.query, "has-session", "-t", mux.target)
            if remaining.returncode == 1:
                return
            assert remaining.returncode == 0, remaining.stderr
        assert stopped.returncode == 0, stopped.stderr
        return
    pytest.fail("Exact fixture mux settlement remains unconfirmed")


def _inherit(transfer: OverlayPipe) -> _StartupInfo:
    startup = cast(_StartupInfo, getattr(subprocess, "STARTUPINFO")())
    startup.lpAttributeList = {"handle_list": [transfer.inherited_handle]}
    return startup


def test_native_file_reader_receives_private_overlay_and_exact_creation_identity(tmp_path: Path) -> None:
    configuration = {"mcpServers": {"chosen": {"env": {"SYNTHETIC": "private-fixture"}}}}
    transfer = OverlayPipe(configuration)
    child, job = None, None
    try:
        child, job = spawn_sync_in_kill_on_close_job(
            [getattr(sys, "_base_executable"), "-I", "-S", "-c",
             "import json,sys;from pathlib import Path;print(json.dumps(json.loads("
             "Path(sys.argv[1]).read_bytes())),flush=True)", transfer.path],
            require_job=True, allow_breakaway=False,
            on_suspended=lambda process: transfer.bind(int(getattr(process, "_handle")), process.pid),
            startupinfo=_inherit(transfer), close_fds=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **no_window_kwargs(),
        )
        transfer.send()
        output, error = child.communicate(timeout=20)
        assert child.returncode == 0, error
        assert json.loads(output) == configuration
    finally:
        if job is not None:
            job.terminate_and_wait(5)
        if child is not None:
            child.wait(timeout=5)
            assert child.stdout is not None and child.stderr is not None
            child.stdout.close()
            child.stderr.close()
        transfer.close()


def test_wrong_native_reader_cannot_receive_overlay_bytes() -> None:
    transfer = OverlayPipe({"synthetic": "must-not-reach-the-other-reader"})
    owners: list[tuple[subprocess.Popen[bytes], Any]] = []
    try:
        expected, job = spawn_sync_in_kill_on_close_job(
            [getattr(sys, "_base_executable"), "-I", "-S", "-c", "import sys;sys.stdin.buffer.read()"],
            require_job=True, allow_breakaway=False,
            on_suspended=lambda process: transfer.bind(int(getattr(process, "_handle")), process.pid),
            startupinfo=_inherit(transfer), close_fds=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **no_window_kwargs(),
        )
        assert job is not None
        owners.append((expected, job))
        other, job = spawn_sync_in_kill_on_close_job(
            [getattr(sys, "_base_executable"), "-I", "-S", "-c",
             "import sys;from pathlib import Path;sys.stdout.buffer.write("
             "Path(sys.argv[1]).read_bytes())", transfer.path],
            require_job=True, allow_breakaway=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **no_window_kwargs(),
        )
        assert job is not None
        owners.append((other, job))
        with pytest.raises(PrivateIPCError) as refused:
            transfer.send()
        assert refused.value.reason == Refusal.IDENTITY
        expected.communicate(timeout=10)
        assert expected.returncode == 0
        transfer.close()
        output, _error = other.communicate(timeout=10)
        assert output == b""
    finally:
        for process, job in owners:
            job.terminate_and_wait(5)
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        transfer.close()


@pytest.mark.parametrize("delivered", [False, True], ids=["cancel-before-open", "consumed-overlay"])
def test_pipe_name_is_reserved_until_original_native_owner_settles(delivered: bool) -> None:
    transfer = OverlayPipe({"synthetic": "private-configuration"})
    child, job = None, None
    api = Native()
    replacement = None
    try:
        script = (
            "import sys;from pathlib import Path;"
            + (
                "print(Path(sys.argv[1]).read_text(),flush=True);"
                if delivered else "print('waiting',flush=True);"
            )
            + "sys.stdin.buffer.read()"
        )
        child, job = spawn_sync_in_kill_on_close_job(
            [getattr(sys, "_base_executable"), "-I", "-S", "-c", script, transfer.path],
            require_job=True, allow_breakaway=False,
            on_suspended=lambda process: transfer.bind(int(getattr(process, "_handle")), process.pid),
            startupinfo=_inherit(transfer), close_fds=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            **no_window_kwargs(),
        )
        assert child.stdout is not None
        if delivered:
            transfer.send()
            assert json.loads(child.stdout.readline()) == {"synthetic": "private-configuration"}
        else:
            transfer.cancel()
            with pytest.raises(PrivateIPCError) as refused:
                transfer.send()
            assert refused.value.reason == Refusal.CANCELLED
            assert child.stdout.readline().strip() == b"waiting"
        assert child.poll() is None
        with pytest.raises(PrivateIPCError):
            replacement = api.listener(transfer.path)
        transfer.close()
        with pytest.raises(PrivateIPCError):
            replacement = api.listener(transfer.path)
        child.communicate(timeout=10)
        assert child.returncode == 0
        transfer.close()
        replacement = api.listener(transfer.path)
    finally:
        if job is not None:
            job.terminate_and_wait(5)
        if child is not None:
            child.wait(timeout=5)
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
        transfer.close()
        if replacement is not None:
            api.close(replacement)


@pytest.mark.asyncio
async def test_actual_retained_headed_receiver_publishes_client_not_coordinator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.environ.get("COPILOT_DRIVER_HEADED_MUX_E2E") != "1":
        pytest.skip("Explicitly select the isolated native headed mux lane")
    driver_tests = Path(__file__).resolve().parents[3] / "agent-remote-driver" / "tests" / "native"
    monkeypatch.syspath_prepend(str(driver_tests))
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    bootstrap = importlib.import_module("test_owner_bootstrap_copilot_windows")
    environment_for = cast(Callable[[Path], dict[str, str]], bootstrap._environment)
    provider_for = cast(Callable[[Path], Path], bootstrap._provider)
    monkeypatch.syspath_prepend(str(driver_tests.parent))
    fixture_mux = cast(
        Callable[[str, Path, dict[str, str]], _PaneMux],
        importlib.import_module("headed_mux_fixture").FixtureMux,
    )
    from agent_remote_driver.session_host.client import SessionHostClient
    from agent_remote_driver.session_host.osutil import pid_alive

    home, workspace = tmp_path / "home", tmp_path / "workspace"
    workspace.mkdir()
    environment = environment_for(home)
    assert os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1"
    environment["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"
    copilot_home = Path(environment["COPILOT_HOME"])
    copilot_home.mkdir()
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    assert executable and Path(executable).is_absolute() and Path(executable).is_file()
    native = SimpleNamespace(
        executable=executable, home=copilot_home, workspace=workspace,
        env=environment, logs=tmp_path / "logs",
    )
    mux: _PaneMux | None = None
    identity: str | None = None
    state: dict[str, Any] | None = None
    try:
        (native.home / "settings.json").write_text(
            json.dumps({"screenReader": True}), encoding="utf-8",
        )
        (native.home / "config.json").write_text(
            json.dumps({"trustedFolders": [str(native.workspace)]}), encoding="utf-8",
        )
        provider = provider_for(tmp_path / "provider")
        configuration = tmp_path / "mcp.json"
        receipt = tmp_path / "mcp-owner.json"
        configuration.write_text(json.dumps({"mcpServers": {"owner-fixture": {
            "command": str(provider),
            "args": ["-I", "-m", "agent_mcp", "forward", "--config", "fixture"],
            "tools": ["*"], "env": {
                "FIXTURE_CONTEXT": "retained-private-overlay", "FIXTURE_RECEIPT": str(receipt),
            },
        }}}), encoding="utf-8")

        session_id = str(uuid4())
        state_file = tmp_path / "host.json"
        command = prepare_headed_command(
            [
                native.executable, "--session-id", session_id, "--screen-reader",
                "--no-auto-update", "--disable-builtin-mcps", "--no-custom-instructions",
                "--no-color", "--no-ask-user", "--log-dir", str(native.logs),
            ],
            configuration_path=configuration, names=["owner-fixture"],
            session_id=session_id, attempt_directory=tmp_path / "original-attempt",
            state_file=state_file, cwd=str(native.workspace),
        )
        binary = os.environ.get("PSMUX_TEST_BIN")
        assert binary, "Select the existing native test mux executable"
        mux = fixture_mux(binary, native.home, {
            **native.env,
            "PSMUX_DATA_DIR": str(tmp_path / "private-mux"), "PSMUX_CONFIG_FILE": os.devnull,
            "PSMUX_NO_WARM": "1",
        })
        async def query(*args: str) -> subprocess.CompletedProcess[str]:
            assert mux is not None
            return await asyncio.to_thread(mux.query, *args)
        assert (await query("has-session", "-t", mux.target)).returncode == 1
        launched = await query(
            "new-session", "-d", "-s", mux.target, "-c", str(native.workspace), "--", *command,
        )
        assert launched.returncode == 0, launched.stderr
        observation = await query(
            "display-message", "-p", "-t", mux.target, "#{session_id}:#{session_created}|#{pane_id}",
        )
        assert observation.returncode == 0 and "|" in observation.stdout
        identity = observation.stdout.strip()
        for _ in range(600):
            if state_file.is_file():
                try:
                    state = json.loads(state_file.read_text())
                except PermissionError:
                    # Windows refuses reads while the host atomically replaces the file.
                    await asyncio.sleep(0.05)
                    continue
                if state["state"] == "running" and receipt.is_file():
                    break
            await asyncio.sleep(0.05)
        else:
            pane = await query("capture-pane", "-p", "-t", mux.target)
            logs = "\n".join(
                path.read_text(encoding="utf-8", errors="replace")[-6000:]
                for path in native.logs.rglob("*.log")
            )
            pytest.fail(
                f"Native MCP receiver did not bind; state={state!r}; pane={pane.stdout!r}; "
                + logs
            )
        assert state is not None
        observed = json.loads(receipt.read_text())
        assert observed["owner_pid"] == state["child_pid"] != state["host_pid"]
        assert observed["owner_birth"] == "windows:" + state["child_process_start_time"]
        assert observed["context"] == "retained-private-overlay"
        assert observed["initialized"] == 1
        pane = await asyncio.to_thread(mux.capture_original_pane, identity)
        assert pane.returncode == 0, pane.stderr
        for archive in (tmp_path / "original-attempt" / "runtime").glob("*.pyz"):
            assert b"retained-private-overlay" not in archive.read_bytes()
        client = await SessionHostClient.connect(port=state["port"])
        try:
            hello = await client.attach(nonce=state["nonce"].encode())
            assert hello.child_pid == observed["owner_pid"]
            await client.terminate()
        finally:
            await client.close()
        for _ in range(300):
            if not any(pid_alive(state[key]) for key in ("pid", "child_pid", "extension_pid")):
                break
            await asyncio.sleep(0.05)
        assert not any(pid_alive(state[key]) for key in ("pid", "child_pid", "extension_pid"))
        assert json.loads(state_file.read_text())["state"] == "child_exited"
    finally:
        if mux is not None:
            await _settle_mux(mux, identity)
