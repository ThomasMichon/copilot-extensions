"""Provider-first carrier controls and shared-host durable fanout."""

import asyncio
import io
import json
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest
from ssh_manager import CarrierBackpressure, CarrierRemoteError, Envelope, EnvelopeType

from agent_bridge.client import BridgeClientError, SseStream
from agent_bridge.remote_operations import CarrierRequestRouter
from agent_bridge.routes import status
from agent_bridge.status_remote import HostFanout
from agent_bridge.status_store import Selection, StatusStore


class ProviderClient:
    def __init__(self, api, *, capability=1, pages=None):
        self.api = api
        self.capability = capability
        self.pages = pages or []
        self.calls = []
        self.opened = []

    def health(self):
        return {"capabilities": {"host_status_feed": self.capability}}

    def _request(self, method, path, *, params=None):
        self.calls.append(path)
        response = self.api.request(method, path, params=params)
        if response.status_code >= 400:
            raise BridgeClientError(response.status_code, response.json()["detail"])
        return response.json()

    def _stream_sse(self, path, *, params):
        self.calls.append(path)
        data = b"".join(
            ("event: status_page\ndata: " + json.dumps(page) + "\n\n").encode()
            for page in self.pages
        )
        response = io.BytesIO(data)
        self.opened.append(response)
        return SseStream(response)


@pytest.fixture
def provider(tmp_db):
    app = FastAPI()
    app.state.db = tmp_db
    app.state.ready = True
    app.include_router(status.router)
    return ProviderClient(TestClient(app))


@pytest.mark.parametrize("capability", [None, 0, 2, "1"])
def test_prior_missing_invalid_and_rollback_provider_refuse_before_stream(provider, capability):
    async def run():
        provider.capability = capability
        router = CarrierRequestRouter(lambda: provider)
        response = await router(Envelope(
            EnvelopeType.REQUEST, payload={"version": 1, "operation": "host.status"},
            position=0,
        ))
        assert response.type is EnvelopeType.ERROR
        assert response.payload["code"] == "host_status_unsupported"
        assert not provider.calls and not provider.opened
    asyncio.run(run())


def test_provider_first_current_handshake_and_real_sse_parser(provider, tmp_db):
    async def run():
        selected = StatusStore(tmp_db)
        selected.publish("agent-a", "error", "first")
        page = selected.page(after=0, epoch=None)
        provider.pages = [page]
        router = CarrierRequestRouter(lambda: provider)
        stream = await router(Envelope(
            EnvelopeType.REQUEST, payload={"version": 1, "operation": "host.status", "epoch": page["epoch"]},
            position=0,
        ))
        accepted = await anext(stream)
        assert accepted.type is EnvelopeType.RESPONSE
        frame = await anext(stream)
        assert frame.payload["data"]["events"][0]["kind"] == "error"
        await stream.aclose()
        assert all(response.closed for response in provider.opened)
    asyncio.run(run())


def test_new_subscription_starts_at_host_head_and_bad_epoch_refuses(provider, tmp_db):
    async def run():
        selected = StatusStore(tmp_db)
        selected.publish("agent-a", "idle", "first")
        router = CarrierRequestRouter(lambda: provider)
        stream = await router(Envelope(
            EnvelopeType.REQUEST, payload={"version": 1, "operation": "host.status"}, position=0,
        ))
        accepted = await anext(stream)
        assert accepted.payload["cursor"] == 1
        await stream.aclose()
        response = await router(Envelope(
            EnvelopeType.REQUEST,
            payload={"version": 1, "operation": "host.status", "epoch": "replaced"}, position=0,
        ))
        assert response.type is EnvelopeType.ERROR
        assert response.payload["code"] == "status_replay_gap"
    asyncio.run(run())


class Subscription:
    def __init__(self, frames):
        self.frames = list(frames)
        self.closed = False

    async def get(self):
        if self.frames:
            item = self.frames.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        await asyncio.Event().wait()

    async def close(self):
        self.closed = True


class Carrier:
    def __init__(self, frames):
        self.frames = frames
        self.calls = []
        self.subscriptions = []

    async def subscribe(self, payload, **kwargs):
        self.calls.append((payload, kwargs))
        subscription = Subscription([
            Envelope(EnvelopeType.RESPONSE, payload={"epoch": "remote-epoch", "cursor": kwargs["position"]}),
            *self.frames,
        ])
        self.subscriptions.append(subscription)
        return subscription


def remote_app(tmp_db, frames):
    carrier = Carrier(frames)
    releases = []

    async def release():
        releases.append(True)

    async def lease(_host):
        return SimpleNamespace(carrier=carrier, release=release)

    resolver = SimpleNamespace(resolve_ssh_environment=lambda _name: (
        SimpleNamespace(), SimpleNamespace(alias="canonical-host"),
    ))
    app = SimpleNamespace(state=SimpleNamespace(
        db=tmp_db, resolver=resolver, remote_operations=SimpleNamespace(_lease=lease),
        status_runtime=SimpleNamespace(wake=asyncio.Event()),
    ))
    return app, carrier, releases


def remote_page():
    return Envelope(EnvelopeType.EVENT, payload={"event": "status_page", "data": {
        "epoch": "remote-epoch", "through": 1, "events": [{
            "id": 1, "source": "local", "session_id": "agent-a",
            "kind": "idle", "scope": "root", "observed_at": 1.0, "evidence": "historical_transition",
        }],
    }})


def test_two_watchers_and_two_aliases_share_one_stream_and_resume_after_restart(tmp_db):
    async def run():
        app, carrier, releases = remote_app(tmp_db, [remote_page()])
        fanout = HostFanout(app)
        first = Selection(watcher="watcher", agents=[{"host": "alias-one", "session_id": "agent-a"}], events=["idle"])
        second = Selection(watcher="watcher", agents=[{"host": "alias-two", "session_id": "agent-b"}], events=["idle"])
        a = await fanout.prepare(first)
        b = await fanout.prepare(second)
        assert a.agents[0].host == b.agents[0].host == "canonical-host"
        assert len(carrier.calls) == 1
        StatusStore(tmp_db).subscribe("watch", a)
        await asyncio.sleep(0)
        result = StatusStore(tmp_db).page(after=0, epoch=None, subscription_id="watch")
        assert len(result["events"]) == 1
        assert result["events"][0]["source"] == "canonical-host"
        await fanout.close()
        assert len(releases) == 1 and carrier.subscriptions[0].closed
        restarted = HostFanout(app)
        await restarted.prepare(first)
        assert carrier.calls[-1][1]["position"] == 1
        await restarted.close()
    asyncio.run(run())


def test_unsupported_host_refuses_before_subscription_write(tmp_db):
    async def run():
        app, _carrier, _releases = remote_app(tmp_db, [])
        async def unsupported(_host):
            raise ValueError("unsupported")
        fanout = HostFanout(app)
        fanout.connect = unsupported
        with pytest.raises(HTTPException) as exc:
            await fanout.prepare(Selection(
                watcher="watcher", agents=[{"host": "host", "session_id": "agent"}], events=["idle"],
            ))
        assert exc.value.status_code == 426
        assert tmp_db.execute_read("SELECT * FROM bridge_status_subscriptions") == []
        await fanout.close()
    asyncio.run(run())


def test_backpressure_and_gap_are_explicit_and_release_transport(tmp_db):
    async def run(error, expected):
        app, carrier, releases = remote_app(tmp_db, [error])
        fanout = HostFanout(app)
        await fanout.ensure("canonical-host")
        await asyncio.sleep(0)
        rows = tmp_db.execute_read("SELECT availability FROM bridge_status_hosts")
        assert rows[0][0] == expected
        await fanout.close()
        assert carrier.subscriptions[0].closed and releases
    asyncio.run(run(CarrierBackpressure("slow consumer"), "unavailable"))
    tmp_db.execute_write("DELETE FROM bridge_status_hosts")
    asyncio.run(run(CarrierRemoteError({
        "code": "status_replay_gap", "message": "retention gap", "status": 409,
    }), "gap"))


def test_unavailable_shared_stream_cannot_authorize_a_new_selection(tmp_db):
    async def run():
        app, carrier, _releases = remote_app(tmp_db, [])
        fanout = HostFanout(app)
        selection = Selection(
            watcher="watcher", agents=[{"host": "host", "session_id": "agent"}], events=["idle"],
        )
        await fanout.prepare(selection)
        fanout.state("canonical-host", "unavailable")
        with pytest.raises(HTTPException):
            await fanout.prepare(selection)
        assert len(carrier.calls) == 1
        assert not tmp_db.execute_read("SELECT * FROM bridge_status_subscriptions")
        await fanout.close()
    asyncio.run(run())
