"""Explicit point composition through the normal synchronous service entry."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI

import test_azure_devops_point_runtime as runtime
import test_azure_devops_point_worker as worker
from test_azure_devops_point_worker import application as application
from test_azure_devops_points import subject as subject

from agent_index import server
from agent_index.config import Config
from agent_index.indexing.point_service import PointService
from agent_index.indexing.task_store import TaskStatus

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("service"),
]


class _Socket:
    closed = False

    def getsockname(self) -> tuple[str, int]:
        return "127.0.0.1", 4321

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("passive", [False, True])
@pytest.mark.parametrize("invalid", [False, True])
def test_serve_preserves_selected_native_binding_and_startup_refusal(
    application: worker.Fixture, monkeypatch: pytest.MonkeyPatch,
    passive: bool, invalid: bool,
) -> None:
    f = application
    f.config = replace(f.config, cluster_enabled=False)
    if invalid:
        f.config = replace(f.config, data_dir=f.config.data_dir.parent / "legacy")
    events: list[tuple[str, str]] = []
    configured, lifetime = runtime._point_service_app(f, monkeypatch, model_events=events)
    create = configured.state.point_service_factory
    constructed: list[PointService] = []
    monkeypatch.setenv("AGENT_INDEX_HOME", str(f.config.data_dir.parent / "host"))
    monkeypatch.setenv("AGENT_INDEX_RUN_DIR", str(f.config.data_dir.parent / "host-run"))
    monkeypatch.delenv("AGENT_INDEX_FLEET_POLICY", raising=False)
    monkeypatch.setattr(server, "_publish_routing", lambda *_, **__: None)
    monkeypatch.setattr(server, "_clear_routing", lambda: None)
    sock = _Socket()
    monkeypatch.setattr(server, "_bind_listen_socket", lambda *_: sock)

    def factory() -> PointService:
        point: PointService = create()
        constructed.append(point)
        return point

    async def exercise(app: FastAPI) -> None:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app), base_url="http://fixture",
            ) as client:
                if passive:
                    assert not constructed and not lifetime
                    promoted = await client.post("/promote")
                    if invalid:
                        assert promoted.status_code == 503
                        assert app.state.task_runner is None
                        assert len(constructed) == 1
                        return
                    assert promoted.status_code == 200 and promoted.json()["promoted"]
                runner = app.state.task_runner
                assert len(constructed) == 1 and runner.point_service is constructed[0]
                assert lifetime == ["open"] and events == [] and not f.provider.requests
                health = (await client.get("/health")).json()
                assert health["point_service_contract"]["state"] == "paused"
                assert not health["point_service_contract"]["execution_admitted"]
                assert 2 not in health["indexing_contract"]["execution_versions"]
                retained = f.tasks.get_task(f.claim.task_id)
                assert retained is not None and retained.status == TaskStatus.QUEUED_HINTS_V2
                await runner.point_service.activate()
                runner.notify()
                await runtime._wait_point_state(lambda: bool(
                    (task := f.tasks.get_task(f.claim.task_id))
                    and task.status == TaskStatus.COMPLETE
                ))
                await runtime._wait_point_state(lambda: not runner.point_service.running)
                observed = await runner.point_service.read_component("workitems/7/body.md")
                assert observed.applied_version == 3 and observed.chunks
                assert f.provider.body_reads == 2
        assert app.state.task_runner is None

    class _Server:
        def __init__(self, settings: Any) -> None:
            self.app = cast(FastAPI, settings.app)

        def run(self, *, sockets: list[Any]) -> None:
            assert sockets == [sock]
            assert self.app.state.bound_host == "127.0.0.1"
            assert self.app.state.bound_port == 4321
            asyncio.run(exercise(self.app))

    monkeypatch.setattr("uvicorn.Server", _Server)
    if invalid and not passive:
        with pytest.raises(ValueError, match="separate legacy data directory"):
            server.serve(Config(), point_service_factory=factory)
    else:
        server.serve(Config(), passive=passive, point_service_factory=factory)
    assert sock.closed and len(constructed) == 1
    if invalid:
        assert not lifetime and not events and not f.provider.requests and not f.calls
    else:
        assert lifetime == ["open", "closed"] and f.connector._http.client.is_closed
        assert sorted(model for action, model in events if action == "close") == ["first", "second"]
        assert [model for action, model in events if action == "stop"] == ["first"]
