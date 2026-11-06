"""Opt-in ordinary Windows installation and literal-command generation witness."""

import asyncio
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import time

import pytest
from agent_procutil import no_window_kwargs
from agent_procutil.daemon_instances import daemon_test_lifecycle

from agent_mcp import cutover, serve
from agent_mcp.ipc import request_via_socket

REPOSITORY = Path(__file__).parents[3]
QUALIFICATION = "COPILOT_EXTENSIONS_MCP_NATIVE_INSTALL_QUALIFICATION"


@pytest.fixture
def dead_endpoint_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        yield reservation.getsockname()[1]

UPSTREAM = """
import json,os,sys
initialized=0
for line in sys.stdin:
    m=json.loads(line)
    method=m.get("method")
    if method=="initialize": initialized+=1
    result={"pid":os.getpid(),"initialized":initialized,"identity":os.environ.get("FIXTURE_INJECTED"),
      "protocolVersion":"2024-11-05","capabilities":{"tools":{}},"serverInfo":{"name":"fixture","version":"1"}}
    if method=="tools/list":
        result={"tools":[{"name":"identity","inputSchema":{"type":"object"}}]}
    if method=="tools/call":
        args=m.get("params",{}).get("arguments",{})
        with open(os.environ["FIXTURE_MUTATIONS"],"a") as ledger:
            ledger.write(json.dumps({"probe":args.get("probe"),"identity":os.environ.get("FIXTURE_INJECTED"),"pid":os.getpid()})+"\\n")
        result={"content":[{"type":"text","text":os.environ.get("FIXTURE_INJECTED","missing")}]}
    if "id" in m:
        print(json.dumps({"jsonrpc":"2.0","id":m["id"],"result":result}),flush=True)
"""

STALE_CLIENT = """
import sys,time
from pathlib import Path
ready,release,mode=sys.argv[1:]
Path(ready).write_text("launched")
while not Path(release).exists(): time.sleep(.02)
from agent_mcp.__main__ import main
args=["call","bridge.json","identity",'{"probe":"stale-'+mode+'"}']
if mode=="direct": args+=["--no-serve"]
raise SystemExit(main(args))
"""


def payload_copy(root, generation):
    source = REPOSITORY / "plugins" / "agent-mcp"
    payload = root / "plugins" / "agent-mcp"
    ignore = shutil.ignore_patterns("tests", "__pycache__", ".venv", "*.egg-info", "build", "dist")
    shutil.copytree(source, payload, ignore=ignore)
    shutil.copytree(REPOSITORY / "libs", root / "libs", ignore=ignore)
    roster = root / ".github" / "plugin" / "marketplace.json"
    roster.parent.mkdir(parents=True)
    shutil.copyfile(REPOSITORY / ".github/plugin/marketplace.json", roster)
    # Preserve the canonical source graph's roster attribution. Other plugins
    # contribute only their unchanged identity documents, never executable code.
    for entry in json.loads(roster.read_text(encoding="utf-8"))["plugins"]:
        relative = entry.get("source")
        if isinstance(relative, str) and relative.startswith("plugins/"):
            manifest = root / relative / "plugin.json"
            manifest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPOSITORY / relative / "plugin.json", manifest)
    pipeline = payload / "src" / "agent_mcp" / "pipeline.py"
    original = pipeline.read_text(encoding="utf-8")
    assert original.count("return await nxt(request)") == 1
    pipeline.write_text(original.replace(
        "return await nxt(request)",
        "response = await nxt(request)\n"
        "        if isinstance(response,dict) and isinstance(response.get('result'),dict):\n"
        f"            response['result']['pipeline_code']={generation!r}\n"
        "        return response",
    ), encoding="utf-8")
    tripwire = root / "retirement-invoked"
    (payload / "scripts" / "reap_versions.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(tripwire)!r}).write_text('unexpected')\n"
        "raise RuntimeError('installation must not inspect or retire predecessors')\n",
        encoding="utf-8",
    )
    # This refreshes actual producer bytes, not an installed-slot receipt.
    spec = importlib.util.spec_from_file_location(
        f"fixture_footprint_{generation}", REPOSITORY / "libs/installer-engine/installer_engine_footprint.py",
    )
    owner = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = owner
    spec.loader.exec_module(owner)
    declaration = payload / "scripts" / "uninstall-profile.json"
    previous = owner.decode(owner._manifest_text(declaration))
    current = json.loads(json.dumps(previous))
    current["producers"] = {
        scope: owner.producer_digest(payload, scope) for scope in previous["producers"]
    }
    owner.require_transition(previous, current, "agent-mcp")
    declaration.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
    return payload, tripwire


async def receive(process):
    line = await asyncio.wait_for(process.stdout.readline(), 8)
    if not line:
        diagnostic = await asyncio.wait_for(process.stderr.read(), 2)
        pytest.fail("literal client closed before its response: " + diagnostic.decode(errors="replace")[-6000:])
    return json.loads(line)


async def send(process, mid, method, params=None):
    process.stdin.write((json.dumps({
        "jsonrpc": "2.0", "id": mid, "method": method, "params": params or {},
    }) + "\n").encode())
    await process.stdin.drain()
    return await receive(process)


def active_data_socket(data, control_home=None):
    from zdd import routing

    home = control_home or data / "current"
    active = routing.read_active_endpoint(home, verify_listener=False)
    assert active is not None
    token = serve._control_token_path(home, active.pid).read_text(encoding="utf-8")
    reply = cutover.control_request(active.client_host, active.port, token, {"op": "ping"})
    assert reply["ready"] and reply["pid"] == active.pid
    return Path(reply["data_socket"]), reply


@pytest.mark.skipif(os.name != "nt", reason="ordinary native Windows installer qualification")
@pytest.mark.skipif(os.environ.get(QUALIFICATION) != "1", reason="explicit full-install qualification")
@pytest.mark.parametrize("configured", [False, True], ids=["explicit-only", "installer-discovered"])
@pytest.mark.timeout(240)
async def test_ordinary_install_publishes_without_predecessor_extinction(
    tmp_path_factory, record_testsuite_property, configured, dead_endpoint_port,
):
    from plugin_test_environment import isolated_environment

    private = tmp_path_factory.mktemp("in")
    environment = isolated_environment(os.environ, private / "profile")
    root = Path(environment["USERPROFILE"]) / ".agent-mcp"
    environment.pop("AGENT_MCP_HOME", None)
    data = root
    bin_dir = Path(environment["USERPROFILE"]) / ".local" / "bin"
    environment.update(
        AGENT_MCP_INSTALL_DIR=str(root), AGENT_MCP_NO_VERSION_REAP="1",
        AGENT_MCP_NO_CUTOVER="", AGENT_MCP_NO_SERVE="", AGENT_MCP_NO_ENSURE_SERVE="",
        AGENT_MCP_NO_MULTIPLEX="", AGENT_MCP_NO_SELFPROVISION="1",
        COPILOT_EXTENSIONS_HUMAN_UTF8="", PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
        UV_CACHE_DIR=str(Path(os.environ["COPILOT_EXTENSIONS_TEST_VENV_ROOT"]) / ".uv-cache"),
        UV_OFFLINE="1",
        UV_HTTP_TIMEOUT="60",
        PYTHONUTF8="1",
        FIXTURE_CREDENTIAL="account-a", FIXTURE_MUTATIONS=str(private / "mutations.jsonl"),
    )
    environment.pop("AGENT_MCP_SERVE_SOCKET", None)
    environment.pop("AGENT_MCP_CONTROL_TOKEN", None)
    shell = shutil.which("pwsh")
    assert shell
    data.mkdir(parents=True, exist_ok=True)
    configuration = data / "bridge.json"
    upstream = private / "upstream.py"
    upstream.write_text(UPSTREAM, encoding="utf-8")
    config_data = {
        "server": {"type": "stdio", "command": [sys.executable, str(upstream)]},
        "auth": {"kind": "env", "source_env": "FIXTURE_CREDENTIAL",
                 "target_env": "FIXTURE_INJECTED", "inject": "env", "format": "{token}"},
    }
    configuration.write_text(json.dumps(config_data), encoding="utf-8")
    if configured:
        catalog = data / "bridges" / "identity.json"
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_text(json.dumps(config_data), encoding="utf-8")
        inactive = private / "configured" / "exact.sock"
        inactive.parent.mkdir()
        inactive_endpoint = serve._endpoint_path(inactive)
        inactive_bytes = json.dumps({"port": dead_endpoint_port, "token": "dead-fixture"}).encode()
        inactive_endpoint.write_bytes(inactive_bytes)
        environment["AGENT_MCP_SERVE_SOCKET"] = str(inactive)
    clients, handles, children, tripwires, readers = [], [], [], [], []

    async def install(payload, *, installation=None, expected_refusal=False):
        installation = installation or root
        install_environment = dict(environment, AGENT_MCP_INSTALL_DIR=str(installation))
        process = await asyncio.create_subprocess_exec(
            shell, "-NoProfile", "-NonInteractive", "-File", str(payload / "scripts/init.ps1"),
            "-InstallDir", str(installation), cwd=payload, env=install_environment,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
        )
        children.append(process)
        output = [bytearray(), bytearray()]

        async def collect(stream, destination):
            while chunk := await stream.read(4096):
                if len(destination) < 1 << 20:
                    destination.extend(chunk)

        streams = [
            asyncio.create_task(collect(process.stdout, output[0])),
            asyncio.create_task(collect(process.stderr, output[1])),
        ]
        readers.extend(streams)
        try:
            await asyncio.wait_for(process.wait(), 90)
            await asyncio.wait_for(asyncio.gather(*streams), 2)
        except TimeoutError as exc:
            raise AssertionError(
                "ordinary installer exceeded its existing child deadline:\n"
                + bytes(output[0]).decode(errors="replace")[-6000:]
                + bytes(output[1]).decode(errors="replace")[-6000:]
            ) from exc
        diagnostic = bytes(output[0]) + bytes(output[1])
        if expected_refusal:
            assert process.returncode != 0
            assert b"installation identity" in diagnostic.lower()
        else:
            assert process.returncode == 0, diagnostic.decode(errors="replace")[-6000:]

    async def literal(command="agent-mcp bridge --config bridge.json", credential="account-b", native_shell=False):
        assert (bin_dir / "agent-mcp.cmd").is_file()
        arguments = [os.environ["ComSpec"], "/d", "/s", "/c", command]
        if native_shell:
            invocation = "& '" + str(bin_dir / "agent-mcp.cmd").replace("'", "''") + "' call bridge.json identity"
            arguments = [shell, "-NoProfile", "-NonInteractive", "-Command", invocation]
        process = await asyncio.create_subprocess_exec(
            *arguments, cwd=data,
            env=dict(environment, FIXTURE_CREDENTIAL=credential),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
        )
        clients.append(process)
        return process

    with daemon_test_lifecycle(
        owner="ordinary-incoming-install", registry_root=private / "instances",
    ) as lifecycle:
        environment.update(lifecycle)
        try:
            old_payload, old_tripwire = payload_copy(private / "a", "old")
            tripwires.append(old_tripwire)
            await install(old_payload)
            old_generation = (root / "current-version").read_text(encoding="utf-8").strip()
            old_manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
            from zdd import routing
            assert routing.read_active_endpoint(data / "current", verify_listener=False) is None
            if configured:
                assert routing.read_active_endpoint(inactive.parent, verify_listener=False) is None
                assert inactive_endpoint.read_bytes() == inactive_bytes
                # The remaining generation witness uses its original default
                # namespace; the inactive explicit handle remains untouched.
                environment.pop("AGENT_MCP_SERVE_SOCKET")
                alternate_home = private / "alternate-profile"
                alternate_bridges = alternate_home / ".agent-mcp" / "bridges"
                alternate_bridges.mkdir(parents=True)
                (alternate_bridges / "invalid.json").write_text("{", encoding="utf-8")
                probe_environment = dict(environment, HOME=str(alternate_home), USERPROFILE=str(alternate_home))
                probe_environment.pop("AGENT_MCP_HOME", None)
                old_python = root / "versions" / old_generation / ".venv" / "Scripts" / "python.exe"
                probe = await asyncio.create_subprocess_exec(
                    str(old_python), "-I", str(old_payload / "scripts" / "publish_incoming.py"),
                    "--root", str(root), cwd=data, env=probe_environment,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **no_window_kwargs(),
                )
                children.append(probe)
                output, error = await asyncio.wait_for(probe.communicate(), 15)
                assert probe.returncode != 0, (output, error)
                assert b"invalid" in error.lower() or b"parse" in error.lower()
            configured_elsewhere = private / "elsewhere" / "configured.sock"
            environment["AGENT_MCP_SERVE_SOCKET"] = str(configured_elsewhere)
            exact = await literal("agent-mcp forward --config bridge.json --socket requested.sock")
            assert (await send(exact, 1, "initialize"))["result"]["pipeline_code"] == "old"
            exact_handle, _ = active_data_socket(data, control_home=data)
            handles.append(exact_handle)
            assert serve._read_endpoint(data / "requested.sock") == serve._read_endpoint(exact_handle)
            assert routing.read_active_endpoint(data / "current", verify_listener=False) is None
            assert not serve._endpoint_path(configured_elsewhere).exists()
            exact.stdin.close()
            await asyncio.wait_for(exact.wait(), 8)
            environment.pop("AGENT_MCP_SERVE_SOCKET")
            held = await literal()
            initialized = await send(held, 1, "initialize")
            assert initialized["result"]["pipeline_code"] == "old"
            assert initialized["result"]["identity"] == "account-b"
            original_pid = initialized["result"]["pid"]
            old_handle, _ = active_data_socket(data)
            handles.append(old_handle)
            assert (await request_via_socket(old_handle, {"op": "ping"}))["attached"] == 1
            for _ in range(2):
                compatible = await literal("agent-mcp call bridge.json identity")
                output, error = await asyncio.wait_for(compatible.communicate(), 8)
                assert compatible.returncode == 0, error.decode(errors="replace")
                assert output.decode().strip() == "account-b"
            compatible_calls = [
                json.loads(line) for line in Path(environment["FIXTURE_MUTATIONS"]).read_text().splitlines()
            ]
            assert len(compatible_calls) == 2
            assert compatible_calls[0]["pid"] == compatible_calls[1]["pid"]
            assert (await request_via_socket(old_handle, {"op": "ping"}))["sessions"] == 1
            if configured:
                other_root = private / "other-installation"
                before_route = routing.read_table(data / "current", strict=True)
                before_handle = cutover.source_handle_state(data / "current" / "serve.sock")
                await install(old_payload, installation=other_root, expected_refusal=True)
                assert (other_root / "current-version").read_text(encoding="utf-8").strip() == old_generation
                assert routing.read_table(data / "current", strict=True) == before_route
                assert cutover.source_handle_state(data / "current" / "serve.sock") == before_handle
                assert (await send(held, 2, "fixture/state"))["result"]["pid"] == original_pid
            stale = []
            release = private / "release"
            old_python = root / "versions" / old_generation / ".venv" / "Scripts" / "python.exe"
            for mode in ("warm", "fallback", "direct"):
                ready = private / ("ready-" + mode)
                actor_environment = dict(environment, FIXTURE_CREDENTIAL="account-b")
                if mode == "fallback":
                    actor_environment["AGENT_MCP_SERVE_SOCKET"] = str(private / "unavailable.sock")
                actor = await asyncio.create_subprocess_exec(
                    str(old_python), "-c", STALE_CLIENT, str(ready), str(release), mode,
                    cwd=data, env=actor_environment,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    **no_window_kwargs(),
                )
                children.append(actor)
                stale.append(actor)
                deadline = time.monotonic() + 8
                while not ready.is_file():
                    assert time.monotonic() < deadline, "old launch did not reach its private gate"
                    await asyncio.sleep(.02)
            # A torn deployment receipt selects the ordinary fresh-repair path.
            # No interpreter layout or completion receipt is manufactured.
            (root / "deploy-manifest.json").write_text("{", encoding="utf-8")
            new_payload, new_tripwire = payload_copy(private / "b", "new")
            tripwires.append(new_tripwire)
            await install(new_payload)
            new_generation = (root / "current-version").read_text(encoding="utf-8").strip()
            manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
            assert new_generation != old_generation
            assert manifest["runtime_version"] == new_generation
            assert manifest["source"]["version"] == old_manifest["source"]["version"]
            new_handle, owner = active_data_socket(data)
            handles.append(new_handle)
            assert owner["version"] == new_generation
            for _ in range(2):
                same_context = await literal(
                    "agent-mcp call bridge.json identity", credential="account-a", native_shell=True,
                )
                output, error = await asyncio.wait_for(same_context.communicate(), 8)
                assert same_context.returncode == 0, error.decode(errors="replace")
                assert output.decode().strip() == "account-a"
            same_context_calls = [
                json.loads(line) for line in Path(environment["FIXTURE_MUTATIONS"]).read_text().splitlines()
            ][-2:]
            assert same_context_calls[0]["pid"] == same_context_calls[1]["pid"]
            assert (await request_via_socket(new_handle, {"op": "ping"}))["sessions"] == 1
            started = time.monotonic()
            fresh = await literal()
            current = await send(fresh, 1, "initialize")
            record_testsuite_property("native_fresh_bridge_seconds", time.monotonic() - started)
            assert current["result"]["pipeline_code"] == "new"
            assert current["result"]["identity"] == "account-b"
            assert (await request_via_socket(new_handle, {"op": "ping"}))["attached"] == 0
            called = await literal("agent-mcp call bridge.json identity")
            output, error = await asyncio.wait_for(called.communicate(), 8)
            assert called.returncode == 0, error.decode(errors="replace")
            assert output.decode().strip() == "account-b"
            release.write_text("selected generation changed", encoding="utf-8")
            for actor in stale:
                output, error = await asyncio.wait_for(actor.communicate(), 8)
                assert actor.returncode == 1, (output, error)
                assert b"physical generation" in error
            mutations = [
                json.loads(line) for line in Path(environment["FIXTURE_MUTATIONS"]).read_text().splitlines()
            ]
            assert [item["identity"] for item in mutations] == [
                "account-b", "account-b", "account-a", "account-a", "account-b",
            ]
            assert not any(str(item["probe"]).startswith("stale-") for item in mutations)
            assert current["result"]["pid"] != original_pid
            preserved = await send(held, 2, "ping")
            assert preserved["result"]["pipeline_code"] == "old"
            assert preserved["result"]["pid"] == original_pid
            assert preserved["result"]["initialized"] == 1
            assert held.returncode is None and (root / "versions" / old_generation).is_dir()
            assert not any(path.exists() for path in tripwires)
        finally:
            for process in clients:
                if process.stdin and not process.stdin.is_closing():
                    process.stdin.close()
            for process in clients:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(process.wait(), 8)
            for handle in handles:
                with contextlib.suppress(OSError):
                    await request_via_socket(handle, {"op": "shutdown"})
            for process in children:
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
            for task in readers:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*readers, return_exceptions=True)
