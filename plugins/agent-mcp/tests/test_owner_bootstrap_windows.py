"""Anonymous Windows bootstrap, actual physical interpreters and inherited stdio."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil._daemon_instance_identity import birth_identity
from agent_procutil._interpreter import physical_executable

from agent_mcp import owner_exec
from agent_mcp.owner_bootstrap import ENTRY_CAPABILITY, ENVIRONMENT_KEYS
from agent_mcp.owner_section import OwnerSection

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="native Windows bootstrap")


@pytest.fixture
def physical_generations(tmp_path):
    program = (
        "import json,os,sys\n"
        "if sys.argv[1]=='session-owner-capabilities':\n"
        " assert os.environ['AGENT_MCP_NO_SELFPROVISION']=='1'\n"
        " assert sys.stdin.buffer.read()==b''\n"
        " open(os.environ['OWNER_QUERY_MARKER'],'x').close()\n"
        " print(open(os.environ['OWNER_REPORT'],encoding='utf-8').read())\n"
        " raise SystemExit(0)\n"
        "request=json.loads(sys.argv[2]) if sys.argv[1]=='session-owner-entry' else {}\n"
        "print(json.dumps({'python':sys.executable,'prefix':sys.prefix,'cwd':os.getcwd(),"
        "'context':os.environ['OWNER_CONTEXT'],'pid':request.get('pid'),"
        "'birth':request.get('birth'),'mode':sys.argv[1],"
        "'probe_only':os.environ.get('AGENT_MCP_NO_SELFPROVISION')}),flush=True)\n"
        "print('physical-stderr',file=sys.stderr,flush=True)\n"
        "while line:=sys.stdin.buffer.readline():\n"
        " sys.stdout.buffer.write(line);sys.stdout.buffer.flush()\n"
        "raise SystemExit(27)\n"
    )
    generations = []
    for name in ("outgoing", "incoming"):
        root = tmp_path / name
        venv.EnvBuilder(with_pip=False).create(root)
        package = root / "Lib" / "site-packages" / "agent_mcp"
        package.mkdir()
        (package / "__init__.py").write_text("", encoding="utf-8")
        (package / "__main__.py").write_text(program, encoding="utf-8")
        module = package / "owner_entry.py"
        module.write_text("", encoding="utf-8")
        generations.append((root, root / "Scripts" / "python.exe", module))
    return generations


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["physical", "unsupported", "spawn-failure"])
async def test_anonymous_start_pins_then_preserves_frames_eof_context_and_exit(
    tmp_path, physical_generations, mode,
):
    outgoing, incoming = physical_generations
    report_file = tmp_path / "report.json"
    environment = {
        **os.environ, "OWNER_REPORT": str(report_file), "OWNER_CONTEXT": 'literal & % | ^ "context"',
        "OWNER_QUERY_MARKER": str(tmp_path / "queried"),
    }
    environment.pop("AGENT_MCP_NO_SELFPROVISION", None)
    writer = OwnerSection()
    try:
        writer.publish(writer._native.k.GetCurrentProcess(), os.getpid())
        configuration = {"mcpServers": {"chosen": {
            "command": str(outgoing[1]), "args": ["-I", "-m", "agent_mcp", "forward", "fixture"],
        }}}
        overlay = owner_exec.windows_overlay(configuration, ["chosen"], binding=writer.binding)
        entry = overlay["mcpServers"]["chosen"]
        request = json.loads(entry["args"][-1])
        assert "pid" not in request and "birth" not in request
        report = {
            "supported": mode != "unsupported", "invocation_allowed": True,
            "contract": {"name": "agent-mcp.session-owner-handoff", "minimum": 1,
                         "maximum": 1, "capabilities": [ENTRY_CAPABILITY]},
            "entry": {
                "python": str(incoming[1] if mode != "spawn-failure" else incoming[1].with_name("gone.exe")),
                "prefix": str(incoming[0]), "module": str(incoming[2]), "cwd": str(tmp_path),
                "environment": {key: environment.get(key) for key in ENVIRONMENT_KEYS},
            },
            "selection": {"generation": "incoming"},
        }
        # Select the incoming generation only after the anonymous overlay exists.
        report_file.write_text(json.dumps(report), encoding="utf-8")
        assert Path(entry["command"]).resolve() == Path(physical_executable(sys._base_executable)).resolve()
        process = await asyncio.create_subprocess_exec(
            entry["command"], *entry["args"], cwd=tmp_path, env=environment,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
        )
        stderr = asyncio.create_task(process.stderr.read())
        try:
            context = json.loads(await asyncio.wait_for(process.stdout.readline(), 15))
            assert Path(environment["OWNER_QUERY_MARKER"]).is_file()
            physical = mode == "physical"
            selected = incoming if physical else outgoing
            assert context == {
                "python": str(selected[1]), "prefix": str(selected[0]), "cwd": str(tmp_path),
                "context": environment["OWNER_CONTEXT"], "pid": os.getpid() if physical else None,
                "birth": birth_identity(os.getpid()) if physical else None,
                "mode": "session-owner-entry" if physical else "forward", "probe_only": None,
            }
            for frame in (b'{"id":1}\n', b'{"id":2,"value":"\\u00e9"}\r\n'):
                process.stdin.write(frame)
                await process.stdin.drain()
                assert await asyncio.wait_for(process.stdout.readline(), 5) == frame
            process.stdin.close()
            assert await asyncio.wait_for(process.wait(), 10) == 27
            assert await asyncio.wait_for(process.stdout.read(), 5) == b""
            assert await asyncio.wait_for(stderr, 5) == b"physical-stderr\r\n"
        finally:
            process.stdin.close()
            if process.returncode is None:
                await asyncio.wait_for(process.wait(), 10)
    finally:
        writer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "exited"])
async def test_unavailable_native_snapshot_never_queries_or_executes_provider(
    tmp_path, physical_generations, failure,
):
    marker = tmp_path / "provider-ran"
    writer, owner = OwnerSection(), None
    try:
        if failure == "exited":
            owner = subprocess.Popen(
                [physical_executable(sys._base_executable), "-I", "-S", "-c",
                 "import sys;sys.stdin.buffer.read()"],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **no_window_kwargs(),
            )
            writer.publish(int(owner._handle), owner.pid)
            owner.stdin.close()
            assert await asyncio.to_thread(owner.wait, 10) == 0
        entry = owner_exec.windows_overlay(
            {"mcpServers": {"chosen": {
                "command": str(physical_generations[0][1]),
                "args": ["-I", "-m", "agent_mcp", "forward", "fixture"],
            }}},
            ["chosen"], binding=writer.binding if owner else "a" * 64,
        )["mcpServers"]["chosen"]
        process = await asyncio.create_subprocess_exec(
            entry["command"], *entry["args"], cwd=tmp_path,
            env={**os.environ, "OWNER_QUERY_MARKER": str(marker)},
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
        )
        output, error = await asyncio.wait_for(process.communicate(), 10)
        assert process.returncode == 1
        assert output == b"" and b"owner bootstrap refused" in error
        assert not marker.exists()
    finally:
        if owner is not None:
            owner.stdin.close()
            assert await asyncio.to_thread(owner.wait, 10) == 0
        writer.close()
