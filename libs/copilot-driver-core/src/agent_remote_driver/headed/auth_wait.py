"""Opt-in, launch-owned source authorization for a headed native session."""

from __future__ import annotations

import asyncio
import json
import math
import secrets
import time
import uuid

CONTRACT = "copilot-driver.headed-auth-wait"
VERSION = 1
MAX_BUDGET_SECONDS = 3600


class AuthWaitReceiver:
    """Keep revocable authorization separate from irreversible control holds.

    The receiving caller supplies source verification; this endpoint never reads
    credentials or infers authorization from a file, process or package version.
    """

    def __init__(self, session_id: str, config: dict) -> None:
        if not isinstance(config, dict) or set(config) != {
            "source_owner", "source_revision", "lease_revision", "budget_seconds", "expires_at",
        }:
            raise ValueError("An explicit source-bound auth-wait configuration is required")
        for key in ("source_owner", "source_revision"):
            if not isinstance(config[key], str) or not 1 <= len(config[key]) <= 256:
                raise ValueError("Auth wait requires a source owner and immutable revision")
        self.session_id = session_id
        self.source_owner = config["source_owner"]
        self.source_revision = config["source_revision"]
        self.incarnation = str(uuid.uuid4())
        self.nonce = secrets.token_hex(32)
        self.lease_revision = 0
        self.phase = "starting"
        self.stopped_for_auth = False
        self.hold: str | None = None
        self._stop = asyncio.Event()
        self._lease_changed = asyncio.Event()
        self._grant = asyncio.Event()
        self._server: asyncio.Server | None = None
        self._connections: dict[asyncio.Task, asyncio.StreamWriter] = {}
        self._install(config)

    def _install(self, value: dict) -> None:
        budget, expiry, revision = (
            value["budget_seconds"], value["expires_at"], value["lease_revision"],
        )
        if (
            type(budget) not in (int, float) or not math.isfinite(budget)
            or not 0 < budget <= MAX_BUDGET_SECONDS
            or type(expiry) not in (int, float) or not math.isfinite(expiry)
            or not time.time() < expiry <= time.time() + MAX_BUDGET_SECONDS
            or type(revision) is not int or revision <= self.lease_revision
        ):
            raise ValueError("Authorization must have a newer revision and finite bounded lifetime")
        self._deadline = time.monotonic() + min(budget, expiry - time.time())
        self._wall_deadline = expiry
        self.lease_revision = revision
        if self._stop_delay() <= 0:
            self._stop.set()
        self._lease_changed.set()

    def _stop_delay(self) -> float:
        return min(
            self._deadline - time.monotonic(), self._wall_deadline - time.time(),
        ) - 1

    def authorized(self) -> bool:
        if self._stop.is_set():
            return False
        if self._stop_delay() <= 0:
            self._stop.set()
            return False
        return True

    def preserve_hold(self, reason: str) -> None:
        """A new lease must never clear loss, ambiguity or uncontrolled input."""
        self.hold = self.hold or reason

    async def expiry(self) -> None:
        # Reserve the bounded SDK-freeze exchange before the source deadline.
        while self.authorized():
            delay = self._stop_delay()
            if delay <= 0:
                break
            self._lease_changed.clear()
            try:
                await asyncio.wait_for(self._lease_changed.wait(), min(.1, delay))
            except asyncio.TimeoutError:
                pass
        self._stop.set()
        self.phase = "stopping"
        self.stopped_for_auth = True

    def stopped(self, snapshot: dict | None) -> None:
        if not snapshot or snapshot != {
            "controlled": True, "closed": False, "pending": False,
            "sending": False, "outstanding": False, "waiting": True,
        }:
            self.preserve_hold("native-control-unconfirmed")
        self.phase = "held" if self.hold else "auth_wait"

    def status(self) -> dict:
        return {
            "contract": CONTRACT, "version": VERSION,
            "session_id": self.session_id, "incarnation": self.incarnation,
            "source_owner": self.source_owner, "source_revision": self.source_revision,
            "lease_revision": self.lease_revision, "phase": self.phase, "hold": self.hold,
            "history": "same-session-id-requested", "native_qualification": "unqualified",
            "replay": "never",
        }

    def declaration(self) -> dict:
        if self._server is None:
            raise RuntimeError("The auth-wait receiver is not listening")
        return {
            **self.status(), "port": self._server.sockets[0].getsockname()[1],
            "nonce": self.nonce,
        }

    async def serve(self) -> None:
        if self._server is not None:
            raise RuntimeError("The auth-wait receiver is already listening")
        self._server = await asyncio.start_server(
            self._accept, "127.0.0.1", 0, limit=8192,
        )

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if len(self._connections) >= 4:
            writer.close()
            return
        task = asyncio.create_task(self._receive(reader, writer))
        self._connections[task] = writer
        task.add_done_callback(lambda done: self._connections.pop(done, None))

    async def _receive(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request = json.loads(await asyncio.wait_for(reader.readline(), 2))
            if not isinstance(request, dict):
                raise ValueError("An auth-wait request object is required")
            base = {
                "contract", "version", "nonce", "session_id", "incarnation",
                "source_owner", "source_revision", "operation",
            }
            operation = request.get("operation")
            extra = {"lease_revision", "budget_seconds", "expires_at"} if operation == "renew" else set()
            if (
                set(request) != base | extra
                or request["contract"] != CONTRACT
                or type(request["version"]) is not int or request["version"] != VERSION
                or not isinstance(request["nonce"], str)
                or not secrets.compare_digest(request["nonce"].encode(), self.nonce.encode())
                or any(request[key] != getattr(self, key) for key in (
                    "session_id", "incarnation", "source_owner", "source_revision",
                ))
                or operation not in {"status", "renew", "revoke"}
            ):
                raise ValueError("Unsupported or obsolete auth-wait binding")
            if operation == "revoke":
                self._stop.set()
                self._lease_changed.set()
            elif operation == "renew":
                # Expiry cannot be raced by a fresh grant before owned stop settles.
                active = self.authorized()
                if not active and (self.hold or self.phase != "auth_wait"):
                    raise ValueError("Native stop or owner reconciliation is required")
                self._install(request)
                if self.phase == "auth_wait" and self._stop_delay() > 0:
                    self.incarnation = str(uuid.uuid4())
                    self._stop.clear()
                    self.phase = "starting"
                    self._grant.set()
            writer.write(json.dumps({"ok": True, **self.status()}).encode() + b"\n")
            await writer.drain()
        except (ValueError, KeyError, TypeError, OSError, asyncio.TimeoutError):
            writer.write(b'{"ok":false,"error":"auth-wait-request-refused"}\n')
            try:
                await writer.drain()
            except OSError:
                pass
        finally:
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (OSError, asyncio.TimeoutError):
                pass

    async def next_grant(self) -> None:
        await self._grant.wait()
        self._grant.clear()
        self.stopped_for_auth = False

    async def close(self) -> None:
        self.preserve_hold("receiver-closed")
        self.phase = "closed"
        self._stop.set()
        self._lease_changed.set()
        if self._server is not None:
            self._server.close()
        tasks = list(self._connections)
        for writer in self._connections.values():
            writer.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
