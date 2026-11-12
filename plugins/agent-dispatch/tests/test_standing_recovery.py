"""A recovery grant is bounded authority for one accepted pairing, not staffing."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import json
import time

import httpx
import pytest

from agent_dispatch import __main__ as cli
from agent_dispatch.client import DispatchClient, DispatchUpgradeRequired
from agent_dispatch.queue import TaskError, TaskQueue
from agent_dispatch.queue_standing_charters import _encoded
from agent_dispatch.queue_standing_recovery import StandingRecoveryGrant
from tests.test_standing_admission import (
    accept,
    admission as admission,
    materialization as materialization,
)
from tests.test_standing_charter_exposure import CONTROL, DATA, app, flags
from tests.test_pool_placement_client import connected


def grant(admission, **changes):
    task = admission.queue.get(admission.task.id)
    return admission.queue.grant_standing_recovery(task.id, admission.workspace, **{
        "grant": StandingRecoveryGrant("grant-one", "operator-approval/one", 200, 2),
        "expected_revision": task.standing_revision,
        "expected_generation": task.generation,
        "owner_session_id": task.owner_session_id,
        "now": 100,
        **changes,
    })


def test_declaration_and_reserved_workspace_do_not_grant_recovery(admission):
    before = admission.queue.get(admission.task.id)
    with pytest.raises(TaskError, match="no active recovery grant"):
        admission.queue.standing_recovery_authority(before.id)
    with pytest.raises(TaskError, match="accepted active native pairing"):
        grant(admission)
    assert admission.queue.get(before.id) == before
    admission.task = accept(admission)
    with pytest.raises(TaskError, match="no active recovery grant"):
        admission.queue.standing_recovery_authority(before.id)


def test_explicit_grant_survives_queue_restart_without_changing_pairing(admission):
    paired = accept(admission)
    granted = grant(admission)
    restarted = TaskQueue(
        admission.queue.db_path, relationship_writer=admission.writer, relationship_authority="local",
    )
    effective = restarted.standing_recovery_authority(paired.id, now=150)
    assert effective["request"]["authority_ref"] == "operator-approval/one"
    assert effective["request"]["expires_at"] == 200
    assert effective["request"]["max_attempts"] == 2
    assert effective["attempts"] == 0 and effective["pending"] is None
    assert (granted.owner, granted.owner_session_id, granted.generation) == (
        paired.owner, paired.owner_session_id, paired.generation,
    )
    assert restarted.task_relationship(paired.id) == admission.queue.task_relationship(paired.id)
    with restarted._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM spawn_reservations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_pool_assignments").fetchone()[0] == 0


def test_grant_retries_preserve_identity_without_renewal_or_extra_events(admission):
    paired = accept(admission)
    granted = grant(admission)
    original_revision = paired.standing_revision
    with ThreadPoolExecutor(max_workers=2) as executor:
        replayed = list(executor.map(
            lambda _: grant(admission, expected_revision=original_revision), range(2),
        ))
    assert replayed == [granted, granted]
    for changed in (
        StandingRecoveryGrant("grant-one", "operator-approval/other", 200, 2),
        StandingRecoveryGrant("grant-one", "operator-approval/one", 300, 2),
        StandingRecoveryGrant("grant-one", "operator-approval/one", 200, 3),
    ):
        with pytest.raises(TaskError, match="identity conflicts"):
            grant(admission, grant=changed, expected_revision=original_revision)
    assert admission.queue.get(paired.id) == granted


@pytest.mark.parametrize("changes", [
    {"expires_at": float("inf")}, {"expires_at": float("nan")},
    {"expires_at": 10**400},
    {"expires_at": True}, {"expires_at": 0}, {"max_attempts": True},
    {"max_attempts": 0}, {"max_attempts": 1.5}, {"grant_id": " grant"},
    {"authority_ref": "approval\nother"},
])
def test_invalid_grant_cannot_be_constructed(changes):
    with pytest.raises(TaskError, match="standing recovery grant"):
        StandingRecoveryGrant(**{
            "grant_id": "one", "authority_ref": "approval", "expires_at": 200,
            "max_attempts": 2, **changes,
        })


@pytest.mark.parametrize("boundary", ["expiry", "hold", "retirement", "declaration"])
def test_later_policy_or_pairing_drift_withholds_retained_grant(admission, boundary):
    accept(admission)
    granted = grant(admission)
    if boundary == "declaration":
        admission.queue.reconcile_standing_charter(
            granted.title, replace(admission.declaration, source_revision="new-source"),
            repo=granted.repo, expected_revision=granted.standing_revision,
        )
    elif boundary == "retirement":
        admission.queue.retire_standing_charter(
            granted.id, expected_revision=granted.standing_revision,
        )
    elif boundary == "hold":
        with admission.queue._connect() as conn:
            conn.execute("UPDATE tasks SET hold_reason='operator hold' WHERE id=?", (granted.id,))
    before = admission.queue.get(granted.id)
    with pytest.raises(TaskError, match="expired|held|not open|current declaration"):
        admission.queue.standing_recovery_authority(granted.id, now=200 if boundary == "expiry" else 150)
    assert admission.queue.get(granted.id) == before
    assert before.standing_charter["recovery"] == granted.standing_charter["recovery"]


def test_revocation_is_durable_and_an_old_grant_retry_cannot_reactivate_it(admission):
    paired = accept(admission)
    granted = grant(admission)
    revoked = admission.queue.revoke_standing_recovery(
        paired.id, grant_id="grant-one", expected_revision=granted.standing_revision, now=120,
    )
    assert revoked.standing_charter["recovery"]["state"] == "revoked"
    assert grant(admission, expected_revision=paired.standing_revision) == revoked
    assert admission.queue.revoke_standing_recovery(
        paired.id, grant_id="grant-one", expected_revision=granted.standing_revision, now=130,
    ) == revoked
    with pytest.raises(TaskError, match="no active recovery grant"):
        admission.queue.standing_recovery_authority(paired.id, now=150)
    renewed = grant(
        admission, grant=StandingRecoveryGrant("grant-two", "approval/two", 250, 1),
    )
    assert renewed.standing_charter["recovery"]["state"] == "granted"
    with pytest.raises(TaskError, match="already retained in history"):
        grant(admission)
    with pytest.raises(TaskError, match="grant identity changed"):
        admission.queue.revoke_standing_recovery(
            paired.id, grant_id="grant-one", expected_revision=renewed.standing_revision,
        )
    assert admission.queue.get(paired.id) == renewed


def test_pending_operation_survives_revocation_and_blocks_replacement(admission):
    accept(admission)
    granted = grant(admission)
    with admission.queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        pending = admission.queue._change_standing(conn, granted, {
            "standing_charter": _encoded({
                **granted.standing_charter, "recovery": {
                    **granted.standing_charter["recovery"],
                    "pending": {"operation_id": "original-operation", "state": "unknown"},
                },
            }),
        }, action="fixture-pending-recovery", ts=120)
    before = pending.standing_charter["recovery"]["pending"]
    with pytest.raises(TaskError, match="unresolved original operation"):
        admission.queue.standing_recovery_authority(granted.id, now=150)
    with pytest.raises(TaskError, match="unresolved original operation"):
        grant(admission, grant=StandingRecoveryGrant("grant-two", "approval/two", 250, 1))
    revoked = admission.queue.revoke_standing_recovery(
        granted.id, grant_id="grant-one", expected_revision=pending.standing_revision, now=150,
    )
    assert revoked.standing_charter["recovery"]["pending"] == before


def prepare_native_recovery(admission, **changes):
    task = admission.queue.get(admission.task.id)
    if task.owner_session_id is None:
        task = accept(admission)
        task = grant(admission)
    from tests.test_assignment_relationship_owner import WRITER_IDENTITY

    admission.queue.retain_standing_native_participant(admission.request, {
        "version": 1, "journal_key": "a" * 64, "bridge_installation": "bridge-owner",
        "bridge_session_id": "original-bridge", "native_session_id": task.owner_session_id,
        "writer_requirement": {
            "version": 1, "identity": WRITER_IDENTITY,
            "creation_nonce": admission.workspace.creation_nonce,
        },
    })
    return admission.queue.prepare_standing_recovery(task.id, **{
        "operation_id": "retained-operation", "grant_id": "grant-one",
        "expected_revision": task.standing_revision, "trigger": "confirmed-loss", "now": 120,
        **changes,
    })


def recovery_result(operation, state):
    return {
        "version": 1, "operation_id": operation["operation_id"], "state": state,
        "journal_key": operation["native_participant"]["journal_key"],
        "bridge_session_id": operation["native_participant"]["bridge_session_id"],
        "native_session_id": operation["binding"]["owner_session_id"],
    }


@pytest.mark.parametrize("phase", ["prepare", "claim"])
def test_write_contention_cannot_carry_recovery_authority_past_expiry(admission, monkeypatch, phase):
    import sqlite3
    from contextlib import contextmanager
    from threading import Event

    operation = prepare_native_recovery(admission)
    queue, task_id = admission.queue, admission.task.id
    if phase == "prepare":
        queue.settle_standing_recovery(
            task_id, operation_id=operation["operation_id"],
            outcome=recovery_result(operation, "not-started"), prepared_only=True, now=125,
        )
    before = queue.get(task_id)
    entered = Event()
    clock = {"now": 150.0}
    connect = queue._connect
    monkeypatch.setattr(queue, "_now", lambda value=None: clock["now"] if value is None else value)

    @contextmanager
    def traced():
        with connect() as conn:
            conn.set_trace_callback(lambda sql: entered.set() if sql == "BEGIN IMMEDIATE" else None)
            yield conn

    monkeypatch.setattr(queue, "_connect", traced)

    def work():
        if phase == "claim":
            return queue.claim_standing_recovery(task_id, operation_id=operation["operation_id"])
        return queue.prepare_standing_recovery(
            task_id, operation_id="after-contention", grant_id="grant-one",
            expected_revision=before.standing_revision, trigger="confirmed-loss",
        )

    with ThreadPoolExecutor(max_workers=1) as pool:
        with sqlite3.connect(queue.db_path, timeout=5) as writer:
            writer.execute("BEGIN IMMEDIATE")
            future = pool.submit(work)
            try:
                assert entered.wait(5)
                clock["now"] = 201.0
            finally:
                writer.rollback()
        with pytest.raises(TaskError, match="expired"):
            future.result(timeout=5)
    assert queue.get(task_id) == before


def test_recovery_write_ahead_survives_restart_and_claims_only_once(admission):
    operation = prepare_native_recovery(admission, trigger="boot")
    task = admission.queue.get(admission.task.id)
    queue = TaskQueue(
        admission.queue.db_path, relationship_writer=admission.writer, relationship_authority="local",
    )
    assert queue.prepare_standing_recovery(
        task.id, operation_id=operation["operation_id"], grant_id="grant-one",
        expected_revision=1, trigger="boot", now=125,
    ) == operation
    with ThreadPoolExecutor(max_workers=2) as executor:
        def claim(_):
            try:
                return queue.claim_standing_recovery(task.id, operation_id=operation["operation_id"], now=130)
            except TaskError:
                return None

        results = list(executor.map(claim, range(2)))
    assert len([result for result in results if result is not None]) == 1
    retained = queue.get(task.id)
    assert retained.standing_charter["recovery"]["attempts"] == 1
    assert retained.standing_charter["recovery"]["pending"]["state"] == "invoking"
    assert (retained.owner, retained.owner_session_id, retained.generation) == (
        task.owner, task.owner_session_id, task.generation,
    )
    assert queue.task_relationship(task.id) == admission.queue.task_relationship(task.id)
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM spawn_reservations").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_pool_assignments").fetchone()[0] == 0


@pytest.mark.parametrize("boundary", ["revoked", "expired", "held", "declaration"])
def test_prepared_recovery_rechecks_policy_before_invocation(admission, boundary):
    operation = prepare_native_recovery(admission)
    task = admission.queue.get(admission.task.id)
    if boundary == "revoked":
        admission.queue.revoke_standing_recovery(
            task.id, grant_id="grant-one", expected_revision=task.standing_revision, now=125,
        )
    elif boundary == "held":
        with admission.queue._connect() as conn:
            conn.execute("UPDATE tasks SET hold_reason='operator hold' WHERE id=?", (task.id,))
    elif boundary == "declaration":
        admission.queue.reconcile_standing_charter(
            task.title, replace(admission.declaration, source_revision="changed-source"),
            repo=task.repo, expected_revision=task.standing_revision,
        )
    before = admission.queue.get(task.id)
    with pytest.raises(TaskError):
        admission.queue.claim_standing_recovery(
            task.id, operation_id=operation["operation_id"], now=201 if boundary == "expired" else 130,
        )
    assert admission.queue.get(task.id) == before


def test_revoked_preparation_can_settle_without_a_native_effect(admission):
    operation = prepare_native_recovery(admission)
    task = admission.queue.get(admission.task.id)
    admission.queue.revoke_standing_recovery(
        task.id, grant_id="grant-one", expected_revision=task.standing_revision, now=125,
    )
    result = admission.queue.settle_standing_recovery(
        task.id, operation_id=operation["operation_id"],
        outcome=recovery_result(operation, "not-started"), now=130,
    )
    assert result.standing_charter["recovery"]["state"] == "revoked"
    assert result.standing_charter["recovery"]["pending"] is None


def test_preparation_cancellation_cannot_clear_a_concurrently_claimed_operation(admission):
    operation = prepare_native_recovery(admission)
    admission.queue.claim_standing_recovery(
        admission.task.id, operation_id=operation["operation_id"], now=125,
    )
    before = admission.queue.get(admission.task.id)
    with pytest.raises(TaskError, match="preparation was consumed"):
        admission.queue.settle_standing_recovery(
            admission.task.id, operation_id=operation["operation_id"],
            outcome=recovery_result(operation, "not-started"), now=130, prepared_only=True,
        )
    assert admission.queue.get(admission.task.id) == before


def test_unknown_pending_recovery_version_cannot_claim_native_effect(admission):
    operation = prepare_native_recovery(admission)
    task = admission.queue.get(admission.task.id)
    with admission.queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = admission.queue._change_standing(conn, task, {
            "standing_charter": _encoded({
                **task.standing_charter, "recovery": {
                    **task.standing_charter["recovery"], "pending": {**operation, "version": True},
                },
            }),
        }, action="fixture-unknown-pending-version", ts=125)
    with pytest.raises(TaskError, match="held; inspect without replay"):
        admission.queue.claim_standing_recovery(
            task.id, operation_id=operation["operation_id"], now=130,
        )
    assert admission.queue.get(task.id) == before


def test_unknown_recovery_holds_original_operation_until_exact_read_only_reconciliation(admission):
    operation = prepare_native_recovery(admission)
    queue, task_id = admission.queue, admission.task.id
    queue.claim_standing_recovery(task_id, operation_id=operation["operation_id"], now=125)
    unknown = recovery_result(operation, "unknown")
    retained = queue.settle_standing_recovery(
        task_id, operation_id=operation["operation_id"], outcome=unknown, now=130,
    )
    assert queue.settle_standing_recovery(
        task_id, operation_id=operation["operation_id"], outcome=unknown, now=135,
    ) == retained
    with pytest.raises(TaskError, match="held; inspect without replay"):
        queue.claim_standing_recovery(task_id, operation_id=operation["operation_id"], now=140)
    revoked = queue.revoke_standing_recovery(
        task_id, grant_id="grant-one", expected_revision=retained.standing_revision, now=140,
    )
    assert revoked.standing_charter["recovery"]["pending"]["operation_id"] == operation["operation_id"]
    result = queue.settle_standing_recovery(
        task_id, operation_id=operation["operation_id"],
        outcome=recovery_result(operation, "complete"), now=150,
    )
    assert result.standing_charter["recovery"]["pending"] is None
    assert result.standing_charter["recovery"]["state"] == "revoked"
    assert queue.settle_standing_recovery(
        task_id, operation_id=operation["operation_id"],
        outcome=recovery_result(operation, "complete"), now=155,
    ) == result


@pytest.mark.parametrize("field,value", [
    ("operation_id", "another-operation"), ("journal_key", "b" * 64),
    ("bridge_session_id", "another-bridge"), ("native_session_id", "another-native"),
    ("version", True), ("state", "unrecognized"),
])
def test_recovery_outcome_cannot_release_another_native_operation(admission, field, value):
    operation = prepare_native_recovery(admission)
    task_id = admission.task.id
    admission.queue.claim_standing_recovery(task_id, operation_id=operation["operation_id"], now=125)
    before = admission.queue.get(task_id)
    outcome = {**recovery_result(operation, "complete"), field: value}
    with pytest.raises(TaskError, match="does not prove its original native operation"):
        admission.queue.settle_standing_recovery(
            task_id, operation_id=operation["operation_id"], outcome=outcome, now=130,
        )
    assert admission.queue.get(task_id) == before


def test_recovery_attempt_budget_and_original_ids_cannot_be_recycled(admission):
    operation = prepare_native_recovery(admission)
    task_id = admission.task.id
    for number in range(2):
        if number:
            task = admission.queue.get(task_id)
            operation = admission.queue.prepare_standing_recovery(
                task_id, operation_id="second-operation", grant_id="grant-one",
                expected_revision=task.standing_revision, trigger="boot", now=140,
            )
        admission.queue.claim_standing_recovery(task_id, operation_id=operation["operation_id"], now=145)
        task = admission.queue.settle_standing_recovery(
            task_id, operation_id=operation["operation_id"],
            outcome=recovery_result(operation, "complete"), now=150,
        )
        if number == 0:
            with pytest.raises(TaskError, match="already retained in history"):
                prepare_native_recovery(admission)
    with pytest.raises(TaskError, match="attempt limit is exhausted"):
        admission.queue.prepare_standing_recovery(
            task_id, operation_id="third-operation", grant_id="grant-one",
            expected_revision=task.standing_revision, trigger="boot", now=160,
        )


@pytest.mark.parametrize("change", [
    {"version": 2}, {"version": True}, {"attempts": True}, {"state": []}, {"binding": {}},
])
def test_unknown_recovery_state_is_retained_not_overwritten(admission, change):
    accept(admission)
    granted = grant(admission)
    with admission.queue._connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        unknown = admission.queue._change_standing(conn, granted, {
            "standing_charter": _encoded({
                **granted.standing_charter,
                "recovery": {**granted.standing_charter["recovery"], **change},
            }),
        }, action="fixture-unknown-recovery", ts=120)
    for call in (
        lambda: grant(admission, grant=StandingRecoveryGrant("new-grant", "approval/new", 300, 1)),
        lambda: admission.queue.standing_recovery_authority(granted.id, now=150),
        lambda: admission.queue.revoke_standing_recovery(
            granted.id, grant_id="grant-one", expected_revision=unknown.standing_revision,
        ),
    ):
        with pytest.raises(TaskError, match="unsupported or unconfirmed"):
            call()
    assert admission.queue.get(granted.id) == unknown


def test_native_client_grant_and_revoke_survive_unavailable_attachment_writer(admission):
    paired = accept(admission)
    requests = []
    with app(admission.queue) as api, connected(api, requests) as client:
        granted = client.grant_standing_recovery(
            paired.id, asdict(admission.workspace), expected_revision=paired.standing_revision,
            expected_generation=paired.generation, owner_session_id=paired.owner_session_id,
            grant_id="native-grant", authority_ref="approval/native",
            expires_at=time.time() + 600, max_attempts=1,
        )
    unavailable = TaskQueue(admission.queue.db_path)
    with app(unavailable) as api, connected(api, requests) as client:
        assert client.health()["standing_charters"]["attachment"] is False
        revoked = client.revoke_standing_recovery(
            paired.id, grant_id="native-grant", expected_revision=granted["standing_revision"],
        )
    assert revoked["standing_charter"]["recovery"]["state"] == "revoked"
    assert (revoked["owner"], revoked["owner_session_id"]) == (paired.owner, paired.owner_session_id)
    assert all(request.headers["authorization"] == CONTROL["Authorization"]
               for request in requests if request.method == "POST")


@pytest.mark.parametrize("support", [None, False, True, 0, 1.0, 2])
def test_old_or_malformed_recovery_capability_refuses_before_mutation(support):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.path == "/health"
        return httpx.Response(200, json={"standing_charters": {
            "version": 1, "finite_child_link": 1, "attachment": True,
            **({"recovery_grants": support} if support is not None else {}),
        }})

    with DispatchClient("http://example.test", transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(DispatchUpgradeRequired, match="recovery grants"):
            client.revoke_standing_recovery("task", grant_id="grant", expected_revision=1)
        with pytest.raises(DispatchUpgradeRequired, match="recovery grants"):
            client.grant_standing_recovery(
                "task", {}, expected_revision=1, expected_generation=1, owner_session_id="native",
                grant_id="grant", authority_ref="approval", expires_at=200, max_attempts=1,
            )
    assert len(requests) == 2


def test_recovery_routes_require_control_and_strict_bounds_before_effects(admission):
    paired = accept(admission)
    body = {
        "workspace": asdict(admission.workspace), "expected_revision": paired.standing_revision,
        "expected_generation": paired.generation, "owner_session_id": paired.owner_session_id,
        "grant_id": "native-grant", "authority_ref": "approval/native",
        "expires_at": time.time() + 600, "max_attempts": 1,
    }
    with app(admission.queue) as api:
        path = f"/tasks/{paired.id}/standing/recovery-grant"
        assert api.post(path, json=body, headers=DATA).status_code == 403
        for change in (
            {"expires_at": True}, {"expires_at": -1}, {"max_attempts": True},
            {"max_attempts": 0}, {"expected_revision": True}, {"extra": "not-supported"},
        ):
            result = api.post(path, json={**body, **change}, headers=CONTROL)
            assert result.status_code == 422, result.text
        revoke = api.post(
            f"/tasks/{paired.id}/standing/recovery-revoke",
            json={"grant_id": "any", "expected_revision": paired.standing_revision}, headers=DATA,
        )
        assert revoke.status_code == 403
    with app(admission.queue, control_token=None) as api:
        assert api.post(path, json=body, headers=DATA).status_code == 503
    assert admission.queue.get(paired.id) == paired


def test_cli_recovery_is_explicit_control_without_ensure_or_failover(admission, monkeypatch, capsys):
    paired = accept(admission)
    options = []
    with app(admission.queue) as api:
        def client(_args, **kwargs):
            options.append(kwargs)
            return connected(api, [])

        monkeypatch.setattr(cli, "_client", client)
        monkeypatch.setattr(cli, "_enrich", lambda result, **_: result)
        arguments = [
            "standing", "recovery-grant", paired.id,
            "--expected-revision", str(paired.standing_revision),
            "--expected-generation", str(paired.generation),
            "--owner-session-id", paired.owner_session_id,
            *flags(asdict(admission.workspace)),
            "--grant-id", "cli-grant", "--authority-ref", "approval/cli",
            "--expires-at", str(time.time() + 600), "--max-attempts", "1",
        ]
        assert cli.main(arguments) == 0
        granted = json.loads(capsys.readouterr().out)
        assert cli.main([
            "standing", "recovery-revoke", paired.id, "--grant-id", "cli-grant",
            "--expected-revision", str(granted["standing_revision"]),
        ]) == 0
        assert json.loads(capsys.readouterr().out)["standing_charter"]["recovery"]["state"] == "revoked"
    assert options == [{"ensure": False, "allow_ssh_failover": False}] * 2
