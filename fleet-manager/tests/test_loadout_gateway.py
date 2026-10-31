"""A real published Git source crosses actual authenticated TLS/PoP/WebSocket custody."""

from __future__ import annotations

import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from fleet_manager.gateway_service_caller import ServiceCaller, ServiceResponse as CallerResponse
from fleet_manager.loadout_gateway import (
    PLAN_CHUNK, PreparedLoadoutClient, PreparedLoadoutService, ReviewScope, _settled,
)
from fleet_manager.loadout_preparation import PreparationStore
from fleet_manager.observation_store import ObservationStore
from fleet_manager.observations import OriginBinding
from fleet_manager.planning import PlanError, canonical
from gateway_service_transport_fixtures import eventually, running
from test_gateway_service_handler import connect_handler

pytest_plugins = ["test_loadout_preparation"]


def service(root: Path, published, *, authorize=None, acquisition_seconds=30):
    store = PreparationStore(root / "prepared.sqlite", fleet="fleet-a", installation="preparation-a")
    observations = ObservationStore(root / "observations.sqlite", authority="authority-a",
                                    installation="gateway-installation")
    origin = OriginBinding("preparation", "preparation-a", 1, "loadout-controller")
    observations.bind_origin(origin)
    document = copy.deepcopy(published["inventory"])
    document["fleet_id"] = "fleet-a"

    async def inventory():
        return copy.deepcopy(document)

    controller = PreparedLoadoutService(
        fleet="fleet-a", gateway="gateway-a", slot="service-a",
        config=published["source"](), catalog=published["source"]("catalog.json"),
        store=store, observations=observations, origin=origin, project="loadouts",
        authorize=authorize or (lambda _context: ReviewScope("account-a", "policy-1", True)),
        inventory=inventory,
        **({"acquisition_seconds": acquisition_seconds} if acquisition_seconds is not None else {}),
    )
    return controller, document


def test_default_acquisition_limit_and_explicit_deadline_refusal(tmp_path, repositories):
    publish_fleet(repositories)
    default, _ = service(tmp_path, repositories, acquisition_seconds=None)
    assert default.acquisition_seconds == 15
    with pytest.raises(PlanError, match="invalid prepared loadout service binding"):
        service(tmp_path, repositories, acquisition_seconds=31)

    async def scenario():
        async with running(tmp_path) as rig:
            controller, _ = service(tmp_path, repositories, acquisition_seconds=0.001)
            await connect_handler(rig, controller.binding().handle)
            blocked = await client(rig).refresh()
            assert blocked["candidate"]["status"] == "blocked"
            assert blocked["candidate"]["reason"] == "preparation-deadline-exceeded"
            assert blocked["candidate"]["plan_digest"] is None
            assert blocked["candidate"]["plan_bytes"] == 0
            assert blocked["decision"] is None
            assert not blocked["activation_authorized"] and not blocked["live_readiness_verified"]
            assert rig.backends[0].requests == []
    asyncio.run(scenario())


def publish_fleet(published):
    config = copy.deepcopy(published["config"])
    config["fleet_id"] = "fleet-a"
    published["publish"](config=config)
    return config


def client(rig):
    async def access():
        rig.h.clock.value = int(time.time())
        return rig.access("caller-a", 3)

    caller = ServiceCaller(
        gateway_origin=rig.origin, gateway_ca=rig.ca,
        proof_key=rig.h.keys[3].get_op_key("sign"), access_token_callback=access,
    )
    return PreparedLoadoutClient(caller, prefix="/api")


def test_real_source_pull_notification_plan_confirmation_repeat_and_restart(tmp_path, repositories, monkeypatch):
    config = publish_fleet(repositories)
    config["extensions"]["custom"]["large"] = "x" * 40000
    repositories["publish"](config=config)

    async def scenario():
        async with running(tmp_path) as rig:
            controller, inventory = service(tmp_path, repositories)
            await connect_handler(rig, controller.binding().handle)
            thin = client(rig)
            assert (await thin.inspect())["status"] == "absent"
            prepared = await thin.refresh()
            assert prepared["candidate"]["status"] == "prepared"
            assert prepared["candidate"]["plan_bytes"] > PLAN_CHUNK * 2
            assert prepared["candidate"]["execution_admission"] == "not-implemented"
            from fleet_manager import loadout_gateway

            encode = loadout_gateway.canonical

            def metadata_only(value, **options):
                if isinstance(value, dict) and value.get("schema") == "fleet-manager.loadout-design":
                    raise AssertionError("full plan encoding on the Gateway event loop")
                return encode(value, **options)

            def no_full_read():
                raise AssertionError("full store read for a plan page")

            with monkeypatch.context() as selected:
                selected.setattr(loadout_gateway, "canonical", metadata_only)
                selected.setattr(controller.store, "read", no_full_read)
                plan = await thin.plan(prepared)
            assert plan["suggested_config"]["roles"] == config["roles"]
            assert plan["composition"]["allocations"]["runners"]["configuration"]["adapter"] == "helm.arc"
            point = controller.observations.read(
                origin="preparation", project="loadouts", entity="preparation-a")
            notification = point["row"]["update"]
            assert json.loads(notification["state"]["summary"])["digest"] == prepared["digest"]
            cursor = point["cursor"]
            delivered = await thin.notifications()
            assert delivered["current"]["digest"] == prepared["digest"]
            delivered_cursor = delivered["cursor"]
            with pytest.raises(PlanError):
                await thin.notifications({**delivered_cursor, "project": "another-project"})
            assert (await thin.refresh()) == prepared
            confirmed = await thin.confirm(
                generation=prepared["generation"], selected_digest=prepared["digest"])
            assert confirmed["decision"]["reviewer"] == {
                "principal": "caller-a", "account": "account-a", "policy_revision": "policy-1",
            }
            assert not confirmed["activation_authorized"] and not confirmed["live_readiness_verified"]
            replay = controller.observations.replay(cursor)
            assert len(replay["events"]) == 1
            assert replay["events"][0]["update"]["state"]["status"] == "review-confirmed"
            replayed = await thin.notifications(delivered_cursor)
            assert len(replayed["events"]) == 1
            assert replayed["events"][0]["status"] == "review-confirmed"
            assert await thin.confirm(
                generation=prepared["generation"], selected_digest=prepared["digest"]) == confirmed
            restarted, _ = service(tmp_path, repositories)
            assert restarted.store.read() == controller.store.read()
            assert await restarted.handle(next_request(rig, "/prepared")) == (
                await controller.handle(next_request(rig, "/prepared")))
            replacement = OriginBinding("preparation", "preparation-a", 2, "loadout-controller")
            controller.observations.bind_origin(replacement, expected=controller.origin)
            controller.origin = replacement
            assert (await thin.inspect()) == confirmed
            rebound = controller.observations.read(
                origin="preparation", project="loadouts", entity="preparation-a")
            assert rebound["row"]["coverage"] == "point-observation"
            assert rebound["row"]["update"]["generation"] == 2
            assert rebound["row"]["update"]["sequence"] == 1
            inventory["revision"] = "i2"
            with pytest.raises(PlanError, match="request refused"):
                await thin.confirm(generation=prepared["generation"], selected_digest=prepared["digest"])
            changed = await thin.inspect()
            assert changed["generation"] > prepared["generation"] and changed["decision"] is None
            assert rig.backends[0].requests == []
    asyncio.run(scenario())


def test_repeated_cancellation_joins_owned_source_work_before_returning():
    async def scenario():
        started, release, completed = threading.Event(), threading.Event(), threading.Event()

        def worker():
            started.set()
            assert release.wait(3)
            completed.set()

        task = asyncio.create_task(_settled(worker))
        try:
            await eventually(started.is_set)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done() and not completed.is_set()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert completed.is_set()
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def next_request(rig, target, method="GET", body=b""):
    from fleet_manager.gateway_service_handler import CallerContext, ServiceRequest

    return ServiceRequest(
        CallerContext("fleet-a", "gateway-a", "service-a", "caller-a", rig.h.clock.value + 3600),
        method, target, (), body,
    )


def test_published_change_invalidates_old_decision_and_never_accepts_uploaded_config(tmp_path, repositories):
    config = publish_fleet(repositories)

    async def scenario():
        async with running(tmp_path) as rig:
            controller, _ = service(tmp_path, repositories)
            await connect_handler(rig, controller.handle)
            thin = client(rig)
            prepared = await thin.refresh()
            config["revision"] = "c2"
            await asyncio.to_thread(repositories["publish"], config=config)
            with pytest.raises(PlanError):
                await thin.confirm(generation=prepared["generation"], selected_digest=prepared["digest"])
            changed = await thin.inspect()
            assert changed["candidate"]["sources"]["config"]["commit"] != (
                prepared["candidate"]["sources"]["config"]["commit"])
            response = await thin.caller.request("POST", "/api/confirm", body=canonical({
                "generation": changed["generation"], "digest": changed["digest"], "config": config,
            }))
            assert response.status == 403
            assert controller.store.read()["decision"] is None
            stale = await thin.caller.request(
                "GET", f"/api/plan/{prepared['generation']}/{prepared['digest']}/0")
            assert stale.status == 409 and json.loads(stale.body) == {
                "error": "prepared-generation-changed",
            }
    asyncio.run(scenario())


def test_matching_foreign_config_and_inventory_cannot_cross_the_gateway_fleet_binding(tmp_path, repositories):
    async def scenario():
        async with running(tmp_path) as rig:
            controller, inventory = service(tmp_path, repositories)
            inventory["fleet_id"] = repositories["config"]["fleet_id"]
            await connect_handler(rig, controller.handle)
            thin = client(rig)
            blocked = await thin.refresh()
            assert blocked["candidate"]["fleet"] == "fleet-a"
            assert blocked["candidate"]["status"] == "blocked"
            assert blocked["candidate"]["reason"] == "published-loadout-invalid"
            assert controller.store.read()["plan"] is None
            with pytest.raises(PlanError):
                await thin.confirm(generation=blocked["generation"], selected_digest=blocked["digest"])
            assert controller.store.read()["decision"] is None
    asyncio.run(scenario())


def test_current_policy_denial_and_withdrawal_during_real_pull_have_no_decision(tmp_path, repositories):
    publish_fleet(repositories)
    selected = {"scope": ReviewScope("account-a", "policy-1", True)}

    def authorize(_context):
        if selected["scope"] is None:
            raise PermissionError
        return selected["scope"]

    async def scenario():
        async with running(tmp_path) as rig:
            controller, _ = service(tmp_path, repositories, authorize=authorize)
            await connect_handler(rig, controller.handle)
            thin = client(rig)
            prepared = await thin.refresh()
            selected["scope"] = ReviewScope("account-a", "policy-1", False)
            with pytest.raises(PlanError):
                await thin.confirm(generation=prepared["generation"], selected_digest=prepared["digest"])
            selected["scope"] = None
            assert (await thin.caller.request("GET", "/api/prepared")).status == 403
            assert controller.store.read()["decision"] is None
            selected["scope"] = ReviewScope("account-a", "policy-1", True)
            original_inventory = controller.inventory

            async def withdrawing():
                result = await original_inventory()
                selected["scope"] = None
                return result

            controller.inventory = withdrawing
            assert (await thin.caller.request("POST", "/api/refresh")).status == 403
            assert controller.store.read()["generation"] == prepared["generation"]
            assert controller.store.read()["decision"] is None
    asyncio.run(scenario())


def test_failed_source_preserves_valid_plan_and_emits_blocked_notification(tmp_path, repositories):
    publish_fleet(repositories)

    async def scenario():
        async with running(tmp_path) as rig:
            controller, _ = service(tmp_path, repositories)
            await connect_handler(rig, controller.handle)
            thin = client(rig)
            previous = await thin.refresh()
            await asyncio.to_thread(
                repositories["run"], "remote", "set-url", "origin",
                str(tmp_path / "missing"), cwd=repositories["controller"])
            blocked = await thin.refresh()
            assert blocked["candidate"]["status"] == "blocked"
            assert blocked["candidate"]["reason"] == "published-source-unavailable"
            assert blocked["generation"] > previous["generation"] and blocked["decision"] is None
            point = controller.observations.read(
                origin="preparation", project="loadouts", entity="preparation-a")
            assert point["row"]["update"]["state"]["status"] == "blocked"
            with controller.store._transaction() as connection:
                assert connection.execute("SELECT length(retained) FROM proposal").fetchone()[0] > 0
    asyncio.run(scenario())


def test_native_adapter_failure_retains_ownership_and_prevents_automatic_replay(tmp_path, repositories):
    from dataclasses import replace
    from types import SimpleNamespace

    failure = subprocess.SubprocessError("synthetic native custody retained")
    calls = []

    def refused(*_args, **_kwargs):
        calls.append(True)
        raise failure

    async def scenario():
        controller, _ = service(tmp_path, repositories)
        controller.config = replace(controller.config, run=refused)
        rig = SimpleNamespace(h=SimpleNamespace(clock=SimpleNamespace(value=int(time.time()))))
        with pytest.raises(subprocess.SubprocessError) as raised:
            await controller.handle(next_request(rig, "/refresh", "POST"))
        assert raised.value is failure and controller.repository_failure is failure
        response = await controller.handle(next_request(rig, "/refresh", "POST"))
        assert response.status == 503 and json.loads(response.body) == {
            "error": "repository-owner-required",
        }
        assert len(calls) == 1 and controller.store.read() is None
    asyncio.run(scenario())


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_settled_native_output_overflow_is_blocked_inspectable_and_recoverable(tmp_path, repositories, stream):
    from dataclasses import replace

    publish_fleet(repositories)

    async def scenario():
        async with running(tmp_path) as rig:
            controller, _ = service(tmp_path, repositories)
            original = controller.config.run
            failed = []

            def overflow_once(argv, **options):
                if not failed:
                    failed.append(True)
                    return original(
                        [sys.executable, "-I", "-c", f"import sys;sys.{stream}.write('x'*5000)"],
                        **options,
                    )
                return original(argv, **options)

            controller.config = replace(controller.config, run=overflow_once)
            await connect_handler(rig, controller.handle)
            thin = client(rig)
            blocked = await thin.refresh()
            assert blocked["candidate"]["status"] == "blocked"
            assert blocked["candidate"]["reason"] == "published-source-unavailable"
            assert controller.repository_failure is None
            assert (await thin.inspect())["digest"] == blocked["digest"]
            assert (await thin.notifications())["current"]["status"] == "blocked"
            recovered = await thin.refresh()
            assert recovered["candidate"]["status"] == "prepared"
            assert recovered["generation"] > blocked["generation"]
            assert controller.repository_failure is None and len(failed) == 1
    asyncio.run(scenario())


def test_maximum_client_download_obeys_real_gateway_budgets_and_recovers_read_admission(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fleet_manager import gateway_service_server, loadout_gateway
    from fleet_manager.gateway_service_channel import CONTROL_RATE, REQUEST_RATE, TransportError
    from fleet_manager.gateway_service_server import ServiceRuntime
    from gateway_service_transport_fixtures import Rig
    from test_loadout_preparation import candidate, maximum_plan

    async def scenario():
        clock = SimpleNamespace(value=0.0)
        pause = asyncio.sleep

        async def advance(delay):
            clock.value += delay
            await pause(0)

        monkeypatch.setattr(loadout_gateway, "monotonic", lambda: clock.value)
        monkeypatch.setattr(loadout_gateway, "sleep", advance)
        monkeypatch.setattr(gateway_service_server, "time", SimpleNamespace(
            monotonic=lambda: clock.value, time=time.time,
        ))
        rig = Rig(tmp_path)
        runtime = ServiceRuntime(rig.h.owner, rig.listener, 0.05)
        runtime.budget = 0
        plan = maximum_plan(fleet="fleet-a")
        store = PreparationStore(tmp_path / "large.sqlite", fleet="fleet-a", installation="preparation-a")
        selected = {**candidate(plan), "fleet": "fleet-a", "installation": "preparation-a"}
        current = store.publish(selected, plan, expected=0, now=150)
        attempts, accepted, refused = [], [], []

        class BudgetCaller:
            async def request(self, method, target, *, body=b""):
                assert method == "GET" and body == b""
                attempts.append(target)
                try:
                    runtime.control_budget()
                except TransportError as error:
                    assert error.code == "control-rate-limit"
                    refused.append(True)
                    raise TransportError("gateway-challenge-refused", 429) from None
                runtime.admit_request()
                parts = target.split("/")
                assert parts[:3] == ["", "api", "plan"]
                assert parts[3:5] == [str(current["generation"]), current["digest"]]
                encoded = store.read_plan_range(
                    generation=int(parts[3]), selected_digest=parts[4], offset=int(parts[5]),
                )
                accepted.append(clock.value)
                return CallerResponse(200, (), encoded)

        thin = PreparedLoadoutClient(BudgetCaller(), prefix="/api")
        assert await thin.plan(PreparedLoadoutService._summary(current)) == plan
        assert len(accepted) == 256 and len(attempts) == 257 and len(refused) == 1
        interval = 2 / min(CONTROL_RATE, REQUEST_RATE)
        assert all(right - left >= interval - 1e-9 for left, right in zip(accepted, accepted[1:]))
        assert clock.value <= (len(accepted) + 4) * interval
        assert not rig.tasks and not rig.connectors
    asyncio.run(scenario())


@pytest.mark.parametrize("code,status,expected", [
    ("gateway-challenge-refused", 429, 3),
    ("gateway-challenge-refused", 401, 1),
    ("gateway-timeout", 502, 1),
    ("outcome-unknown", 502, 1),
])
def test_page_read_admission_recovery_is_bounded_and_never_retries_other_failures(monkeypatch, code, status, expected):
    from types import SimpleNamespace

    from fleet_manager import loadout_gateway
    from fleet_manager.gateway_service_channel import TransportError

    async def scenario():
        clock = SimpleNamespace(value=0.0)
        attempts = []
        failure = TransportError(code, status)

        async def advance(delay):
            clock.value += delay

        class RefusedCaller:
            async def request(self, method, target, *, body=b""):
                attempts.append((method, target, body))
                raise failure

        monkeypatch.setattr(loadout_gateway, "monotonic", lambda: clock.value)
        monkeypatch.setattr(loadout_gateway, "sleep", advance)
        thin = PreparedLoadoutClient(RefusedCaller(), prefix="/api")
        with pytest.raises(TransportError) as raised:
            await thin.plan({
                "generation": 1, "digest": "1" * 64,
                "candidate": {"plan_bytes": 1, "plan_digest": "2" * 64},
            })
        assert raised.value is failure and len(attempts) == expected
        assert len(set(attempts)) == 1 and attempts[0][0] == "GET" and attempts[0][2] == b""
    asyncio.run(scenario())
