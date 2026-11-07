"""Ordinary ACP retries retain the real pool/Bridge/native incarnation."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from agent_dispatch import bridge, bridge_command_delivery as transport, ordinary_bridge, pool_startup_acp as acp
from agent_dispatch.client import DispatchError, DispatchUpgradeRequired
from agent_dispatch.queue import SpawnState, Status, TaskQueue
from agent_dispatch.supervisor import Supervisor
from tests import test_pool_placement_client as controls
from tests._hosting_owner import admission_owner as admission_owner
from tests.test_ordinary_pool_startup import launch, ordinary as ordinary
from tests.test_pool_startup_retry import legacy_peer, legacy_v1_retry

q = controls.q
api = controls.api


def arguments(revision: int = 1) -> dict:
    return {
        "generation": 1, "startup_revision": revision, "native_id": "native-a",
        "worktree": "worktree-a", "machine": "host-a", "reserved_by": "allocator",
        "driver": "ordinary-bridge", "bridge_session_id": "bridge-a",
    }


def failed(o: SimpleNamespace) -> str:
    result = launch(o)
    key = o.placed["reservation"]["key"]
    o.q.record_spawn(key, session_handle=result["session"], worktree=result["worktree"])
    o.q.record_pool_startup_outcome(
        key, task_id=o.task["id"], generation=1, startup_revision=1, native_id="native-a",
        outcome_kind="startup_failed", detail="original caller returned before native admission",
    )
    return key


def complete(o: SimpleNamespace, key: str) -> dict:
    return o.client.complete_pool_startup(
        o.task["id"], key, **arguments(2), outcome_kind="startup_succeeded", detail="original receipt",
    )


def test_real_ordinary_retry_preserves_distinct_ids_and_resets_only_on_acceptance(ordinary: SimpleNamespace) -> None:
    o = ordinary
    key = failed(o)
    pending = o.client.retry_pool_startup(o.task["id"], key, **arguments())
    assert pending["reservation"]["session_handle"] == "local-body:bridge-a"
    assert pending["invocation"]["bridge_session_id"] == "bridge-a"
    assert pending["invocation"]["native_id"] == "native-a"
    assert pending["invocation"]["driver"] == "ordinary-bridge"
    assert "agent-dispatch --shared start" in pending["invocation"]["prompt"]
    assert "charter show" in pending["invocation"]["prompt"]
    assert "--evaluation" not in pending["invocation"]["prompt"]
    assert TaskQueue(o.q.db_path).pool_startup(o.task["id"]).retry_invocation == pending["invocation"]
    assert o.q.pool_startup(o.task["id"]).startup_attempts == 2
    complete(o, key)
    assert complete(o, key)["reservation"]["startup_complete"] is True
    assert o.q.pool_startup(o.task["id"]).startup_attempts == 2
    assert o.q.get(o.task["id"]).status == Status.CLAIMED
    assert len(o.q.list_reservations(task_id=o.task["id"])) == 1
    o.client.start_pool_task(o.task["id"], "host-a/worktree-a",
                            owner_session_id="native-a", expected_generation=1)
    assert o.q.get(o.task["id"]).status == Status.STARTED
    assert o.q.pool_startup(o.task["id"]).startup_attempts == 0


@pytest.mark.parametrize("peer", ["client", "server"])
def test_v1_ordinary_mixed_peer_and_retained_retry(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, peer: str,
) -> None:
    o = ordinary
    key = failed(o)
    legacy_peer(monkeypatch, peer)
    pending = o.client.retry_pool_startup(o.task["id"], key, **arguments())
    binding = {name: value for name, value in pending["invocation"].items()
               if name not in {"idempotency_key", "prompt"}}
    legacy = legacy_v1_retry(binding, 2, reservation=pending["reservation"])
    assert pending["invocation"] == legacy
    with o.q._connect() as conn:
        conn.execute("UPDATE task_pool_assignments SET retry_invocation=? WHERE task_id=?",
                     (json.dumps(legacy), o.task["id"]))
    assert o.client.pool_startup_snapshot(o.task["id"])["budget"]["retry_invocation"] == legacy
    assert complete(o, key)["invocation"] == legacy
    assert o.q.get_reservation(key).session_handle == "local-body:bridge-a"
    assert o.q.get(o.task["id"]).status == Status.CLAIMED


def test_refused_first_prompt_retry_contains_original_queue_routing_and_charters(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    o = ordinary
    run = ordinary_bridge.subprocess.run

    def refuse_first(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[2] == "send":
            return subprocess.CompletedProcess(argv, 69, "original prompt not admitted", "")
        return run(argv, **kwargs)

    monkeypatch.setattr(ordinary_bridge.subprocess, "run", refuse_first)
    with pytest.raises(bridge.BridgeCarriedSessionBusy, match="send exited"):
        launch(o)
    key = o.placed["reservation"]["key"]
    o.q.record_spawn(key, session_handle="local-body:bridge-a", worktree="worktree-a")
    o.q.record_pool_startup_outcome(
        key, task_id=o.task["id"], generation=1, startup_revision=1, native_id="native-a",
        outcome_kind="startup_rejected", detail="original send refused before admission",
    )
    pending = o.client.retry_pool_startup(o.task["id"], key, **arguments())
    prompt = pending["invocation"]["prompt"]
    assert prompt.endswith(o.q.get_reservation(key).agent_info["startup_invocation"]["prompt"])
    assert "agent-dispatch --shared charter show" in prompt
    assert f"agent-dispatch --shared start {o.task['id']} --pool-generation 1" in prompt
    assert "`agent-dispatch start" not in prompt and "--evaluation" not in prompt


@pytest.mark.parametrize("field,value", [
    ("native_id", "other"), ("worktree", "other"), ("machine", "other"),
    ("bridge_session_id", "other"), ("reserved_by", "other"), ("generation", 2),
    ("driver", "cli"),
])
@pytest.mark.parametrize("operation", ["retry", "complete"])
def test_real_write_refuses_changed_ordinary_identity(
    ordinary: SimpleNamespace, field: str, value: object, operation: str,
) -> None:
    o = ordinary
    key = failed(o)
    if operation == "complete":
        o.client.retry_pool_startup(o.task["id"], key, **arguments())
    before = o.q.get(o.task["id"]), o.q.get_reservation(key), o.q.pool_startup(o.task["id"])
    kwargs = {**arguments(2 if operation == "complete" else 1), field: value}
    if operation == "complete":
        kwargs.update(outcome_kind="startup_succeeded", detail="return")
    with pytest.raises(DispatchError):
        getattr(o.client, f"{operation}_pool_startup")(o.task["id"], key, **kwargs)
    assert (o.q.get(o.task["id"]), o.q.get_reservation(key), o.q.pool_startup(o.task["id"])) == before


@pytest.mark.parametrize("change", ["missing", "driver", "rollback"])
def test_ordinary_retry_negotiates_before_any_effect(
    ordinary: SimpleNamespace, change: str,
) -> None:
    o = ordinary
    key = failed(o)
    requests = []

    def rewrite(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if request.url.path.endswith("/contract"):
            if change == "rollback":
                return httpx.Response(404, json={"detail": "old provider"})
            value = response.json()
            if change == "missing":
                value.pop("ordinary_same_native_retry")
            else:
                value["supported_drivers"] = ["cli"]
            return httpx.Response(200, json=value)
        return response

    before = o.q.get_reservation(key), o.q.pool_startup(o.task["id"])
    with controls.connected(o.api, requests, rewrite=rewrite) as client, pytest.raises(DispatchUpgradeRequired):
        client.retry_pool_startup(o.task["id"], key, **arguments())
    assert all(request.method == "GET" for request in requests)
    assert (o.q.get_reservation(key), o.q.pool_startup(o.task["id"])) == before


@pytest.fixture
def worker(ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    o = ordinary
    key = failed(o)
    clock = [101.0]
    monkeypatch.setattr(o.q, "_now", lambda now=None: clock[0] if now is None else now)
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins/agent-bridge/src"))
    from agent_bridge.__main__ import build_parser
    from agent_bridge.native_send_receipt import validate_options
    parser = build_parser()
    calls, receipts = [], {}
    state = {"lose_send": False, "busy": False, "native": "native-a", "worktree": "worktree-a"}
    monkeypatch.setattr(transport.remote_dispatch, "local_machine", lambda: "host-a")
    monkeypatch.setattr(bridge, "_agent_bridge_launch_prefix", lambda: ["fixture-bridge"])

    def run(argv: list[str], *, input: str, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        args = parser.parse_args(argv[1:])
        calls.append((args, input))
        assert argv[1] == "--json"
        if args.command == "status":
            assert args.full and args.session_id == "bridge-a"
            value = {
                "session_id": "bridge-a", "acp_session_id": state["native"],
                "durable_session_id": state["native"], "worktree_id": state["worktree"],
                "project": "example", "target_dir": str(o.q.get_reservation(key).agent_info["connection"]["target_dir"]),
                "mode": "acp", "read_only": False, "at_rest": True,
                "status": "idle", "liveness": None,
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
        assert args.command == "send" and args.target == "bridge-a" and args.expected_native_session_id == "native-a"
        assert args.idempotency_key and args.caller == f"agent-dispatch:{args.idempotency_key}"
        assert args.prompt_file == "-" and args.no_wait
        assert args.no_resume
        assert not args.live_exact and not args.queue and not args.force
        assert args.native_invocation_file is None and args.expected_session_id is None
        if args.receipt_only:
            validate_options(args)
            if args.idempotency_key not in receipts:
                return subprocess.CompletedProcess(argv, 69, json.dumps({
                    "target": "bridge-a", "outcome": "refused_unavailable", "reason": "native_receipt_missing",
                }), "")
        elif state["busy"]:
            return subprocess.CompletedProcess(argv, 75, json.dumps({
                "target": "bridge-a", "outcome": "refused_busy", "reason": "busy",
            }), "")
        else:
            receipts[args.idempotency_key] = {
                "version": 1, "session_id": "bridge-a", "native_session_id": "native-a",
                "invocation_key": args.idempotency_key, "admission": "accepted",
                "queued": False, "turn_index": len(receipts) + 1,
            }
            if state["lose_send"]:
                raise subprocess.TimeoutExpired(argv, 20)
        return subprocess.CompletedProcess(argv, 0, json.dumps(receipts[args.idempotency_key]), "")

    monkeypatch.setattr(transport.subprocess, "run", run)
    owner = Supervisor(
        o.client, repo="example", machine="host-a", supervisor_id="allocator",
        spawn_fn=lambda _: pytest.fail("pool startup cannot create a replacement"),
        verdict_fn=lambda *args: pytest.fail("ACP cannot use a live-CLI probe"),
        liveness_fn=lambda *args: pytest.fail("ACP cannot follow a worktree live alias"),
        redrive_fn=lambda *args: pytest.fail("pool startup cannot use legacy/evaluation redrive"),
    )
    return SimpleNamespace(o=o, key=key, owner=owner, clock=clock, calls=calls, receipts=receipts, state=state)


def cycle(w: SimpleNamespace, now: float) -> int:
    w.clock[0] = now
    return w.owner.redrive_unclaimed_spawns(now=now)


def test_actual_supervisor_acp_retry_reachable_nudge_and_fenced_exhaustion(worker: SimpleNamespace) -> None:
    w = worker
    assert cycle(w, 101) == 1
    assert w.o.q.pool_startup(w.o.task["id"]).startup_attempts == 2
    assert cycle(w, 102) == 1
    budget = w.o.q.pool_startup(w.o.task["id"])
    assert budget.reachable_at == 102 and budget.decision_deadline == 1902 and budget.next_nudge_at == 402
    assert cycle(w, 401) == 0
    assert cycle(w, 402) == 1
    assert cycle(w, 402) == 0
    assert w.o.q.get(w.o.task["id"]).status == Status.CLAIMED
    assert cycle(w, 1902) == 1
    current = w.o.q.get(w.o.task["id"])
    assert current.status == Status.QUEUED and current.generation == 2 and current.not_before == 5502
    retained = w.o.q.get_reservation(w.key)
    assert retained.state == SpawnState.RELEASING and retained.startup_fenced
    assert not retained.exclusive_released and retained.session_handle == "local-body:bridge-a"
    assert len(w.receipts) == 2


def test_lost_original_send_recovers_only_its_receipt_without_new_attempt(worker: SimpleNamespace) -> None:
    w = worker
    w.state["lose_send"] = True
    assert cycle(w, 101) == 0
    budget = w.o.q.pool_startup(w.o.task["id"])
    assert budget.outcome_kind == "startup_unknown" and budget.startup_attempts == 2
    assert not w.o.q.get_reservation(w.key).startup_complete
    assert cycle(w, 102) == 1
    assert len([args for args, _ in w.calls if args.command == "send" and not args.receipt_only]) == 1
    assert w.o.q.get_reservation(w.key).startup_complete
    assert w.o.q.pool_startup(w.o.task["id"]).startup_attempts == 2


def test_lost_retry_admission_reply_never_replays_even_after_restart(worker: SimpleNamespace) -> None:
    w = worker
    requests = []

    def lose(request: httpx.Request, response: httpx.Response) -> httpx.Response:
        if request.url.path.endswith("/retry"):
            raise httpx.ReadError("committed retry response lost", request=request)
        return response

    with controls.connected(w.o.api, requests, rewrite=lose) as client:
        w.owner.client = client
        assert cycle(w, 101) == 0
    w.owner.client = w.o.client
    assert cycle(w, 102) == 0
    assert w.o.q.pool_startup(w.o.task["id"]).startup_attempts == 2
    assert all(args.command == "status" or args.receipt_only for args, _ in w.calls)
    assert cycle(w, 702) == 1
    assert w.o.q.get(w.o.task["id"]).status == Status.QUEUED
    assert not w.o.q.get_reservation(w.key).startup_complete


def test_three_proven_busy_refusals_exhaust_without_a_fourth_send(worker: SimpleNamespace) -> None:
    w = worker
    w.state["busy"] = True
    assert cycle(w, 101) == 1 and cycle(w, 102) == 1
    assert w.o.q.pool_startup(w.o.task["id"]).startup_attempts == 3
    assert cycle(w, 103) == 1
    assert w.o.q.pool_startup(w.o.task["id"]).exhaustion_reason == "startup_attempts_exhausted"
    sends = [args for args, _ in w.calls if args.command == "send" and not args.receipt_only]
    assert len(sends) == 2 and not w.receipts


@pytest.mark.parametrize("field", ["native", "worktree"])
def test_changed_live_identity_never_opens_retry_or_resets_budget(worker: SimpleNamespace, field: str) -> None:
    w = worker
    w.state[field] = "successor"
    before = w.o.q.pool_startup(w.o.task["id"]), w.o.q.get_reservation(w.key)
    assert cycle(w, 101) == 0
    assert (w.o.q.pool_startup(w.o.task["id"]), w.o.q.get_reservation(w.key)) == before
    assert not w.receipts


@pytest.mark.parametrize("after", ["accepted", "exhausted", "submitted", "completed"])
def test_late_acp_retry_receipt_only_settles_original_caller(worker: SimpleNamespace, after: str) -> None:
    w = worker
    w.state["lose_send"] = True
    assert cycle(w, 101) == 0
    if after == "exhausted":
        assert cycle(w, 702) == 1
    else:
        w.o.client.start_pool_task(w.o.task["id"], "host-a/worktree-a",
                                  owner_session_id="native-a", expected_generation=1)
        if after in {"submitted", "completed"}:
            with w.o.q._connect() as conn:
                conn.execute("UPDATE tasks SET require_verification=? WHERE id=?",
                             (after == "submitted", w.o.task["id"]))
            w.o.q.complete_with_outcome(w.o.task["id"], "host-a/worktree-a",
                                       expected_generation=1, expected_owner_session_id="native-a")
            assert w.o.q.get(w.o.task["id"]).status == (
                Status.SUBMITTED if after == "submitted" else Status.COMPLETED
            )
    before = w.o.q.get(w.o.task["id"]), w.o.q.pool_startup(w.o.task["id"])
    from agent_dispatch.supervisor_pool_retry import reconcile_retry_completion
    assert reconcile_retry_completion(
        w.owner, asdict(w.o.q.get_reservation(w.key)), asdict(w.o.q.get(w.o.task["id"])),
    ) is True
    assert w.o.q.get(w.o.task["id"]) == before[0]
    assert w.o.q.pool_startup(w.o.task["id"]).startup_attempts == before[1].startup_attempts
    assert w.o.q.get_reservation(w.key).session_handle == "local-body:bridge-a"
    assert not w.o.q.get_reservation(w.key).exclusive_released


@pytest.mark.parametrize("observe_only", [True, False])
@pytest.mark.parametrize("response", [
    (None, None), (0, {}), (69, {"outcome": "refused_unavailable", "reason": "unsupported_protocol"}),
    (69, {"outcome": "refused_unavailable", "reason": "native_receipt_missing", "target": "other"}),
    (0, {"version": 1, "session_id": "bridge-a", "native_session_id": "successor",
         "invocation_key": "original", "admission": "accepted", "queued": False, "turn_index": 1}),
])
def test_unqualified_ordinary_receipt_never_permits_a_send(
    monkeypatch: pytest.MonkeyPatch, observe_only: bool, response: tuple,
) -> None:
    calls = []
    monkeypatch.setattr(acp, "_run", lambda *args: calls.append(args) or response)
    assert acp.deliver({
        "bridge_session_id": "bridge-a", "native_id": "native-a", "idempotency_key": "original",
        "prompt": "same native", "machine": "host-a",
    }, observe_only=observe_only)[0] == "startup_unknown"
    assert len(calls) == 1 and "--receipt-only" in calls[0][0]


@pytest.mark.parametrize("native", [False, True])
def test_initial_ordinary_caller_exhaustion_preserves_unknown_effect_capacity(
    ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, native: bool,
) -> None:
    o = ordinary
    if native:
        launch(o)
    owner = Supervisor(o.client, repo="example", machine="host-a", supervisor_id="allocator",
                       spawn_fn=lambda _: pytest.fail("no replacement on exhaustion"))
    key = o.placed["reservation"]["key"]
    monkeypatch.setattr(o.q, "_now", lambda now=None: 700 if now is None else now)
    assert owner.reconcile_reserving(now=700) == 1
    current = o.q.get_reservation(key)
    assert current.state == SpawnState.RELEASING and not current.startup_complete
    assert not current.startup_fenced and not current.exclusive_released
    assert current.session_handle == ("local-body:bridge-a" if native else None)
    assert o.q.get(o.task["id"]).generation == 2
