"""Actual offline Copilot reads the anonymous overlay and starts a fixture MCP."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import venv

import pytest

from agent_procutil import no_window_kwargs
from agent_procutil._daemon_instance_identity import birth_identity
from agent_mcp import owner_exec
from agent_mcp.owner_capabilities import CONTRACT
from agent_mcp.owner_bootstrap import ENVIRONMENT_KEYS

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
    provider = tmp_path / "provider"
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(provider)
    python = provider / "bin" / "python"
    package = provider / "lib" / f"python{sys.version_info.major}.{sys.version_info.minor}" / "site-packages" / "agent_mcp"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "owner_entry.py").write_text("# Controlled physical receiver fixture only.\n", encoding="utf-8")
    receipt = tmp_path / "mcp-owner.json"
    procutil = str(Path(sys.modules[birth_identity.__module__].__file__).resolve().parents[1])
    (package / "__main__.py").write_text(
        "import json,os,sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0,{procutil!r})\n"
        "from agent_procutil._daemon_instance_identity import birth_identity\n"
        "if sys.argv[1:]==['session-owner-capabilities']:\n"
        " assert os.environ.get('AGENT_MCP_NO_SELFPROVISION')=='1'\n"
        f" report={{'contract':{CONTRACT!r},'supported':True,'invocation_allowed':True}}\n"
        " report['entry']={'python':sys.executable,'prefix':str(Path(sys.prefix).resolve()),\n"
        " 'module':str(Path(__file__).with_name('owner_entry.py').resolve()),\n"
        f" 'environment':{{key:os.environ.get(key) for key in {ENVIRONMENT_KEYS!r}}}}}\n"
        " report['selection']={'generation':'fixture-only'}\n"
        " print(json.dumps(report))\n"
        " raise SystemExit(0)\n"
        "args=sys.argv[1:]\n"
        "request=json.loads(args[1]) if args[0]=='session-owner-entry' else {}\n"
        "assert 'AGENT_MCP_NO_SELFPROVISION' not in os.environ\n"
        "with open(os.environ['FIXTURE_RECEIPT'],'a') as receipt: receipt.write(json.dumps({\n"
        " 'owner_pid':request.get('pid'), 'owner_birth':request.get('birth'),\n"
        " 'server_pid':os.getpid(),'server_birth':birth_identity(os.getpid()),\n"
        " 'cwd':os.getcwd(),'context':os.environ['FIXTURE_CONTEXT']})+'\\n')\n"
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
        "type": "local", "command": str(python),
        "args": ["-I", "-m", "agent_mcp", "bridge", "fixture"], "tools": ["*"],
        "env": {
            "FIXTURE_RECEIPT": str(receipt),
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
    async def run(adopted, previous_count):
        native = [
            str(executable), "--acp", "--stdio", "--no-auto-update",
            "--disable-builtin-mcps", "--no-custom-instructions", "--no-color", "--no-ask-user",
            "--log-dir", str(tmp_path / "logs"),
        ]
        command = (
            [sys.executable, "-I", "-c", script, "exec-client", "--mcp-config",
             str(configuration), "--server", "owner-fixture", "--", *native]
            if adopted else [*native, "--additional-mcp-config", "@" + str(configuration)]
        )
        process = await asyncio.create_subprocess_exec(
            *command, cwd=workspace, env=environment,
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

        observed = None
        try:
            initialized = await rpc(1, "initialize", {
                "protocolVersion": 1, "clientCapabilities": {},
                "clientInfo": {"name": "owner-exec-fixture", "version": "1"},
            })
            assert initialized["protocolVersion"] == 1
            await rpc(2, "session/new", {"cwd": str(workspace), "mcpServers": []})
            for _ in range(100):
                lines = receipt.read_text(encoding="utf-8").splitlines() if receipt.exists() else []
                if len(lines) > previous_count:
                    observed = json.loads(lines[-1])
                    break
                await asyncio.sleep(.05)
            assert observed is not None, "Native Copilot did not launch the configured fixture"
            if adopted:
                assert observed["owner_pid"] == process.pid
                assert observed["owner_birth"] == native_birth == birth_identity(process.pid)
            else:
                assert observed["owner_pid"] is None and observed["owner_birth"] is None
            assert observed["server_pid"] != process.pid
            assert observed["context"] == "literal-original-context"
            return observed
        finally:
            if process.returncode is None:
                process.terminate()
            await asyncio.wait_for(process.wait(), 10)
            stderr = await asyncio.wait_for(stderr_task, 5)
            if observed is not None:
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

    baseline = await run(False, 0)
    adopted = await run(True, 1)
    assert adopted["cwd"] == baseline["cwd"]
