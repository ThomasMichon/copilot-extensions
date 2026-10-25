"""Opt-in actual disposable-account OpenSSH forwarding, never production enrollment."""

import asyncio
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import pytest

from agent_ssh_service.config import ServiceConfig
from agent_ssh_service.relay import NativeRelay
from agent_ssh_service.store import DirectoryStore

from conftest import document
from test_relay_native import wait_ready

pytestmark = pytest.mark.skipif(
    sys.platform != "linux"
    or os.environ.get("SSH_SERVICE_NATIVE_ACCEPTANCE") != "1",
    reason="Select native acceptance only in an explicitly disposable Linux account",
)


def free_port():
    import socket

    with socket.socket() as endpoint:
        endpoint.bind(("127.0.0.1", 0))
        return endpoint.getsockname()[1]


def generate(root, name):
    from agent_procutil.owned_process import run_caller_owned

    result = run_caller_owned(
        [shutil.which("ssh-keygen"), "-q", "-t", "ed25519", "-N", "", "-f", str(root / name)],
        timeout=10, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    assert result.returncode == 0
    return " ".join((root / (name + ".pub")).read_text().split()[:2])


@contextmanager
def client(root, selected, name, *forwards):
    from agent_procutil import no_window_kwargs
    from agent_procutil.native_child import NativeChildCustody

    with (root / (name + ".log")).open("w+b") as output:
        process = subprocess.Popen([
            shutil.which("ssh"), "-F", "/dev/null", "-N", "-T", "-v",
            "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=5",
            "-o", "ExitOnForwardFailure=yes", "-o", "ControlMaster=no",
            "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "UserKnownHostsFile=" + str(root / "known_hosts"),
            "-i", str(root / name), "-p", str(selected["port"]),
            *forwards, selected["user"] + "@127.0.0.1",
        ], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=output,
            cwd=root, start_new_session=True, **no_window_kwargs())
        custody = NativeChildCustody(process.pid)
        try:
            yield process, output
        finally:
            process.returncode = custody.retire()


async def authenticated(process, output, *, refused=False):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        output.seek(0)
        text = output.read(65536).decode("utf-8", errors="replace")
        exited = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if refused:
            if exited is not None:
                assert "remote port forwarding failed" in text, text
                return
        else:
            assert exited is None, text
            if "Authenticated to " in text:
                return
        await asyncio.sleep(0.02)
    pytest.fail("Native SSH did not reach its finite authentication outcome: " + text)


async def payload(port, expected):
    reader, writer = await asyncio.wait_for(asyncio.open_connection("127.0.0.1", port), 5)
    try:
        writer.write(expected)
        await writer.drain()
        assert await asyncio.wait_for(reader.readexactly(len(expected)), 5) == expected
    finally:
        writer.close()
        await writer.wait_closed()


async def test_real_distinct_keys_reverse_forward_peer_route_and_denied_bind():
    import pwd

    assert os.geteuid() != 0, "Native acceptance must run as its actual non-root login account"
    assert all(shutil.which(name) for name in ("ssh", "sshd", "ssh-keygen"))
    assert Path("/run/sshd").is_dir()
    account = pwd.getpwuid(os.geteuid())
    # StrictModes checks the complete authorization path. This explicitly selected
    # disposable account owns the finite test directory; no existing SSH file is read.
    with tempfile.TemporaryDirectory(prefix=".ce-ssh-native-", dir=account.pw_dir) as temporary:
        root = Path(temporary)
        state = root / "state"
        state.mkdir(mode=0o700)
        value = document(state)
        ports = []
        while len(set(ports)) != 6:
            ports = [free_port() for _ in range(6)]
        for index, row in enumerate(value["bindings"]):
            row["reverse_port"] = ports[index]
        selected = {
            "sshd": str(Path(shutil.which("sshd")).resolve()),
            "ssh_keygen": str(Path(shutil.which("ssh-keygen")).resolve()),
            "host_key_file": str(root / "relay-host"),
            "host_public_key": generate(root, "relay-host"),
            "listen_host": "127.0.0.1", "port": ports[2], "user": account.pw_name,
        }
        value["relay"] = selected
        store = DirectoryStore(ServiceConfig.from_dict(value))
        for index, row in enumerate(value["bindings"]):
            store.enroll(row["principal"], {
                "request_id": "native-join-" + str(index),
                "machine": row["machine"], "environment": row["environment"], "generation": 1,
                "public_key": generate(root, "login-" + str(index)),
                "host_key": generate(root, "target-host-" + str(index)),
                "relay_public_key": generate(root, "relay-" + str(index)),
            })
        (root / "known_hosts").write_text(
            f'[127.0.0.1]:{ports[2]} {selected["host_public_key"]}\n', encoding="ascii",
        )
        relay = NativeRelay(store)
        stop, ready = asyncio.Event(), asyncio.Event()
        owner = asyncio.create_task(relay.run(stop, ready))

        async def echo(reader, writer):
            try:
                data = await reader.read(256)
                writer.write(data)
                await writer.drain()
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_server(echo, "127.0.0.1", ports[5])
        try:
            await wait_ready(owner, ready)
            with client(root, selected, "relay-0",
                        "-R", f"127.0.0.1:{ports[0]}:127.0.0.1:{ports[5]}") as first:
                await authenticated(*first)
                with client(root, selected, "relay-1",
                            "-L", f"127.0.0.1:{ports[3]}:127.0.0.1:{ports[0]}") as second:
                    await authenticated(*second)
                    await payload(ports[3], b"owned-native-relay-payload")
                with client(root, selected, "relay-1",
                            "-R", f"127.0.0.1:{ports[4]}:127.0.0.1:{ports[5]}") as denied:
                    await authenticated(*denied, refused=True)
        finally:
            stop.set()
            await asyncio.wait_for(owner, 10)
            server.close()
            await server.wait_closed()
        assert relay.process.returncode is not None
