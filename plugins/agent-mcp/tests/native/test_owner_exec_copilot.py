"""Actual offline Copilot reads the anonymous overlay and starts a fixture MCP."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys

import pytest

from agent_procutil import no_window_kwargs
from agent_procutil._daemon_instance_identity import birth_identity
from agent_mcp import owner_exec
from agent_mcp.owner_capabilities import CONTRACT

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="native Linux exec"),
]


@pytest.mark.asyncio
async def test_offline_copilot_consumes_overlay_with_its_actual_native_owner(tmp_path):
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable:
        pytest.skip("Select the pinned native Copilot asset on a disposable runner")
    executable = Path(executable)
    assert executable.is_absolute() and executable.name == "copilot" and executable.is_file()
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    package = tmp_path / "provider" / "agent_mcp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    receipt = tmp_path / "mcp-owner.json"
    (package / "__main__.py").write_text(
        "import json,os,sys\n"
        "from pathlib import Path\n"
        "from agent_procutil._daemon_instance_identity import birth_identity\n"
        "if sys.argv[1:]==['session-owner-capabilities']:\n"
        f" print(json.dumps({{'contract':{CONTRACT!r},'supported':True,'invocation_allowed':True}}))\n"
        " raise SystemExit(0)\n"
        "args=sys.argv[1:]\n"
        "Path(os.environ['FIXTURE_RECEIPT']).write_text(json.dumps({\n"
        " 'owner_pid':int(args[args.index('--session-owner-pid')+1]),\n"
        " 'owner_birth':args[args.index('--session-owner-birth')+1],\n"
        " 'server_pid':os.getpid(),'server_birth':birth_identity(os.getpid()),\n"
        " 'cwd':os.getcwd(),'context':os.environ['FIXTURE_CONTEXT']}))\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        " if 'id' not in request: continue\n"
        " method=request.get('method')\n"
        " result=({'protocolVersion':request['params']['protocolVersion'],\n"
        " 'capabilities':{'tools':{}},'serverInfo':{'name':'owner-fixture','version':'1'}}\n"
        " if method=='initialize' else {'tools':[]} if method=='tools/list' else {})\n"
        " print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)\n",
        encoding="utf-8",
    )
    configuration = tmp_path / "entries.json"
    configuration.write_text(json.dumps({"mcpServers": {"owner-fixture": {
        "type": "local", "command": sys.executable,
        "args": ["-m", "agent_mcp", "bridge", "fixture"], "tools": ["*"],
        "env": {
            "PYTHONPATH": str(package.parent), "FIXTURE_RECEIPT": str(receipt),
            "FIXTURE_CONTEXT": "literal-original-context",
        },
    }}}), encoding="utf-8")
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("COPILOT_", "AGENT_", "WORKTREE_", "OTEL_"))
        and key not in {"GH_TOKEN", "GITHUB_TOKEN", "GITHUB_API_TOKEN", "TMUX", "TMUX_PANE"}
    }
    environment.update(
        HOME=str(home), USERPROFILE=str(home), COPILOT_HOME=str(home / ".copilot"),
        COPILOT_OFFLINE="true", COPILOT_PROVIDER_TYPE="openai",
        COPILOT_PROVIDER_BASE_URL="http://127.0.0.1:1",
        COPILOT_PROVIDER_MODEL_ID="gpt-4.1", COPILOT_MODEL="gpt-4.1", USE_TGREP="false",
    )
    source = str(Path(owner_exec.__file__).resolve().parents[1])
    script = (
        f"import sys;sys.path.insert(0,{source!r});"
        "from agent_mcp.__main__ import main;raise SystemExit(main(sys.argv[1:]))"
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-I", "-c", script,
        "exec-client", "--mcp-config", str(configuration), "--server", "owner-fixture",
        "--", str(executable), "--acp", "--stdio", "--no-auto-update",
        "--disable-builtin-mcps", "--no-custom-instructions", "--no-color", "--no-ask-user",
        "--log-dir", str(tmp_path / "logs"),
        cwd=workspace, env=environment,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
    )
    stderr_task = asyncio.create_task(process.stderr.read())
    native_birth = birth_identity(process.pid)

    async def rpc(identifier, method, params):
        process.stdin.write((json.dumps({
            "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
        }) + "\n").encode())
        await process.stdin.drain()
        while True:
            line = await asyncio.wait_for(process.stdout.readline(), 30)
            assert line, (await asyncio.wait_for(stderr_task, 5)).decode("utf-8", "replace")
            message = json.loads(line)
            if message.get("id") == identifier and "method" not in message:
                assert "error" not in message, message
                return message["result"]

    try:
        initialized = await rpc(1, "initialize", {
            "protocolVersion": 1, "clientCapabilities": {},
            "clientInfo": {"name": "owner-exec-fixture", "version": "1"},
        })
        assert initialized["protocolVersion"] == 1
        await rpc(2, "session/new", {"cwd": str(workspace), "mcpServers": []})
        for _ in range(100):
            if receipt.exists():
                break
            await asyncio.sleep(.05)
        assert receipt.exists(), "Native Copilot did not launch the explicitly configured fixture"
        observed = json.loads(receipt.read_text(encoding="utf-8"))
        assert observed["owner_pid"] == process.pid
        assert observed["owner_birth"] == native_birth == birth_identity(process.pid)
        assert observed["server_pid"] != process.pid
        assert observed["context"] == "literal-original-context"
        assert observed["cwd"] == str(workspace)
    finally:
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 10)
        stderr = await asyncio.wait_for(stderr_task, 5)
        if receipt.exists():
            observed = json.loads(receipt.read_text(encoding="utf-8"))
            for _ in range(100):
                try:
                    current_birth = birth_identity(observed["server_pid"])
                except ProcessLookupError:
                    break
                if current_birth != observed["server_birth"]:
                    break
                await asyncio.sleep(.05)
            else:
                pytest.fail("Native fixture MCP did not settle with its exact client: " +
                            stderr.decode("utf-8", "replace"))
