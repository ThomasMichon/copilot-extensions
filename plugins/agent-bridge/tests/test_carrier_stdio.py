"""Agent Bridge carrier command and process-boundary tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from agent_bridge.carrier import (
    acquire_remote_carrier,
    build_remote_carrier_command,
)
from ssh_manager import (
    Envelope,
    EnvelopeType,
    hello_envelope,
)
from ssh_manager.carrier import read_envelope_sync, write_envelope_sync


def test_remote_carrier_command_is_cross_platform():
    posix = build_remote_carrier_command("linux")
    assert posix == '"$HOME/.local/bin/agent-bridge" carrier --stdio'

    windows = build_remote_carrier_command("windows")
    assert windows == (
        'cmd.exe /d /s /c ""%USERPROFILE%\\.local\\bin\\agent-bridge.cmd" '
        'carrier --stdio"'
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows command-shim regression")
def test_windows_remote_carrier_command_preserves_binary_hello(tmp_path: Path):
    bin_dir = tmp_path / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    shim = bin_dir / "agent-bridge.cmd"
    shim.write_text(
        f'@echo off\r\n"{sys.executable}" -m agent_bridge %*\r\n',
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["USERPROFILE"] = str(tmp_path)

    proc = subprocess.Popen(
        build_remote_carrier_command("windows"),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    try:
        peer_hello = read_envelope_sync(proc.stdout)
        assert peer_hello is not None
        assert peer_hello.type is EnvelopeType.HELLO
        proc.stdin.close()
        assert proc.wait(timeout=5) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


@pytest.mark.asyncio
async def test_agent_bridge_acquires_carrier_through_connection_manager():
    manager = AsyncMock()
    lease = object()
    manager.acquire_carrier.return_value = lease

    result = await acquire_remote_carrier(
        "example-host",
        "windows",
        manager=manager,
    )

    assert result is lease
    manager.ensure_connected.assert_awaited_once()
    manager.acquire_carrier.assert_awaited_once()
    host, command = manager.acquire_carrier.await_args.args
    assert host == "example-host"
    assert command.startswith("cmd.exe /d /s /c")


def test_carrier_stdio_negotiates_errors_and_exits_on_eof():
    proc = subprocess.Popen(
        [sys.executable, "-m", "agent_bridge", "carrier", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    try:
        peer_hello = read_envelope_sync(proc.stdout)
        assert peer_hello is not None
        assert peer_hello.type is EnvelopeType.HELLO
        write_envelope_sync(proc.stdin, hello_envelope())
        write_envelope_sync(
            proc.stdin,
            Envelope(
                EnvelopeType.REQUEST,
                request_id="request-1",
                payload={"operation": "synthetic"},
            ),
        )
        response = read_envelope_sync(proc.stdout)
        assert response is not None
        assert response.type is EnvelopeType.ERROR
        assert response.request_id == "request-1"
        assert response.payload["code"] == "unsupported_operation"

        proc.stdin.close()
        assert proc.wait(timeout=5) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
