"""Retained single-stdio connection custody, independent of replaceable policy."""

from __future__ import annotations

import asyncio
import hmac
import inspect
import json

from .auth import build_injector
from .checkpoint import Binding, CheckpointRefused, data_copy
from .custody_contract import CONTRACT, MAX_APPLICATION, MAX_CONTROL, supports
from .custody_state import CapacityUnavailable, CustodyState, MAX_OUTPUT_BYTES
from .custody_wire import Peer
from .execution_context import SharingUnavailable
from .transports import build_transport


class Link:
    def __init__(self, owner, attachment, reader, writer):
        self.attachment = attachment
        self.peer = Peer(
            reader, writer,
            lambda operation, data: owner.from_backend(self, operation, data),
        )
        self.pump = asyncio.create_task(self.peer.run())
        self.pump.add_done_callback(lambda task: owner.disconnected(self, task))

    async def close(self):
        await self.peer.close()
        await asyncio.gather(self.pump, return_exceptions=True)


class CustodySession:
    """Orchestrate owned streams; the connector must authenticate the backend first.

    No production route constructs this class yet. In particular, accepting an
    arbitrary reader/writer here is not endpoint or physical-generation proof.
    """

    def __init__(self, cfg, write_to_client, *, transport=None, required_trace_bookkeeping=0):
        if type(required_trace_bookkeeping) is not int or required_trace_bookkeeping not in (0, 1):
            raise CheckpointRefused("required checkpoint identity mode is invalid")
        self.cfg, self.write_to_client = cfg, write_to_client
        self._required_trace_bookkeeping = required_trace_bookkeeping
        self.binding = Binding.issue()
        self.identity = self.binding.identity(cfg)
        self._binding_selected = False
        self.state = CustodyState()
        self.transport = transport
        self._opened = False
        self._links = {}
        self._admissions = {}
        self._origin = 0
        self._changed = asyncio.Event()
        self._input_lock = asyncio.Lock()
        self._upstream_lock = asyncio.Lock()
        self._transport_lock = asyncio.Lock()
        self._migration_lock = asyncio.Lock()
        self._early = []
        self._early_bytes = 0
        self._closing = False
        self._input_ended = False
        self._queued_replies = set()
        self._ending_upstream = False

    def _fail(self):
        self.state.unknown()
        self._changed.set()
        for link in tuple(self._links.values()):
            link.peer.writer.close()

    def _unanswered(self):
        replies = {
            item.message["id"] for item in self.state.accepted.values()
            if "method" not in item.message
        }
        return self.state.server_requests.keys() - replies - self._queued_replies

    def client_eof(self, queued_replies=()):
        """Only responses already read or admitted can settle after input EOF."""
        self._input_ended = True
        self._queued_replies = set(queued_replies)
        if self._unanswered():
            self._fail()

    def _reader_done(self, task):
        if not task.cancelled():
            task.exception()
        if not self._ending_upstream:
            self._fail()

    def disconnected(self, link, task):
        if not task.cancelled():
            task.exception()
        if link.attachment is self.state.current and not self._closing:
            self._fail()
        self._changed.set()

    async def _open(self, link, data):
        self.state._known()
        if (
            link.attachment is not self.state.candidate
            or not isinstance(data.get("identity"), str)
            or not hmac.compare_digest(data["identity"], self.identity)
            or self.binding.identity(self.cfg) != self.identity
        ):
            raise ValueError("custody connection context or preparation differs")
        async with self._transport_lock:
            if self._opened:
                return {"retained": True}
            if self.state.current is not None:
                raise ValueError("checkpoint cannot create a missing retained connection")
            if self.transport is None:
                self.transport = build_transport(self.cfg, build_injector(self.cfg))
            self.transport.on_message(self.upstream)
            await asyncio.wait_for(self.transport.start(), self.cfg.timeout)
            reader = getattr(self.transport, "_reader_task", None)
            if reader is not None:
                reader.add_done_callback(self._reader_done)
            self._opened = True
            return {"retained": False}

    async def from_backend(self, link, operation, data):
        attachment = link.attachment
        if type(data.get("epoch")) is not int or data["epoch"] != attachment.epoch:
            raise ValueError("custody attachment epoch differs")
        if operation == "open":
            return await self._open(link, data)
        self.state.authorize(attachment, data["epoch"])
        if operation == "send":
            message = self.state.sending(attachment, data.get("origin"), data.get("message"))
            try:
                await self.transport.send(message)
            except BaseException:
                self._fail()
                raise
            self.state.sent(attachment, message)
            self._changed.set()
            return {"sent": True}
        if operation == "output":
            delivery = self.state.output(attachment, data.get("origin"), data.get("message"))
            if delivery is not None:
                try:
                    result = self.write_to_client(data["message"])
                    if inspect.isawaitable(result):
                        await result
                except BaseException:
                    self._fail()
                    raise
                self.state.delivered(attachment, delivery)
                self._changed.set()
            return {"delivered": True}
        if operation == "settled":
            self.state.settled(attachment, data.get("origin"))
            self._changed.set()
            return {"settled": data["origin"]}
        raise ValueError(f"unknown custody operation: {operation}")

    async def upstream(self, message):
        try:
            async with self._upstream_lock:
                self.state._known()
                if self.state.current is None:
                    message = data_copy(message)
                    size = len(json.dumps(message).encode())
                    if len(self._early) >= MAX_APPLICATION or self._early_bytes + size > MAX_OUTPUT_BYTES:
                        raise ValueError("pre-activation upstream window is full")
                    self._early.append(message)
                    self._early_bytes += size
                    return
                await self._forward_upstream(message)
        except BaseException:
            self._fail()
            raise

    async def _forward_upstream(self, message):
        try:
            message = self.state.upstream(message)
            if self._input_ended and self._unanswered():
                raise ConnectionError("client input is closed; server response cannot arrive")
            link = self._links[self.state.current]
            result = await link.peer.request(
                "upstream", epoch=link.attachment.epoch, message=message,
            )
            if not isinstance(result, dict) or result.get("delivered") is not True:
                raise ConnectionError("upstream delivery outcome is unknown")
        except BaseException:
            self._fail()
            raise
        self._changed.set()

    def _select_binding(self, connect):
        mode = getattr(connect, "checkpoint_trace_bookkeeping", 0)
        if type(mode) is not int or mode not in (0, 1):
            raise CheckpointRefused("backend checkpoint identity support is invalid")
        if self._required_trace_bookkeeping and not mode:
            raise SharingUnavailable("backend cannot provide the required checkpoint identity mode")
        if not self._binding_selected:
            if self.binding.identity(self.cfg) != self.identity:
                raise CheckpointRefused("custody context changed before initial preparation")
            if mode:
                self.binding = Binding(self.binding.session, self.binding.key, mode)
                self.identity = self.binding.identity(self.cfg)
            self._binding_selected = True
        if self.binding.trace_bookkeeping and not mode:
            raise CheckpointRefused("backend cannot import the selected checkpoint identity mode")

    async def prepare(self, connect, snapshot=None):
        """Allocate custody's epoch before requesting authenticated streams."""
        attachment = self.state.prepare()
        link = None
        try:
            reader, writer = await connect(attachment.epoch)
            link = Link(self, attachment, reader, writer)
            self._links[attachment] = link
            self._select_binding(connect)
            result = await link.peer.request(
                "prepare", epoch=attachment.epoch, contract=CONTRACT,
                binding=self.binding.wire(),
                checkpoint=snapshot,
            )
            if (
                not isinstance(result, dict) or not supports(result.get("contract"))
                or result.get("prepared") is not True
                or type(result.get("epoch")) is not int or result["epoch"] != attachment.epoch
            ):
                raise ValueError("incoming backend preparation was not confirmed")
            self.state.prepared(attachment)
            return link
        except BaseException:
            if not self.state.uncertain:
                self.state.discard(attachment)
            if link is not None:
                await link.close()
                if not self.state.uncertain:
                    self._links.pop(attachment)
            raise

    async def commit(self, link):
        self.state.commit(link.attachment)
        try:
            result = await link.peer.request("activate", epoch=link.attachment.epoch)
            if (
                not isinstance(result, dict) or result.get("active") is not True
                or type(result.get("epoch")) is not int
                or result["epoch"] != link.attachment.epoch
            ):
                raise ConnectionError("backend activation outcome is unknown")
            self.state.activated(link.attachment)
            self._changed.set()
        except BaseException:
            self._fail()
            self._changed.set()
            raise

    async def start(self, connect, *, ready=None):
        """Activate before acknowledging attach, then release early upstream."""
        link = await self.prepare(connect)
        async with self._upstream_lock:
            await self.commit(link)
            if ready is not None:
                await ready()
            early, self._early = self._early, []
            self._early_bytes = 0
            for message in early:
                await self._forward_upstream(message)

    def _admitted(self, task, origin):
        self._admissions.pop(task)
        try:
            result = task.result()
            if (
                not isinstance(result, dict) or type(result.get("accepted")) is not int
                or result["accepted"] != origin
            ):
                raise ConnectionError("backend input admission outcome is unknown")
        except BaseException:
            self._fail()
        self._changed.set()

    async def submit(self, message, *, wait=False):
        while True:
            async with self._input_lock:
                self._changed.clear()
                if self._closing:
                    raise ValueError("custody input is closed")
                attachment = self.state.current
                if attachment is None:
                    raise ValueError("custody has no active backend")
                control = isinstance(message, dict) and (
                    "method" not in message or message["method"] == "notifications/cancelled"
                )
                limit = MAX_CONTROL if control else MAX_APPLICATION
                origin = self._origin + 1
                try:
                    if sum(value == control for value in self._admissions.values()) >= limit:
                        raise CapacityUnavailable("custody publication window is full; input not accepted")
                    admitted = self.state.admit(attachment, origin, message)
                except CapacityUnavailable:
                    if not wait:
                        raise
                else:
                    self._origin = origin
                    task = asyncio.create_task(
                        self._links[attachment].peer.request(
                            "input", epoch=attachment.epoch, origin=origin, message=admitted,
                        ),
                    )
                    self._admissions[task] = control
                    task.add_done_callback(lambda done: self._admitted(done, origin))
                    break
            await self._changed.wait()
        result = await asyncio.shield(task)
        self.state._known()
        return result

    async def handover(self, connect):
        """Quiesce, import data and commit; never move live coroutines or replay."""
        async with self._migration_lock:
            old = self._links[self.state.current]
            candidate = None
            async with self._input_lock:
                self.state.fence()
            try:
                while True:
                    while self._admissions:
                        await asyncio.shield(asyncio.gather(*tuple(self._admissions)))
                    await old.peer.request("quiesce", epoch=old.attachment.epoch)
                    async with self._input_lock:
                        async with self._upstream_lock:
                            self._changed.clear()
                            if (
                                not self.state.accepted and not self.state.server_requests
                                and not self.state.deliveries and not self._admissions
                            ):
                                self.state.require_quiescent()
                                await old.peer.request("quiesce", epoch=old.attachment.epoch)
                                snapshot = await old.peer.request(
                                    "checkpoint", epoch=old.attachment.epoch,
                                )
                                candidate = await self.prepare(connect, snapshot)
                                await self.commit(candidate)
                                break
                    await self._changed.wait()
                await old.close()
                self._links.pop(old.attachment)
            except BaseException:
                if self.state.current is old.attachment and not self.state.uncertain:
                    if candidate is not None:
                        self.state.discard(candidate.attachment)
                        await candidate.close()
                        self._links.pop(candidate.attachment)
                    try:
                        result = await old.peer.request("resume", epoch=old.attachment.epoch)
                        if (
                            not isinstance(result, dict) or result.get("active") is not True
                            or type(result.get("epoch")) is not int
                            or result["epoch"] != old.attachment.epoch
                        ):
                            raise ConnectionError("outgoing epoch resumption is unknown")
                        self.state.resumed(old.attachment)
                        self._changed.set()
                    except BaseException:
                        self._fail()
                        raise
                raise

    async def close(self):
        """Only client EOF/lifetime settlement may call this owning teardown."""
        self.client_eof()
        self._closing = True
        self._changed.set()
        try:
            if not self.state.uncertain and self.state.current is not None:
                while self._admissions:
                    await asyncio.shield(asyncio.gather(*tuple(self._admissions)))
                link = self._links[self.state.current]
                await link.peer.request("quiesce", epoch=link.attachment.epoch)
        except BaseException:
            self._fail()
            raise
        finally:
            try:
                if self.transport is not None:
                    self._ending_upstream = True
                    await self.transport.end_input()
            finally:
                try:
                    await asyncio.gather(*(link.close() for link in tuple(self._links.values())))
                finally:
                    if self.transport is not None:
                        await self.transport.aclose()
