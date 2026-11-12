"""Private physical runtimes and caller-contained actors for handover evidence."""

from __future__ import annotations

import asyncio
from contextlib import ExitStack
import importlib.util
import json
import os
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys

from agent_procutil import no_window_kwargs

REPOSITORY = Path(__file__).parents[3]
_spec = importlib.util.spec_from_file_location(
    "native_incoming_payload", Path(__file__).with_name("test_incoming_installer.py"),
)
_payload = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_payload)
payload_copy = _payload.payload_copy


ACTOR = r"""
import asyncio, json, os, sys
from pathlib import Path
from agent_procutil.daemon_instances import daemon_entrypoint
from agent_mcp import __version__, incoming, sockio
from agent_mcp.custody_host import CustodyHost
from agent_mcp.custody_launch import attach
from agent_mcp.serve import Server

role, root, handle, fixed, config = sys.argv[1:6]
if role == "conduit-optional":
    import subprocess
    from agent_procutil import no_window_kwargs
    from agent_procutil._daemon_instance_identity import birth_identity
    from agent_mcp.owner_exec import overlay
    owner_birth = birth_identity(os.getpid())
    entry = overlay(
        {"mcpServers": {"fixture": {
            "command": sys.executable,
            "args": ["-I", "-m", "agent_mcp", "bridge", "--config", config],
        }}}, ["fixture"], pid=os.getpid(), birth=owner_birth,
        environment=dict(os.environ),
    )["mcpServers"]["fixture"]
    # This controlled caller holds the originating lifetime, not a production launcher.
    with subprocess.Popen(
        [entry["command"], *entry["args"]], **no_window_kwargs(),
    ) as conduit:
        Path(f"{os.getpid()}.owner.json").write_text(json.dumps({
            "owner_pid": os.getpid(), "owner_birth": owner_birth,
            "conduit_pid": conduit.pid, "conduit_birth": birth_identity(conduit.pid),
        }), encoding="utf-8")
        raise SystemExit(conduit.wait())
if role.startswith("conduit"):
    if len(sys.argv) > 6:
        from agent_mcp.custody_launch import run_adopted
        raise SystemExit(run_adopted(
            fixed, config, owner_pid=int(sys.argv[6]), owner_birth=sys.argv[7]))
    admission = incoming.ensure_current(fixed)
    connection = attach(fixed, config, admission, ensure=role == "conduit-launcher")
    raise SystemExit(sockio.pump(connection))

@daemon_entrypoint(
    "agent-mcp-custody" if role == "keeper" else "agent-mcp",
    source=__file__, runtime_root=Path(handle).parent, purpose="unattended-test",
)
async def run():
    if role == "keeper":
        server = CustodyHost(handle, fixed, root, version=__version__, idle_timeout=1)
    else:
        server = Server(handle, version=__version__, passive=True,
                        enable_lease=False, idle_timeout=0)
    serving = asyncio.create_task(server.serve_forever())
    while server._server is None or (
        role != "keeper" and server._control_server is None
    ):
        if serving.done():
            return await serving
        await asyncio.sleep(.01)
    if role != "keeper":
        Path(handle + ".control.json").write_text(json.dumps({
            "pid":os.getpid(), "port":server._control_server.sockets[0].getsockname()[1],
        }), encoding="utf-8")
    await serving

asyncio.run(run())
"""

UPSTREAM = r"""
import json, os, sys
initialized = mutations = 0
cancelled = False
pending = None
def emit(message):
    print(json.dumps(message), flush=True)
def result(mid):
    return {"jsonrpc":"2.0", "id":mid, "result":{
        "pid":os.getpid(), "initialized":initialized, "mutations":mutations,
        "upstream_trace":os.environ.get("COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS"),
        "cancelled":cancelled, "content":[]}}
for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    mid = message.get("id")
    if method == "initialize":
        initialized += 1
        emit(result(mid))
    elif method == "notifications/cancelled":
        cancelled = True
    elif mid == "server-approval" and method is None:
        emit(result(pending))
        pending = None
        emit({"jsonrpc":"2.0", "method":"notifications/tools/list_changed"})
    elif method == "tools/call" and message["params"]["name"] == "mutate":
        mutations += 1
        pending = mid
        emit({"jsonrpc":"2.0", "method":"notifications/progress", "params":{"progress":1}})
        emit({"jsonrpc":"2.0", "id":"server-approval", "method":"sampling/createMessage",
              "params":{}})
    elif mid is not None:
        emit(result(mid))
emit({"jsonrpc":"2.0", "method":"notifications/upstream-eof",
      "params":{"pid":os.getpid(), "initialized":initialized, "mutations":mutations}})
"""


def build_runtimes(
    private: Path, retained: ExitStack, *, observations: Path | None = None,
    caller_context: Path | None = None,
) -> tuple[Path, dict[str, tuple[str, Path]]]:
    """Use the construction owner, not a copied venv or patched launch identity."""
    for library in ("installer-engine", "versioned-runtime"):
        sys.path.insert(0, str(REPOSITORY / "libs" / library))
    import installer_engine as engine
    import versioned_runtime_source as source

    git_command = ["git", "-C", str(REPOSITORY)]
    metadata = REPOSITORY / ".git"
    if os.name != "nt" and metadata.is_file():
        declaration = metadata.read_text(encoding="utf-8").strip()
        if declaration.startswith("gitdir: ") and PureWindowsPath(declaration[8:]).drive:
            admin = subprocess.check_output(
                ["wslpath", "-u", declaration[8:]], text=True, timeout=10,
                **no_window_kwargs(),
            ).strip()
            git_command = ["git", "--git-dir=" + admin, "--work-tree=" + str(REPOSITORY)]

    def git(revision):
        return subprocess.check_output(
            [*git_command, "rev-parse", revision],
            text=True, timeout=10, **no_window_kwargs(),
        ).strip()

    commit, tree = git("HEAD"), git("HEAD^{tree}")
    root = private / "runtime"
    result = {}
    for label in ("old", "new"):
        payload, _ = payload_copy(private / label, label)
        pipeline = payload / "src" / "agent_mcp" / "pipeline.py"
        body = pipeline.read_text(encoding="utf-8")
        needle = f"response['result']['pipeline_code']={label!r}"
        assert body.count(needle) == 1
        witness = ""
        if observations is not None:
            witness = (
                f"\n            with open({str(observations)!r},'a') as witness:\n"
                "                witness.write(__import__('json').dumps({"
                "'method':request.get('method'),'request':request,'response':response})+'\\n')"
            )
        pipeline.write_text(body.replace(
            needle,
            needle + "\n"
            "            response['result']['backend_pid']=__import__('os').getpid()\n"
            "            response['result']['backend_prefix']=__import__('sys').prefix\n"
            "            response['result']['backend_module']=__file__\n"
            "            response['result']['backend_trace']=__import__('os').environ.get("
            "'COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS')" + witness,
        ), encoding="utf-8")
        if observations is not None:
            receiver = payload / "src" / "agent_mcp" / "owner_entry.py"
            body = receiver.read_text(encoding="utf-8")
            needle = "        return args.func(args)"
            assert body.count(needle) == 1
            receiver.write_text(body.replace(
                needle,
                "        from agent_procutil._daemon_instance_identity import birth_identity\n"
                f"        Path({str(observations.with_suffix('.owner.json'))!r}).write_text("
                "json.dumps({'owner_pid':request['pid'],'owner_birth':request['birth'],"
                "'conduit_pid':os.getpid(),'conduit_birth':birth_identity(os.getpid())}))\n"
                + needle,
            ).replace(
                "        except (OSError, ValueError):",
                "        except (OSError, ValueError) as refusal:\n"
                f"            Path({str(observations.with_suffix('.refusal'))!r}).write_text(str(refusal))",
            ), encoding="utf-8")
            if caller_context is not None:
                body = receiver.read_text(encoding="utf-8")
                needle = "            args = _qualify(request)"
                assert body.count(needle) == 1
                receiver.write_text(body.replace(
                    needle,
                    "            import time\n"
                    f"            Path({str(caller_context)!r}).write_text(json.dumps(dict(os.environ)))\n"
                    "            deadline = time.monotonic() + 10\n"
                    f"            while not Path({str(caller_context.with_suffix('.ready'))!r}).exists():\n"
                    "                if time.monotonic() >= deadline:\n"
                    "                    raise RuntimeError('fixture backend preparation deadline expired')\n"
                    "                time.sleep(.02)\n"
                    + needle,
                ), encoding="utf-8")
            capabilities = payload / "src" / "agent_mcp" / "owner_capabilities.py"
            body = capabilities.read_text(encoding="utf-8")
            needle = "    print(json.dumps(result, sort_keys=True))"
            assert body.count(needle) == 1
            capabilities.write_text(body.replace(
                needle,
                f"    Path({str(observations.with_suffix('.capabilities'))!r}).write_text(json.dumps(result))\n"
                + needle,
            ), encoding="utf-8")
            host = payload / "src" / "agent_mcp" / "custody_host.py"
            body = host.read_text(encoding="utf-8")
            needle = "execution_context.config_failure(exc)"
            assert body.count(needle) == 2
            host.write_text(body.replace(
                needle, "(print('fixture custody refusal:', repr(exc), flush=True) or "
                + needle + ")",
            ), encoding="utf-8")
            connector = payload / "src" / "agent_mcp" / "custody_connector.py"
            body = connector.read_text(encoding="utf-8")
            needle = 'AdmissionRefused("backend attachment admission was not confirmed")'
            assert body.count(needle) == 1
            connector.write_text(body.replace(
                needle,
                'AdmissionRefused("backend attachment admission was not confirmed: " + repr(ack))',
            ), encoding="utf-8")
            custody = payload / "src" / "agent_mcp" / "custody_session.py"
            body = custody.read_text(encoding="utf-8")
            needle = "        self.state.unknown()"
            assert body.count(needle) == 1
            custody.write_text(body.replace(
                needle,
                needle + "\n"
                f"        with open({str(observations.with_suffix('.unknown'))!r},'a') as witness:\n"
                "            witness.write(__import__('json').dumps({"
                "'keeper_pid':__import__('os').getpid(),'uncertain':self.state.uncertain,"
                "'fenced':self.state.fenced,"
                "'accepted':[item.message for item in self.state.accepted.values()]})+'\\n')",
            ), encoding="utf-8")
            backend = payload / "src" / "agent_mcp" / "backend.py"
            body = backend.read_text(encoding="utf-8")
            needle = '            raise ConnectionError("upstream send outcome is unknown; no replay")'
            assert body.count(needle) == 1
            body = body.replace(
                needle,
                needle + "\n"
                f"        with open({str(observations)!r},'a') as witness:\n"
                "            witness.write(__import__('json').dumps({"
                "'method':'client/reply','request':message,'response':{'result':{"
                f"'pipeline_code':{label!r},"
                "'backend_pid':__import__('os').getpid(),"
                "'backend_prefix':__import__('sys').prefix,'backend_module':__file__,"
                "'backend_trace':__import__('os').environ.get("
                "'COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS')}}})+'\\n')",
            )
            needle = '            self.state = "quiescing"\n            while self._settlers:'
            assert body.count(needle) == 1
            body = body.replace(
                needle,
                '            self.state = "quiescing"\n'
                f"            with open({str(observations.with_suffix('.quiescing'))!r},'a') as witness:\n"
                "                witness.write(__import__('json').dumps({"
                "'backend_pid':__import__('os').getpid(),'epoch':self.epoch,"
                "'accepted':len(self.accepted),'settlers':len(self._settlers)})+'\\n')\n"
                "            while self._settlers:",
            )
            needle = "    async def output(self, message):\n"
            assert body.count(needle) == 1
            backend.write_text(body.replace(
                needle,
                needle
                + f"        loss=__import__('pathlib').Path({str(observations.with_suffix('.lose-tool'))!r})\n"
                "        if (loss.is_file() and message.get('result',{}).get('content') == "
                "[{'type':'text','text':'fixture-result:'+loss.read_text()}]):\n"
                f"            __import__('pathlib').Path({str(observations.with_suffix('.lost-tool'))!r}).write_text("
                "__import__('json').dumps({'backend_pid':__import__('os').getpid(),"
                "'nonce':loss.read_text(),'message':message}))\n"
                "            self.peer.writer.close()\n"
                "            await self.peer.writer.wait_closed()\n"
                "            raise ConnectionError('fixture accepted tool output lost; no replay')\n"
                + f"        drop=__import__('pathlib').Path({str(observations.with_suffix('.drop'))!r})\n"
                "        if (message.get('method')=='ping' and drop.is_file()\n"
                "            and message.get('id')==drop.read_text()):\n"
                f"            __import__('pathlib').Path({str(observations.with_suffix('.dropped'))!r}).write_text(message['id'])\n"
                "            async with asyncio.timeout(5):\n"
                f"                while not __import__('pathlib').Path({str(observations.with_suffix('.release'))!r}).is_file():\n"
                "                    await asyncio.sleep(.02)\n",
            ), encoding="utf-8")
        package = source.payload_package_version(payload, "agent-mcp")
        digest = source.assembly_hash(payload)
        identity = source.SourceIdentity(
            "agent-mcp", package, commit, tree, digest,
            source.runtime_version(package, commit, digest),
        )
        (payload / source.SOURCE_FILE).write_text(
            json.dumps(identity.document()), encoding="utf-8",
        )
        candidate = retained.enter_context(engine.numbered_candidate(
            root, identity.runtime_version, plugin=payload,
            uv=Path(shutil.which("uv")), base_python=Path(sys.executable),
            health_module="agent_mcp", expected_source_identity=source.manifest_fields(identity),
            child_timeout=60, wait_timeout=60,
        ))
        assert engine.runtime.is_complete(
            root, identity.runtime_version, expect_hash=candidate.payload_hash,
        )
        result[label] = (identity.runtime_version, candidate.slot)
    assert result["old"][0] != result["new"][0]
    return root, result


async def eventually(operation, predicate=bool):
    async def wait():
        while True:
            try:
                value = await operation()
                if predicate(value):
                    return value
            except (OSError, ValueError):
                pass
            await asyncio.sleep(0.02)

    return await asyncio.wait_for(wait(), 10)


async def send(process, message):
    process.stdin.write((json.dumps(message) + "\n").encode())
    await process.stdin.drain()


async def receive(process):
    line = await asyncio.wait_for(process.stdout.readline(), 10)
    if not line:
        error = await asyncio.wait_for(process.stderr.read(), 2)
        raise AssertionError(error.decode(errors="replace")[-6000:])
    return json.loads(line)


async def spawn(slot, actor, role, root, handle, fixed, config, environment, home):
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return await asyncio.create_subprocess_exec(
        str(python), "-I", str(actor), role, str(root), str(handle), str(fixed), str(config),
        cwd=home, env=environment, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        **no_window_kwargs(),
    )


async def check_owner_capabilities(
    runtime: tuple[str, Path], root: Path, environment: dict[str, str], home: Path,
    *, selected: bool,
) -> None:
    generation, slot = runtime
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    result = await asyncio.to_thread(
        subprocess.run,
        [str(python), "-I", "-m", "agent_mcp", "session-owner-capabilities"],
        cwd=home, env=environment, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10,
        **no_window_kwargs(),
    )
    reply = json.loads(result.stdout)
    assert reply["supported"] is selected, result.stderr.decode(errors="replace")
    assert result.returncode == (0 if selected else 1)
    if selected:
        assert reply["selection"]["generation"] == generation
        assert Path(reply["selection"]["root"]) == root.resolve()
        assert reply["invocation_allowed"] is True
    else:
        assert "selection" not in reply


async def shutdown(handle):
    from agent_mcp.cutover import control_request
    from agent_mcp.serve import _control_token_path

    record = json.loads(Path(str(handle) + ".control.json").read_text(encoding="utf-8"))
    token = _control_token_path(handle.parent, record["pid"]).read_text(encoding="utf-8")
    reply = await asyncio.to_thread(
        control_request, "127.0.0.1", record["port"], token, {"op": "ping"},
    )
    assert reply["pid"] == record["pid"]
    reply = await asyncio.to_thread(
        control_request, "127.0.0.1", record["port"], token, {"op": "shutdown"},
    )
    assert reply["ok"] is True
