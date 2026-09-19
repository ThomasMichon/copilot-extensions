"""Real PTY ownership, concurrent observation, and leader-exited retirement."""

import asyncio
import os
import sys
from pathlib import Path

import pytest

from agent_bridge.session_host.client import SessionHostClient, TerminalOwnershipError
from agent_bridge.session_host.host import SessionHost
from agent_bridge.session_host.terminal import spawn_terminal

pytestmark = [
    pytest.mark.skipif(not sys.platform.startswith("linux"), reason="real Linux PTY contract"),
    pytest.mark.contract("native.terminal-ownership"),
]


@pytest.mark.asyncio
async def test_one_writer_many_observers_explicit_takeover_and_read_only_fence(tmp_path):
    child = await spawn_terminal(
        [sys.executable, "-u", "-c", "import sys;print('READY');\nfor line in sys.stdin: print('OUTPUT:'+line,flush=True)"],
        str(tmp_path), dict(os.environ),
    )
    host = SessionHost(child, nonce="fixture-nonce", terminal=True)
    port = await host.serve()
    clients = []

    async def attach(**options):
        client = await SessionHostClient.connect(port=port)
        clients.append(client)
        await client.attach(nonce=b"fixture-nonce", **options)
        return client

    try:
        writer = await attach()
        observers = [await attach(observer=True) for _ in range(4)]
        with pytest.raises(TerminalOwnershipError, match="writer_busy"):
            await attach()
        await writer.write(b"hello\n")

        async def output(client, expected):
            data = b""
            async for _, frame in client.frames():
                data += frame
                if expected in data:
                    return
            pytest.fail("terminal ended before output")

        await asyncio.wait_for(asyncio.gather(*(output(c, b"OUTPUT:hello") for c in [writer, *observers])), 3)
        successor = await attach(takeover=True)
        with pytest.raises(TerminalOwnershipError, match="writer_revoked"):
            await output(writer, b"unreachable")
        with pytest.raises(TerminalOwnershipError, match="writer_busy"):
            await attach()
        await observers[0].write(b"not allowed\n")
        with pytest.raises(TerminalOwnershipError, match="read_only"):
            await output(observers[0], b"unreachable")
        await successor.write(b"successor\n")
        await asyncio.wait_for(output(observers[1], b"OUTPUT:successor"), 3)
        await successor.close()
        await asyncio.sleep(.05)
        reconnected = await attach()
        await reconnected.write(b"reconnected\n")
        await asyncio.wait_for(output(observers[2], b"OUTPUT:reconnected"), 3)
    finally:
        child.kill()
        await child.wait()
        for client in clients:
            await client.close()
        await host.close()
        child.close()


@pytest.mark.asyncio
async def test_exited_leader_keeps_owned_group_authority_until_explicit_retirement(tmp_path):
    marker = tmp_path / "background.pid"
    descendant = (
        "import os,signal,time\nsignal.signal(signal.SIGHUP,signal.SIG_IGN)\n"
        f"open({str(marker)!r},'w').write(str(os.getpid()))\ntime.sleep(60)\n"
    )
    program = (
        "import subprocess,time\n"
        f"subprocess.Popen({[sys.executable, '-c', descendant]!r})\n"
        f"from pathlib import Path\nwhile not Path({str(marker)!r}).exists(): time.sleep(.01)\n"
    )
    child = await spawn_terminal([sys.executable, "-c", program], str(tmp_path), dict(os.environ))
    host = SessionHost(child, nonce="fixture-nonce", terminal=True)
    port = await host.serve()
    control = None
    try:
        assert await asyncio.wait_for(child.wait(), 3) == 0
        background = int(marker.read_text())
        os.kill(background, 0)
        assert Path(f"/proc/{child.pid}/stat").read_text().rpartition(")")[2].split()[0] == "Z"
        control = await SessionHostClient.connect(port=port)
        await asyncio.wait_for(control.retire(child_pid=child.pid, nonce=b"fixture-nonce"), 3)
        for _ in range(100):
            try:
                state = Path(f"/proc/{background}/stat").read_text().rpartition(")")[2].split()[0]
            except FileNotFoundError:
                break
            if state == "Z":
                break
            await asyncio.sleep(.01)
        else:
            pytest.fail("leader-exited native descendant survived retirement")
    finally:
        if not host._closing:
            child.kill()
        if control:
            await control.close()
        await host.close()
        child.close()
