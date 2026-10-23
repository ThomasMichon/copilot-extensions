"""Installed standalone process over real Gateway TLS/PoP, synthetic provider inputs."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from agent_pull_requests_service.hosting import ready

_EMBEDDED = """
import asyncio,sys
from pathlib import Path
from agent_pull_requests_service.config import load_config
from agent_pull_requests_service.hosting import run
async def main():
    stop=asyncio.Event()
    async def input_stop():
        await asyncio.to_thread(sys.stdin.readline)
        stop.set()
    reader=asyncio.create_task(input_stop())
    try:
        await run(load_config(Path(sys.argv[1])),stop=stop)
    finally:
        if not reader.done():
            reader.cancel()
        await asyncio.gather(reader,return_exceptions=True)
asyncio.run(main())
"""


def test_installed_host_process_register_observe_reconnect_restart_and_ack(tmp_path, monkeypatch):
    # The existing Fleet fixture owns issuer, policy and TLS; do not add a fake Gateway.
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "fleet-manager" / "tests"))
    from gateway_service_transport_fixtures import eventually, running

    async def scenario():
        path = tmp_path / "host.json"
        state = tmp_path / "replay"
        status = state / "service-status.json"
        calls = tmp_path / "native-calls"
        proof, access = tmp_path / "service.pem", tmp_path / "access"
        snapshot = {
            "schema": "agent-worktrees.pr-snapshot", "version": 1,
            "repo": "example/project", "pr": 1, "provider": "github",
            "pr_state": "closed", "merged": True, "head_sha": "head", "base_ref": "main",
            "mergeable": True, "checks_state": "success", "title": "Fixture", "draft": False,
        }
        native = (
            "import json,pathlib,sys;"
            "assert sys.argv[1:6]==['pr-watch','snapshot','example/project','1','--json'];"
            "assert sys.argv[6]=='--credential-command';"
            f"pathlib.Path({str(calls)!r}).open('a').write('read\\n');"
            f"print(json.dumps({snapshot!r}))"
        )
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}

        async def command(*arguments):
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-m", "agent_pull_requests_service", *arguments,
                cwd=tmp_path, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                **flags,
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 15)
                return process.returncode, stdout, stderr
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

        async def launch():
            # Windows cannot deliver POSIX SIGTERM. Its control uses the public
            # foreground embedding stop event, not TerminateProcess as "drain".
            arguments = (
                ["-I", "-c", _EMBEDDED, str(path)] if os.name == "nt"
                else ["-I", "-m", "agent_pull_requests_service", "serve", "--config", str(path)]
            )
            return await asyncio.create_subprocess_exec(
                sys.executable, *arguments, cwd=tmp_path,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE, **flags,
            )

        async def close(process):
            if process.returncode is None:
                if os.name == "nt":
                    process.stdin.write(b"stop\n")
                    await process.stdin.drain()
                else:
                    process.send_signal(signal.SIGTERM)
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
                if stderr:
                    print(stderr.decode("utf-8", errors="replace")[-2048:])
                assert process.returncode == 0, (stdout, stderr)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()

        async with running(tmp_path) as rig:
            proof.write_bytes(rig.h.keys[1].get_op_key("sign").private_bytes(
                serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ))
            access.write_text(rig.access("service-a", 1))
            config = {
                "schema": "agent-pull-requests.service", "version": 1,
                "root": str(state), "namespace": "installation-a",
                "fleet": "fleet-a", "gateway": "gateway-a", "slot": "service-a",
                "gateway_origin": rig.origin, "gateway_ca": rig.ca,
                "proof_key_file": str(proof), "access_token_file": str(access),
                "bindings": [{
                    "principal": "caller-a", "target": "project", "consumer": "dispatch-a",
                    "account": "account-a", "repo": "example/project",
                }],
                "observations": [{
                    "account": "account-a", "repo": "example/project", "command": sys.executable,
                    "prefix": [sys.executable, "-I", "-c", native],
                    # The native fixture asserts transport of the required reference;
                    # it does not claim a live credential-provider exchange.
                    "credential_command": [sys.executable, "-I", "-m", "fleet_manager"],
                }],
                "interval": 1, "operation_seconds": 60, "drain_timeout": 5,
            }
            path.write_text(json.dumps(config))
            assert (await command("provision", "--config", str(path)))[0] == 0
            assert (await command("provision", "--config", str(path)))[0] == 1
            owner = await launch()
            try:
                await eventually(lambda: ready(status), timeout=10)
                assert (await command("ready", "--status", str(status)))[0] == 0
                # A second actual host cannot adopt or withdraw this writer.
                assert (await command("serve", "--config", str(path)))[0] == 1
                assert ready(status)

                async def send(operation, payload=None):
                    for _ in range(100 if operation == "pending" else 1):
                        rig.h.clock.value = int(time.time())
                        target = "/api/v1/watch/project/" + operation
                        async with rig.session.post(
                            rig.origin + target, headers=await rig.auth(target, "POST"),
                            json=payload or {},
                        ) as response:
                            value = await response.json()
                            if not (
                                operation == "pending" and response.status == 503
                                and value == {"error": "watch_store_busy"}
                            ):
                                assert response.status == 200, value
                                return value
                        # The real nonblocking store lock can be held by observation.
                        # Retry only this bounded read, never an ambiguous mutation.
                        await asyncio.sleep(0.05)
                    raise AssertionError("pending read remained busy")

                original = {
                    "request_id": "original", "issued_at": time.time(),
                    "number": 1, "until": ["merged"],
                }
                receipt = await send("register", original)
                assert await send("register", original) == receipt
                for _ in range(100):
                    pending = await send("pending")
                    if pending["events"]:
                        break
                    await asyncio.sleep(0.05)
                assert len(pending["events"]) == 1
                event = pending["events"][0]
                assert event["registration_id"] == receipt["registration_id"]
                assert not event["timed_out"]
                settled_reads = calls.read_text()
                assert settled_reads.splitlines() and set(settled_reads.splitlines()) == {"read"}
                persisted = (state / "watch-replay.json").read_bytes()
                assert access.read_bytes() not in persisted
                assert b"PRIVATE KEY" not in persisted
                channel = rig.state.channels["service-a"]
                # Fresh token contents are consumed by the actual reattachment.
                access.write_text(rig.access("service-a", 1))
                await channel.close()
                await eventually(
                    lambda: "service-a" in rig.state.channels
                    and rig.state.channels["service-a"] is not channel,
                    timeout=10,
                )
                assert await send("pending") == pending
            finally:
                await close(owner)
            successor = await launch()
            try:
                await eventually(lambda: ready(status), timeout=10)
                # A stale predecessor snapshot can pass briefly; require actual attachment too.
                await eventually(lambda: "service-a" in rig.state.channels, timeout=10)
                assert await send("pending") == pending
                completion = {
                    "request_id": original["request_id"], "issued_at": original["issued_at"],
                    "registration_id": receipt["registration_id"], "event_id": event["event_id"],
                }
                ack = await send("acknowledge", completion)
                assert ack["status"] == "acknowledged"
                assert await send("acknowledge", completion) == ack
                assert await send("pending") == {"events": [], "more": False}
                assert calls.read_text() == settled_reads
                assert rig.backends[0].requests == []
            finally:
                await close(successor)

    asyncio.run(scenario())
