"""Independent terminal observers and an explicitly acquired writer lease."""

from __future__ import annotations

import asyncio

from . import protocol as proto


class TerminalFronts:
    def __init__(self, host) -> None:
        self.host = host
        self.writer = None
        self.fronts = {}

    def changed(self) -> None:
        for wake in self.fronts.values():
            wake.set()

    async def serve(self, reader, writer, kind, last_acked, hello) -> None:
        from .host import _Front

        observer = kind == proto.MsgType.OBSERVE
        if last_acked > self.host.max_seq:
            await proto.write_message(writer, proto.MsgType.ERROR, b"invalid_cursor")
            return
        incumbent = self.writer
        if not observer and incumbent is not None and not incumbent.closed:
            if kind != proto.MsgType.TAKEOVER:
                await proto.write_message(writer, proto.MsgType.ERROR, b"writer_busy")
                return
            # Fence before the first await: queued input from the old writer
            # cannot regain ownership while the revocation is being delivered.
            incumbent.closed = True
            self.writer = None
            incumbent.writer.write(proto.encode(proto.MsgType.ERROR, b"writer_revoked"))
            incumbent.writer.close()
        front = _Front(reader, writer, last_acked + 1)
        if not observer:
            self.writer = front
        wake = asyncio.Event()
        self.fronts[front] = wake
        pump = None
        try:
            await proto.write_message(writer, proto.MsgType.HELLO, hello)

            async def output():
                while not front.closed:
                    wake.clear()
                    minimum = next(iter(self.host._frames), self.host.max_seq + 1)
                    front.next_seq = max(front.next_seq, minimum)
                    await asyncio.wait_for(self.host._flush_front(front), 5)
                    if self.host._terminal_eof:
                        await proto.write_message(
                            writer, proto.MsgType.LIVENESS,
                            proto.pack_liveness(False, self.host.child_exit_code or 0),
                        )
                        return
                    await wake.wait()

            pump = asyncio.create_task(output())
            while not front.closed:
                incoming = asyncio.create_task(proto.read_message(reader))
                try:
                    done, _ = await asyncio.wait([incoming, pump], return_when=asyncio.FIRST_COMPLETED)
                    if pump in done:
                        pump.result()
                        return
                    message = incoming.result()
                finally:
                    incoming.cancel()
                    await asyncio.gather(incoming, return_exceptions=True)
                if message is None or front.closed:
                    return
                kind, payload = message
                if kind == proto.MsgType.DETACH:
                    return
                if kind == proto.MsgType.ACK:
                    continue  # each observer owns its cursor; shared replay is bounded
                if observer or self.writer is not front:
                    await proto.write_message(writer, proto.MsgType.ERROR, b"read_only")
                    return
                if kind == proto.MsgType.WRITE:
                    await self.host._on_write(payload)
                elif kind == proto.MsgType.RESIZE:
                    self.host._child.resize(*proto.unpack_resize(payload))
                else:
                    await proto.write_message(writer, proto.MsgType.ERROR, b"unsupported_control")
                    return
        finally:
            front.closed = True
            self.fronts.pop(front, None)
            if self.writer is front:
                self.writer = None
            if pump is not None:
                pump.cancel()
                await asyncio.gather(pump, return_exceptions=True)
            writer.close()
