"""Opt-in existing-owner metadata cadence through real HTTP and registrar input."""

from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import config, coordinator_loops, server
from agent_dispatch.client import DispatchClient
from agent_dispatch.companion import CompanionIndeterminate
from agent_dispatch.coordinator import create_app
from agent_dispatch.coordinator_loops import LoopHealth
from agent_dispatch.pool_publication_custody import PoolPublicationError
from agent_dispatch.pool_source_snapshot import read_supervisor_declarations
from agent_dispatch.queue_records import TaskError
from agent_dispatch.registrar_discovery import RegistrarSources, add_pointer
from agent_dispatch.supervisor_daemon import SupervisorDaemon
from tests.test_pool_cohort_preparation import _write
from tests.test_pool_source_collection import _registration
from tests.test_supervisor_daemon import FakeCompanionController, FakeLauncher


@pytest.fixture
def owners(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    base = tmp_path / "registrar"
    base.mkdir()
    members = [{"machine": "logical", "execution_machine": "host-a", "env": "default"}]
    _write(base, members=members)
    sources = RegistrarSources(
        base=base, dropins=tmp_path / "absent-registrar.d", activation_source=lambda: None,
    )
    active = {"value": True}
    monkeypatch.setattr(server, "RegistrarSources", lambda: sources)
    monkeypatch.setattr(server, "_owns_active_route", lambda: active["value"])
    monkeypatch.setattr(server.telemetry, "load_sink_from_config", lambda: True)
    monkeypatch.setattr(config, "run_dir", lambda: tmp_path / "run")
    app = server.build_app(config.Config(
        db_path=str(tmp_path / "queue.db"), token="data", control_token="control",
        pool_publication_enabled=True, sweep_interval=0.01,
    ))
    api = TestClient(app)
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        response = api.request(
            request.method, str(request.url), headers=request.headers, content=request.content,
        )
        return httpx.Response(response.status_code, json=response.json())

    client = DispatchClient(
        "http://coordinator", token="data", control_token="control",
        transport=httpx.MockTransport(handle),
    )
    daemon = SupervisorDaemon(
        client, "host-a", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(),
        declared_source=lambda: read_supervisor_declarations(sources),
        overrides_source=lambda: {}, pool_publication_enabled=True,
    )
    assert daemon.acquire_singleton()
    try:
        yield SimpleNamespace(
            app=app, queue=app.state.queue, source=app.state.pool_cohort_source,
            sources=sources, base=base, members=members, active=active,
            daemon=daemon, client=client, requests=requests,
        )
    finally:
        daemon.shutdown()
        daemon.release_singleton()
        client.close()
        api.close()


def _tick(owner: Any) -> Any:
    return asyncio.run(owner.source.maintain(timeout=5))


@pytest.mark.parametrize(("enabled", "interval", "expected"), [
    (True, 30.0, 5.0), (False, 30.0, 30.0), (True, 1.0, 1.0),
])
def test_actual_master_loop_renews_before_legacy_poll_interval(
    owners: Any, enabled: bool, interval: float, expected: float,
) -> None:
    daemon = owners.daemon
    daemon.pool_publication_enabled = enabled
    daemon.poll_interval = interval
    sleeps = []
    snapshots = []
    daemon.sleep = sleeps.append
    _tick(owners)

    def observe(_summary: Any) -> None:
        snapshots.append(owners.queue.pool_catalog_snapshot())
        if len(snapshots) == 3:
            raise KeyboardInterrupt

    assert daemon.serve(single_instance=False, on_cycle=observe) == 0
    assert sleeps == [expected, expected]
    assert daemon.poll_interval == interval
    if enabled:
        assert snapshots[-1].configured_ready
        assert snapshots[-1].scopes[0].revision > snapshots[-2].scopes[0].revision
    else:
        assert snapshots[-1].scopes == ()


def test_actual_scope_cadence_enrolls_publishes_renews_and_observes_overrides(owners: Any) -> None:
    trusted = owners.base / "profiles"
    trusted.mkdir()
    (trusted / "worker.json").write_text(json.dumps({
        "name": "worker", "owner": "repo:example", "labels": ["worker"],
        "repos": "example", "priority": 10, "concurrency": 2,
    }), encoding="utf-8")
    add_pointer("trusted", trusted, base=owners.base)
    rid = "declared:repo:example:worker"
    assert _tick(owners).revision == 1
    owners.daemon.reconcile_once()
    # Mirroring the declaration changes the coordinator input fence first.
    assert owners.queue.pool_catalog_snapshot().scopes == ()
    owners.daemon.reconcile_once()
    assert owners.queue.pool_catalog_snapshot().scopes[0].state == "pending"
    owners.daemon.reconcile_once()
    complete = owners.queue.pool_catalog_snapshot()
    assert complete.configured_ready and len(complete.definitions) == 1
    assert complete.definitions[0].registration_id == rid
    owners.daemon.reconcile_once()
    renewed = owners.queue.pool_catalog_snapshot()
    assert renewed.configured_ready
    assert renewed.scopes[0].revision == complete.scopes[0].revision + 1
    owners.daemon.overrides_source = lambda: {rid: {"disabled": True}}
    owners.daemon.reconcile_once()
    assert owners.queue.pool_catalog_snapshot().configured_ready
    assert owners.queue.pool_catalog_snapshot().definitions == ()
    assert not any("/cohort/project" in str(request.url) for request in owners.requests)
    assert owners.queue.pool_configured_cohort().observation.members[0].machine == "logical"


def test_legacy_direct_row_without_complete_filters_withholds_entire_publication(owners: Any) -> None:
    row = _registration()
    stored = owners.queue.register_registration(
        row["kind"], row["spec"], machine=row["machine"], reg_id=row["id"],
    )
    assert stored.id == row["id"]
    assert "pool_definition" not in owners.client.list_registrations()[0]
    _tick(owners)
    owners.daemon.reconcile_once()
    assert owners.queue.pool_catalog_snapshot().scopes == ()
    assert "effective-worker-definition-invalid" in owners.daemon.pool_source_snapshot().reasons


def test_actual_scope_cadence_qualifies_the_installed_legacy_env_composition(
    owners: Any, monkeypatch: Any,
) -> None:
    from agent_dispatch import registrar_discovery

    monkeypatch.setattr(registrar_discovery, "install_dir", lambda: owners.base)
    (owners.base / "supervisor.env").write_text(
        "AGENT_DISPATCH_SUPERVISE_LABELS=review\n"
        "AGENT_DISPATCH_SUPERVISE_MAX_CONCURRENT=2\n", encoding="utf-8",
    )
    owners.daemon.declared_source = lambda: read_supervisor_declarations(owners.sources, legacy=True)
    _tick(owners)
    for _ in range(3):
        owners.daemon.reconcile_once()
    snapshot = owners.queue.pool_catalog_snapshot()
    assert snapshot.configured_ready and len(snapshot.definitions) == 1
    definition = snapshot.definitions[0]
    assert definition.registration_id == "declared:legacy-env:supervisor:supervisor"
    assert definition.max_concurrent == 2
    assert definition.filters.permit["task-type"] == frozenset({"review", "supervisor"})
    (owners.base / "supervisor.env").write_text(
        "unsupported statement\nAGENT_DISPATCH_SUPERVISE_LABELS=review\n", encoding="utf-8",
    )
    owners.daemon.reconcile_once()
    assert not owners.daemon.pool_source_snapshot().complete
    assert not owners.queue.pool_catalog_snapshot().configured_ready


@pytest.mark.parametrize("lost", [False, True])
def test_actual_scope_cadence_continues_same_queue_after_endpoint_reconnect(
    owners: Any, lost: bool,
) -> None:
    _tick(owners)
    if lost:
        send = owners.client.send_pool_metadata

        def accepted(request: Any, *, boundary: Any) -> Any:
            send(request, boundary=boundary)
            raise httpx.ReadError("synthetic original accepted reply lost")

        owners.client.send_pool_metadata = accepted
    owners.daemon.reconcile_once()
    before = owners.queue.pool_catalog_snapshot().scopes[0]
    original = owners.daemon._pool_publisher_custody.pending
    with TestClient(create_app(
        owners.queue, token="data", control_token="control",
        enable_mcp=False, verification_interval=0,
    )) as api:
        def handle(wire: httpx.Request) -> httpx.Response:
            response = api.request(
                wire.method, str(wire.url), headers=wire.headers, content=wire.content,
            )
            return httpx.Response(response.status_code, json=response.json())

        with DispatchClient(
            "http://replacement", token="data", control_token="control",
            transport=httpx.MockTransport(handle),
        ) as replacement:
            owners.daemon._client_factory = lambda: replacement
            assert owners.daemon._reconnect()
            owners.daemon.reconcile_once()
            if lost:
                assert owners.daemon._pool_publisher_custody.settled == (original, before)
                assert owners.queue.pool_catalog_snapshot().scopes[0] == before
                owners.daemon.reconcile_once()
            after = owners.queue.pool_catalog_snapshot().scopes[0]
            assert after.publisher_id == before.publisher_id
            assert after.generation == before.generation
            assert after.revision == before.revision + 1 and after.state == "complete"
            assert owners.queue.pool_catalog_snapshot().configured_ready
            assert owners.client._http.is_closed


@pytest.mark.parametrize("blocked", ["disabled", "unprojected", "excluded", "no-lock"])
def test_automatic_scope_publication_requires_opt_in_cohort_membership_and_singleton(
    owners: Any, blocked: str,
) -> None:
    if blocked == "excluded":
        _write(owners.base, members=[])
    if blocked != "unprojected":
        _tick(owners)
    if blocked == "disabled":
        owners.daemon.pool_publication_enabled = False
    if blocked == "no-lock":
        owners.daemon.release_singleton()
    owners.daemon.reconcile_once()
    assert owners.queue.pool_catalog_snapshot().scopes == ()
    assert owners.daemon._pool_publisher_custody is None or owners.daemon._pool_publisher_custody.pending is None


def test_scope_cadence_recovers_lost_original_before_publishing_new_input(
    owners: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _tick(owners)
    original_send = owners.client.send_pool_metadata
    sent = []

    def lost(request: Any, *, boundary: Any) -> Any:
        sent.append(request)
        result = original_send(request, boundary=boundary)
        if len(sent) == 1:
            raise httpx.ReadError("synthetic lost accepted response")
        return result

    monkeypatch.setattr(owners.client, "send_pool_metadata", lost)
    owners.daemon.reconcile_once()
    custody = owners.daemon._pool_publisher_custody
    original = custody.pending
    assert original is sent[0] and owners.queue.pool_catalog_snapshot().scopes[0].generation == 1
    owners.daemon.reconcile_once()
    assert custody.pending is None and custody.settled[0] is original
    assert len(sent) == 1
    owners.daemon.reconcile_once()
    assert len(sent) == 2 and sent[1].operation == "publish"
    assert owners.queue.pool_catalog_snapshot().configured_ready


def test_known_pre_post_refusal_can_refresh_on_later_cadence(
    owners: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _tick(owners)
    prepare = owners.daemon.prepare_pool_metadata_request
    prepared = []

    def changed() -> Any:
        request = prepare()
        prepared.append(request)
        if len(prepared) == 1:
            _write(owners.base, revision=1, members=owners.members)
            _tick(owners)
        return request

    monkeypatch.setattr(owners.daemon, "prepare_pool_metadata_request", changed)
    owners.daemon.reconcile_once()
    assert owners.queue.pool_catalog_snapshot().scopes == ()
    assert owners.daemon._pool_publisher_custody.pending is None
    owners.daemon.reconcile_once()
    assert len(prepared) == 2 and prepared[1] is not prepared[0]
    assert owners.queue.pool_catalog_snapshot().scopes[0].enrollment_cohort_revision == 2


@pytest.mark.parametrize("blocked", ["passive", "draining", "source-target", "queue-target"])
def test_coordinator_cadence_requires_original_targets_and_current_route(
    owners: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, blocked: str,
) -> None:
    if blocked == "passive":
        owners.active["value"] = False
    elif blocked == "draining":
        owners.app.state.drain_gate.set_draining(True)
    elif blocked == "source-target":
        owners.sources.base = tmp_path / "other-registrar"
    else:
        monkeypatch.setattr(owners.queue, "db_path", str(tmp_path / "other.db"))
    with pytest.raises(TaskError):
        _tick(owners)
    if blocked != "queue-target":
        assert owners.queue.pool_configured_cohort().revision == 0


def test_coordinator_cadence_recovers_post_commit_source_change_before_fresh_projection(
    owners: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    observe = owners.queue.observe_pool_configured_cohort
    writes = []

    def change(observation: Any, *, expected_revision: int) -> Any:
        writes.append(observation)
        result = observe(observation, expected_revision=expected_revision)
        if len(writes) == 1:
            _write(owners.base, revision=1, members=owners.members)
        return result

    monkeypatch.setattr(owners.queue, "observe_pool_configured_cohort", change)
    with pytest.raises(PoolPublicationError):
        _tick(owners)
    original = owners.source._preparation.pending
    assert original is not None
    assert _tick(owners).revision == 1 and len(writes) == 1
    assert owners.source._preparation.pending is None
    assert _tick(owners).revision == 2 and len(writes) == 2


def test_timeout_and_cancel_keep_one_actual_projection_and_its_drain_custody(
    owners: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, finish = Event(), Event()
    observe = owners.queue.observe_pool_configured_cohort
    calls = []

    def held(observation: Any, *, expected_revision: int) -> Any:
        calls.append(observation)
        entered.set()
        assert finish.wait(5)
        return observe(observation, expected_revision=expected_revision)

    monkeypatch.setattr(owners.queue, "observe_pool_configured_cohort", held)

    async def run() -> None:
        waiter = asyncio.create_task(owners.source.maintain(timeout=5))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            assert owners.app.state.drain_gate.claims == 1
            original = owners.source._maintenance
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert owners.source._maintenance is original and not original.done()
            with pytest.raises(TimeoutError):
                await owners.source.maintain(timeout=0.01)
            assert owners.source._maintenance is original and not original.done()
            assert not await owners.source.close(timeout=0)
            assert owners.app.state.drain_gate.claims == 1 and len(calls) == 1
        finally:
            finish.set()
        assert await owners.source.close(timeout=5)
        assert owners.app.state.drain_gate.claims == 0 and len(calls) == 1
        with pytest.raises(PoolPublicationError, match="closed"):
            await owners.source.maintain(timeout=5)

    asyncio.run(run())


def test_existing_gc_loop_calls_actual_whole_source_without_another_timer(
    owners: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import diagnostic_logs

    monkeypatch.setattr(diagnostic_logs, "maintain_file_logging", lambda: None)
    gc_ran = asyncio.Event()
    queue = SimpleNamespace(
        reconcile_liveness=lambda: gc_ran.set() or {},
        reconcile_cooldowns=lambda: 0,
        reconcile_reviewer_deadlines=lambda: 0,
    )

    async def cycle(_health: Any, work: Any, **_kwargs: Any) -> Any:
        return work()

    async def run() -> None:
        task = asyncio.create_task(coordinator_loops._gc_loop(
            queue, 0.01, None, health=LoopHealth("test", 0.01),
            pool_source=owners.source, run_supervised_cycle=cycle,
        ))
        try:
            await asyncio.wait_for(gc_ran.wait(), timeout=5)
            assert owners.queue.pool_configured_cohort().revision == 1
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await owners.source.close(timeout=5)

    asyncio.run(run())


def test_master_source_collection_does_not_wait_unbounded_behind_a_sender(owners: Any) -> None:
    entered, finish = Event(), Event()

    def hold() -> None:
        with owners.daemon._source_collection_lock:
            entered.set()
            assert finish.wait(5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(hold)
        try:
            assert entered.wait(5)
            owners.daemon._reconcile_deadline = time.monotonic() + 0.02
            started = time.monotonic()
            with pytest.raises(CompanionIndeterminate, match="remains in progress"):
                owners.daemon._effective_registrations()
            assert time.monotonic() - started < 1
        finally:
            owners.daemon._reconcile_deadline = None
            finish.set()
        future.result(timeout=5)


def test_exhausted_reconcile_budget_cannot_submit_a_fresh_send(owners: Any) -> None:
    _tick(owners)
    original = owners.daemon.prepare_pool_metadata_request()
    before = len(owners.requests)
    token = owners.daemon._pool_capture_deadline.set(time.monotonic() - 1)
    try:
        with pytest.raises(PoolPublicationError, match="deadline exhausted before submission"):
            owners.daemon.send_pool_metadata_request()
    finally:
        owners.daemon._pool_capture_deadline.reset(token)
    assert owners.daemon._pool_metadata_flight is None
    assert owners.daemon._pool_publisher_custody.pending is original
    assert len(owners.requests) == before


def test_config_defaults_off_and_explicit_enabled_construction_needs_gc(
    monkeypatch: pytest.MonkeyPatch, owners: Any,
) -> None:
    monkeypatch.delenv("AGENT_DISPATCH_POOL_PUBLICATION", raising=False)
    assert not config.load_config().pool_publication_enabled
    monkeypatch.setenv("AGENT_DISPATCH_POOL_PUBLICATION", "1")
    assert config.load_config().pool_publication_enabled
    with pytest.raises(ValueError, match="enabled GC cadence"):
        create_app(
            owners.queue, cohort_sources=owners.sources, wake_is_active=lambda: True,
            control_token="control", pool_publication_enabled=True, sweep_interval=0,
            enable_mcp=False,
        )
    with pytest.raises(ValueError, match="configured control authority"):
        create_app(
            owners.queue, cohort_sources=owners.sources, wake_is_active=lambda: True,
            pool_publication_enabled=True, sweep_interval=1, enable_mcp=False,
        )


def test_actual_server_lifespan_selects_its_source_on_the_existing_gc_owner(
    owners: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import coordinator

    projected = Event()
    monkeypatch.setenv("AGENT_DISPATCH_SELF_RETIRE", "0")
    monkeypatch.setenv("AGENT_DISPATCH_SELF_UPDATE", "0")
    monkeypatch.setenv("AGENT_DISPATCH_ABANDONED_PASSIVE_REAP", "0")

    async def gc(*_args: Any, **kwargs: Any) -> None:
        assert kwargs["pool_source"] is owners.source
        await kwargs["pool_source"].maintain(timeout=5)
        projected.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(coordinator, "_gc_loop", gc)
    with TestClient(owners.app):
        assert projected.wait(5)
        assert owners.queue.pool_configured_cohort().revision == 1
    assert owners.source._closed and owners.source._maintenance is None


@pytest.mark.parametrize("publication,interval", [(False, 0), (False, 1), (True, 1), (True, 0)])
def test_actual_serve_command_preserves_publication_and_its_gc_prerequisite(
    owners: Any, monkeypatch: pytest.MonkeyPatch, publication: bool, interval: int,
) -> None:
    from agent_dispatch import coordinator_cli

    monkeypatch.setenv("AGENT_DISPATCH_POOL_PUBLICATION", "1" if publication else "0")
    monkeypatch.setenv("AGENT_DISPATCH_GC_INTERVAL", str(interval))
    monkeypatch.setenv("AGENT_DISPATCH_DB", str(owners.base / "cli.db"))
    monkeypatch.setattr(coordinator_cli, "_reroot_serve_cwd", lambda: None)
    seen = []

    def serve(cfg: config.Config, **_kwargs: Any) -> None:
        seen.append(cfg)
        server.build_app(cfg)

    monkeypatch.setattr(server, "serve", serve)
    args = SimpleNamespace(
        host="127.0.0.1", port=None, db=None, token="data",
        control_token="control", passive=True,
    )
    if publication and interval == 0:
        with pytest.raises(ValueError, match="enabled GC cadence"):
            coordinator_cli._cmd_serve(args)
    else:
        assert coordinator_cli._cmd_serve(args) == 0
    assert len(seen) == 1
    assert seen[0].pool_publication_enabled is publication
    assert seen[0].sweep_interval == interval
    assert owners.queue.pool_configured_cohort().revision == 0
