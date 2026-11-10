"""One bounded subscription per canonical remote daemon, shared by local watchers."""

from __future__ import annotations

import asyncio
import time

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from ssh_manager import CarrierBackpressure, CarrierRemoteError, CarrierUnavailable, EnvelopeType

from .remote_errors import RemoteBridgeError
from .remote_operations import RemoteOperationService
from .status_store import SafeId, SelectedAgent, Selection, StatusKind, StatusStore

MAX_HOSTS = 16
_TRANSIENT = (RemoteBridgeError, CarrierRemoteError, CarrierUnavailable, CarrierBackpressure, TimeoutError, asyncio.TimeoutError, OSError)
_TERMINAL = {"status_replay_gap", "host_status_unsupported", "unsupported_operation", "unsupported_version"}


def failure_state(exc):
    code = getattr(exc, "code", "")
    if code == "status_replay_gap":
        return "gap"
    if code in _TERMINAL:
        return "unsupported"
    return "unavailable"


class RemoteEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: int = Field(gt=0, le=(1 << 63) - 1)
    source: str
    session_id: str = Field(min_length=1, max_length=256, pattern=SafeId)
    kind: StatusKind
    scope: str = Field(pattern=r"^(root|nested)$")
    observed_at: float = Field(allow_inf_nan=False)
    evidence: str = Field(max_length=128)


class RemotePage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    epoch: str = Field(min_length=1, max_length=128)
    through: int = Field(ge=0, le=(1 << 63) - 1)
    events: list[RemoteEvent] = Field(max_length=256)


class HostFanout:
    def __init__(self, app):
        self.app = app
        self.tasks: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.service = getattr(app.state, "remote_operations", None) or RemoteOperationService(app.state.resolver)

    def state(self, host, availability, *, epoch=None, cursor=None):
        db = self.app.state.db
        db.execute_write(
            "INSERT INTO bridge_status_hosts (host,availability,observed_at) VALUES (?,?,?) "
            "ON CONFLICT(host) DO UPDATE SET availability=excluded.availability,observed_at=excluded.observed_at",
            (host, availability, time.time()),
        )
        if epoch is not None and cursor is not None:
            db.execute_write(
                "UPDATE bridge_status_hosts SET epoch=?,cursor=? WHERE host=?", (epoch, cursor, host),
            )

    async def connect(self, host):
        saved = self.app.state.db.execute_read("SELECT * FROM bridge_status_hosts WHERE host=?", (host,))
        epoch = saved[0]["epoch"] if saved else None
        cursor = saved[0]["cursor"] if saved else 0
        lease = await self.service._lease(host)
        subscription = None
        try:
            subscription = await lease.carrier.subscribe(
                {"version": 1, "operation": "host.status", "epoch": epoch},
                replayable=False, progress_timeout=45, position=cursor,
                retain_buffered_on_reconnect=False,
            )
            accepted = await asyncio.wait_for(subscription.get(), timeout=20)
            if accepted.type is not EnvelopeType.RESPONSE:
                raise ValueError("Host status stream did not acknowledge")
            body = accepted.payload
            if (
                not isinstance(body.get("epoch"), str) or not 1 <= len(body["epoch"]) <= 128
                or type(body.get("cursor")) is not int or not cursor <= body["cursor"] <= (1 << 63) - 1
                or (epoch is not None and body["epoch"] != epoch)
            ):
                raise ValueError("Host status continuity changed")
            self.state(host, "available", epoch=body["epoch"], cursor=body["cursor"])
            return lease, subscription, body["epoch"], body["cursor"]
        except BaseException:
            try:
                if subscription is not None:
                    await subscription.close()
            finally:
                await lease.release()
            raise

    async def ensure(self, host):
        if host in self.tasks and not self.tasks[host].done():
            return
        if len(self.tasks) >= MAX_HOSTS and host not in self.tasks:
            raise ValueError("Remote status host capacity reached")
        saved = self.app.state.db.execute_read("SELECT availability FROM bridge_status_hosts WHERE host=?", (host,))
        if saved and saved[0][0] in {"gap", "unsupported", "invalid"}:
            raise ValueError("Host status requires explicit reconciliation")
        connection = await self.connect(host)
        self.tasks[host] = asyncio.create_task(self.pump(host, connection))

    async def prepare(self, selection):
        agents = []
        async with self.lock:
            for agent in selection.agents:
                if agent.host == "local":
                    agents.append(agent)
                    continue
                try:
                    # Alias normalization prevents two handles for the same
                    # daemon from allocating two status streams.
                    _machine, environment = self.app.state.resolver.resolve_ssh_environment(agent.host)
                    host = environment.alias
                    await self.ensure(host)
                    observed = self.app.state.db.execute_read(
                        "SELECT availability,observed_at FROM bridge_status_hosts WHERE host=?", (host,),
                    )
                    if (
                        not observed or observed[0]["availability"] != "available"
                        or time.time() - observed[0]["observed_at"] > 45
                    ):
                        raise ValueError("Selected host status evidence is unavailable")
                except (*_TRANSIENT, ValueError) as exc:
                    raise HTTPException(426, {
                        "code": "host_status_unavailable",
                        "message": "Selected host does not provide a qualified status feed",
                    }) from exc
                agents.append(SelectedAgent(host=host, session_id=agent.session_id))
        return selection.model_copy(update={"agents": agents})

    async def pump(self, host, connection):
        backoff = 1
        while True:
            if connection is None:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
                try:
                    connection = await self.connect(host)
                except _TRANSIENT as exc:
                    self.state(host, failure_state(exc))
                    if getattr(exc, "code", "") in _TERMINAL:
                        return
                    continue
                except ValueError:
                    self.state(host, "invalid")
                    return
            lease, subscription, epoch, cursor = connection
            try:
                while True:
                    frame = await subscription.get()
                    if frame.type is not EnvelopeType.EVENT:
                        raise ValueError("Unexpected host status frame")
                    if frame.payload.get("event") == "_heartbeat":
                        self.state(host, "available")
                        continue
                    page = RemotePage.model_validate(frame.payload.get("data"))
                    ids = [e.id for e in page.events]
                    if (
                        page.epoch != epoch or page.through < cursor
                        or ids != sorted(set(ids))
                        or any(e.id <= cursor or e.id > page.through or e.source != "local" for e in page.events)
                    ):
                        raise ValueError("Invalid host status replay")
                    selected = StatusStore(self.app.state.db)
                    for event in page.events:
                        selected.publish(
                            event.session_id, event.kind, f"{epoch}:{event.id}",
                            source=host, scope=event.scope, observed_at=event.observed_at,
                            evidence="remote_historical_transition",
                        )
                    cursor = page.through
                    self.state(host, "available", epoch=epoch, cursor=cursor)
                    self.app.state.status_runtime.wake.set()
                    backoff = 1
            except asyncio.CancelledError:
                raise
            except _TRANSIENT as exc:
                self.state(host, failure_state(exc))
                if getattr(exc, "code", "") in _TERMINAL:
                    return
            except ValueError:
                self.state(host, "invalid")
                return
            finally:
                try:
                    await subscription.close()
                finally:
                    await lease.release()
            connection = None

    async def reconcile(self):
        wanted = {
            agent.host for row in self.app.state.db.execute_read("SELECT selection FROM bridge_status_subscriptions")
            for agent in Selection.model_validate_json(row["selection"]).agents if agent.host != "local"
        }
        async with self.lock:
            for host in list(self.tasks):
                if host not in wanted:
                    task = self.tasks.pop(host)
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    self.app.state.db.execute_write("DELETE FROM bridge_status_hosts WHERE host=?", (host,))
            for host in wanted:
                saved = self.app.state.db.execute_read(
                    "SELECT availability FROM bridge_status_hosts WHERE host=?", (host,),
                )
                if saved and saved[0]["availability"] in {"gap", "unsupported", "invalid"}:
                    continue
                try:
                    await self.ensure(host)
                except _TRANSIENT as exc:
                    self.state(host, failure_state(exc))
                except ValueError:
                    self.state(host, "invalid")

    async def reset(self, host):
        async with self.lock:
            task = self.tasks.pop(host, None)
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            self.app.state.db.execute_write("DELETE FROM bridge_status_hosts WHERE host=?", (host,))
            await self.ensure(host)

    async def close(self):
        tasks = list(self.tasks.values())
        self.tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
