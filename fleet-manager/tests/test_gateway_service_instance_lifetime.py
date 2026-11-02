"""Embedded TLS service admission outlives all application-owned cleanup."""

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import socket
import threading
from unittest.mock import AsyncMock, Mock

import aiohttp
from aiohttp import web
import pytest

from agent_procutil.daemon_instances import inspect_instances
from fleet_manager import gateway_service_server as server
from fleet_manager.gateway_service_connector import verified_tls
from fleet_manager.gateway_service_context import ServicePolicyError
from gateway_service_transport_fixtures import Rig


def bootstrap(root):
    root.mkdir(exist_ok=True)
    rig = Rig(root)
    rig.save()
    return rig


def observe_admission(monkeypatch):
    guards = []
    admit = server.admit_daemon_instance

    def acquire(*args, **kwargs):
        guard = admit(*args, **kwargs)
        guards.append(guard)
        return guard

    monkeypatch.setattr(server, "admit_daemon_instance", acquire)
    return guards


def record(receipt):
    return next(
        row for row in inspect_instances(receipt.registry_root)
        if row["instance_id"] == receipt.instance_id
    )


async def challenge(serving, rig):
    async with aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(ssl=verified_tls(rig.ca)), trust_env=False,
    ) as client:
        async with client.get(serving.origin + "/_fleet/challenge") as response:
            assert response.status == 200
            assert set(await response.json()) == {"nonce"}


@pytest.mark.parametrize("embedding", ["build", "start"])
def test_policy_reader_uses_its_admitted_lexical_selection(tmp_path, monkeypatch, embedding):
    launch = tmp_path / "launch"
    launch.mkdir()
    rig = bootstrap(launch / "state")
    redirected = tmp_path / "redirected"
    (redirected / "child").mkdir(parents=True)
    (redirected / "state").mkdir()
    (redirected / "state" / rig.policy.name).write_text("{}")
    alias = launch / "link"
    try:
        alias.symlink_to(redirected / "child", target_is_directory=True)
    except OSError as error:
        pytest.skip(f"native directory symlink unavailable: {error}")
    original_read = server.read_bounded
    expected = original_read(rig.policy)
    reads = []

    def read(path):
        value = original_read(path)
        reads.append((path, value))
        return value

    monkeypatch.setattr(server, "read_bounded", read)

    async def scenario():
        serving = None
        try:
            selected = alias / ".." / "state" / rig.policy.name
            if embedding == "start":
                serving = await server.start(
                    rig.listener, selected, reload_interval=60, clock=rig.h.clock,
                )
                await challenge(serving, rig)
            else:
                await server.build_app(rig.listener, selected, clock=rig.h.clock)
            assert reads
            assert all(path == rig.policy and value == expected for path, value in reads)
        finally:
            if serving is not None:
                await serving.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("ending", ["startup", "shutdown", "repeated-reload-cancel"])
def test_native_policy_reload_settles_before_admission(tmp_path, monkeypatch, ending):
    async def scenario():
        rig = bootstrap(tmp_path)
        guards = observe_admission(monkeypatch)
        blocked, entered, release, retired = (
            threading.Event(), threading.Event(), threading.Event(), threading.Event()
        )
        original_reload = server.ServiceAuthority.reload
        original_loop = server.ServiceRuntime.reload_loop
        owners = []

        def reload(authority):
            if not blocked.is_set():
                return original_reload(authority)
            entered.set()
            try:
                assert release.wait(10)
                return original_reload(authority)
            finally:
                retired.set()

        async def reload_loop(state):
            owners.append(asyncio.current_task())
            await original_loop(state)

        monkeypatch.setattr(server.ServiceAuthority, "reload", reload)
        monkeypatch.setattr(server.ServiceRuntime, "reload_loop", reload_loop)
        serving = None
        closing = None
        if ending == "startup":
            blocked.set()
        starting = asyncio.create_task(server.start(
            rig.listener, rig.policy, reload_interval=0.05, clock=rig.h.clock,
        ))
        try:
            if ending != "startup":
                serving = await asyncio.wait_for(asyncio.shield(starting), 5)
                await challenge(serving, rig)
                blocked.set()
            assert await asyncio.to_thread(entered.wait, 5)
            if ending == "startup":
                starting.cancel()
                pending = starting
            else:
                closing = asyncio.create_task(serving.close())
                while serving._close_task is None or owners[0].cancelling() == 0:
                    await asyncio.sleep(0.001)
                pending = serving._close_task
                if ending == "repeated-reload-cancel":
                    for _ in range(3):
                        owners[0].cancel()
                        await asyncio.sleep(0)
            await asyncio.wait({pending}, timeout=0.05)
            assert (
                pending.done(), retired.is_set(), record(guards[0].receipt)["state"],
            ) == (False, False, "running")
            release.set()
            if ending == "startup":
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(asyncio.shield(starting), 5)
            else:
                await asyncio.wait_for(asyncio.shield(closing), 5)
            assert retired.is_set()
            assert record(guards[0].receipt)["state"] == "finished"
        finally:
            release.set()
            await asyncio.gather(starting, return_exceptions=True)
            if serving is not None:
                await serving.close()
            if closing is not None:
                await asyncio.gather(closing, return_exceptions=True)

    asyncio.run(scenario())


def test_embedded_tls_admission_spans_completed_cross_task_idempotent_close(tmp_path, monkeypatch):
    async def scenario():
        rig = bootstrap(tmp_path)
        guards = observe_admission(monkeypatch)
        entered, release = asyncio.Event(), asyncio.Event()
        build = server.build_app
        effects = []

        async def build_app(*args, **kwargs):
            app = await build(*args, **kwargs)

            async def late_cleanup(application):
                effects.append(record(guards[0].receipt)["state"])
                entered.set()
                await release.wait()

            app.on_cleanup.append(late_cleanup)
            return app

        monkeypatch.setattr(server, "build_app", build_app)
        serving = await asyncio.create_task(server.start(
            rig.listener, rig.policy, reload_interval=0.05, clock=rig.h.clock,
        ))
        receipt = guards[0].receipt
        assert len(guards) == 2
        assert guards[1].receipt == receipt
        assert record(receipt)["state"] == "running"
        assert record(receipt)["purpose"] == "unattended-test"
        assert float(record(receipt)["deadline"]) > 0
        await challenge(serving, rig)
        closing = asyncio.create_task(serving.close())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert effects == ["running"]
            assert record(receipt)["state"] == "running"
            assert not guards[0]._closed and guards[1]._closed
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            assert record(receipt)["state"] == "running"
        finally:
            release.set()
            await asyncio.wait_for(asyncio.create_task(serving.close()), 5)
        assert record(receipt)["state"] == "finished"
        assert serving.runner.addresses == []
        await serving.close()
        assert record(receipt)["state"] == "finished"

    asyncio.run(scenario())


def test_app_runner_lifecycle_admits_before_reload_and_tls(tmp_path, monkeypatch):
    async def scenario():
        rig = bootstrap(tmp_path)
        guards = observe_admission(monkeypatch)
        loop_started = asyncio.Event()
        reload_loop = server.ServiceRuntime.reload_loop

        async def observed(state):
            assert record(guards[0].receipt)["state"] == "running"
            loop_started.set()
            await reload_loop(state)

        monkeypatch.setattr(server.ServiceRuntime, "reload_loop", observed)
        app = await server.build_app(rig.listener, rig.policy, clock=rig.h.clock)
        assert guards == []
        runner = web.AppRunner(app)
        try:
            await asyncio.create_task(runner.setup())
            await asyncio.wait_for(loop_started.wait(), 5)
            assert len(guards) == 1
            await web.TCPSite(
                runner, rig.listener.host, 0, ssl_context=rig.listener.tls(),
            ).start()
            serving = server.Serving(
                runner, app, rig.listener.bound_origin(runner.addresses[0][1]),
            )
            await challenge(serving, rig)
        finally:
            await asyncio.create_task(runner.cleanup())
        assert record(guards[0].receipt)["state"] == "finished"

    asyncio.run(scenario())


def test_embedded_tls_borrows_cli_guard_without_duplicate_or_early_release(tmp_path):
    async def scenario():
        rig = bootstrap(tmp_path)
        guard = server.admit_daemon_instance(
            "fleet-manager-service-gateway", source=server.__file__,
            runtime_root=tmp_path, purpose="production",
        )
        receipt = guard.receipt
        before = {row["instance_id"] for row in inspect_instances(receipt.registry_root)}
        serving = None
        try:
            serving = await server.start(rig.listener, rig.policy, clock=rig.h.clock)
            assert serving._instance_guard.receipt == receipt
            assert {row["instance_id"] for row in inspect_instances(receipt.registry_root)} == before
            await challenge(serving, rig)
            await serving.close()
            assert record(receipt)["state"] == "running"
        finally:
            if serving is not None:
                await serving.close()
            guard.close()
        assert record(receipt)["state"] == "finished"

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["policy", "bind"])
def test_embedded_startup_failure_finishes_all_acquired_guards(tmp_path, monkeypatch, stage):
    async def scenario():
        rig = bootstrap(tmp_path)
        guards = observe_admission(monkeypatch)
        listener = rig.listener
        with socket.socket() as occupied:
            occupied.bind(("127.0.0.1", 0))
            occupied.listen()
            if stage == "policy":
                rig.policy.write_text("{}", encoding="utf-8")
            else:
                port = occupied.getsockname()[1]
                listener = replace(listener, port=port, origin=f"https://localhost:{port}")
            with pytest.raises(ServicePolicyError if stage == "policy" else OSError):
                await server.start(listener, rig.policy, clock=rig.h.clock)
        assert len(guards) == (1 if stage == "policy" else 2)
        assert all(guard._closed for guard in guards)
        assert record(guards[0].receipt)["state"] == "finished"

    asyncio.run(scenario())


def test_closing_one_embedded_root_preserves_another_live_tls_root(tmp_path):
    async def scenario():
        first = bootstrap(tmp_path / "first")
        second = bootstrap(tmp_path / "second")
        sentinel = first.root / "unrelated-active.txt"
        sentinel.write_text("retained", encoding="utf-8")
        first_serving = await server.start(first.listener, first.policy, clock=first.h.clock)
        receipt = first_serving._instance_guard.receipt
        try:
            second_serving = await server.start(second.listener, second.policy, clock=second.h.clock)
            try:
                await challenge(first_serving, first)
                await challenge(second_serving, second)
            finally:
                await second_serving.close()
            assert record(receipt)["state"] == "running"
            await challenge(first_serving, first)
            assert sentinel.read_text(encoding="utf-8") == "retained"
            assert first.policy.is_file() and second.policy.is_file()
        finally:
            await first_serving.close()
        assert record(receipt)["state"] == "finished"
        assert sentinel.read_text(encoding="utf-8") == "retained"

    asyncio.run(scenario())


def test_cancelled_embedded_start_joins_listener_and_reload_cleanup(tmp_path, monkeypatch):
    async def scenario():
        rig = bootstrap(tmp_path)
        guards = observe_admission(monkeypatch)
        entered = asyncio.Event()
        sites = []
        start_site = web.TCPSite.start

        async def start(site):
            await start_site(site)
            sites.append(site)
            entered.set()
            await asyncio.Future()

        monkeypatch.setattr(web.TCPSite, "start", start)
        task = asyncio.create_task(server.start(rig.listener, rig.policy, clock=rig.h.clock))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            assert record(guards[0].receipt)["state"] == "running"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 5)
        assert all(guard._closed for guard in guards)
        assert record(guards[0].receipt)["state"] == "finished"
        assert not sites[0]._server.is_serving()
        assert sites[0]._runner.addresses == []

    asyncio.run(scenario())


def test_embedded_admission_refusal_precedes_tls_policy_and_listener(tmp_path, monkeypatch):
    async def scenario():
        rig = bootstrap(tmp_path)
        monkeypatch.setattr(
            server, "admit_daemon_instance", Mock(side_effect=ValueError("admission refused")),
        )
        tls = Mock(side_effect=AssertionError("TLS before admission"))
        build = AsyncMock(side_effect=AssertionError("policy before admission"))
        monkeypatch.setattr(server.Listener, "tls", tls)
        monkeypatch.setattr(server, "build_app", build)
        with pytest.raises(ValueError, match="admission refused"):
            await server.start(rig.listener, rig.policy)
        tls.assert_not_called()
        build.assert_not_awaited()

    asyncio.run(scenario())


def test_embedded_parent_relative_policy_reaches_real_tls_listener(tmp_path, monkeypatch):
    async def scenario():
        rig = bootstrap(tmp_path / "config")
        launch = tmp_path / "launch"
        launch.mkdir()
        policy = Path(os.path.relpath(rig.policy, launch))
        monkeypatch.chdir(launch)
        serving = await server.start(rig.listener, policy, clock=rig.h.clock)
        receipt = serving._instance_guard.receipt
        try:
            assert record(receipt)["state"] == "running"
            await challenge(serving, rig)
        finally:
            await serving.close()
        assert record(receipt)["state"] == "finished"

    asyncio.run(scenario())
