"""A proxy connection cancelled while draining (every broker close) must not
leave an orphaned future for asyncio to report as "never retrieved"."""

from __future__ import annotations

import asyncio
import gc

from ssh_manager import proxy


class _Sink:
    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass


class _Proc:
    def __init__(self):
        self.stdin = _Sink()
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.returncode = None
        self.exited = asyncio.Event()

    async def wait(self):
        await self.exited.wait()
        return self.returncode

    def end(self):
        self.returncode = 0
        self.stdout.feed_eof()
        self.stderr.feed_eof()
        self.exited.set()


def test_cancel_mid_drain_leaves_no_unretrieved_future(monkeypatch):
    reports = []

    async def scenario():
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: reports.append(ctx))
        proc = _Proc()

        async def spawn(*args, **kwargs):
            return proc

        async def terminate(process):
            process.end()

        monkeypatch.setattr(proxy.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(proxy, "terminate_ssh_process_tree", terminate)
        broker = proxy._ProxyBroker(["ssh"], None, capability="a" * 64)
        reader = asyncio.StreamReader()
        reader.feed_data(b"a" * 64)
        reader.feed_eof()  # the client side closes first: _serve starts draining
        serving = asyncio.create_task(broker._serve(reader, _Sink()))
        await asyncio.sleep(0.2)
        assert not serving.done()  # still inside the drain window
        serving.cancel()
        try:
            await serving
        except asyncio.CancelledError:
            pass
        assert proc.exited.is_set()  # cleanup still reaped the child
        gc.collect()
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert not [r for r in reports if "never retrieved" in str(r.get("message", ""))]
