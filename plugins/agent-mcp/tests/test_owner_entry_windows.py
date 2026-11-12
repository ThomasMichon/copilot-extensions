"""Real Windows pre-effect fallback inherits untouched binary stdio and context."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import time
from typing import Callable

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil._daemon_instance_identity import birth_identity

from agent_mcp import owner_entry
from agent_mcp.owner_bootstrap import ENVIRONMENT_KEYS

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows fallback")


def _driver() -> str:
    source = str(Path(owner_entry.__file__).resolve().parents[1])
    return (
        f"import sys;sys.path.insert(0,{source!r});"
        "from agent_mcp.__main__ import main;"
        "raise SystemExit(main(['session-owner-entry',sys.argv[1]]))"
    )


def _request(pid: int, birth: str, cwd: Path, original: list[str]) -> dict:
    return {
        "pid": pid, "birth": birth,
        "receiver": {
            "python": str(Path(sys.executable).absolute()),
            "prefix": str(Path(sys.prefix).resolve()),
            "module": str(Path(sys.prefix) / "not-the-physical-receiver.py"),
            "cwd": str(cwd),
            "environment": {key: os.environ.get(key) for key in ENVIRONMENT_KEYS},
        },
        "selection": {}, "arguments": ["bridge", "unused"], "original": original,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["ordinary", "receiver"])
async def test_ineligible_receiver_preserves_small_frames_eof_context_and_exit(
    tmp_path: Path, path: str,
    record_testsuite_property: Callable[[str, object], None],
) -> None:
    ordinary = (
        "import json,os,sys\n"
        "print(json.dumps({'cwd':os.getcwd(),'context':os.environ['OWNER_CONTEXT'],"
        "'payload':os.environ['AGENT_MCP_PAYLOAD_ROOT'],'args':sys.argv[1:]}),flush=True)\n"
        "print('ordinary-stderr',file=sys.stderr,flush=True)\n"
        "while line:=sys.stdin.buffer.readline():\n"
        " sys.stdout.buffer.write(line);sys.stdout.buffer.flush()\n"
        "raise SystemExit(27)\n"
    )
    literal = 'spaces & percent% | caret^ "quoted"'
    environment = {
        **os.environ, "OWNER_CONTEXT": "original-context",
        "AGENT_MCP_PAYLOAD_ROOT": "original-payload",
    }
    request = _request(
        os.getpid(), birth_identity(os.getpid()), tmp_path,
        [sys.executable, "-I", "-c", ordinary, literal],
    )
    request["receiver"]["environment"]["AGENT_MCP_PAYLOAD_ROOT"] = "receiver-only-delta"
    command = (
        [sys.executable, "-I", "-c", _driver(), json.dumps(request)]
        if path == "receiver" else request["original"]
    )
    started = time.monotonic()
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=tmp_path, env=environment, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **no_window_kwargs(),
    )
    stderr = asyncio.create_task(process.stderr.read())
    try:
        context = json.loads(await asyncio.wait_for(process.stdout.readline(), 10))
        record_testsuite_property(
            "windows_" + path + "_first_frame_ms", (time.monotonic() - started) * 1000,
        )
        assert context == {
            "cwd": str(tmp_path), "context": "original-context",
            "payload": "original-payload", "args": [literal],
        }
        for frame in (b'{"id":1}\n', b'{"id":2,"value":"\\u00e9"}\r\n'):
            process.stdin.write(frame)
            await process.stdin.drain()
            assert await asyncio.wait_for(process.stdout.readline(), 5) == frame
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 10) == 27
        assert await asyncio.wait_for(process.stdout.read(), 5) == b""
        assert await asyncio.wait_for(stderr, 5) == b"ordinary-stderr\r\n"
    finally:
        if process.returncode is None:
            process.stdin.close()
            await asyncio.wait_for(process.wait(), 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exited", "wrong-birth"])
async def test_native_owner_failure_never_executes_ordinary_fallback(
    tmp_path: Path, failure: str,
) -> None:
    owner = await asyncio.create_subprocess_exec(
        sys._base_executable, "-I", "-c",
        "import sys;print('ready',flush=True);sys.stdin.buffer.read()",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
    )
    try:
        assert await asyncio.wait_for(owner.stdout.readline(), 10) == b"ready\r\n"
        birth = birth_identity(owner.pid)
        if failure == "exited":
            owner.stdin.close()
            assert await asyncio.wait_for(owner.wait(), 10) == 0
        else:
            birth = "windows:wrong-birth"
        request = _request(
            owner.pid, birth, tmp_path,
            [sys.executable, "-I", "-c", "print('unexpected ordinary application')"],
        )
        receiver = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", _driver(), json.dumps(request),
            cwd=tmp_path, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            **no_window_kwargs(),
        )
        output, error = await asyncio.wait_for(receiver.communicate(), 10)
        assert receiver.returncode == 1
        assert output == b""
        assert b"originating owner could not be pinned" in error
    finally:
        owner.stdin.close()
        assert await asyncio.wait_for(owner.wait(), 10) == 0
        assert await asyncio.wait_for(owner.stdout.read(), 5) == b""
        assert await asyncio.wait_for(owner.stderr.read(), 5) == b""
