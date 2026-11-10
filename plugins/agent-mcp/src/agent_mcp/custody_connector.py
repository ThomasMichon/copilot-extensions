"""Read-only admission of the current backend, independent of keeper generation."""

from __future__ import annotations

import asyncio
import hmac
import json
import math
from pathlib import Path

from . import execution_context, incoming, ipc, sockio
from .checkpoint import Binding
from .custody_contract import CONTRACT, supports
from .custody_wire import MAX_FRAME
from .request_context import capture_context, supports_context


class BackendConnector:
    """Connect only to a selected, authenticated backend in a known installation.

    The installation root comes from adoption admission, not keeper metadata or
    an untrusted readiness reply. This connector never launches or repairs hosts.
    """

    def __init__(self, handle, bridge, runtime_root, cfg, *, timeout=10):
        root = Path(runtime_root)
        if not root.is_absolute() or not root.is_dir():
            raise ValueError("backend installation root must be an existing absolute directory")
        if (
            not isinstance(bridge, str) or not bridge
            or type(timeout) not in (int, float)
            or not math.isfinite(timeout) or not 0 < timeout <= 60
        ):
            raise ValueError("backend connector requires a bridge and bounded timeout")
        self.handle = sockio.absolute_handle(handle)
        self.bridge, self.root, self.cfg = bridge, root.resolve(), cfg
        self.timeout = timeout
        self.generation = None
        self.context = capture_context(bridge)
        self._binding = Binding.issue()
        self._identity = self._binding.identity(cfg)

    def _context(self):
        if not hmac.compare_digest(self._binding.identity(self.cfg), self._identity):
            raise execution_context.SharingUnavailable("custody caller execution context changed")

    def _selected(self, reply):
        from .cutover import _require_selected
        from .source_serving import installation_matches

        generation = reply.get("version")
        if (
            reply.get("ok") is not True or reply.get("pong") is not True
            or not isinstance(generation, str) or not generation
            or not installation_matches(reply, self.root)
            or not incoming.supports(reply.get("fresh_admission"))
            or not supports(reply.get("custody_backend"))
            or not supports_context(reply)
            or reply.get("ready") is not True or reply.get("draining") is not False
        ):
            raise incoming.AdmissionRefused("current custody backend did not qualify")
        _require_selected((generation, None, self.root))
        return generation

    @staticmethod
    async def _exchange(reader, writer, token, request):
        if token is not None:
            request = {**request, "token": token}
        writer.write((json.dumps(request, allow_nan=False) + "\n").encode())
        await writer.drain()
        line = await reader.readline()
        if not line or len(line) > MAX_FRAME or not line.endswith(b"\n"):
            raise incoming.AdmissionRefused("backend admission reply is missing or oversized")
        reply = json.loads(line)
        if not isinstance(reply, dict):
            raise incoming.AdmissionRefused("backend admission reply must be an object")
        return reply

    async def __call__(self, epoch):
        """Return owned streams after admission; no application has been accepted."""
        if type(epoch) is not int or epoch <= 0:
            raise ValueError("backend attachment requires a positive integer epoch")
        self._context()
        return await asyncio.wait_for(self._connect(epoch), self.timeout)

    async def _connect(self, epoch):
        reader, writer, token = await ipc._connect(self.handle, limit=MAX_FRAME)
        try:
            if not ipc._HAS_AF_UNIX and (not isinstance(token, str) or not token):
                raise incoming.AdmissionRefused("backend endpoint authentication is missing")
            reply = await self._exchange(reader, writer, token, {"op": "ping"})
            generation = self._selected(reply)
            fields = execution_context.fields_for_peer(self.cfg, reply)
            self._context()
            ack = await self._exchange(
                reader, writer, token,
                {"op": "backend", "bridge": self.bridge, "epoch": epoch,
                 "contract": CONTRACT, "generation": generation, "runtime_root": str(self.root),
                 "configuration_context": self.context, **fields},
            )
            if (
                ack.get("ok") is not True or not supports(ack.get("contract"))
                or ack.get("generation") != generation
                or ack.get("runtime_root") != str(self.root)
                or type(ack.get("epoch")) is not int or ack["epoch"] != epoch
            ):
                raise incoming.AdmissionRefused("backend attachment admission was not confirmed")
            self._context()
            from .cutover import _require_selected

            _require_selected((generation, None, self.root))
            self.generation = generation
            return reader, writer
        except BaseException:
            await ipc.aclose_writer(writer)
            raise
