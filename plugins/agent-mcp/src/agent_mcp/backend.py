"""Replaceable policy execution over an authenticated custody attachment."""

from __future__ import annotations

import asyncio
import contextvars
import hmac

from . import execution_context, incoming
from .checkpoint import Binding, CheckpointRefused
from .config import load_config
from .custody_contract import CONTRACT, MAX_APPLICATION, MAX_CONTROL, MAX_SEQUENCE, supports
from .custody_wire import Peer
from .request_context import request_root
from .session import BridgeSession
from .transports.base import Transport

_origin = contextvars.ContextVar("custody_input_sequence", default=0)


def capability(server):
    """Advertise only from an admitted physical backend, never a keeper."""
    if (
        server._runtime_root is not None
        and incoming.launch_generation() == server._version
        and not server._draining and not server._closing
        and incoming.accepts(server, {})
    ):
        return {"custody_backend": CONTRACT}
    return {}


class RetainedTransport(Transport):
    """Address one existing custody connection; never own its process or pipes."""

    def __init__(self, cfg, binding, epoch, peer) -> None:
        self.cfg, self.binding, self.epoch, self.peer = cfg, binding, epoch, peer
        self._emit = None

    async def start(self) -> None:
        result = await self.peer.request(
            "open", epoch=self.epoch, identity=self.binding.identity(self.cfg),
        )
        if not isinstance(result, dict) or type(result.get("retained")) is not bool:
            raise ValueError("custody did not confirm the retained connection")

    async def send(self, message: dict) -> None:
        result = await self.peer.request(
            "send", epoch=self.epoch, origin=_origin.get(), message=message,
        )
        if not isinstance(result, dict) or result.get("sent") is not True:
            raise ConnectionError("upstream send outcome is unknown; no replay")

    async def aclose(self) -> None:
        # Detaching policy execution has no authority to close retained pipes.
        return None


class BackendSession:
    """One epoch on one authenticated link; a lost receipt never causes replay."""

    def __init__(self, reader, writer, request, qualify) -> None:
        self.request, self.qualify = request, qualify
        self.epoch = request["epoch"]
        self.peer = Peer(reader, writer, self.handle)
        self.session = None
        self.transport = None
        self.state = "new"
        self.committed = False
        self.accepted = {}
        self._settlers = set()
        self._last_origin = 0
        self._application = 0
        self._control = 0
        self._quiescence = asyncio.Lock()

    async def output(self, message):
        result = await self.peer.request(
            "output", epoch=self.epoch, origin=_origin.get(), message=message,
        )
        if not isinstance(result, dict) or result.get("delivered") is not True:
            raise ConnectionError("client delivery outcome is unknown; no replay")

    async def _prepare(self, data):
        if self.state != "new" or not supports(data.get("contract")):
            raise ValueError("unsupported or repeated custody preparation")
        raw = data.get("binding")
        if not isinstance(raw, dict) or set(raw) != {"session", "key"}:
            raise CheckpointRefused("private checkpoint binding is required")
        binding = Binding(**raw)
        cfg = self.qualify()
        binding.identity(cfg)
        self.state = "preparing"
        self.transport = RetainedTransport(cfg, binding, self.epoch, self.peer)
        self.session = BridgeSession(
            cfg, self.output, transport=self.transport,
            checkpoint_binding=binding, resume=data.get("checkpoint"),
        )
        try:
            await self.session.start()
        except BaseException:
            self.state = "failed"
            raise
        self.state = "prepared"
        return {"contract": CONTRACT, "prepared": True, "epoch": self.epoch}

    async def _settle(self, task, sequence, control):
        await task
        result = await self.peer.request("settled", epoch=self.epoch, origin=sequence)
        if (
            not isinstance(result, dict) or type(result.get("settled")) is not int
            or result["settled"] != sequence
        ):
            raise ConnectionError("input settlement outcome is unknown; no replay")
        self.accepted.pop(sequence)
        if control:
            self._control -= 1
        else:
            self._application -= 1

    def _settled(self, task):
        self._settlers.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.state = "failed"
            self.peer.writer.close()

    def _input(self, data):
        message, sequence = data.get("message"), data.get("origin")
        if (
            not isinstance(message, dict)
            or type(sequence) is not int
            or sequence <= self._last_origin
        ):
            raise ValueError("invalid or duplicate custody input")
        control = (
            "method" not in message or message.get("method") == "notifications/cancelled"
        )
        if not self.committed or (
            self.state != "active"
            and not (self.state in {"quiescing", "quiescent"} and control)
        ):
            raise ValueError("backend epoch is not admitting this input")
        if (self._control >= MAX_CONTROL if control else self._application >= MAX_APPLICATION):
            raise ValueError("backend input window is full; input not accepted")
        self._last_origin = sequence
        if control:
            self._control += 1
            if self.state == "quiescent":
                self.state = "quiescing"
        else:
            self._application += 1
        token = _origin.set(sequence)
        try:
            task = self.session.submit(message)
            self.accepted[sequence] = task
            settled = asyncio.create_task(self._settle(task, sequence, control))
            self._settlers.add(settled)
            settled.add_done_callback(self._settled)
        finally:
            _origin.reset(token)
        return {"accepted": sequence}

    async def _quiesce(self):
        async with self._quiescence:
            if not self.committed or self.state not in {"active", "quiescing", "quiescent"}:
                raise ValueError("backend epoch cannot quiesce")
            self.state = "quiescing"
            while self._settlers:
                tasks = tuple(self._settlers)
                await asyncio.shield(asyncio.gather(*tasks))
            await self.session.quiesce()
            if self.accepted or self.state != "quiescing":
                raise ValueError("backend epoch did not settle")
            self.state = "quiescent"
            return {"quiescent": True}

    async def handle(self, operation, data):
        if type(data.get("epoch")) is not int or data["epoch"] != self.epoch:
            raise ValueError("backend attachment epoch differs")
        if operation == "prepare":
            return await self._prepare(data)
        if self.session is None or self.state in {"new", "preparing", "failed", "closed"}:
            raise ValueError("backend epoch is not prepared")
        if operation == "activate":
            if self.state != "prepared" or self.committed:
                raise ValueError("backend epoch cannot be activated twice")
            cfg = self.qualify()
            if self.transport.binding.identity(cfg) != self.session._checkpoint_identity:
                raise CheckpointRefused("execution context changed before epoch activation")
            self.committed, self.state = True, "active"
            return {"active": True, "epoch": self.epoch}
        if operation == "input":
            return self._input(data)
        if operation == "upstream":
            if not self.committed or self.state not in {"active", "quiescing", "quiescent"}:
                raise ValueError("backend upstream delivery is fenced")
            if not isinstance(data.get("message"), dict):
                raise ValueError("invalid upstream message")
            await self.transport._emit_message(data["message"])
            return {"delivered": True}
        if operation == "quiesce":
            return await self._quiesce()
        if operation == "checkpoint":
            if self.state != "quiescent" or self.accepted:
                raise ValueError("backend epoch has not quiesced")
            return self.session.checkpoint()
        if operation == "resume":
            if self.state not in {"active", "quiescing", "quiescent"} or not self.committed:
                raise ValueError("backend epoch cannot resume")
            if self.transport.binding.identity(self.session.cfg) != self.session._checkpoint_identity:
                raise CheckpointRefused("execution context changed before epoch resumption")
            self.state = "active"
            return {"active": True, "epoch": self.epoch}
        raise ValueError(f"unknown backend operation: {operation}")

    async def run(self):
        try:
            await self.peer.run()
        finally:
            self.state = "closed"
            tasks = (*self.accepted.values(), *self._settlers)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.session is not None:
                await self.session.aclose()


async def run_backend(server, request, reader, writer):
    """Prepare only the selected physical backend, never an old capable keeper."""
    token = request.get("token")
    if server._token is not None and (
        not isinstance(token, str) or not hmac.compare_digest(token, server._token)
    ):
        await server._send(writer, {"ok": False, "error": "unauthorized"})
        return

    def qualify():
        if (
            not isinstance(request.get("generation"), str) or not request["generation"]
            or not isinstance(request.get("runtime_root"), str) or not request["runtime_root"]
            or incoming.launch_generation() != request["generation"]
            or not incoming.accepts(server, request)
            or server._draining or server._closing
        ):
            raise incoming.AdmissionRefused("physical backend generation is not admitted")
        return execution_context.load_config(
            request["bridge"], request, load_config, repository_root=request_root(request),
        )

    try:
        if (
            not supports(request.get("contract"))
            or not isinstance(request.get("bridge"), str) or not request["bridge"]
            or type(request.get("epoch")) is not int or not 0 < request["epoch"] <= MAX_SEQUENCE
        ):
            raise ValueError("unsupported custody attachment")
        qualify()
    except Exception as exc:
        await server._send(writer, execution_context.config_failure(exc))
        return
    server._attached += 1
    server._touch()
    try:
        await server._send(
            writer,
            {"ok": True, "contract": CONTRACT, "generation": server._version,
             "runtime_root": str(server._runtime_root), "epoch": request["epoch"]},
        )
        await BackendSession(reader, writer, request, qualify).run()
    finally:
        server._attached -= 1
        server._touch()
