"""The real queue and route fences around provider-owned restricted startup."""

from __future__ import annotations

import json
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import restricted_spawn as delivery
from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.registrations import RegistrationError, validate_registration
from agent_dispatch.restricted_delivery import DRIVER
from agent_dispatch.supervisor import Supervisor
from agent_dispatch.task_scope import TASK_SCOPE_HEADER, verify_task_scope_token
from tests._helpers import TEST_REPO, RepoDefaultingQueue


@pytest.fixture
def rig(tmp_path, monkeypatch):
    queue = RepoDefaultingQueue(tmp_path / "tasks.db")
    task = queue.create(
        "restricted task", repo=TEST_REPO, labels=["restricted"], payload_inline="own work",
    )
    # The supervisor must use the returned claim generation, not assume 1.
    queue.claim_one("previous-owner", task_id=task.id, repo=TEST_REPO)
    queue.yield_task(task.id, "previous-owner")
    task = queue.get(task.id)
    spec = {
        "command": ["provider-fixture"], "name": "worker",
        "instance_id": "a" * 64, "coordinator_url": "http://coordinator.test",
    }
    scheme = "Bear" + "er "
    control = {"Authorization": scheme + "control-fixture"}
    app = create_app(queue, token="fleet-fixture", control_token="control-fixture", enable_mcp=False)
    with TestClient(app) as api:
        def send(request):
            response = api.request(
                request.method, request.url.raw_path.decode(),
                headers=dict(request.headers), content=request.content,
            )
            return httpx.Response(response.status_code, content=response.content)

        with DispatchClient(
            "http://coordinator.test", token="fleet-fixture", control_token="control-fixture",
            transport=httpx.MockTransport(send),
        ) as client:
            calls, grants = [], []
            prepared = {}
            agent = f"container:dispatch/{spec['instance_id']}/{'b' * 32}/worker"
            monkeypatch.setattr(delivery, "agent_bridge_launch_prefix", lambda: ["bridge-fixture"])

            def command(argv, payload=None):
                calls.append((list(argv), payload))
                if argv[0] == "provider-fixture":
                    body = json.loads(payload)
                    if argv[1] == "dispatch-prepare":
                        prepared.update(
                            body, container_id=spec["instance_id"], binding_id="b" * 32, agent=agent,
                        )
                        return dict(prepared)
                    assert argv[1] == "dispatch-project"
                    claims = verify_task_scope_token("control-fixture", body["task_token"])
                    assert claims.task_id == task.id
                    assert claims.generation == prepared["generation"]
                    grants.append(body["task_token"])
                    return {**prepared, **{key: value for key, value in body.items() if key != "task_token"}}
                if "create" in argv:
                    assert "--provider-instance" in argv and "--creation-key" in argv
                    assert "--prompt" not in argv and "--prompt-file" not in argv
                    return {
                        "session_id": "bridge-worker", "ready": True, "status": "awaiting_first_prompt",
                        "connection": {
                            "native_session_id": "native-worker",
                            "venue": {"instance_id": spec["instance_id"], "target_id": agent, "dispatch_only": True},
                        },
                    }
                assert "send" in argv
                assert argv[argv.index("--expected-native-session-id") + 1] == "native-worker"
                assert grants
                return {"session_id": "bridge-worker", "native_session_id": "native-worker", "outcome": "delivered"}

            monkeypatch.setattr(delivery, "_run", command)
            spawn = delivery.make_spawn(spec)
            supervisor = Supervisor(
                client, spawn_fn=spawn, repo=TEST_REPO, labels=["restricted"],
                supervisor_id="restricted-supervisor", machine="fixture-host",
                recover=False, nudge=False, heartbeat=False, consistency_sweep=False,
                local_body_verdict_fn=lambda _sid: "live",
                local_acp_session_fn=lambda _sid: "native-worker",
                local_body_activity_fn=lambda _sid: "ACTIVE",
            )
            yield SimpleNamespace(
                queue=queue, task=task, spec=spec, client=client, api=api, control=control,
                supervisor=supervisor, spawn=spawn, calls=calls, grants=grants,
                command=command, app=app,
            )


def reservation(rig):
    return rig.client.list_reservations(task_id=rig.task.id)[0]


def test_existing_supervisor_claims_binds_projects_and_sends_in_order(rig):
    assert rig.supervisor.poll_once() == [rig.task.id]
    task = rig.queue.get(rig.task.id)
    receipt = reservation(rig)
    assert receipt["driver"] == DRIVER and receipt["state"] == "spawned"
    assert receipt["worktree"] is None and receipt["allocation_project"] is None
    info = receipt["agent_info"]
    assert task.generation == rig.task.generation + 1 == info["task_generation"]
    assert task.owner == info["worker_id"] == f"dispatch-{receipt['key']}"
    assert task.owner_session_id == info["native_session_id"] == "native-worker"
    assert task.status == "claimed" and info["delivered"] is True
    assert ["dispatch-prepare", "create", "dispatch-project", "send"] == [
        next(value for value in argv if value in {"dispatch-prepare", "create", "dispatch-project", "send"})
        for argv, _ in rig.calls
    ]
    assert all(secret not in json.dumps(receipt) for secret in rig.grants)
    assert "control-fixture" not in json.dumps(receipt)
    assert rig.supervisor.poll_once() == []
    assert sum("create" in argv for argv, _ in rig.calls) == 1
    assert sum("send" in argv for argv, _ in rig.calls) == 1


def test_projected_worker_starts_and_completes_without_fleet_authority(rig):
    rig.supervisor.poll_once()
    headers = {TASK_SCOPE_HEADER: rig.grants[-1]}
    task_id = rig.task.id
    response = rig.api.post(
        f"/tasks/{task_id}/start", headers=headers,
        json={"worker_id": "untrusted-body", "owner_session_id": "untrusted-body"},
    )
    assert response.status_code == 200, response.text
    assert "task_token" not in response.json()
    assert rig.queue.get(task_id).owner_session_id == "native-worker"
    assert rig.api.get(f"/tasks/{task_id}/payload", headers=headers).status_code == 200
    other = rig.queue.create("other", repo=TEST_REPO)
    assert rig.api.get(f"/tasks/{other.id}", headers=headers).status_code == 403
    assert rig.api.get("/tasks", headers=headers).status_code == 403
    assert rig.api.post("/claim", headers=headers, json={}).status_code == 403
    assert rig.api.post(
        f"/tasks/{task_id}/complete", headers=headers,
        json={"worker_id": "untrusted-body", "result": {"done": True}},
    ).status_code == 200
    assert rig.queue.get(task_id).status == "completed"
    count = len(rig.grants)
    assert rig.supervisor.renew_restricted_grants() == 0
    assert len(rig.grants) == count


def test_restart_before_prepare_reuses_actual_claim_and_receipt(rig, monkeypatch):
    original = delivery._provider
    monkeypatch.setattr(delivery, "_provider", lambda *_args: (_ for _ in ()).throw(delivery.BridgeUnavailable("interrupted")))
    assert rig.supervisor.poll_once() == []
    held = rig.queue.get(rig.task.id)
    assert held.status == "claimed" and held.owner_session_id is None
    assert "binding_id" not in reservation(rig)["agent_info"]
    monkeypatch.setattr(delivery, "_provider", original)
    assert rig.supervisor.reconcile_reserving() == 1
    assert reservation(rig)["state"] == "spawned"
    assert rig.queue.get(rig.task.id).generation == held.generation
    assert sum("create" in argv for argv, _ in rig.calls) == 1


def test_lost_native_create_ack_reuses_original_creation_key(rig, monkeypatch):
    created = []

    def interrupted(argv, payload=None):
        result = rig.command(argv, payload)
        if "create" in argv:
            created.append(argv[argv.index("--creation-key") + 1])
            if len(created) == 1:
                raise delivery.BridgeUnavailable("lost acknowledgement")
        return result

    monkeypatch.setattr(delivery, "_run", interrupted)
    assert rig.supervisor.poll_once() == []
    assert rig.supervisor.reconcile_reserving() == 1
    assert created[0] == created[1]
    assert reservation(rig)["state"] == "spawned"


def test_lost_create_ack_recovers_the_original_stopped_keyed_native(rig, monkeypatch):
    keys, resumed = [], []
    stopped = False

    def command(argv, payload=None):
        nonlocal stopped
        if "status" in argv:
            return {"session_id": "bridge-worker", "status": "stopped", "turn_count": 0}
        if "resume" in argv:
            assert "--strict" in argv
            assert reservation(rig)["agent_info"]["native_session_id"] == "native-worker"
            resumed.append(argv)
            stopped = False
            return {"session_id": "bridge-worker", "status": "idle"}
        result = rig.command(argv, payload)
        if "create" in argv:
            keys.append(argv[argv.index("--creation-key") + 1])
            if len(keys) == 1:
                stopped = True
                raise delivery.BridgeUnavailable("lost create acknowledgement")
            if stopped:
                return {**result, "ready": False, "status": "stopped"}
        return result

    monkeypatch.setattr(delivery, "_run", command)
    assert rig.supervisor.poll_once() == []
    assert "session_id" not in reservation(rig)["agent_info"]
    assert rig.supervisor.reconcile_reserving() == 1
    assert len(keys) == 3 and len(set(keys)) == 1 and len(resumed) == 1
    assert reservation(rig)["agent_info"]["native_session_id"] == "native-worker"
    assert sum("send" in argv for argv, _ in rig.calls) == 1


def test_overlapping_starters_acquire_only_one_first_send(rig, monkeypatch):
    project = delivery._project
    monkeypatch.setattr(
        delivery, "_project",
        lambda *_args: (_ for _ in ()).throw(delivery.BridgeUnavailable("interrupted")),
    )
    assert rig.supervisor.poll_once() == []
    receipt = reservation(rig)
    monkeypatch.setattr(delivery, "_project", project)

    def command(argv, payload=None):
        if "status" in argv:
            return {"session_id": "bridge-worker", "status": "idle", "turn_count": 0}
        return rig.command(argv, payload)

    monkeypatch.setattr(delivery, "_run", command)
    save = delivery._save
    gate = threading.Barrier(2)

    def synchronized_intent(client, key, info):
        if info.get("send_attempted") and not info.get("delivered"):
            gate.wait(timeout=5)
        return save(client, key, info)

    monkeypatch.setattr(delivery, "_save", synchronized_intent)
    task = {
        **rig.client.get(rig.task.id), "_spawn_client": rig.client,
        "spawn_reservation_key": receipt["key"],
    }
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(rig.spawn, [task, task]))
    assert sum(ok for ok, _ in results) == 1
    assert sum("send" in argv for argv, _ in rig.calls) == 1
    assert reservation(rig)["agent_info"]["delivered"] is True


def test_delivery_ack_cannot_skip_the_retained_first_send_intent(rig, monkeypatch):
    monkeypatch.setattr(
        delivery, "_project",
        lambda *_args: (_ for _ in ()).throw(delivery.BridgeUnavailable("interrupted")),
    )
    assert rig.supervisor.poll_once() == []
    before = reservation(rig)
    response = rig.api.post(
        f"/spawn-reservations/{before['key']}/restricted-delivery", headers=rig.control,
        json={**before["agent_info"], "send_attempted": True, "delivered": True},
    )
    assert response.status_code == 409, response.text
    assert reservation(rig) == before
    assert not any("send" in argv for argv, _ in rig.calls)


@pytest.mark.parametrize("outcome", ["complete", "abandon"])
@pytest.mark.parametrize("lost_publication", [False, True])
def test_terminal_transition_during_send_does_not_strand_lane_capacity(
    rig, monkeypatch, outcome, lost_publication,
):
    def command(argv, payload=None):
        result = rig.command(argv, payload)
        if "send" in argv:
            headers = {TASK_SCOPE_HEADER: rig.grants[-1]}
            assert rig.api.post(
                f"/tasks/{rig.task.id}/start", headers=headers, json={"worker_id": "ignored"},
            ).status_code == 200
            response = rig.api.post(
                f"/tasks/{rig.task.id}/{outcome}", headers=headers,
                json={
                    "worker_id": "ignored",
                    **({"reason": "fixture failure", "permitted": True} if outcome == "abandon" else {}),
                },
            )
            assert response.status_code == 200, response.text
        return result

    monkeypatch.setattr(delivery, "_run", command)
    if lost_publication:
        monkeypatch.setattr(
            rig.client, "record_spawn",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(DispatchError(503, "lost publication")),
        )
    assert rig.supervisor.poll_once() == ([] if lost_publication else [rig.task.id])
    assert reservation(rig)["state"] == "spawned"
    assert reservation(rig)["agent_info"]["delivered"] is True
    rig.supervisor.local_body_verdict_fn = lambda _sid: "gone"
    rig.supervisor.local_body_target_dir_fn = lambda _sid: None
    rig.supervisor.reconcile()
    assert reservation(rig)["state"] == "settled"
    assert rig.supervisor._active_reservations() == []
    assert sum("send" in argv for argv, _ in rig.calls) == 1


def test_terminal_ack_cannot_adopt_a_reclaimed_generation(rig, monkeypatch):
    def interrupted(argv, payload=None):
        result = rig.command(argv, payload)
        if "send" in argv:
            raise delivery.BridgeUnavailable("lost acknowledgement")
        return result

    monkeypatch.setattr(delivery, "_run", interrupted)
    assert rig.supervisor.poll_once() == []
    receipt = reservation(rig)
    held = rig.queue.get(rig.task.id)
    rig.queue.yield_task(held.id, held.owner)
    rig.queue.claim_one("replacement-owner", task_id=held.id, repo=TEST_REPO)
    rig.queue.abandon(
        held.id, worker_id="replacement-owner", permitted=True, reason="replacement concluded",
    )
    before = reservation(rig)
    assert rig.queue.get(held.id).generation > receipt["agent_info"]["task_generation"]
    response = rig.api.post(
        f"/spawn-reservations/{receipt['key']}/restricted-delivery", headers=rig.control,
        json={**receipt["agent_info"], "delivered": True},
    )
    assert response.status_code == 409, response.text
    assert reservation(rig) == before
    assert "delivered" not in reservation(rig)["agent_info"]


@pytest.mark.parametrize("replaced", [False, True])
def test_restart_before_projection_strictly_resumes_original_native(rig, monkeypatch, replaced):
    project = delivery._project
    monkeypatch.setattr(delivery, "_project", lambda *_args: (_ for _ in ()).throw(delivery.BridgeUnavailable("interrupted")))
    assert rig.supervisor.poll_once() == []
    assert reservation(rig)["agent_info"]["native_session_id"] == "native-worker"
    monkeypatch.setattr(delivery, "_project", project)
    resumed = []

    def command(argv, payload=None):
        if "status" in argv:
            return {"session_id": "bridge-worker", "status": "stopped", "turn_count": 0}
        if "resume" in argv:
            assert "--strict" in argv
            resumed.append(argv)
            return {"session_id": "bridge-worker", "status": "idle"}
        result = rig.command(argv, payload)
        if replaced and "create" in argv:
            result["connection"]["native_session_id"] = "replacement-native"
        return result

    monkeypatch.setattr(delivery, "_run", command)
    rig.supervisor.reconcile_reserving()
    assert len(resumed) == 1
    assert reservation(rig)["agent_info"]["native_session_id"] == "native-worker"
    assert reservation(rig)["state"] == ("reserving" if replaced else "spawned")
    assert len(rig.grants) == (0 if replaced else 1)


def test_uncertain_first_send_is_not_automatically_repeated(rig, monkeypatch):
    def interrupted(argv, payload=None):
        result = rig.command(argv, payload)
        if "send" in argv:
            raise delivery.BridgeUnavailable("lost send acknowledgement")
        return result

    monkeypatch.setattr(delivery, "_run", interrupted)
    assert rig.supervisor.poll_once() == []
    assert reservation(rig)["agent_info"]["send_attempted"] is True
    rig.supervisor.reconcile_reserving()
    assert sum("send" in argv for argv, _ in rig.calls) == 1
    assert reservation(rig)["state"] == "reserving"


@pytest.mark.parametrize("change", ["epoch", "generation", "native", "configuration", "unknown"])
def test_trusted_renewal_refuses_changed_authority_or_unknown_liveness(rig, change):
    rig.supervisor.poll_once()
    count = len(rig.grants)
    if change == "epoch":
        rig.app.state.directory.register(
            "central", role="coordinator", epoch=7, machine="central", capabilities=[],
        )
    elif change == "generation":
        rig.queue.yield_task(rig.task.id, rig.queue.get(rig.task.id).owner)
    elif change == "native":
        rig.supervisor.local_acp_session_fn = lambda _sid: "replacement-native"
    elif change == "configuration":
        rig.supervisor.spawn_fn = delivery.make_spawn({**rig.spec, "instance_id": "c" * 64})
    else:
        rig.supervisor.local_body_verdict_fn = lambda _sid: "unknown"
    assert rig.supervisor.renew_restricted_grants() == 0
    assert len(rig.grants) == count


def test_renewal_rotates_token_without_mutating_task_lease(rig, monkeypatch):
    rig.supervisor.poll_once()
    before = rig.queue.get(rig.task.id)
    old = rig.grants[-1]
    now = time.time()
    monkeypatch.setattr("agent_dispatch.task_authority.time.time", lambda: now + 60)
    assert rig.supervisor.renew_restricted_grants() == 1
    assert rig.grants[-1] != old
    assert rig.queue.get(rig.task.id) == before


def test_scoped_start_race_cannot_start_a_reclaimed_incarnation(rig, monkeypatch):
    rig.supervisor.poll_once()
    before = rig.queue.get(rig.task.id)
    original = rig.queue.start

    def race(task_id, worker_id, **kwargs):
        rig.queue.yield_task(task_id, worker_id)
        rig.queue.claim_one(worker_id, task_id=task_id, repo=TEST_REPO)
        return original(task_id, worker_id, **kwargs)

    monkeypatch.setattr(rig.queue, "start", race)
    response = rig.api.post(
        f"/tasks/{before.id}/start", headers={TASK_SCOPE_HEADER: rig.grants[-1]},
        json={"worker_id": before.owner},
    )
    assert response.status_code == 409, response.text
    current = rig.queue.get(before.id)
    assert current.generation > before.generation and current.status == "claimed"
    assert current.owner_session_id is None


def test_restricted_lease_heartbeat_is_scoped_and_native_fenced(rig, monkeypatch):
    rig.supervisor.poll_once()
    before = rig.queue.get(rig.task.id)
    rig.supervisor.heartbeat = True
    now = time.time()
    monkeypatch.setattr("agent_dispatch.task_authority.time.time", lambda: now + 60)
    assert rig.supervisor.renew_restricted_grants() == 1
    current = rig.queue.get(before.id)
    assert current.lease_expires_at > before.lease_expires_at
    assert current.generation == before.generation and current.owner_session_id == before.owner_session_id
    assert rig.supervisor.hold_live_leases() == 0


def test_restricted_heartbeat_race_does_not_extend_reclaimed_task(rig, monkeypatch):
    rig.supervisor.poll_once()
    rig.supervisor.heartbeat = True
    project = delivery._project
    replacement = []

    def race(*args):
        grant = project(*args)
        task_id = rig.task.id
        worker_id = rig.queue.get(task_id).owner
        rig.queue.yield_task(task_id, worker_id)
        rig.queue.claim_one(worker_id, task_id=task_id, repo=TEST_REPO)
        replacement.append(rig.queue.get(task_id))
        return grant

    monkeypatch.setattr(delivery, "_project", race)
    assert rig.supervisor.renew_restricted_grants() == 0
    assert rig.queue.get(rig.task.id) == replacement[0]


def test_receipt_route_requires_control_and_never_accepts_credentials_or_identity_replacement(rig):
    rig.supervisor.poll_once()
    receipt = reservation(rig)
    route = f"/spawn-reservations/{receipt['key']}/restricted-delivery"
    info = receipt["agent_info"]
    scheme = "Bear" + "er "
    assert rig.api.post(route, headers={"Authorization": scheme + "fleet-fixture"}, json=info).status_code == 403
    assert rig.api.post(route, headers={TASK_SCOPE_HEADER: rig.grants[-1]}, json=info).status_code == 403
    for change in ({"task_token": "forbidden"}, {"native_session_id": "other"}, {"task_generation": 999}, {"delivered": False}):
        response = rig.api.post(route, headers=rig.control, json={**info, **change})
        assert response.status_code == 409, response.text
    assert reservation(rig)["agent_info"] == info


def test_ordinary_created_agent_route_cannot_adopt_restricted_receipt(rig):
    rig.supervisor.poll_once()
    receipt = reservation(rig)
    with pytest.raises(DispatchError):
        rig.client.record_created_agent(
            receipt["key"], task_generation=rig.queue.get(rig.task.id).generation,
            agent_info=receipt["agent_info"],
        )


@pytest.mark.parametrize("change", [
    {"max_concurrent": 2}, {"labels": []}, {"embody_backend": "cli"},
    {"cli_labels": ["ordinary"]}, {"fleet": {"pool": ["host"]}},
    {"restricted_worker": {"command": ["provider"]}},
])
def test_registration_rejects_ambiguous_or_incomplete_restricted_lane(rig, change):
    with pytest.raises(RegistrationError):
        validate_registration("supervised-lane", {
            "repo": TEST_REPO, "labels": ["restricted"], "restricted_worker": rig.spec, **change,
        })


def test_receipt_is_not_a_second_task_or_authority_store(rig):
    rig.supervisor.poll_once()
    stored = rig.queue.get(rig.task.id)
    info = reservation(rig)["agent_info"]
    assert "task_token" not in json.dumps(info)
    assert asdict(stored)["owner_session_id"] == info["native_session_id"]


def test_legacy_unauthenticated_ingress_refuses_delivery_receipts(rig):
    rig.supervisor.poll_once()
    receipt = reservation(rig)
    with TestClient(create_app(
        rig.queue, control_token="control-fixture", enable_mcp=False,
    )) as legacy:
        response = legacy.post(
            f"/spawn-reservations/{receipt['key']}/restricted-delivery",
            headers=rig.control, json=receipt["agent_info"],
        )
    assert response.status_code == 503, response.text
    assert "authenticated coordinator ingress" in response.text


def test_registration_and_daemon_keep_exact_provider_invocation(rig, monkeypatch):
    from agent_dispatch import __main__ as cli
    from agent_dispatch.supervise_cli import _build_registration_spec
    from agent_dispatch.supervisor_registration import _lane_flags

    args = cli.build_parser().parse_args([
        "supervise", "register", "--repo", TEST_REPO, "--label", "restricted",
        "--restricted-worker", json.dumps(rig.spec),
    ])
    monkeypatch.setattr("agent_dispatch.supervise_cli._scope_repo", lambda _args: TEST_REPO)
    spec = _build_registration_spec(args)
    assert spec["restricted_worker"] == rig.spec
    validate_registration("supervised-lane", spec)
    flags = _lane_flags(spec)
    parsed = cli.build_parser().parse_args(["supervise", *flags])
    assert delivery.configuration(parsed.restricted_worker) == rig.spec
    assert parsed.max_concurrent == 1 and parsed.label == ["restricted"]


def test_supervisor_does_not_adopt_a_replacement_native_via_generic_binding(rig):
    rig.supervisor.poll_once()
    owner = rig.queue.get(rig.task.id).owner
    rig.queue.yield_task(rig.task.id, owner)
    rig.queue.claim_one(owner, task_id=rig.task.id, repo=TEST_REPO)
    rig.supervisor.local_acp_session_fn = lambda _sid: "replacement-native"
    assert rig.supervisor.bind_headless_owner_sessions() == 0
    assert rig.queue.get(rig.task.id).owner_session_id is None
