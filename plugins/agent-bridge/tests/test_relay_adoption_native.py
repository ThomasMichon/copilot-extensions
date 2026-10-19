"""Three real processes prove exact exit and fresh native singleton grants."""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn
from credential_relay.server import CredentialRelayServer
from credential_relay.registry import TokenRegistry
from credential_relay.sources.injected_token import InjectedTokenSource
from credential_relay.token_gate import RelayTokenGate
from fastapi import FastAPI
from zdd.private_child import OwnedChild
from zdd.private_protocol import (
    Deadline, PeerIdentity, PrivateIPCError, Refusal, read_frame, write_frame,
)
from zdd.private_receiver import Operation
from zdd.routing import Endpoint

from agent_bridge import __version__
from agent_bridge.app import create_app
from agent_bridge.db import Database
from agent_bridge.effective_runtime import ServingRuntimeCapture
from agent_bridge.launch_runtime import executing_executable
from agent_bridge.runtime_controls import RuntimeControls
from agent_bridge.runtime_restore import restore_runtime
from agent_bridge.runtime_transfer import _self, receive_runtime, transfer_runtime
from test_roster_runtime import roster_capture, warm_roster
from test_runtime_controls import publish
from test_runtime_restore import captured as captured
from test_staged_relay import free_port, ping


async def credential(port: int, auth: str) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write((
            "get-azure-token\nresource=https://synthetic.example\nauth=" + auth + "\n\n"
        ).encode())
        await writer.drain()
        try:
            return await asyncio.wait_for(reader.read(), timeout=1)
        except (ConnectionResetError, BrokenPipeError):
            return b""
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionResetError, BrokenPipeError):
            pass


def native_listener(root: Path, label: str):
    if os.name == "nt":
        from zdd.private_windows import WindowsListener

        address = rf"\\.\pipe\relay-adoption-test-{label}-{secrets.token_hex(16)}"
        return address, WindowsListener(address)
    from zdd.private_unix import UnixListener

    root.chmod(0o700)
    address = root / f"{label}.sock"
    return str(address), UnixListener(address)


def connect(address: str, peer: PeerIdentity, deadline: Deadline):
    if os.name == "nt":
        from zdd.private_windows import connect as native_connect

        return native_connect(address, peer, deadline)
    from zdd.private_unix import connect as native_connect

    return native_connect(Path(address), peer, deadline)


def launch(body: str, *arguments: str, **kwargs) -> subprocess.Popen:
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(sys.path)
    return subprocess.Popen(
        [str(executing_executable()), "-c", body, *arguments],
        env=environment, close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        **kwargs,
    )


def predecessor_main() -> None:
    driver_address, driver_json = sys.argv[1:3]
    payload = json.loads(sys.stdin.buffer.readline())
    runtime = restore_runtime(payload)
    os.environ.update(runtime.environment_for_child(os.environ))

    async def scenario() -> None:
        db = Database(runtime.config.db_path)
        relay = runtime.relay
        address = channel = custody = None
        server = None
        with socket.socket() as frontend:
            frontend.bind(("127.0.0.1", 0))
            frontend.listen()
            peer = _self()
            old = Endpoint(
                "127.0.0.1", frontend.getsockname()[1], peer.pid, __version__, 1, peer.start_time,
            )
            runtime.origins.config_root.mkdir()
            publish(runtime.origins.config_root, old)
            address, server = native_listener(runtime.origins.config_root, "predecessor")
            try:
                await relay.start()
                source = FastAPI()
                source.state.config = runtime.config
                source.state.db = db
                source.state.session_manager = runtime.build_manager(db)
                source.state.resolver = runtime.resolver
                source.state.auth_token = runtime.auth_token
                source.state.runtime_origins = runtime.origins
                source.state.credential_relay = relay
                source.state.ready = source.state.topology_ready = True
                warm_roster(source)
                capture = ServingRuntimeCapture(
                    source, runtime.origins, include_resolver=True,
                    include_relay=True, include_roster=True,
                )
                namespace = secrets.token_hex(32)
                child = launch(
                    "from test_relay_adoption_native import candidate_main; candidate_main()",
                    address, json.dumps(peer.__dict__), namespace, json.dumps(old.to_dict()),
                    driver_address, driver_json,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                custody = OwnedChild(child, executing_executable(), Deadline(3))
                # Independent launch custody reaches the coordinator before it accepts.
                print(json.dumps(custody.peer.__dict__), flush=True)
                channel = server.accept(Deadline(10), expected=custody.peer)
                transfer_runtime(
                    channel, custody, capture, capture.capture(),
                    namespace=namespace, deadline=Deadline(10),
                )
                assert await asyncio.to_thread(sys.stdin.buffer.read, 1) == b"x"
            finally:
                await relay.stop()
                db.close()
                if channel is not None:
                    channel.close()
                if custody is not None:
                    custody.close()
                if server is not None:
                    server.close()

    asyncio.run(scenario())


def candidate_main() -> None:
    address, parent_json, namespace, old_json, driver_address, driver_json = sys.argv[1:7]
    parent = PeerIdentity(**json.loads(parent_json))
    driver_peer = PeerIdentity(**json.loads(driver_json))
    old = Endpoint(**json.loads(old_json))
    preparation = Deadline(30)
    channel = connect(address, parent, preparation)
    driver = connect(driver_address, driver_peer, preparation)
    try:
        pending = receive_runtime(channel, parent, namespace=namespace, deadline=preparation)
        os.environ.update(pending.runtime.environment_for_child(os.environ))

        async def scenario() -> None:
            app = create_app(restored_runtime=pending.runtime)
            server = uvicorn.Server(uvicorn.Config(
                app, lifespan="off", access_log=False, log_level="critical",
            ))
            app.state.uvicorn_server = server
            async with app.router.lifespan_context(app):
                pending.bind_predecessor(app, old)
                pending.acknowledge(app)
                with socket.socket() as frontend:
                    frontend.bind(("127.0.0.1", 0))
                    frontend.listen()
                    task = asyncio.create_task(server.serve(sockets=[frontend]))
                    try:
                        while not server.started:
                            preparation.remaining()
                            if task.done():
                                await task
                                raise AssertionError("fixture frontend failed to start")
                            await asyncio.sleep(0.005)
                        controls = RuntimeControls(
                            app, frontend, pending.runtime.origins, pending=pending,
                            predecessor=old,
                        )
                        current = replace(controls.endpoint(), generation=2)
                        publish(pending.runtime.origins.config_root, current, old)

                        async def control(operation: Operation, refusal: bool = False):
                            deadline = Deadline(10)
                            token = controls.register(
                                driver_peer, operation, receiver=current, active=current,
                                deadline=deadline,
                            )
                            write_frame(driver, {"grant": token, "operation": operation.value}, deadline)
                            wire = await asyncio.to_thread(read_frame, driver, deadline)
                            if refusal:
                                with pytest.raises(PrivateIPCError) as caught:
                                    controls.execute(driver, wire)
                                assert caught.value.reason is Refusal.AUTHORITY
                                result = {"refused": True}
                            else:
                                result = controls.execute(driver, wire)
                            write_frame(driver, result, deadline)
                            with pytest.raises(PrivateIPCError):
                                controls.execute(driver, wire)
                            write_frame(driver, {"replay_refused": True}, deadline)

                        await control(Operation.ACTIVATE_RESOURCES)
                        pending.prepare_serving(app, Deadline(3))
                        await control(Operation.ACTIVATE_SERVING)
                        await control(Operation.RESERVE_SINGLETON, refusal=True)
                        assert pending._relay_stage is None
                        write_frame(driver, {
                            "serving": current.to_dict(), "relay_port": pending.runtime.relay.port,
                        }, preparation)
                        # The wire merely coordinates timing. The retained reference proves exit.
                        assert await asyncio.to_thread(read_frame, driver, preparation) == {
                            "predecessor_finished": True,
                        }
                        channel.close()
                        await control(Operation.RESERVE_SINGLETON)
                        await pending.prepare_relay(app)
                        assert await ping(pending.runtime.relay.port) == b""
                        assert await credential(pending.runtime.relay.port, "synthetic-gate") == b""
                        write_frame(driver, {"admission_closed": True}, preparation)
                        await control(Operation.ACTIVATE_SINGLETON)
                        assert await ping(pending.runtime.relay.port) == b"pong\n\n"
                        result = await credential(pending.runtime.relay.port, "synthetic-gate")
                        assert (b"token=" + os.environ["RUNTIME_FIXTURE_SECRET"].encode()) in result
                        assert await credential(pending.runtime.relay.port, "wrong-gate") == b""
                        pending.verify_resources(app)
                        with pytest.raises(PrivateIPCError):
                            controls.register(
                                driver_peer, Operation.ADOPT, receiver=current,
                                active=current, deadline=Deadline(1),
                            )
                        write_frame(driver, {"relay_ready": True, "adopted": False}, preparation)
                        assert await asyncio.to_thread(read_frame, driver, preparation) == {"finish": True}
                    finally:
                        server.should_exit = True
                        await task
            assert not pending.runtime.relay.running
            assert pending._relay_stage._closed and pending._predecessor._closed
            write_frame(driver, {"owned_cleanup": True}, Deadline(2))

        asyncio.run(scenario())
    finally:
        channel.close()
        driver.close()


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="native IPC and exact exit")
def test_real_relay_adoption_after_exact_predecessor_exit(captured, tmp_path):
    source, origins, _capture = captured
    source.state.config.enable_credential_relay = True
    registry = TokenRegistry()
    registry.add("synthetic-gate")
    gate = RelayTokenGate((registry.validate,), (), frozenset())
    source.state.credential_relay = CredentialRelayServer(
        port=free_port(),
        sources=[InjectedTokenSource(["https://synthetic.example"], env_var="RUNTIME_FIXTURE_SECRET")],
        token_validator=gate.validate, token_required_actions=frozenset({"get-azure-token"}),
    )
    origins = replace(origins, relay_policy=True)
    capture = roster_capture((source, origins, _capture))
    driver_address, listener = native_listener(tmp_path, "coordinator")
    process = channel = None
    try:
        process = launch(
            "from test_relay_adoption_native import predecessor_main; predecessor_main()",
            driver_address, json.dumps(_self().__dict__),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        process.stdin.write(json.dumps(capture.capture().private_payload()).encode() + b"\n")
        process.stdin.flush()
        candidate = PeerIdentity(**json.loads(process.stdout.readline()))
        channel = listener.accept(Deadline(10), expected=candidate)

        def exchange(operation: Operation):
            wire = read_frame(channel, Deadline(10))
            assert wire["operation"] == operation.value
            write_frame(channel, wire, Deadline(10))
            result = read_frame(channel, Deadline(10))
            assert read_frame(channel, Deadline(10)) == {"replay_refused": True}
            return result

        assert exchange(Operation.ACTIVATE_RESOURCES)["resources_active"]
        assert exchange(Operation.ACTIVATE_SERVING)["ready"]
        assert exchange(Operation.RESERVE_SINGLETON) == {"refused": True}
        serving = read_frame(channel, Deadline(10))
        assert asyncio.run(ping(serving["relay_port"])) == b"pong\n\n"
        output, error = process.communicate(b"x", timeout=10)
        assert process.returncode == 0, error.decode(errors="replace")
        assert output == b""
        write_frame(channel, {"predecessor_finished": True}, Deadline(2))
        assert exchange(Operation.RESERVE_SINGLETON) == {
            "relay_reserved": True, "relay_ready": False, "adopted": False,
        }
        assert read_frame(channel, Deadline(10)) == {"admission_closed": True}
        assert exchange(Operation.ACTIVATE_SINGLETON) == {"relay_ready": True, "adopted": False}
        assert read_frame(channel, Deadline(10)) == {"relay_ready": True, "adopted": False}
        assert asyncio.run(ping(serving["relay_port"])) == b"pong\n\n"
        write_frame(channel, {"finish": True}, Deadline(2))
        assert read_frame(channel, Deadline(10)) == {"owned_cleanup": True}
    finally:
        if channel is not None:
            channel.close()
        listener.close()
        if process is not None and process.poll() is None:
            process.communicate(b"x", timeout=10)
