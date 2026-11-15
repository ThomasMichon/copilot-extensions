"""Actual Windows Copilot consumes the snapshot after its publisher exits."""

from __future__ import annotations

import asyncio
import ctypes as c
import json
import os
from pathlib import Path
import shutil
import sys
import time
import venv

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil._daemon_instance_identity import birth_identity
from agent_procutil._interpreter import physical_executable

from agent_mcp import owner_exec, owner_snapshot
from agent_mcp.owner_bootstrap import ENVIRONMENT_KEYS
from agent_mcp.owner_capabilities import CONTRACT
from agent_mcp.owner_section_native import SectionNative

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.skipif(sys.platform != "win32", reason="native Windows client"),
]


def _literal(value):
    return "'" + str(value).replace("'", "''") + "'"


def _exit_code(native, handle):
    value = c.c_uint32()
    query = native.k.GetExitCodeProcess
    query.argtypes, query.restype = [c.c_void_p, c.POINTER(c.c_uint32)], c.c_int32
    native.require(query(handle, c.byref(value)))
    return value.value


def _pin(native, pid, birth, rights):
    handle = native.require(native.k.OpenProcess(rights, False, pid))
    try:
        identity = native.identity(handle, pid)
        if "windows:" + identity.start_time != birth or identity.principal != native.current_sid():
            raise ValueError("Native fixture identity mismatch")
        native.alive(handle)
    except BaseException:
        native.close(handle)
        raise
    return handle


async def _settle_client(native, client, stderr=None):
    if client is None:
        return
    natural = await asyncio.to_thread(native.k.WaitForSingleObject, client, 15000) == 0
    if not natural:
        terminate = native.k.TerminateProcess
        terminate.argtypes, terminate.restype = [c.c_void_p, c.c_uint32], c.c_int32
        native.require(terminate(client, 91))
        assert await asyncio.to_thread(native.k.WaitForSingleObject, client, 10000) == 0
    assert natural, "Exact-owned cleanup succeeded only after forced native exit"
    code = _exit_code(native, client)
    diagnostic = ""
    if code != 0 and stderr is not None:
        diagnostic = (await asyncio.wait_for(asyncio.shield(stderr), 10)).decode(errors="replace")
    assert code == 0, diagnostic


def _environment(home):
    allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "OS", "SYSTEMDRIVE"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    temporary, local, roaming = home / "tmp", home / "AppData" / "Local", home / "AppData" / "Roaming"
    for path in (temporary, local, roaming):
        path.mkdir(parents=True)
    environment.update(
        HOME=str(home), USERPROFILE=str(home), APPDATA=str(roaming), LOCALAPPDATA=str(local),
        TEMP=str(temporary), TMP=str(temporary), COPILOT_HOME=str(home / ".copilot"),
        COPILOT_PLUGIN_DIR_ONLY="true", COPILOT_OFFLINE="true",
        COPILOT_DISABLE_LOGIN_SHELL_ENV="1", COPILOT_PROVIDER_TYPE="openai",
        COPILOT_PROVIDER_BASE_URL="http://127.0.0.1:1",
        COPILOT_PROVIDER_MODEL_ID="gpt-4.1", COPILOT_MODEL="gpt-4.1",
    )
    return environment


def _provider(root):
    venv.EnvBuilder(with_pip=False).create(root)
    package = root / "Lib" / "site-packages" / "agent_mcp"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "owner_entry.py").write_text("", encoding="utf-8")
    procutil = str(Path(sys.modules[birth_identity.__module__].__file__).resolve().parents[1])
    (package / "__main__.py").write_text(
        "import atexit,json,os,sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0,{procutil!r})\n"
        "from agent_procutil._daemon_instance_identity import birth_identity\n"
        "if sys.argv[1:]==['session-owner-capabilities']:\n"
        " assert os.environ.get('AGENT_MCP_NO_SELFPROVISION')=='1'\n"
        f" report={{'contract':{CONTRACT!r},'supported':True,'invocation_allowed':True}}\n"
        " report['entry']={'python':sys.executable,'prefix':str(Path(sys.prefix).resolve()),\n"
        " 'module':str(Path(__file__).with_name('owner_entry.py').resolve()),'cwd':os.getcwd(),\n"
        f" 'environment':{{key:os.environ.get(key) for key in {ENVIRONMENT_KEYS!r}}}}}\n"
        " report['selection']={'generation':'fixture-only'}\n"
        " print(json.dumps(report));raise SystemExit(0)\n"
        "pair=json.loads(sys.argv[2]) if sys.argv[1]=='session-owner-entry' else {}\n"
        "assert 'AGENT_MCP_NO_SELFPROVISION' not in os.environ\n"
        "identity={'owner_pid':pair.get('pid'),'owner_birth':pair.get('birth'),\n"
        " 'server_pid':os.getpid(),'server_birth':birth_identity(os.getpid()),\n"
        " 'python':sys.executable,'prefix':sys.prefix,'cwd':os.getcwd(),\n"
        " 'context':os.environ['FIXTURE_CONTEXT']}\n"
        "receipt=Path(os.environ['FIXTURE_RECEIPT']);initialized=0;eof=False\n"
        "atexit.register(lambda:receipt.with_suffix('.exit').write_text(\n"
        " json.dumps({'initialized':initialized,'eof':eof}),encoding='utf-8'))\n"
        "for line in sys.stdin:\n"
        " request=json.loads(line)\n"
        " if request.get('id')=='owner-ready' and 'method' not in request:\n"
        "  assert 'result' in request and 'error' not in request\n"
        "  receipt.write_text(json.dumps({**identity,'initialized':initialized}),encoding='utf-8')\n"
        "  continue\n"
        " if 'id' not in request: continue\n"
        " method=request.get('method')\n"
        " if method=='initialize':\n"
        "  initialized+=1\n"
        "  result={'protocolVersion':request['params']['protocolVersion'],\n"
        "   'capabilities':{'tools':{}},'serverInfo':{'name':'owner-fixture','version':'1'}}\n"
        " elif method=='tools/list': result={'tools':[]}\n"
        " elif method=='ping': result={}\n"
        " else:\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':request['id'],\n"
        "   'error':{'code':-32601,'message':'Method not found'}}),flush=True);continue\n"
        " print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)\n"
        " if method=='tools/list':\n"
        "  print(json.dumps({'jsonrpc':'2.0','id':'owner-ready','method':'ping'}),flush=True)\n"
        "eof=True\n",
        encoding="utf-8",
    )
    return root / "Scripts" / "python.exe"


def _publisher(
    root, workspace, executable, configuration, environment, adopted,
    *, server="owner-fixture", tools=(), controlled_wait=False,
):
    source = Path(owner_exec.__file__)
    scripts = source.resolve().parents[3] / "agent-worktrees" / "scripts"
    plan = root / "launch.json"
    plan.write_text(json.dumps({
        "configuration": configuration, "environment": environment,
    }), encoding="utf-8")
    script = root / "publisher.ps1"
    launch = (
        " $child=New-AwNativeSeedSuspendedProcess $info -InheritedHandles @($publisher.InheritedHandle)\n"
        " if(-not $publisher.Publish($child.CreationHandle,$child.Process.Id,$child.Birth))"
        "{throw 'publication unconfirmed'}\n"
        " $receipt=@{pid=$child.Process.Id;birth=$child.Birth;binding=$publisher.Binding}\n"
        " $publisher.Dispose()\n"
        " if(-not $child.Resume()){throw 'resume unconfirmed'}\n"
        " ConvertTo-Json -InputObject $receipt -Compress | Write-Output\n"
    )
    if controlled_wait:
        launch = (
            f" . {_literal(scripts / 'native-seed-boundary.ps1')}\n"
            " function Get-AwNativeSeedRuntime {param([string]$Root) return @{Python='fixture';Bootstrap='fixture'}}\n"
            " function Test-AwHeadedPreparation {return $false}\n"
            " $script:FixtureArgv=@($info.FileName)+@($info.ArgumentList)\n"
            # Only finite admission is substituted; native creation, publication,
            # standard streams and the original wait all use the real owner.
            " function Invoke-AwNativeSeedPhase {\n"
            "  param([string]$Root,[string[]]$Arguments)\n"
            "  if($Arguments[1] -eq 'prepare'){return @{\n"
            "   version=1;argv=$script:FixtureArgv;environment=[pscustomobject]$plan.environment;\n"
            "   attempt_id=('a'*32);resolution_id=('b'*32);operation_id=('c'*32);parent_id=$null\n"
            "  }}\n"
            "  return @{recorded=$true}\n"
            " }\n"
            " $capture={param($handle,$createdPid,$birth)\n"
            "  if(-not $publisher.Publish($handle,$createdPid,$birth)){return $false}\n"
            "  $receipt=@{pid=$createdPid;birth=$birth;binding=$publisher.Binding}\n"
            f"  [IO.File]::WriteAllText({_literal(root / 'launch-owner.json')},"
            "(ConvertTo-Json -InputObject $receipt -Compress))\n"
            "  $publisher.Dispose()\n"
            "  return $true\n"
            " }.GetNewClosure()\n"
            f" exit (Invoke-AwNativeSeedBackend -Root {_literal(root)} "
            f"-Record {_literal(root / 'record.yaml')} -SeedId fixture "
            "-Command $script:FixtureArgv -RequireDirectClient -BeforeResume $capture "
            "-InheritedHandles @($publisher.InheritedHandle))\n"
        )
    script.write_text(
        "$ErrorActionPreference='Stop'\n"
        f". {_literal(scripts / 'native-seed-process.ps1')}\n"
        f"Add-Type -Path {_literal(source.with_name('owner_section.cs'))}\n"
        f"$plan=Get-Content -Raw -LiteralPath {_literal(plan)} | ConvertFrom-Json -AsHashtable\n"
        "$publisher=[AgentMcp.NativeOwnerSection]::new();$child=$null\n"
        "try {\n"
        + (
            f" $entry=$plan.configuration.mcpServers[{_literal(server)}]\n"
            " $request=ConvertFrom-Json -InputObject $entry.args[-1] -AsHashtable\n"
            " $request.binding=$publisher.Binding\n"
            " $entry.args[-1]=ConvertTo-Json -InputObject $request -Depth 20 -Compress\n"
            if adopted else ""
        )
        + f" $info=[Diagnostics.ProcessStartInfo]::new({_literal(executable)})\n"
        " $info.UseShellExecute=$false;$info.CreateNoWindow=$true\n"
        f" $info.WorkingDirectory={_literal(workspace)}\n"
        " $info.Environment.Clear()\n"
        " foreach($key in $plan.environment.Keys){$info.Environment[$key]=$plan.environment[$key]}\n"
        " foreach($arg in @('--acp','--stdio','--no-auto-update','--disable-builtin-mcps',"
        "'--no-custom-instructions','--no-color','--no-ask-user','--log-dir',"
        + _literal(root / "logs")
        + ",'--additional-mcp-config')){$info.ArgumentList.Add($arg)}\n"
        " $info.ArgumentList.Add((ConvertTo-Json -InputObject $plan.configuration -Depth 20 -Compress))\n"
        + "".join(f" $info.ArgumentList.Add({_literal('--allow-tool=' + tool)})\n" for tool in tools)
        + launch
        + "} finally {if($child){$child.Dispose()};$publisher.Dispose()}\n",
        encoding="utf-8",
    )
    return script


async def _rpc(process, identifier, method, params):
    process.stdin.write((json.dumps({
        "jsonrpc": "2.0", "id": identifier, "method": method, "params": params,
    }) + "\n").encode())
    await process.stdin.drain()
    while True:
        line = await asyncio.wait_for(process.stdout.readline(), 30)
        assert line, "Native client closed ACP before its response"
        message = json.loads(line)
        if message.get("id") == identifier and "method" not in message:
            assert "error" not in message, message
            return message["result"]


def test_publisher_plan_does_not_persist_inherited_credentials(tmp_path, monkeypatch):
    for name in ("AWS_SECRET_ACCESS_KEY", "OPENAI_API_KEY", "UNLISTED_FIXTURE_SECRET"):
        monkeypatch.setenv(name, "synthetic-sensitive-value-" + name)
    home = tmp_path / "home"
    _publisher(
        tmp_path, tmp_path, Path("copilot.exe"), {"mcpServers": {}}, _environment(home), False,
    )
    document = (tmp_path / "launch.json").read_text(encoding="utf-8")
    assert "synthetic-sensitive-value-" not in document
    environment = json.loads(document)["environment"]
    assert environment["USERPROFILE"] == str(home)
    assert environment["TEMP"] == str(home / "tmp")


@pytest.mark.asyncio
async def test_mismatched_identity_is_closed_without_becoming_a_cleanup_target():
    process = await asyncio.create_subprocess_exec(
        physical_executable(sys._base_executable), "-I", "-S", "-c",
        "import sys;print('ready',flush=True);sys.stdin.buffer.read()",
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
    )
    native, client = SectionNative(), None
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 10) == b"ready\r\n"
        with pytest.raises(ValueError, match="identity mismatch"):
            client = _pin(native, process.pid, "windows:1", 0x101001)
        assert client is None
        await _settle_client(native, client)
        assert process.returncode is None
        assert birth_identity(process.pid).startswith("windows:")
    finally:
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), 10) == 0
        assert await asyncio.wait_for(process.stdout.read(), 5) == b""
        assert await asyncio.wait_for(process.stderr.read(), 5) == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("controlled_wait", [False, True], ids=["publisher-exit", "existing-wait-owner"])
async def test_offline_native_client_consumes_creation_bound_snapshot(
    tmp_path, record_testsuite_property, controlled_wait,
):
    executable = os.environ.get("COPILOT_NATIVE_TEST_EXECUTABLE")
    if not executable or not shutil.which("pwsh"):
        pytest.skip("Select an explicit native Copilot executable and PowerShell")
    executable = Path(executable)
    assert executable.is_absolute() and executable.name == "copilot.exe"
    with executable.open("rb") as stream:
        assert stream.read(2) == b"MZ"
    python = _provider(tmp_path / "provider")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    baseline = None
    for adopted in (False, True):
        root = tmp_path / ("adopted" if adopted else "ordinary")
        root.mkdir()
        home, receipt_file = root / "home", root / "mcp.json"
        home.mkdir()
        environment = _environment(home)
        configuration = {"mcpServers": {"owner-fixture": {
            "type": "local", "command": str(python),
            "args": ["-I", "-m", "agent_mcp", "bridge", "fixture"], "tools": ["*"],
            "env": {"FIXTURE_RECEIPT": str(receipt_file), "FIXTURE_CONTEXT": 'literal & % | ^ "context"'},
        }}}
        if adopted:
            configuration = owner_exec.windows_overlay(
                configuration, ["owner-fixture"], binding="a" * 64,
            )
        retained_owner = controlled_wait and adopted
        script = _publisher(
            root, workspace, executable, configuration, environment, adopted,
            controlled_wait=retained_owner,
        )
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            shutil.which("pwsh"), "-NoLogo", "-NoProfile", "-File", str(script),
            cwd=workspace, env=environment, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
        )
        native = SectionNative()
        producer = native.require(native.k.OpenProcess(0x101000, False, process.pid))
        client = server = None
        stderr = asyncio.create_task(process.stderr.read())
        try:
            if retained_owner:
                publication = root / "launch-owner.json"
                published = None
                for _ in range(600):
                    try:
                        published = json.loads(publication.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        pass
                    if published is not None:
                        break
                    assert process.returncode is None, (await stderr).decode(errors="replace")
                    await asyncio.sleep(.05)
                assert published is not None, "Existing native owner never published its actual child"
            else:
                line = await asyncio.wait_for(process.stdout.readline(), 30)
                assert line, (await asyncio.wait_for(stderr, 5)).decode(errors="replace")
                published = json.loads(line)
            client = _pin(native, published["pid"], published["birth"], 0x101001)
            if retained_owner:
                assert native.k.WaitForSingleObject(producer, 0) == 258
                assert published["pid"] != process.pid
            else:
                assert await asyncio.to_thread(native.k.WaitForSingleObject, producer, 10000) == 0
            assert not receipt_file.exists()
            with owner_snapshot.WindowsOrigin(published["binding"]) as owner:
                assert (owner.pid, owner.birth) == (published["pid"], published["birth"])
            initialized = await _rpc(process, 1, "initialize", {
                "protocolVersion": 1, "clientCapabilities": {},
                "clientInfo": {"name": "owner-bootstrap-fixture", "version": "1"},
            })
            assert initialized["protocolVersion"] == 1
            mode = ("existing_wait_" if controlled_wait else "publisher_exit_") + (
                "adopted" if adopted else "ordinary"
            )
            record_testsuite_property(
                "windows_" + mode + "_acp_ready_ms", (time.monotonic() - started) * 1000,
            )
            await _rpc(process, 2, "session/new", {"cwd": str(workspace), "mcpServers": []})
            for _ in range(200):
                if receipt_file.exists():
                    break
                assert native.k.WaitForSingleObject(client, 0) == 258
                await asyncio.sleep(.05)
            assert receipt_file.exists(), "Actual client never initialized the configured MCP"
            observed = json.loads(receipt_file.read_text(encoding="utf-8"))
            server = _pin(native, observed["server_pid"], observed["server_birth"], 0x101000)
            assert observed["initialized"] == 1
            record_testsuite_property(
                "windows_" + mode + "_mcp_roundtrip_ms", (time.monotonic() - started) * 1000,
            )
            assert observed["context"] == 'literal & % | ^ "context"'
            assert Path(observed["python"]) == python
            assert Path(observed["prefix"]) == python.parent.parent
            if adopted:
                assert (observed["owner_pid"], observed["owner_birth"]) == (
                    published["pid"], published["birth"],
                )
                assert observed["cwd"] == baseline["cwd"]
            else:
                assert observed["owner_pid"] is None and observed["owner_birth"] is None
                baseline = observed
        finally:
            process.stdin.close()
            try:
                await _settle_client(native, client, stderr)
                assert await asyncio.wait_for(process.wait(), 10) == 0
                remaining = await asyncio.wait_for(process.stdout.read(), 5)
                for line in remaining.splitlines():
                    notification = json.loads(line)
                    assert notification.get("jsonrpc") == "2.0" and "id" not in notification
                    assert notification.get("method") == "session/update", notification
                errors = await asyncio.wait_for(stderr, 5)
                if server is not None:
                    assert await asyncio.to_thread(native.k.WaitForSingleObject, server, 10000) == 0
                    exit_code = _exit_code(native, server)
                    finalizer = receipt_file.with_suffix(".exit")
                    settlement = {
                        "exit_code": exit_code,
                        "finalizer": json.loads(finalizer.read_text()) if finalizer.exists() else None,
                    }
                    record_testsuite_property(
                        "windows_" + mode + "_mcp_settlement",
                        json.dumps(settlement, sort_keys=True),
                    )
                    if settlement["finalizer"] is not None:
                        assert settlement["finalizer"] == {"initialized": 1, "eof": True}
                        assert exit_code == 0
                    if adopted:
                        assert settlement == baseline["settlement"], errors.decode(errors="replace")
                    elif baseline is not None:
                        baseline["settlement"] = settlement
                if client is not None:
                    mapping = native.k.OpenFileMappingW(4, False, owner_snapshot.section_name(published["binding"]))
                    error = c.get_last_error()
                    native.close(mapping or None)
                    assert not mapping and error == 2
            finally:
                native.close(server)
                native.close(client)
                native.close(producer)
