"""Real instance-bound placement through the ordinary supervision caller."""

from __future__ import annotations

from dataclasses import asdict
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
from threading import Barrier

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import __main__ as cli, embody, identity, session_acquisition, spawn_factories
from agent_dispatch.client import DispatchError, DispatchUpgradeRequired
from agent_dispatch.coordinator import create_app
from agent_dispatch.ordinary_bridge import reserve_for_backend
from agent_dispatch.ordinary_invocation import startup_seed
from agent_dispatch.queue import Status, TaskError
from agent_dispatch.supervisor import Supervisor
from agent_dispatch.supervisor_pool_allocator import launch_scope
from agent_dispatch.spawn_factories import SpawnPreparationRetained
from agent_dispatch.supervisor_registration import build_command
from tests import test_pool_placement_client as controls
from tests.test_pool_placement import pool, publish, task

q = controls.q
api = controls.api


def test_other_global_winner_does_not_claim_slot_or_change_driver(q, api):
    revision = publish(q, pool("high", priority=9), pool("low", priority=1))
    requested = task(q)
    before = q.get(requested.id)
    requests = []
    with controls.connected(api, requests) as client:
        with pytest.raises(DispatchError, match="another launch owner"):
            client.place_pool_task(
                requested.id, reserved_by="low", catalog_revision=revision, driver="ordinary-bridge",
                expected_instance=("low", "host-a", "linux"),
            )
        assert q.get(requested.id) == before
        assert q.task_pool_assignment(requested.id) is None
        assert q.list_reservations(task_id=requested.id) == []
        placed = client.place_pool_task(
            requested.id, reserved_by="high", catalog_revision=revision, driver="ordinary-bridge",
            expected_instance=("high", "host-a", "linux"),
        )
    assert placed["reserved"]
    assert placed["assignment"]["registration_id"] == "high"
    assert placed["reservation"]["driver"] == "ordinary-bridge"
    assert len(q.list_reservations(task_id=requested.id)) == 1


@pytest.mark.parametrize("instance", [
    ("other", "host-a", "linux"), ("worker", "host-b", "linux"),
    ("worker", "host-a", "other"),
])
def test_active_replay_cannot_be_adopted_by_another_instance(q, api, instance):
    revision = publish(q, pool())
    requested = task(q)
    placed = q.place_pool_task(
        requested.id, reserved_by="worker", catalog_revision=revision, driver="ordinary-bridge",
    )
    before = q.get(requested.id)
    with controls.connected(api, []) as client:
        with pytest.raises(DispatchError, match="another launch owner"):
            client.place_pool_task(
                requested.id, reserved_by=instance[0], catalog_revision=revision - 1,
                driver="ordinary-bridge", expected_instance=instance,
            )
    assert q.get(requested.id) == before
    assert q.get_reservation(placed.reservation.key) == placed.reservation


def test_same_registration_instances_keep_global_ties_and_independent_slots(q, api):
    revision = publish(q, pool(), pool(env="other"), pool(machine="host-b"))
    requests = []
    with controls.connected(api, requests) as client:
        for instance in (
            ("worker", "host-a", "linux"), ("worker", "host-a", "other"),
            ("worker", "host-b", "linux"),
        ):
            placed = client.place_pool_task(
                task(q).id, reserved_by="worker", catalog_revision=revision,
                driver="ordinary-bridge", expected_instance=instance,
            )
            assert tuple(placed["assignment"][key] for key in ("registration_id", "machine", "env")) == instance
        with pytest.raises(DispatchError, match="capacity"):
            client.place_pool_task(
                task(q).id, reserved_by="worker", catalog_revision=revision,
                driver="ordinary-bridge", expected_instance=("worker", "host-a", "linux"),
            )


@pytest.mark.parametrize("versions", [None, [], [True], [2]])
def test_old_bound_placement_contract_does_not_write(q, api, versions):
    revision = publish(q, pool())
    requested = task(q)
    requests = []

    def rewrite(request, response):
        if request.url.path == "/pool-placements/contract":
            body = response.json()
            body["instance_bound_placement_versions"] = versions
            return httpx.Response(200, json=body)
        return response

    with controls.connected(api, requests, rewrite=rewrite) as client:
        with pytest.raises(DispatchUpgradeRequired, match="instance-bound"):
            client.place_pool_task(
                requested.id, reserved_by="worker", catalog_revision=revision,
                driver="ordinary-bridge", expected_instance=("worker", "host-a", "linux"),
            )
    assert all(request.method == "GET" for request in requests)
    assert q.get(requested.id).status == Status.QUEUED


def test_changed_bound_result_preserves_unknown_committed_claim_without_replay(q, api):
    revision = publish(q, pool())
    requested = task(q)
    requests = []

    def rewrite(request, response):
        if request.url.path == "/pool-placements":
            body = response.json()
            body["assignment"]["env"] = "other"
            return httpx.Response(200, json=body)
        return response

    with controls.connected(api, requests, rewrite=rewrite) as client:
        with pytest.raises(DispatchError, match="outcome is unknown"):
            client.place_pool_task(
                requested.id, reserved_by="worker", catalog_revision=revision,
                driver="ordinary-bridge", expected_instance=("worker", "host-a", "linux"),
            )
    assert q.get(requested.id).status == Status.CLAIMED
    assert len(q.list_reservations(task_id=requested.id)) == 1
    assert sum(request.method == "POST" for request in requests) == 1


def managed_args(machine="host-a", env="linux", supervisor="worker"):
    return cli.build_parser().parse_args([
        "supervise", "--all-repos", "--once", "--supervisor-id", supervisor,
        "--registration-machine", machine, "--registration-env", env,
    ])


def test_actual_command_parser_preserves_registration_scope_not_ambient_env(monkeypatch):
    monkeypatch.setattr(identity, "resolve_machine", lambda: "HOST-A")
    monkeypatch.setenv("AGENT_DISPATCH_ENV", "wrong-ambient")
    command = build_command({
        "id": "worker", "kind": "supervised-lane", "machine": "host-a", "env": "linux",
        "spec": {"all_repos": True},
    }, python="python")
    args = cli.build_parser().parse_args(command[3:])
    from agent_dispatch.supervise_cli import _registration_scope
    assert launch_scope(args) == _registration_scope(args) == ("host-a", "linux")


@pytest.mark.parametrize("machine,env,supervisor,actual", [
    ("host-a", "", "worker", "host-a"), ("", "linux", "worker", "host-a"),
    ("host-a", "linux", "", "host-a"), ("host-a", "linux", "worker", "host-b"),
    ("host-a", "linux", "worker", None),
])
def test_bad_managed_scope_refuses_before_launch(monkeypatch, machine, env, supervisor, actual):
    monkeypatch.setattr(identity, "resolve_machine", lambda: actual)
    with pytest.raises(ValueError, match="managed lane"):
        launch_scope(managed_args(machine, env, supervisor))


def test_legacy_foreground_scope_does_not_invent_managed_instance():
    assert launch_scope(cli.build_parser().parse_args(["supervise", "--all-repos"])) is None


@pytest.mark.parametrize("binding", ["machine", "supervisor.env"])
def test_managed_lane_from_neutral_service_directory_uses_installed_binding(monkeypatch, tmp_path, binding):
    root = tmp_path / "installation"
    root.mkdir()
    (root / binding).write_text(
        "HOST-A" if binding == "machine" else 'AGENT_DISPATCH_SUPERVISE_MACHINE="HOST-A"\n',
        encoding="utf-8",
    )
    neutral = tmp_path / "service"
    neutral.mkdir()
    monkeypatch.chdir(neutral)
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(root))
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISE_MACHINE", "ambient-other-host")
    monkeypatch.setattr(identity, "resolve_machine", lambda: pytest.fail("no project lookup needed"))
    command = build_command({
        "id": "worker", "kind": "supervised-lane", "machine": "host-a", "env": "linux",
        "spec": {"all_repos": True},
    }, python="python")
    args = cli.build_parser().parse_args(command[3:])
    assert launch_scope(args) == ("host-a", "linux")
    for foreign in ("host-b", "host-a-wsl"):
        with pytest.raises(ValueError, match="differs from its executing host"):
            launch_scope(managed_args(machine=foreign))


def test_managed_lane_without_binding_does_not_trust_environment_or_hostname(monkeypatch):
    monkeypatch.setenv("AGENT_DISPATCH_SUPERVISE_MACHINE", "host-a")
    monkeypatch.setattr("platform.node", lambda: "host-a")
    monkeypatch.setattr(identity, "resolve_machine", lambda: None)
    with pytest.raises(ValueError, match="differs from its executing host"):
        launch_scope(managed_args())


@pytest.mark.parametrize("unreadable", ["encoding", "directory"])
def test_unreadable_installed_binding_cannot_fall_back_to_matching_project(monkeypatch, tmp_path, unreadable):
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    if unreadable == "encoding":
        (tmp_path / "machine").write_bytes(b"\xff")
    else:
        (tmp_path / "machine").mkdir()
    monkeypatch.setattr(identity, "resolve_machine", lambda: pytest.fail("unreadable binding must refuse"))
    with pytest.raises(ValueError, match="installed machine binding is unreadable"):
        launch_scope(managed_args())


def test_installed_machine_marker_precedes_supervisor_file(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    (tmp_path / "machine").write_text("host-b", encoding="utf-8")
    (tmp_path / "supervisor.env").write_text("AGENT_DISPATCH_SUPERVISE_MACHINE=host-a\n", encoding="utf-8")
    monkeypatch.setattr(identity, "resolve_machine", lambda: pytest.fail("configured mismatch must refuse"))
    with pytest.raises(ValueError, match="differs from its executing host"):
        launch_scope(managed_args())


@pytest.mark.parametrize("enabled", [None, "0", "1"])
def test_actual_cli_allocation_is_default_off_and_passes_exact_instance(q, api, monkeypatch, enabled):
    monkeypatch.setattr(identity, "resolve_machine", lambda: "host-a")
    monkeypatch.setattr("agent_dispatch.supervise_cli.configure_file_logging", lambda *_: None)
    if enabled is None:
        monkeypatch.delenv("AGENT_DISPATCH_POOL_ALLOCATION", raising=False)
    else:
        monkeypatch.setenv("AGENT_DISPATCH_POOL_ALLOCATION", enabled)
    observed = []
    with controls.connected(api, []) as client:
        monkeypatch.setattr(cli, "_client", lambda *args, **kwargs: client)
        monkeypatch.setattr(Supervisor, "poll_once", lambda self: observed.append(
            (self.pool_instance, self.pool_allocation_enabled),
        ) or [])
        args = managed_args()
        assert args.func(args) == 0
    assert observed == [(("worker", "host-a", "linux"), enabled == "1")]


def test_ordinary_backend_places_then_refreshes_real_claim_before_first_seed(q, api, monkeypatch):
    publish(q, pool())
    requested = task(q)
    initial = asdict(requested)
    requests = []
    with controls.connected(api, requests) as client:
        placed = reserve_for_backend(
            client, initial, "worker", ordinary=True,
            instance=("worker", "host-a", "linux"), startup_timeout=900,
        )
        owner = Supervisor(
            client, spawn_fn=lambda _: None, machine="host-a", supervisor_id="worker",
            pool_instance=("worker", "host-a", "linux"),
        )
        owner.spawn_fn.ordinary_bridge_for = lambda _: True
        prepared = owner._prepare_spawn_task(initial, placed["reservation"])
        assert initial["generation"] == 0 and prepared["generation"] == 1
        assert prepared["status"] == Status.CLAIMED and prepared["owner"] is None
        assert q.pool_startup(requested.id).startup_deadline == 100 + 900
        seed = startup_seed(prepared, client, route=" --shared")
        assert "--evaluation" not in seed and "claim --task" not in seed
        assert prepared["spawn_reservation_key"] == placed["reservation"]["key"]
        replay = reserve_for_backend(
            client, prepared, "worker", ordinary=True,
            instance=("worker", "host-a", "linux"), startup_timeout=900,
        )
        assert replay["reserved"] is False
    assert len(q.list_reservations(task_id=requested.id)) == 1
    assert not any(request.url.path == "/spawn-reservations" for request in requests)


@pytest.mark.parametrize("ordinary", [True, False])
def test_origin_discovery_precedes_current_catalog_snapshot(q, api, monkeypatch, ordinary):
    from agent_dispatch import session_origin

    publish(q, pool())
    requested = task(q)
    revisions = []

    def discover(client, *, ordinary=False):
        q.publish_pool_catalog_scope(
            "host-a", "linux", "publisher-0", generation=1, expected_revision=1,
            registration_revision=q.pool_registration_revision(), expected_cohort_revision=1,
            input_revision="inputs", definitions=(pool(),), now=101,
        )
        revisions.append(q.pool_catalog_snapshot().revision)
        return {}

    monkeypatch.setattr(session_origin, "reservation_options", discover)
    requests = []
    with controls.connected(api, requests) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=ordinary, pool_driver="cli",
            instance=("worker", "host-a", "linux"),
        )
    assert placed["reserved"]
    assert placed["assignment"]["catalog_revision"] == revisions[-1]
    assert q.get(requested.id).status == Status.CLAIMED
    assert sum(request.method == "POST" for request in requests) == 1


def test_catalog_losing_readiness_during_origin_discovery_does_not_place(q, api, monkeypatch):
    from agent_dispatch import session_origin

    publish(q, pool())
    requested = task(q)

    def discover(client, *, ordinary=False):
        q.publish_pool_catalog_scope(
            "host-a", "linux", "publisher-0", generation=1, expected_revision=1,
            registration_revision=q.pool_registration_revision(), expected_cohort_revision=1,
            input_revision="unavailable", definitions=(), reason="source unavailable", now=101,
        )
        return {}

    monkeypatch.setattr(session_origin, "reservation_options", discover)
    requests = []
    with controls.connected(api, requests) as client:
        with pytest.raises(DispatchError, match="complete current catalog"):
            reserve_for_backend(
                client, asdict(requested), "worker", ordinary=True,
                instance=("worker", "host-a", "linux"),
            )
    assert all(request.method == "GET" for request in requests)
    assert q.get(requested.id).status == Status.QUEUED
    assert q.list_reservations(task_id=requested.id) == []


@pytest.mark.parametrize("ordinary,restricted", [(False, False), (False, True), (True, True)])
def test_unqualified_backend_cannot_relabel_the_selected_driver(q, api, ordinary, restricted):
    publish(q, pool())
    requested = task(q)
    requests = []
    with controls.connected(api, requests) as client:
        with pytest.raises(DispatchUpgradeRequired, match="qualified ordinary"):
            reserve_for_backend(
                client, asdict(requested), "worker", ordinary=ordinary, restricted=restricted,
                instance=("worker", "host-a", "linux"),
            )
    assert all(request.method == "GET" for request in requests)
    assert q.get(requested.id).status == Status.QUEUED


def test_unprojected_managed_scope_keeps_existing_ordinary_reservation(q, api):
    requested = task(q)
    requests = []
    with controls.connected(api, requests) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=True,
            instance=("worker", "host-a", "linux"),
        )
    assert placed["reserved"] and q.get(requested.id).status == Status.QUEUED
    assert q.task_pool_assignment(requested.id) is None
    assert requests[-1].url.path == "/spawn-reservations"


@pytest.mark.parametrize("bad", [(), ("worker",), ("worker", "host-a", "linux", "extra")])
def test_internal_bound_identity_requires_exact_three_fields(q, bad):
    revision = publish(q, pool())
    requested = task(q)
    with pytest.raises(TaskError, match="exact launch instance"):
        q.place_pool_task(
            requested.id, reserved_by="worker", catalog_revision=revision, expected_instance=bad,
        )
    assert q.get(requested.id).status == Status.QUEUED


def test_concurrent_wrong_and_right_instance_only_commit_one_global_winner(q):
    revision = publish(q, pool("high", priority=9), pool("low"))
    requested = task(q)
    barrier = Barrier(2)

    def place(name):
        barrier.wait()
        try:
            return q.place_pool_task(
                requested.id, reserved_by=name, catalog_revision=revision, driver="ordinary-bridge",
                expected_instance=(name, "host-a", "linux"),
            )
        except TaskError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        high, low = list(executor.map(place, ("high", "low")))
    assert high.reserved and "another launch owner" in low
    assert q.get(requested.id).generation == 1
    assert q.task_pool_assignment(requested.id).registration_id == "high"
    assert len(q.list_reservations(task_id=requested.id)) == 1


def test_instance_query_filters_before_limit_and_preserves_history_binding(q, api):
    revision = publish(q, pool(capacity=3), pool(machine="host-b", capacity=3))
    own = task(q)
    bound = q.place_pool_task(
        own.id, reserved_by="worker", catalog_revision=revision, driver="ordinary-bridge",
        expected_instance=("worker", "host-a", "linux"),
    )
    foreign = task(q, target_machine="host-b")
    other = q.place_pool_task(
        foreign.id, reserved_by="worker", catalog_revision=revision, driver="ordinary-bridge",
        expected_instance=("worker", "host-b", "linux"),
    )
    with q._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET reserved_at=200 WHERE key=?", (other.reservation.key,))
        # Current assignment metadata cannot retarget immutable reservation ownership.
        conn.execute("UPDATE task_pool_assignments SET env='other' WHERE task_id=?", (own.id,))
    with controls.connected(api, []) as client:
        selected = client.list_reservations(pool_instance=("worker", "host-a", "linux"), limit=1)
        assert [row["key"] for row in selected] == [bound.reservation.key]
        assert client.list_reservations(pool_instance=("worker", "host-a", "other")) == []
        assert [row["key"] for row in client.list_reservations(
            pool_instance=("worker", "host-b", "linux"), pool_accountable=True, limit=1,
        )] == [other.reservation.key]


def test_instance_queries_and_task_matching_preserve_unbound_legacy_and_manual(q, api, monkeypatch):
    legacy = task(q)
    retained, _ = q.reserve_spawn(legacy.id, reserved_by="worker", driver="ordinary-bridge")
    claimed = q.claim_one("host-a/legacy", task_id=legacy.id)
    manual = task(q)
    manual_claim = q.claim_one("host-a/manual", task_id=manual.id)
    publish(q, pool())
    with controls.connected(api, []) as client:
        owner = cycle_owner(client, monkeypatch, [])
        owner._configured_pool_mode = True
        assert owner._matches_pool(asdict(claimed))
        assert owner._matches_pool(asdict(manual_claim))
        assert [row["key"] for row in client.list_reservations(
            pool_instance=("worker", "host-a", "linux"), state="reserving",
        )] == [retained.key]


def test_old_query_cannot_silently_ignore_instance_filter(q, api):
    publish(q, pool())
    requests = []

    def rewrite(request, response):
        return httpx.Response(response.status_code, content=response.content)

    with controls.connected(api, requests, rewrite=rewrite) as client:
        with pytest.raises(DispatchError, match="pool-instance/v1"):
            client.list_reservations(pool_instance=("worker", "host-a", "linux"))
    assert len(requests) == 1


def cycle_owner(client, monkeypatch, calls):
    def spawn(prepared):
        calls.append(prepared)
        seed = startup_seed(prepared, client, route="")
        assert prepared["status"] == Status.CLAIMED and prepared["generation"] == 1
        assert "--evaluation" not in seed and "claim --task" not in seed
        return False, {"retained": True, "error": "contained native launch boundary"}

    spawn.ordinary_bridge_for = lambda _: True
    owner = Supervisor(
        client, spawn_fn=spawn, machine="host-a", supervisor_id="worker",
        pool_instance=("worker", "host-a", "linux"), heartbeat=False, recover=False,
        nudge=False, consistency_sweep=False, pool_allocation_enabled=True,
    )
    for name in (
        "reconcile", "reconcile_reserving", "release_requested_bodies",
        "bind_headless_owner_sessions", "renew_restricted_grants", "nudge_idle_headless_tasks",
        "cool_dormant_bodies", "release_resumed_cold_tasks", "recover_stranded_cold_reservations",
        "recover_stranded_releasing_reservations", "redrive_unclaimed_spawns",
        "_sync_event_subscriptions",
    ):
        monkeypatch.setattr(owner, name, lambda **kwargs: None)
    monkeypatch.setattr(owner, "_failed_spawn_counts", lambda: pytest.fail("legacy dead-letter counter"))
    monkeypatch.setattr(owner, "_active_reservations", lambda: pytest.fail("legacy label capacity"))
    return owner


def test_disabled_allocator_still_reconciles_exact_custody_without_new_placement(q, api, monkeypatch):
    publish(q, pool())
    requested = task(q)
    calls, reconciled, requests = [], [], []
    with controls.connected(api, requests) as client:
        owner = cycle_owner(client, monkeypatch, calls)
        owner.pool_allocation_enabled = False
        monkeypatch.setattr(owner, "reconcile", lambda: reconciled.append(owner._configured_pool_mode))
        assert owner.poll_once(now=100) == []
    assert reconciled == [True] and not calls
    assert q.get(requested.id).status == Status.QUEUED
    assert all(request.method == "GET" for request in requests)


@pytest.mark.parametrize("enabled", [False, True])
def test_unprojected_default_control_none_keeps_managed_legacy_cycle(q, monkeypatch, enabled):
    requested = task(q)
    calls, reconciled, requests = [], [], []
    with TestClient(create_app(
        q, token="data", control_token=None, enable_mcp=False, verification_interval=0,
    )) as api, controls.connected(api, requests, control=False) as client:
        owner = cycle_owner(client, monkeypatch, calls)
        owner.pool_allocation_enabled = enabled
        monkeypatch.setattr(owner, "reconcile", lambda: reconciled.append(True))
        monkeypatch.setattr(owner, "_failed_spawn_counts", lambda: {})
        monkeypatch.setattr(owner, "_active_reservations", lambda: [])
        monkeypatch.setattr(owner, "_prepare_spawn_task", lambda task, reservation: calls.append(
            (task["id"], reservation["driver"]),
        ) or task)
        owner.spawn_fn = lambda task: (False, {"retained": True, "error": "contained legacy launch"})
        owner.spawn_fn.ordinary_bridge_for = lambda task: True
        assert owner.poll_once(now=100) == []
    assert reconciled == [True] and calls == [(requested.id, "ordinary-bridge")]
    assert q.get(requested.id).status == Status.QUEUED
    assert not any(request.url.path.startswith("/pool-") for request in requests)


@pytest.mark.parametrize("enabled", [False, True])
def test_midcycle_projection_cannot_upgrade_legacy_cycle_to_pool_allocation(q, api, monkeypatch, enabled):
    requested = task(q)
    calls, requests, published = [], [], []

    def rewrite(request, response):
        if request.url.path == "/tasks" and not published:
            published.append(publish(q, pool()))
        return response

    with controls.connected(api, requests, rewrite=rewrite) as client:
        owner = cycle_owner(client, monkeypatch, calls)
        owner.pool_allocation_enabled = enabled
        monkeypatch.setattr(owner, "_failed_spawn_counts", lambda: {})
        monkeypatch.setattr(owner, "_active_reservations", lambda: [])
        assert owner.poll_once(now=100) == []
    assert published and not calls
    assert q.get(requested.id).status == Status.QUEUED
    assert q.task_pool_assignment(requested.id) is None
    assert q.list_reservations(task_id=requested.id) == []
    assert not any(request.method == "POST" and request.url.path == "/pool-placements" for request in requests)
    assert sum(request.url.path == "/spawn-reservations" and request.method == "POST"
               for request in requests) == 1


@pytest.mark.parametrize("observation", [None, {}, {"version": True, "revision": 0},
                                        {"version": 1, "revision": True}, {"version": 1, "revision": -1}])
def test_unknown_cohort_without_control_does_not_authorize_legacy_fallback(q, api, monkeypatch, observation):
    requested = task(q)
    calls, requests, reconciled = [], [], []

    def rewrite(request, response):
        if request.url.path == "/health":
            body = response.json()
            body["pool_cohort"] = observation
            return httpx.Response(200, json=body)
        return response

    with controls.connected(api, requests, control=False, rewrite=rewrite) as client:
        owner = cycle_owner(client, monkeypatch, calls)
        monkeypatch.setattr(owner, "reconcile", lambda: reconciled.append(True))
        assert owner.poll_once(now=100) == []
    assert not calls and not reconciled
    assert q.get(requested.id).status == Status.QUEUED
    assert all(request.method == "GET" for request in requests)


def test_configured_cohort_without_control_does_not_fall_back_to_legacy(q, monkeypatch):
    publish(q, pool())
    requested = task(q)
    requests, calls = [], []
    with TestClient(create_app(
        q, token="data", control_token=None, enable_mcp=False, verification_interval=0,
    )) as api, controls.connected(api, requests, control=False) as client:
        owner = cycle_owner(client, monkeypatch, calls)
        assert owner.poll_once(now=100) == []
    assert not calls and q.get(requested.id).status == Status.QUEUED
    assert all(request.method == "GET" for request in requests)


def test_disabled_pool_allocator_keeps_independent_ci_repair_legacy_budget(q, api, monkeypatch):
    publish(q, pool())
    requested = task(q)
    calls, budgets = [], []
    with controls.connected(api, []) as client:
        owner = cycle_owner(client, monkeypatch, calls)
        owner.pool_allocation_enabled = False
        monkeypatch.setattr(owner, "_eligible", lambda now: [{**asdict(requested), "source": "ci-repair"}])
        monkeypatch.setattr(owner, "_failed_spawn_counts", lambda: budgets.append(True) or {requested.id: 3})
        monkeypatch.setattr(owner, "_active_reservations", lambda: [])
        assert owner.poll_once(now=100) == []
    assert budgets == [True] and not calls
    assert q.get(requested.id).status == Status.QUEUED


def test_actual_cycle_uses_provider_capacity_and_correct_postclaim_prompt(q, api, monkeypatch):
    revision = publish(q, pool(), pool(machine="host-b", priority=9))
    other = task(q, target_machine="host-b")
    q.place_pool_task(
        other.id, reserved_by="worker", catalog_revision=revision, driver="ordinary-bridge",
        expected_instance=("worker", "host-b", "linux"),
    )
    requested = task(q)
    calls = []
    with controls.connected(api, []) as client:
        owner = cycle_owner(client, monkeypatch, calls)
        assert owner.poll_once(now=100) == []
        assert len(calls) == 1 and calls[0]["id"] == requested.id
        assert q.get(requested.id).status == Status.CLAIMED
        assert owner.poll_once(now=100) == []
        assert len(calls) == 1
        assert not owner._matches_pool(asdict(q.get(other.id)))
        assert owner._matches_pool(asdict(q.get(requested.id)))


def test_actual_cycle_waits_for_other_selected_owner_without_claiming(q, api, monkeypatch, caplog):
    publish(q, pool(), pool(machine="host-b", priority=9))
    requested = task(q)
    calls = []
    with controls.connected(api, []) as client:
        owner = cycle_owner(client, monkeypatch, calls)
        assert owner.poll_once(now=100) == []
    assert not calls and q.get(requested.id).status == Status.QUEUED
    assert q.list_reservations(task_id=requested.id) == []
    assert "another launch owner" in caplog.text


@pytest.mark.parametrize("change", ["hold", "binding", "generation", "driver"])
def test_preparation_readback_loss_retains_claim_without_legacy_fallback(q, api, monkeypatch, change):
    publish(q, pool())
    requested = task(q)
    requests = []

    def rewrite(request, response):
        if change == "binding" and request.method == "GET" and "/spawn-reservations/" in request.url.path:
            body = response.json()
            body.pop("reservation_pool_bound", None)
            body.pop("reservation_pool_assignment", None)
            return httpx.Response(200, json=body)
        return response

    with controls.connected(api, requests, rewrite=rewrite) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=True,
            instance=("worker", "host-a", "linux"),
        )
        if change == "hold":
            q.set_hold(requested.id, reason="operator hold", actor="operator")
        elif change in {"generation", "driver"}:
            with q._connect() as conn:
                if change == "generation":
                    conn.execute("UPDATE tasks SET generation=generation+1 WHERE id=?", (requested.id,))
                else:
                    conn.execute("UPDATE spawn_reservations SET driver='cli' WHERE task_id=?", (requested.id,))
        owner = cycle_owner(client, monkeypatch, [])
        with pytest.raises(SpawnPreparationRetained):
            owner._prepare_spawn_task(asdict(requested), placed["reservation"])
    assert q.get(requested.id).status == Status.CLAIMED
    assert len(q.list_reservations(task_id=requested.id)) == 1
    assert sum(request.method == "POST" and request.url.path == "/pool-placements" for request in requests) == 1


def test_cli_pool_claim_reaches_actual_acquisition_seed_and_affirmative_start(q, api, monkeypatch):
    publish(q, pool())
    requested = task(q)
    calls = []
    monkeypatch.setattr(embody, "prepare_reusable_worktree", lambda task, reservation, **kw: (
        calls.append(("workspace", task, kw)) or {
            "worktree": "worktree", "path": "/workspace/example", "project": "example",
            "ownership": "created",
        }
    ))
    monkeypatch.setattr(session_acquisition, "agent_bridge_launch_prefix", lambda: ["bridge"])

    def transport(argv, **kwargs):
        payload = json.loads(kwargs["input"])
        calls.append(("acquire", payload))
        assert q.get(requested.id).status == Status.CLAIMED
        assert q.get_reservation(payload["reservation_key"]).worktree == "worktree"
        assert payload["task_generation"] == 1 and payload["mode"] == "cli"
        assert "claim --task" not in payload["prompt"] and "--evaluation" not in payload["prompt"]
        assert f"start {requested.id} --pool-generation 1" in payload["prompt"]
        assert "agent-dispatch --shared" in payload["prompt"]
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            **{name: payload[name] for name in (
                "mode", "task_id", "task_generation", "worktree_id", "delivery_id",
            )},
            "session_id": "native-cli", "native_session_id": "native-cli",
        }), "")

    monkeypatch.setattr(session_acquisition.subprocess, "run", transport)
    factory = spawn_factories.make_embody_spawn(route=" --shared")
    with controls.connected(api, []) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=False, pool_driver=factory.pool_placement_driver,
            instance=("worker", "host-a", "linux"),
        )
        owner = Supervisor(
            client, spawn_fn=factory, machine="host-a", supervisor_id="worker",
            pool_instance=("worker", "host-a", "linux"),
        )
        prepared = owner._prepare_spawn_task(asdict(requested), placed["reservation"])
        assert calls[0][2]["driver"] == "cli" and prepared["generation"] == 1
        ok, handle = factory(prepared)
        assert ok and handle["session"] == "native-cli"
        assert q.get(requested.id).status == Status.CLAIMED
        client.record_spawn(placed["reservation"]["key"], session_handle=handle["session"], worktree=handle["worktree"])
        accepted = client.start_pool_task(
            requested.id, "host-a/worktree", owner_session_id="native-cli", expected_generation=1,
        )
        assert accepted["status"] == Status.STARTED
    assert [call[0] for call in calls] == ["workspace", "acquire"]


def test_mixed_driver_selection_never_infers_cli_from_workspace_interface(q, api):
    def script(task):
        pytest.fail("unqualified script must not spawn")

    script.allocation_interface = "cli"
    routed = spawn_factories.make_label_routed_spawn(
        spawn_factories.make_headless_spawn(),
        overrides={"interactive": spawn_factories.make_embody_spawn(), "script": script},
    )
    publish(q, pool(capacity=3))
    with controls.connected(api, []) as client:
        owner = Supervisor(client, spawn_fn=routed)
        for label, driver in (("interactive", "cli"), ("ordinary", "ordinary-bridge"), ("script", None)):
            requested = task(q, labels=[label])
            ordinary = routed.ordinary_bridge_for(asdict(requested))
            selected = owner._spawn_attribute(asdict(requested), "pool_placement_driver", "") or None
            if driver is None:
                with pytest.raises(DispatchUpgradeRequired):
                    reserve_for_backend(
                        client, asdict(requested), "worker", ordinary=ordinary, pool_driver=selected,
                        instance=("worker", "host-a", "linux"),
                    )
                assert q.get(requested.id).status == Status.QUEUED
            else:
                placed = reserve_for_backend(
                    client, asdict(requested), "worker", ordinary=ordinary, pool_driver=selected,
                    instance=("worker", "host-a", "linux"),
                )
                assert placed["reservation"]["driver"] == driver


def test_actual_cycle_uses_selected_cli_placement_driver(q, api, monkeypatch):
    publish(q, pool())
    requested = task(q)
    calls = []
    with controls.connected(api, []) as client:
        owner = cycle_owner(client, monkeypatch, calls)

        def spawn(prepared):
            calls.append(prepared)
            assert prepared["status"] == Status.CLAIMED and prepared["generation"] == 1
            assert "--pool-generation 1" in startup_seed(prepared, client, driver="cli")
            return False, {"retained": True, "error": "contained CLI launch boundary"}

        spawn.pool_placement_driver = "cli"
        owner.spawn_fn = spawn
        assert owner.poll_once(now=100) == []
        assert len(calls) == 1
        assert q.list_reservations(task_id=requested.id)[0].driver == "cli"


def test_cli_seed_refuses_provider_rollback_before_acquisition(q, api, monkeypatch):
    publish(q, pool())
    requested = task(q)
    rolled_back = False

    def rewrite(request, response):
        if rolled_back and request.url.path == "/pool-placements/contract":
            body = response.json()
            body["explicit_start_versions"] = []
            return httpx.Response(200, json=body)
        return response

    monkeypatch.setattr(session_acquisition, "acquire", lambda *a, **kw: pytest.fail("native effect"))
    with controls.connected(api, [], rewrite=rewrite) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=False, pool_driver="cli",
            instance=("worker", "host-a", "linux"),
        )
        rolled_back = True
        factory = spawn_factories.make_embody_spawn()
        ok, result = factory({
            **asdict(q.get(requested.id)), "_spawn_client": client,
            "spawn_reservation_key": placed["reservation"]["key"],
            "spawn_pool_bound": True,
        })
        assert not ok and result["retained"] is True and "deferred" not in result
    assert q.get(requested.id).status == Status.CLAIMED
    assert len(q.list_reservations(task_id=requested.id)) == 1


@pytest.mark.parametrize("versions", [None, [], [True], [2]])
def test_managed_cli_requires_live_explicit_start_support_before_placement(q, api, versions):
    publish(q, pool())
    requested = task(q)
    requests = []

    def rewrite(request, response):
        if request.url.path == "/pool-placements/contract":
            body = response.json()
            body["explicit_start_versions"] = versions
            return httpx.Response(200, json=body)
        return response

    with controls.connected(api, requests, rewrite=rewrite) as client:
        with pytest.raises(DispatchUpgradeRequired, match="start enforcement"):
            reserve_for_backend(
                client, asdict(requested), "worker", ordinary=False, pool_driver="cli",
                instance=("worker", "host-a", "linux"),
            )
    assert all(request.method == "GET" for request in requests)
    assert q.get(requested.id).status == Status.QUEUED


@pytest.mark.parametrize("failure", ["rollback", "readback", "ambiguous", "unavailable", "workspace"])
def test_actual_cli_cycle_retains_failed_acquisition_then_exhausts_without_losing_capacity(
    q, api, monkeypatch, failure,
):
    from agent_dispatch.bridge import BridgeCarriedSessionBusy, BridgeUnavailable
    from agent_dispatch.supervisor_pool_startup import supervise_pool_startup

    publish(q, pool())
    requested = task(q)
    clock = [100.0]
    monkeypatch.setattr(q, "_now", lambda now=None: clock[0] if now is None else now)
    prepared = []
    fault_active = [True]

    def workspace(*args, **kwargs):
        prepared.append(True)
        if failure == "workspace":
            raise embody.EmbodyUnavailable("workspace preparation unavailable")
        return {
            "worktree": "worktree", "path": "/workspace/example", "project": "example",
            "ownership": "created",
        }

    def rewrite(request, response):
        if fault_active[0] and prepared and failure == "rollback" and request.url.path == "/pool-placements/contract":
            body = response.json()
            body["explicit_start_versions"] = []
            return httpx.Response(200, json=body)
        if fault_active[0] and prepared and failure == "readback" and request.method == "GET" and "/spawn-reservations/" in request.url.path:
            return httpx.Response(503, json={"detail": "lost readback"})
        return response

    def acquire(*args, **kwargs):
        if failure == "unavailable":
            raise BridgeUnavailable("acquisition executable unavailable")
        assert failure == "ambiguous"
        raise BridgeCarriedSessionBusy("acquisition outcome is unknown")

    monkeypatch.setattr(embody, "prepare_reusable_worktree", workspace)
    monkeypatch.setattr(session_acquisition, "acquire", acquire)
    with controls.connected(api, [], rewrite=rewrite) as client:
        owner = cycle_owner(client, monkeypatch, [])
        owner.spawn_fn = spawn_factories.make_embody_spawn()
        owner.reserving_timeout = 10
        assert owner.poll_once(now=100) == []
        fault_active[0] = False
        reservation = q.list_reservations(task_id=requested.id)[0]
        assert reservation.state == "reserving"
        assert q.get(requested.id).status == Status.CLAIMED
        with pytest.raises(DispatchError, match="retain its active reservation"):
            client.defer_spawn(reservation.key, detail="old caller cannot strand a pool claim")
        clock[0] = 100 + owner.reserving_timeout
        assert supervise_pool_startup(
            owner, asdict(reservation), asdict(q.get(requested.id)), now=clock[0],
        ) is True
        current = q.get(requested.id)
        assert current.status == Status.QUEUED and current.generation == 2
        assert q.get_reservation(reservation.key).state == "releasing"
        assert q.pool_startup(requested.id).exhaustion_reason == "startup_timeout"
        with pytest.raises(DispatchError, match="capacity"):
            reserve_for_backend(
                client, asdict(task(q)), "worker", ordinary=False, pool_driver="cli",
                instance=("worker", "host-a", "linux"),
            )
    assert len(prepared) == 1
    assert len(q.list_reservations(task_id=requested.id)) == 1


@pytest.mark.parametrize("stage", ["workspace", "task-read", "acquisition"])
@pytest.mark.parametrize("now", [700.0, 701.0])
def test_cli_coordinator_deadline_refuses_before_each_initial_effect(q, api, monkeypatch, stage, now):
    publish(q, pool())
    requested = task(q)
    clock = [100.0]
    monkeypatch.setattr(q, "_now", lambda now=None: clock[0] if now is None else now)
    calls = []

    def workspace(*args, **kwargs):
        calls.append("workspace")
        clock[0] = now
        return {
            "worktree": "worktree", "path": "/workspace/example", "project": "example",
            "ownership": "created",
        }

    monkeypatch.setattr(embody, "prepare_reusable_worktree", workspace)
    monkeypatch.setattr(session_acquisition, "acquire", lambda *a, **kw: pytest.fail("native after deadline"))

    def rewrite(request, response):
        if stage == "task-read" and request.url.path == f"/tasks/{requested.id}":
            clock[0] = now
        return response

    with controls.connected(api, [], rewrite=rewrite) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=False, pool_driver="cli",
            instance=("worker", "host-a", "linux"), startup_timeout=600,
        )
        owner = Supervisor(
            client, spawn_fn=spawn_factories.make_embody_spawn(), machine="host-a", supervisor_id="worker",
            pool_instance=("worker", "host-a", "linux"),
        )
        if stage != "acquisition":
            if stage == "workspace":
                clock[0] = now
            with pytest.raises(SpawnPreparationRetained, match="deadline expired"):
                owner._prepare_spawn_task(asdict(requested), placed["reservation"])
            assert calls == []
        else:
            prepared = owner._prepare_spawn_task(asdict(requested), placed["reservation"])
            ok, result = owner.spawn_fn(prepared)
            assert not ok and result["retained"] is True
            assert "deadline expired" in result["error"] and calls == ["workspace"]
    assert q.get(requested.id).status == Status.CLAIMED
    assert q.get_reservation(placed["reservation"]["key"]).state == "reserving"


def test_prior_cli_initial_admission_provider_refuses_before_claim(q, api):
    publish(q, pool())
    requested = task(q)
    requests = []

    def rewrite(request, response):
        if request.url.path == "/pool-placements/contract":
            body = response.json()
            body.pop("cli_initial_start_versions", None)
            return httpx.Response(200, json=body)
        return response

    with controls.connected(api, requests, rewrite=rewrite) as client:
        with pytest.raises(DispatchUpgradeRequired, match="CLI pool initial-start"):
            reserve_for_backend(
                client, asdict(requested), "worker", ordinary=False, pool_driver="cli",
                instance=("worker", "host-a", "linux"),
            )
    assert all(request.method == "GET" for request in requests)
    assert q.get(requested.id).status == Status.QUEUED


@pytest.mark.parametrize("change", ["hold", "generation", "driver", "missing-instance"])
def test_cli_preparation_fence_blocks_workspace_and_native_creation(q, api, monkeypatch, change):
    publish(q, pool())
    requested = task(q)
    monkeypatch.setattr(embody, "prepare_reusable_worktree", lambda *a, **kw: pytest.fail("workspace effect"))
    with controls.connected(api, []) as client:
        placed = reserve_for_backend(
            client, asdict(requested), "worker", ordinary=False, pool_driver="cli",
            instance=("worker", "host-a", "linux"),
        )
        if change == "hold":
            q.set_hold(requested.id, reason="operator hold", actor="operator")
        elif change != "missing-instance":
            with q._connect() as conn:
                conn.execute(
                    "UPDATE tasks SET generation=generation+1 WHERE id=?" if change == "generation"
                    else "UPDATE spawn_reservations SET driver='ordinary-bridge' WHERE task_id=?",
                    (requested.id,),
                )
        owner = Supervisor(
            client, spawn_fn=spawn_factories.make_embody_spawn(), machine="host-a", supervisor_id="worker",
            pool_instance=None if change == "missing-instance" else ("worker", "host-a", "linux"),
        )
        with pytest.raises(SpawnPreparationRetained):
            owner._prepare_spawn_task(asdict(requested), placed["reservation"])
    assert len(q.list_reservations(task_id=requested.id)) == 1
