"""Tests for the generic embody spawn supervisor.

The load-bearing property under test is **spawn-at-most-once**: a task is
embodied only when a fresh spawn reservation is acquired, so a slow-but-alive
embody (whose lease expired and whose task was re-queued) is never
double-spawned.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import NoReturn

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import bridge, remote_dispatch, tracking
from agent_dispatch import supervisor as supervisor_module
from agent_dispatch.bridge_events import BridgeSubscription, SupervisorEventWake
from agent_dispatch.client import DispatchClient, DispatchError
from agent_dispatch.producers.evaluator import SpecEvaluator
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import SpawnReservation, SpawnState, Status, TaskError
from agent_dispatch.spawn_factories import _default_liveness, make_redrive_sender
from agent_dispatch.supervise_cli import _cmd_supervise
from agent_dispatch.supervisor import Supervisor
from agent_dispatch.supervisor_conclusion import _CONCLUSION_MAX_ATTEMPTS
from tests._helpers import TEST_REPO, required
from tests._legacy_supervisor_history import (
    before_retention_abandon,
    before_retention_gc,
    before_retention_release,
    before_retention_supersession,
)
from tests._supervisor_fixtures import OwnerResultQueue as OwnerResultQueue
from tests._supervisor_fixtures import ProbeOptions, RecordingSpawn, record_result
from tests._supervisor_fixtures import QueueBackedClient as QueueBackedClient
from tests._supervisor_fixtures import _ok_spawn as _ok_spawn
from tests._supervisor_fixtures import client as client
from tests._supervisor_fixtures import q as q
from tests._supervisor_fixtures import supervisor_api as supervisor_api
from tests._supervisor_fixtures import supervisor_http as supervisor_http
from tests.test_pool_placement_client import connected


def test_default_liveness_uses_local_bridge_for_local_machine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        remote_dispatch, "is_peer_machine", lambda machine: False
    )
    monkeypatch.setattr(
        tracking,
        "resolve_live_session",
        lambda worktree, *, machine=None: record_result(calls, (worktree, machine), {}),
    )

    _default_liveness("wt-local", "LOCAL-HOST")

    assert calls == [("wt-local", None)]


def test_default_liveness_uses_ssh_for_peer_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        remote_dispatch, "is_peer_machine", lambda machine: True
    )
    monkeypatch.setattr(
        tracking,
        "resolve_live_session",
        lambda worktree, *, machine=None: record_result(calls, (worktree, machine), {}),
    )

    _default_liveness("wt-peer", "peer-host")

    assert calls == [("wt-peer", "peer-host")]


# -- happy path --------------------------------------------------------------


def test_poll_spawns_eligible_task_once(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)

    spawned = sup.poll_once()
    assert spawned == [t.id]
    assert spawn.calls == [t.id]
    res = required(q.latest_reservation(t.id))
    assert res.state == SpawnState.SPAWNED
    assert res.worktree == "wt-1"

    # a second cycle does NOT spawn again (active spawned reservation)
    assert sup.poll_once() == []
    assert spawn.calls == [t.id]


def test_eligible_scopes_the_200_limit_per_own_label_not_globally(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A per-label eligibility regression: `client.list()` truncates at
    `limit` (200) newest-first ACROSS THE WHOLE COORDINATOR -- every
    label/pool sharing it, not just this one. Flood the queue with 250
    newer, differently-labeled tasks (simulating heavy unrelated activity
    from other pools) ahead of one older task in THIS supervisor's own
    label -- it must still be found eligible, not silently truncated off a
    shared newest-first page before this supervisor's own label filter ever
    gets a chance to apply."""
    old_task = q.create("old durable review", labels=["intelligence-dampener-review"])
    for i in range(250):
        q.create(f"unrelated newer task {i}", labels=["some-other-pool"])

    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        labels=["intelligence-dampener-review"],
    )

    spawned = sup.poll_once()
    assert spawned == [old_task.id]


def test_eligible_excludes_standing_charters_before_the_200_limit(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """Standing charters stay queued for explicit attachment admission only.
    A newest-first page full of them must not starve older finite work."""
    from agent_dispatch.queue_standing_charters import StandingCharterDeclaration

    finite = q.create("older finite work")
    for i in range(201):
        q.reconcile_standing_charter(f"Standing {i}", StandingCharterDeclaration(
            namespace="owner", charter_key="dispatcher", lane_key=f"lane-{i}",
            effort_ref="efforts/dispatcher", materialization_ref="roles/dispatcher",
            source_revision="source-1", expected_materialization_revision="effective-1",
        ), repo=TEST_REPO)
    assert finite.id not in {t.id for t in q.list(status=Status.QUEUED, limit=200)}

    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    assert sup.poll_once() == [finite.id]
    assert spawn.calls == [finite.id]


def test_poll_skips_a_held_queued_task(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """PR #2913 review finding: `Supervisor._eligible()` must exclude a
    queued task with a durable operator hold (Phase 1's Pause primitive) --
    otherwise a spawn reservation is minted for it, its follow-up
    `claim_one` fails on the hold, and the stray reservation is left
    behind."""
    t = q.create("work")
    q.set_hold(t.id, reason="operator paused before spawn", actor="operator")
    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert q.latest_reservation(t.id) is None

    q.clear_hold(t.id, actor="operator")
    assert sup.poll_once() == [t.id]


def test_poll_treats_a_sessionless_success_as_a_failure(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    # A spawn_fn (built-in or custom) reporting success with no session id
    # must not be recorded as SPAWNED with session_handle=None --
    # indistinguishable from a reservation that never launched anything at
    # all, which release_requested_bodies' absent-worktree shortcut relies on
    # to avoid bypassing a real (if unidentifiable) body's own liveness
    # resolution.
    t = q.create("work")
    calls = []

    def sessionless_spawn(task: dict[str, str]) -> tuple[bool, dict[str, object]]:
        calls.append(task["id"])
        return True, {"worktree": "wt-1"}  # no "session" key at all

    sup = Supervisor(supervisor_http, spawn_fn=sessionless_spawn, repo=TEST_REPO, max_concurrent=5)

    assert sup.poll_once() == []
    assert calls == [t.id]
    res = required(q.latest_reservation(t.id))
    assert res.state != SpawnState.SPAWNED
    assert res.session_handle is None


def test_poll_records_failed_headless_session_before_releasing_worktree(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    from agent_dispatch import embody

    task = q.create("work")

    class FailedSpawn:
        requires_reusable_worktree = True
        allocation_interface = "acp"
        allocation_project = "worker-harness"

        def __call__(self, _task: dict[str, object]) -> tuple[bool, dict[str, object]]:
            return False, {"error": "ACP launch failed", "session": "local-body:failed-session"}

    monkeypatch.setattr(
        embody,
        "prepare_reusable_worktree",
        lambda *_args, **_kwargs: {
            "worktree": "wt-created",
            "path": "/fixture/wt-created",
            "project": "worker-harness",
            "created": True,
            "replaced": False,
            "ownership": "created",
        },
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=FailedSpawn(),
        repo=TEST_REPO,
        machine="host-a",
    )

    assert sup.poll_once() == []
    reservation = required(q.latest_reservation(task.id))
    assert reservation.state == SpawnState.RELEASING
    assert reservation.session_handle == "local-body:failed-session"
    assert reservation.worktree == "wt-created"


def test_poll_threads_allocation_no_pair_into_worktree_preparation(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """Regression: a registrar/pool declaration's `body.no_pair` (rendered
    as `--no-pair` and surfaced on the spawn_fn as `allocation_no_pair`)
    must reach `embody.prepare_reusable_worktree`'s own `no_pair` kwarg --
    the mechanism that skips the paired-knowledge carve for a worker pool
    with no bound knowledge repo."""
    from agent_dispatch import embody

    task = q.create("work")
    captured_kwargs: dict[str, object] = {}

    def fake_prepare(*_args: object, **kwargs: object) -> dict[str, object]:
        captured_kwargs.update(kwargs)
        return {
            "worktree": "wt-created",
            "path": "/fixture/wt-created",
            "project": kwargs["project"],
            "created": True,
            "replaced": False,
            "ownership": "created",
        }

    monkeypatch.setattr(embody, "prepare_reusable_worktree", fake_prepare)

    spawn = _ok_spawn({"session": "sess-1", "worktree": "wt-created"})
    spawn.requires_reusable_worktree = True
    spawn.allocation_no_pair = True
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, machine="host-a")

    assert sup.poll_once() == [task.id]
    assert captured_kwargs.get("no_pair") is True


def test_protected_label_pool_does_not_claim_unlabeled_task(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    q.handoff_producer_scope(
        TEST_REPO,
        "scheduled",
        producer_id="scheduler-a",
        expected_generation=0,
        required_label="board",
    )
    ordinary = q.create("ordinary", source="manual")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        labels=["board"],
        max_concurrent=5,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert required(q.get(ordinary.id)).status == Status.QUEUED


def test_suspended_reservation_does_not_consume_supervisor_capacity(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    first = q.create("dormant")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=1
    )
    assert sup.poll_once() == [first.id]
    q.claim_one("host-a/wt-1", task_id=first.id)
    q.start(first.id, "host-a/wt-1")
    q.suspend(first.id, "host-a/wt-1", reason="waiting")
    second = q.create("runnable")

    assert sup.poll_once() == [second.id]
    assert spawn.calls == [first.id, second.id]
    assert required(q.get(first.id)).status == Status.SUSPENDED
    assert required(q.get(first.id)).attempts == 1


def test_live_releasing_reservation_consumes_supervisor_capacity(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    first = q.create("releasing")
    reservation, _ = q.reserve_spawn(first.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-live",
        worktree="wt-created",
    )
    q.request_spawn_release(reservation.key, disposition="failed")
    second = q.create("runnable")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=1,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: "live",
        local_body_activity_fn=lambda _sid: "ACTIVE",
        local_acp_session_fn=lambda _sid: "acp-live",
        local_end_fn=lambda _sid: False,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert required(q.latest_reservation(first.id)).state == SpawnState.RELEASING
    assert q.latest_reservation(second.id) is None


def _raise_probe_error(*_args: object) -> NoReturn:
    raise RuntimeError("probe failed")


@pytest.mark.parametrize(
    ("session_handle", "worktree", "verdict_kwarg"),
    [
        (
            "local-body:session-unknown",
            None,
            {"local_body_verdict_fn": lambda _sid: tracking.UNKNOWN},
        ),
        (
            "local-body:session-error",
            None,
            {"local_body_verdict_fn": _raise_probe_error},
        ),
        (
            "fleet-body:host-b:session-unknown",
            None,
            {"fleet_verdict_fn": lambda _host, _sid: tracking.UNKNOWN},
        ),
        (
            "fleet-body:host-b:session-error",
            None,
            {"fleet_verdict_fn": _raise_probe_error},
        ),
        (
            "session-unknown",
            "wt-unknown",
            {
                "verdict_fn": lambda *_args: tracking.UNKNOWN,
                # Pin the local agent-worktrees registry probe to "unresolved"
                # so this stays a genuinely-unresolvable-probe case (this
                # test's actual point), independent of whatever
                # worktree_directory_present_fn's real default would report
                # for a fabricated, never-created worktree id on this host.
                "worktree_directory_present_fn": lambda _wt, _project: None,
            },
        ),
        (
            "session-error",
            "wt-error",
            {
                "verdict_fn": _raise_probe_error,
                "worktree_directory_present_fn": lambda _wt, _project: None,
            },
        ),
    ],
)
def test_unknown_releasing_body_consumes_process_capacity(
    q: OwnerResultQueue,
    supervisor_http: DispatchClient,
    session_handle: str,
    worktree: str | None,
    verdict_kwarg: ProbeOptions,
) -> None:
    first = q.create("releasing")
    reservation, _ = q.reserve_spawn(first.id)
    q.record_spawn(
        reservation.key,
        session_handle=session_handle,
        worktree=worktree,
    )
    q.request_spawn_release(reservation.key, disposition="failed")
    q.create("runnable")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=1,
        **verdict_kwarg,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert required(q.latest_reservation(first.id)).state == SpawnState.RELEASING


@pytest.mark.parametrize(
    ("session_handle", "worktree", "verdict_kwarg"),
    [
        (
            "local-body:session-gone",
            None,
            {"local_body_verdict_fn": lambda _sid: tracking.GONE},
        ),
        (
            "fleet-body:host-b:session-gone",
            None,
            {"fleet_verdict_fn": lambda _host, _sid: tracking.GONE},
        ),
        (
            "session-gone",
            "wt-gone",
            {"verdict_fn": lambda *_args: tracking.GONE},
        ),
    ],
)
def test_exact_gone_releasing_body_frees_process_capacity(
    q: OwnerResultQueue,
    supervisor_http: DispatchClient,
    session_handle: str,
    worktree: str | None,
    verdict_kwarg: ProbeOptions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = q.create("releasing")
    if worktree:
        monkeypatch.setattr(supervisor_http, "attachments", lambda _task: [{
            "session_id": session_handle, "worktree_id": worktree, "machine": "peer-host",
        }])
    reservation, _ = q.reserve_spawn(first.id)
    q.record_spawn(
        reservation.key,
        session_handle=session_handle,
        worktree=worktree,
    )
    q.request_spawn_release(
        reservation.key,
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    q.create("runnable")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=1,
        local_end_fn=lambda _sid: True,
        **verdict_kwarg,
    )

    spawned = sup.poll_once()
    assert len(spawned) == 1
    assert spawn.calls == spawned


def test_unknown_spawned_body_still_consumes_process_capacity(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    first = q.create("spawned")
    reservation, _ = q.reserve_spawn(first.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-unknown",
    )
    second = q.create("runnable")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=1,
        local_body_verdict_fn=lambda _sid: tracking.UNKNOWN,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert q.latest_reservation(second.id) is None


def test_other_pool_reservation_does_not_consume_process_capacity(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    other = q.create("other pool", repo="github.com/example/other")
    reservation, _ = q.reserve_spawn(other.id)
    q.record_spawn(reservation.key, session_handle="local-body:other-session")
    runnable = q.create("this pool")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=1
    )

    assert sup.poll_once() == [runnable.id]


def test_other_label_reservations_are_not_probed(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    held = q.create("other held task", labels=["other"])
    held_reservation, _ = q.reserve_spawn(held.id)
    q.record_spawn(
        held_reservation.key,
        session_handle="other-session",
        worktree="other-worktree",
    )
    owner = "other-machine/other-worktree"
    q.claim_one(owner, task_id=held.id)
    q.start(held.id, owner)

    unclaimed = q.create("other unclaimed task", labels=["other"])
    queued_reservation, _ = q.reserve_spawn(unclaimed.id)
    q.record_spawn(
        queued_reservation.key,
        session_handle="queued-session",
        worktree="queued-worktree",
    )

    probes: list[tuple[str, str]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        stall_seconds=1,
        liveness_fn=lambda worktree, _machine: (
            record_result(probes, ('live', worktree), {"session_id": "session"})
        ),
        verdict_fn=lambda worktree, _machine, _session: (
            record_result(probes, ('verdict', worktree), tracking.LIVE)
        ),
    )

    assert sup.poll_once(now=held.created_at + 10) == []
    assert probes == []


def test_suspended_local_headless_body_is_cooled(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("dormant review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:bridge-session-1",
    )
    q.claim_one("headless-owner", task_id=task.id)
    q.start(task.id, "headless-owner")
    q.suspend(task.id, "headless-owner", reason="waiting for author")
    stopped: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_cold_fn=lambda session_id: record_result(stopped, session_id, True),
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.cool_dormant_bodies() == 1
    assert stopped == ["bridge-session-1"]
    assert required(q.get_reservation(reservation.key)).state == SpawnState.COLD
    assert sup.cool_dormant_bodies() == 0


def test_cli_suspensions_do_not_consume_headless_cooling_budget(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    for index in range(12):
        task = q.create(f"cli dormant {index}", labels=["review"])
        reservation, _ = q.reserve_spawn(task.id)
        q.record_spawn(
            reservation.key, startup_complete=True, startup_revision=1,
            session_handle=f"cli-session-{index}",
            worktree=f"wt-{index}",
        )
        q.claim_one(f"machine/wt-{index}", task_id=task.id)
        q.start(task.id, f"machine/wt-{index}")
        q.suspend(task.id, f"machine/wt-{index}", reason="waiting")
    headless = q.create("headless dormant", labels=["review"])
    reservation, _ = q.reserve_spawn(headless.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:headless-session",
    )
    q.claim_one("headless-owner", task_id=headless.id)
    q.start(headless.id, "headless-owner")
    q.suspend(headless.id, "headless-owner", reason="waiting")
    stopped: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_cold_fn=lambda session_id: record_result(stopped, session_id, True),
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.cool_dormant_bodies() == 1
    assert stopped == ["headless-session"]


def test_blocking_card_cools_body_and_frees_process_capacity(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.set_card(
        blocked.id,
        "headless-owner",
        card={"request_input": [{"name": "decision", "type": "text"}]},
    )
    runnable = q.create("next review", labels=["review"])
    stopped: list[str] = []
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        labels=["review"],
        max_concurrent=1,
        local_cold_fn=lambda session_id: record_result(stopped, session_id, True),
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.poll_once() == [runnable.id]
    assert stopped == ["blocked-session"]
    assert required(q.get(blocked.id)).status == Status.SUSPENDED
    assert required(q.get(blocked.id)).awaiting_steer is True


def test_stopped_cold_body_does_not_consume_capacity_after_restart(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    blocked = q.create("waiting", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:stopped-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="waiting")
    q.record_cold(reservation.key)
    runnable = q.create("next", labels=["review"])
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        labels=["review"],
        max_concurrent=1,
        local_body_verdict_fn=lambda _sid: tracking.GONE,
    )

    assert sup.poll_once() == [runnable.id]
    assert spawn.calls == [runnable.id]


def test_cold_steer_resumes_existing_acp_session(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    steered = q.submit_steer(
        blocked.id,
        fields={"decision": "continue"},
        sender="operator",
    )
    assert steered.status == Status.SUSPENDED
    assert steered.resume_requested is True
    assert required(q.get_reservation(reservation.key)).state == SpawnState.COLD
    spawn = _ok_spawn()
    resumed: list[tuple[str, str]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        labels=["review"],
        local_body_activity_fn=lambda _session_id: "ACTIVE",
        local_body_verdict_fn=lambda _session_id: "live",
        local_resume_fn=lambda session_id, prompt: (
            record_result(resumed, (session_id, prompt), True)
        ),
    )
    sup._cooled_reservations.add(reservation.key)

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert resumed and resumed[0][0] == "blocked-session"
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED
    assert required(q.get(blocked.id)).status == Status.STARTED
    assert required(q.get(blocked.id)).resume_requested is False
    assert reservation.key not in sup._cooled_reservations


def test_cold_resume_missing_worktree_preserves_identity_until_cleanup(
    q: OwnerResultQueue, supervisor_http: DispatchClient, tmp_path: Path
) -> None:
    missing_worktree = tmp_path / "removed-review-worktree"
    blocked = q.create(
        "needs operator",
        labels=["review"],
        goal="finish the current review",
        done_criteria="post a verdict for the current head",
    )
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:blocked-session",
        worktree="removed-review-worktree",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.record_progress(
        blocked.id,
        "headless-owner",
        phase="reviewing",
        summary="reviewed the prior head",
    )
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(
        blocked.id,
        fields={"head": "corrected-head"},
        sender="producer",
    )
    before = required(q.get(blocked.id))
    before_progress: list[dict[str, object]] = q.progress_log(blocked.id)
    before_steers = q.steer_log(blocked.id)
    resumed: list[str] = []
    cleanup_complete = False
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        labels=["review"],
        local_body_target_dir_fn=lambda _sid: str(missing_worktree),
        local_body_verdict_fn=lambda _sid: "gone",
        local_end_fn=lambda _sid: cleanup_complete,
        local_acp_session_fn=lambda _sid: None,
        local_resume_fn=lambda sid, _prompt: record_result(resumed, sid, True),
    )
    sup._cooled_reservations.add(reservation.key)

    assert sup.release_resumed_cold_tasks(now=1000) == 1
    released = required(q.get(blocked.id))
    assert released.id == before.id
    assert released.goal == before.goal
    assert released.done_criteria == before.done_criteria
    assert released.latest_progress == before.latest_progress
    after_progress: list[dict[str, object]] = q.progress_log(blocked.id)
    assert after_progress == before_progress
    assert q.steer_log(blocked.id) == before_steers
    assert released.status == Status.SUSPENDED
    assert released.owner == before.owner
    assert resumed == []
    held = required(q.get_reservation(reservation.key))
    assert held.state == SpawnState.RELEASING
    assert held.release_requested is True
    assert held.conclusion_state == "pending"

    sup.release_requested_bodies(now=1001)
    assert required(q.get_reservation(reservation.key)).conclusion_state == "pending"
    with pytest.raises(TaskError, match="requires queued and unowned"):
        q.reserve_spawn(blocked.id)
    assert spawn.calls == []
    assert len(q.list_reservations(task_id=blocked.id)) == 1
    assert required(q.get(blocked.id)).owner == before.owner
    cleanup_complete = True
    sup.release_requested_bodies(now=time.time() + 600)
    closed = required(q.get_reservation(reservation.key))
    assert closed.state == SpawnState.SETTLED and closed.conclusion_state == "complete"
    queued = required(q.get(blocked.id))
    assert queued.status == Status.QUEUED and queued.owner is None
    assert q.progress_log(blocked.id) == before_progress
    assert q.steer_log(blocked.id) == before_steers
    assert q.reserve_spawn(blocked.id)[1]


@pytest.mark.parametrize("target_kind", ["unknown", "present", "relative"])
def test_cold_resume_preserves_unknown_or_present_worktree(
    q: OwnerResultQueue, supervisor_http: DispatchClient, tmp_path: Path, target_kind: str
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")
    resumed: list[str] = []
    target_dir = {
        "unknown": None,
        "present": str(tmp_path),
        "relative": "relative/worktree",
    }[target_kind]
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_target_dir_fn=lambda _sid: target_dir,
        local_resume_fn=lambda sid, _prompt: record_result(resumed, sid, True),
    )

    assert sup.release_resumed_cold_tasks() == 1
    assert resumed == ["blocked-session"]
    assert required(q.get(blocked.id)).status == Status.STARTED
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED


def test_cold_resume_preserves_worktree_on_stat_error(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")

    real_stat = Path.stat

    def deny_stat(path: Path, *, follow_symlinks: bool = True) -> os.stat_result:
        if str(path) == "/inaccessible/worktree":
            raise PermissionError("temporarily inaccessible")
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", deny_stat)
    resumed: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_target_dir_fn=lambda _sid: "/inaccessible/worktree",
        local_resume_fn=lambda sid, _prompt: record_result(resumed, sid, True),
    )

    assert sup.release_resumed_cold_tasks() == 1
    assert resumed == ["blocked-session"]
    assert required(q.get(blocked.id)).status == Status.STARTED


def test_cold_resume_missing_worktree_release_failure_backs_off(
    q: OwnerResultQueue,
    supervisor_http: DispatchClient,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")
    attempts: list[str] = []

    def fail_release(task_id: str, **_observed: object) -> NoReturn:
        attempts.append(task_id)
        raise DispatchError(500, "release failed")

    monkeypatch.setattr(supervisor_http, "requeue_gone_owner", fail_release)
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_target_dir_fn=lambda _sid: str(tmp_path / "missing"),
        local_body_verdict_fn=lambda _sid: "gone",
    )

    assert sup.release_resumed_cold_tasks(now=1000) == 0
    assert sup.release_resumed_cold_tasks(now=1299) == 0
    assert attempts == [blocked.id]
    assert sup.release_resumed_cold_tasks(now=1300) == 0
    assert attempts == [blocked.id, blocked.id]
    assert required(q.get(blocked.id)).status == Status.SUSPENDED


# -- recover_stranded_cold_reservations: the queued+cold orphan gap ---------
#
# Confirmed live (the downstream PR's stall, 5+ hours, survived a full
# supervisor restart): a COLD reservation whose task ends up QUEUED/unowned
# instead of the SUSPENDED-with-owner shape release_resumed_cold_tasks()
# expects is a permanent orphan no other sweep ever revisits --
# release_resumed_cold_tasks() requires resume_requested (set only by a
# steer landing while the task is STILL suspended) and requires status ==
# SUSPENDED with a live owner; recover_gone() only looks at SPAWNED
# reservations and explicitly skips SUSPENDED; reconcile() only settles
# TERMINAL tasks. None of them ever touch this combination.


def test_recover_stranded_cold_reservation_releases_confirmed_gone_body(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A reservation can go COLD (a pure reservation-state operation, see
    ``record_cold``) independent of its task's own status -- exactly the gap
    this sweep closes: whatever upstream path leaves a queued/unowned task
    paired with a stale COLD reservation for its exclusive_key, nothing else
    ever revisits it.

    Uses ``defer_spawn`` (state ``deferred``), not ``fail_spawn`` (rubber-duck
    review, 2026-09-28): the task/reservation bookkeeping fell out of sync,
    but no spawn attempt actually failed, so this repair must never count
    toward dead-lettering -- see
    ``test_recover_stranded_cold_reservation_never_counts_toward_dead_letter``
    below."""
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.record_cold(reservation.key)
    assert required(q.get(blocked.id)).status == Status.QUEUED
    assert required(q.get(blocked.id)).owner is None
    assert required(q.get_reservation(reservation.key)).state == SpawnState.COLD

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "gone",
    )

    assert sup.recover_stranded_cold_reservations() == 1
    assert required(q.get_reservation(reservation.key)).state == SpawnState.DEFERRED

    # Freed for a fresh attempt on the very next cycle.
    assert sup.poll_once() == [blocked.id]
    assert required(q.latest_reservation(blocked.id)).state == SpawnState.SPAWNED
    assert required(q.latest_reservation(blocked.id)).attempt == 2


def test_recover_stranded_cold_reservation_ignores_unconfirmed_liveness(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """Never act on an unknown/live verdict -- only a COMPLETED-gone (or
    cold-probe-confirmed) body is released."""
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.record_cold(reservation.key)

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "unknown",
        local_cold_fn=lambda _sid: False,
    )

    assert sup.recover_stranded_cold_reservations() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.COLD


def test_recover_stranded_cold_reservation_ignores_still_suspended_tasks(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A reservation that's COLD because its task is genuinely still
    SUSPENDED (the normal, intentional dormancy) is release_resumed_cold_tasks's
    job, never this sweep's -- even with a confirmed-gone body."""
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "gone",
    )

    assert sup.recover_stranded_cold_reservations() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.COLD
    assert required(q.get(blocked.id)).status == Status.SUSPENDED


def test_recover_stranded_cold_reservation_ignores_terminal_tasks(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.record_cold(reservation.key)
    q.claim_one("headless-owner-2", task_id=blocked.id)
    q.start(blocked.id, "headless-owner-2")
    q.complete(blocked.id, "headless-owner-2", result_ref="done")

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "gone",
    )

    assert sup.recover_stranded_cold_reservations() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.COLD


def test_recover_stranded_cold_reservation_never_counts_toward_dead_letter(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """Repeatedly repairing this benign cross-FSM inconsistency must never
    dead-letter a task that never actually failed a spawn attempt
    (rubber-duck review, 2026-09-28): the sweep uses ``defer_spawn``, which
    -- unlike ``fail_spawn`` -- never counts toward
    ``Supervisor._failed_spawn_counts``'s dead-letter budget."""
    blocked = q.create("needs operator", labels=["review"])

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "gone",
        max_attempts=3,
    )

    # Repeat the stranded-cold shape (a fresh reservation each time, cooled,
    # then repaired) many more times than max_attempts -- none of it may
    # dead-letter, because none of it is an actual spawn failure.
    for _ in range(10):
        reservation = q.latest_reservation(blocked.id)
        if reservation is None or reservation.state not in (
            SpawnState.RESERVING,
            SpawnState.SPAWNED,
        ):
            reservation, _ = q.reserve_spawn(blocked.id)
        q.record_spawn(
            reservation.key,
            startup_complete=True,
            startup_revision=1,
            session_handle=f"local-body:{reservation.key}",
        )
        q.record_cold(reservation.key)
        assert sup.recover_stranded_cold_reservations() == 1

    assert required(q.get(blocked.id)).status == Status.QUEUED
    assert len(q.list_reservations(task_id=blocked.id, state="failed")) == 0
    assert (
        len(q.list_reservations(task_id=blocked.id, state="deferred")) == 10
    )
    # Still freely spawnable -- never dead-lettered by repair debt.
    assert sup.poll_once() == [blocked.id]


# -- recover_stranded_releasing_reservations: the no-handle releasing gap ---
#
# Confirmed live (copilot-extensions#3179): a
# RELEASING reservation that never recorded a session_handle (the spawn
# itself crashed/errored before ever launching a body) has nothing an
# automatic exact-absence proof could ever check -- there's no handle to
# probe liveness against -- so it sits RELEASING forever with no other sweep
# ever revisiting it, permanently blocking its exclusive_key across every
# future task sharing that key, not just its own retry. The CLI's own
# `reservations fail --force` already carries the correct judgment for this
# exact shape; this sweep applies it automatically past a bounded age.


def test_recover_stranded_releasing_reservation_with_no_handle(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    blocked = q.create("needs recovery", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.request_spawn_release(reservation.key, disposition="failed")
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING
    assert not required(q.get_reservation(reservation.key)).session_handle

    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, labels=["review"])

    old_enough = time.time() + 601
    assert sup.recover_stranded_releasing_reservations(now=old_enough) == 1
    assert required(q.get_reservation(reservation.key)).state == SpawnState.FAILED

    # Freed for a fresh attempt on the very next cycle.
    assert sup.poll_once() == [blocked.id]


def test_recover_stranded_releasing_reservation_ignores_handle_carrying(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A RELEASING reservation that DOES carry a session_handle is a
    liveness-checkable case -- never this sweep's job (it would otherwise
    cut off a release that's genuinely still in flight)."""
    blocked = q.create("needs recovery", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(reservation.key, session_handle="local-body:still-live")
    q.request_spawn_release(reservation.key, disposition="failed")
    assert required(q.get_reservation(reservation.key)).session_handle

    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, labels=["review"])

    old_enough = time.time() + 601
    assert sup.recover_stranded_releasing_reservations(now=old_enough) == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING


def test_recover_stranded_releasing_reservation_respects_the_age_bound(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """Never cut off a release call that might still be genuinely in
    flight -- only a reservation past the bound is stranded."""
    blocked = q.create("needs recovery", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.request_spawn_release(reservation.key, disposition="failed")

    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, labels=["review"])

    still_fresh = time.time() + 1  # well under the 600s bound
    assert sup.recover_stranded_releasing_reservations(now=still_fresh) == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING


def test_recover_stranded_releasing_reservation_never_blocks_other_lanes(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """The exact blast-radius shape confirmed live: a stranded no-handle
    RELEASING reservation for one task permanently blocks `reserve_spawn`
    for every OTHER task sharing its exclusive_key, even brand-new ones --
    this sweep clearing it must free the whole exclusive_key, not just the
    one stuck task."""
    stuck = q.create("stuck", labels=["review"], exclusive_key="pr-42")
    reservation, _ = q.reserve_spawn(stuck.id)
    q.request_spawn_release(reservation.key, disposition="failed")

    # A fresh task sharing the same exclusive_key (the real-world shape: the
    # original task got abandoned/recreated, but the stale reservation still
    # fences the key) cannot even be reserved while the stuck one stands.
    fresh = q.create("fresh retry", labels=["review"], exclusive_key="pr-42")
    _, reserved = q.reserve_spawn(fresh.id)
    assert reserved is False

    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, labels=["review"])
    old_enough = time.time() + 601
    assert sup.recover_stranded_releasing_reservations(now=old_enough) == 1

    # Now a fresh reservation for the other task succeeds.
    _, reserved_after = q.reserve_spawn(fresh.id)
    assert reserved_after is True


def test_reconcile_settles_reservation_once_task_is_confirmed(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """``COMPLETED`` is the true completion terminal (superseding the
    provisional ``SUBMITTED``, see queue_records.py) -- reconcile() must
    settle a still-active reservation once a task reaches it, exactly like
    it already does for ``SUBMITTED``, or the reservation (and its
    exclusive_key) is fenced forever (rubber-duck review, 2026-09-28:
    ``Supervisor._TERMINAL`` had never been updated when ``COMPLETED`` was
    introduced)."""
    t = q.create("work")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:sess",
    )
    q.claim_one("owner", task_id=t.id)
    q.start(t.id, "owner")
    q.complete(t.id, "owner", result_ref="done")
    q.confirm(t.id, actor="evaluator")
    assert required(q.get(t.id)).status == Status.COMPLETED
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: "live",
        local_end_fn=lambda _sid: True,
    )

    assert sup.reconcile() == 1
    settled = required(q.get_reservation(reservation.key))
    assert settled.state in (SpawnState.RELEASING, SpawnState.SETTLED)


def test_reconcile_settles_abandoned_cold_reservation(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """Reconcile a previously abandoned task without allowing new premature abandonment."""
    t = q.create("work")
    stale_reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(
        stale_reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="retained-session",
    )
    q.record_cold(stale_reservation.key)

    before_retention_abandon(q, t.id, reason="test")
    assert required(q.get(t.id)).status == Status.ABANDONED
    assert required(q.get_reservation(stale_reservation.key)).state == SpawnState.COLD

    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO)
    assert sup.reconcile() == 1
    assert required(q.get_reservation(stale_reservation.key)).state == SpawnState.SETTLED


def test_pool_reservations_transient_transport_error_does_not_abort_cycle(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connection blip listing reservations must not propagate.

    Regression test for copilot-extensions#2857: `_pool_reservations` (and
    every `release_resumed_cold_tasks`-style caller built on it) used to let
    a raw `httpx.TransportError` escape uncaught, which aborted the entire
    per-cycle sweep -- not just this one sub-check -- whenever the
    coordinator was briefly unreachable (e.g. during a version-restart
    race). It must instead be treated like "nothing to report this cycle."
    """
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")

    def refused(*_args: object, **_kwargs: object) -> None:
        raise httpx.ConnectError(
            "[WinError 10061] No connection could be made because the target "
            "machine actively refused it"
        )

    monkeypatch.setattr(supervisor_http, "list_reservations", refused)
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
    )

    assert sup._pool_reservations(state=SpawnState.COLD, resume_requested=True) == []
    # The whole cycle must not raise either -- this is the actual bug shape:
    # release_resumed_cold_tasks previously let the ConnectError propagate.
    assert sup.release_resumed_cold_tasks() == 0
    assert required(q.get(blocked.id)).status == Status.SUSPENDED


def test_active_reservations_fails_closed_on_transport_error(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Capacity gating must not treat "unknown" as "zero active".

    `_active_reservations` feeds directly into the active >= max_concurrent
    capacity gate in `poll_once`. Swallowing a transient transport error into
    an empty list there (as the generic `_pool_reservations` resilience does
    for every other, non-capacity-critical caller) would make the supervisor
    believe it has full spare capacity during an outage and over-spawn past
    max_concurrent. It must instead fail closed: report capacity as fully
    consumed so this cycle spawns nothing, and let the next interval retry
    once the coordinator is reachable again.
    """
    queued = q.create("work")

    def refused(*_args: object, **_kwargs: object) -> None:
        raise httpx.ConnectError(
            "[WinError 10061] No connection could be made because the target "
            "machine actively refused it"
        )

    monkeypatch.setattr(supervisor_http, "list_reservations", refused)
    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)

    active = sup._active_reservations()
    assert len(active) == sup.max_concurrent

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert required(q.get(queued.id)).status == Status.QUEUED


def test_failed_spawn_counts_fails_closed_on_transport_error(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dead-letter/retry gating must not treat "unknown" as "zero failures".

    `_failed_spawn_counts` feeds `_is_dead_lettered` (via `max_attempts`).
    Swallowing a transient transport error into `{}` there would make every
    task look like it has zero failed attempts, letting one that has
    already exhausted `max_attempts` keep retrying past its bound during an
    outage. It must instead fail closed: block *new* spawns entirely this
    cycle (existing active work is unaffected) rather than guess either way.
    """
    queued = q.create("work")

    def refused(*_args: object, **_kwargs: object) -> None:
        raise httpx.ConnectError(
            "[WinError 10061] No connection could be made because the target "
            "machine actively refused it"
        )

    monkeypatch.setattr(supervisor_http, "list_reservations", refused)
    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)

    assert sup._failed_spawn_counts() is None

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert required(q.get(queued.id)).status == Status.QUEUED


def test_release_requested_legacy_missing_worktree_recovers_from_held_conclusion(
    q: OwnerResultQueue, supervisor_http: DispatchClient, tmp_path: Path
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:blocked-session",
        worktree="removed-review-worktree",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    before_retention_release(
        q, blocked.id,
        "headless-owner",
        reason="recorded worktree is missing",
    )
    q.record_spawn_conclusion(
        reservation.key,
        conclusion_state="held",
        conclusion_detail=json.dumps(
            {"action": "skipped", "reason": "allocation-ownership-unknown"}
        ),
    )
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        labels=["review"],
        local_body_target_dir_fn=lambda _sid: str(tmp_path / "missing"),
        local_body_verdict_fn=lambda _sid: "gone",
        local_end_fn=lambda _sid: True,
    )

    assert sup.release_requested_bodies() == 1
    settled = required(q.get_reservation(reservation.key))
    assert settled.state == SpawnState.SETTLED
    assert settled.conclusion_state == "complete"
    assert "recorded-target-directory-missing" in (
        settled.conclusion_detail or ""
    )

    assert sup.poll_once() == [blocked.id]
    assert spawn.calls == [blocked.id]


def test_release_requested_rechecks_target_after_live_body_teardown(
    q: OwnerResultQueue, supervisor_http: DispatchClient, tmp_path: Path
) -> None:
    target_dir = tmp_path / "recreated-worktree"
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:blocked-session",
        worktree="recreated-worktree",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    before_retention_release(
        q, blocked.id,
        "headless-owner",
        reason="recorded worktree is missing",
    )

    def end_and_recreate(_session_id: str) -> bool:
        target_dir.mkdir()
        return True

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_target_dir_fn=lambda _sid: str(target_dir),
        local_body_verdict_fn=lambda _sid: "live",
        local_end_fn=end_and_recreate,
    )

    assert sup.release_requested_bodies() == 1
    held = required(q.get_reservation(reservation.key))
    assert held.state == SpawnState.SETTLED
    assert held.conclusion_state == "held"
    assert "allocation-ownership-unknown" in (
        held.conclusion_detail or ""
    )


def test_cold_resume_does_not_start_process_before_reservation_transition(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")
    resumed: list[str] = []

    def fail_record_spawn(*_args: object, **_kwargs: object) -> NoReturn:
        raise DispatchError(500, "write failed")

    monkeypatch.setattr(
        supervisor_http,
        "record_spawn",
        fail_record_spawn,
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_resume_fn=lambda sid, _prompt: record_result(resumed, sid, True),
    )

    assert sup.release_resumed_cold_tasks() == 0
    assert resumed == []
    assert required(q.get_reservation(reservation.key)).state == SpawnState.COLD
    assert required(q.get(blocked.id)).status == Status.SUSPENDED


def test_cold_resume_task_failure_retains_unresolved_finalization(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")

    def fail_resume(*_args: object, **_kwargs: object) -> NoReturn:
        raise DispatchError(500, "resume failed")

    monkeypatch.setattr(
        supervisor_http,
        "resume",
        fail_resume,
    )
    stopped: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
        local_resume_fn=lambda _sid, _prompt: True,
        local_cold_fn=lambda sid: record_result(stopped, sid, True),
    )

    assert sup.release_resumed_cold_tasks() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED
    assert required(q.get_reservation(reservation.key)).startup_complete is False
    assert required(q.get(blocked.id)).status == Status.SUSPENDED
    assert sup.cool_dormant_bodies() == 0
    assert stopped == []
    assert len(sup._active_reservations()) == 1
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED


def test_cold_resume_process_exception_retains_unresolved_startup(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")

    def fail_process_resume(_sid: str, _prompt: str) -> NoReturn:
        raise OSError("bridge unavailable")

    stopped: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
        local_resume_fn=fail_process_resume,
        local_cold_fn=lambda sid: record_result(stopped, sid, True),
    )

    assert sup.release_resumed_cold_tasks() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED
    assert required(q.get(blocked.id)).status == Status.SUSPENDED
    assert sup.cool_dormant_bodies() == 0
    assert stopped == []
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED
    assert required(q.get_reservation(reservation.key)).startup_complete is False


def test_cold_resume_process_failure_backs_off_before_retry(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:blocked-session",
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="turn ended")
    q.record_cold(reservation.key)
    q.submit_steer(blocked.id, fields={"decision": "continue"}, sender="operator")
    attempts: list[str] = []

    def fail_process_resume(sid: str, _prompt: str) -> NoReturn:
        attempts.append(sid)
        raise OSError("bridge unavailable")

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
        local_resume_fn=fail_process_resume,
        local_cold_fn=lambda _sid: True,
    )

    assert sup.release_resumed_cold_tasks(now=1000) == 0
    assert sup.cool_dormant_bodies() == 0
    assert sup.release_resumed_cold_tasks(now=1299) == 0
    assert attempts == ["blocked-session"]
    assert sup.release_resumed_cold_tasks(now=1300) == 0
    assert attempts == ["blocked-session"]


def test_idle_confirm_nudge_asks_whether_the_task_is_done(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[tuple[str, str | None, str]] = []
    monkeypatch.setattr(
        "agent_dispatch.bridge.resume_session",
        lambda target, message, **k: record_result(sent, (target, k.get('host'), message), True),
    )
    from agent_dispatch.idle_confirm import default_idle_confirm_nudge

    assert default_idle_confirm_nudge(
        "sid-1",
        {
            "id": "abc",
            "title": "do the thing",
            "done_criteria": "comment posted",
        },
    )
    assert sent[0][0] == "sid-1"
    assert sent[0][1] is None
    msg = sent[0][2]
    assert "task abc (do the thing) is still in progress" in msg
    assert "Re-read the task and continue from its recorded state" in msg
    assert "agent-dispatch complete abc" in msg
    assert "Done-criteria: comment posted" in msg
    assert "do not complete just to clear this nudge" in msg
    assert "steering card" not in msg


def test_idle_confirm_nudge_without_goal_or_criteria_falls_back_to_title() -> None:
    from agent_dispatch.idle_confirm import idle_confirm_message

    msg = idle_confirm_message({"id": "abc", "title": "do the thing"})
    assert "Your assignment: do the thing" in msg
    assert "Re-read the task and continue from its recorded state" in msg
    assert "Done-criteria" not in msg
    assert "if the assignment is genuinely, fully complete" in msg
    assert "if the done-criteria are genuinely met" not in msg


def test_idle_headless_fleet_nudge_includes_remote_host(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("review turn", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, session_handle="fleet-body:host-a:sess-9"
    )
    q.claim_one("headless-owner", task_id=task.id)
    q.start(task.id, "headless-owner")
    nudged: list[tuple[str, str | None]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        fleet_activity_fn=lambda _host, _sid: "IDLE",
        # Isolates this from the DEFAULT fleet_verdict_fn, which probes a
        # REAL local agent-bridge carrier (embody.fleet_body_verdict) --
        # on a machine where one happens to be running, "host-a"/"sess-9"
        # genuinely resolves to GONE (session not found), triggering real
        # recovery/respawn instead of the plain idle-nudge path this test
        # means to exercise. An explicit unknown-liveness stub (matching
        # the style already used elsewhere in this file) keeps the test's
        # outcome independent of whatever bridge infrastructure happens to
        # be reachable from the box running it.
        fleet_verdict_fn=lambda _host, _sid: "unknown",
        idle_nudge_fn=lambda target, t: record_result(nudged, (target, t.get('_idle_host')), True),
    )

    assert sup.poll_once() == []
    assert nudged == [("sess-9", "host-a")]
    assert required(q.get(task.id)).status == Status.STARTED


def test_idle_confirm_nudge_routes_fleet_via_resume_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[tuple[str, str | None, str]] = []
    monkeypatch.setattr(
        "agent_dispatch.bridge.resume_session",
        lambda target, message, **k: record_result(sent, (target, k.get('host'), message), True),
    )
    from agent_dispatch.idle_confirm import default_idle_confirm_nudge

    assert default_idle_confirm_nudge(
        "sess-9", {"id": "abc", "_idle_host": "host-a"}
    )
    assert sent[0][0] == "sess-9"
    assert sent[0][1] == "host-a"
    assert "task abc is still in progress" in sent[0][2]


def test_idle_headless_turn_nudges_confirm_done_instead_of_suspend(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("review turn", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:review-session"
    )
    q.claim_one("headless-owner", task_id=task.id)
    q.start(task.id, "headless-owner")
    stopped: list[str] = []
    nudged: list[tuple[str, str | None]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_activity_fn=lambda _session_id: "IDLE",
        local_cold_fn=lambda session_id: record_result(stopped, session_id, True),
        local_body_verdict_fn=lambda _session_id: "live",
        idle_nudge_fn=lambda target, t: record_result(nudged, (target, t['id']), True),
    )

    assert sup.poll_once() == []
    current = required(q.get(task.id))
    assert current.status == Status.STARTED
    assert current.activity == "IDLE"
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED
    assert stopped == []
    assert nudged == [("review-session", task.id)]
    assert sup.poll_once() == []
    assert nudged == [("review-session", task.id)]


def test_idle_nudge_exempt_label_never_nudges(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A task carrying an idle-nudge-exempt label owns its own resume path (an
    in-process evaluator, or an external one driven entirely through this
    CLI) -- going idle with no new activity is that task's correct resting
    state, not an unfinished turn. Nudging it anyway can encourage the worker
    to reach for a resolution its own charter never sanctioned (confirmed
    live)."""
    task = q.create("review turn", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:review-session"
    )
    q.claim_one("headless-owner", task_id=task.id)
    q.start(task.id, "headless-owner")
    nudged: list[tuple[str, str | None]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        idle_nudge_exempt_labels=["review"],
        local_body_activity_fn=lambda _session_id: "IDLE",
        local_body_verdict_fn=lambda _session_id: "live",
        idle_nudge_fn=lambda target, t: record_result(nudged, (target, t['id']), True),
    )

    assert sup.poll_once() == []
    assert nudged == []
    current = required(q.get(task.id))
    assert current.status == Status.STARTED
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED


def test_supervisor_binds_headless_owner_to_acp_session(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """Binds the durable ACP session id, not agent-bridge's own ephemeral
    escrow handle -- see ``bind_headless_owner_sessions``'s docstring."""
    task = q.create("review turn", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:review-session"
    )
    claimed = q.claim_one("headless-owner", task_id=task.id)
    assert claimed is not None
    q.start(task.id, "headless-owner")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_activity_fn=lambda _session_id: "ACTIVE",
        local_body_verdict_fn=lambda _session_id: "live",
        local_acp_session_fn=lambda _sid: "acp-session-uuid",
    )

    assert sup.bind_headless_owner_sessions() == 1
    assert required(q.get(task.id)).owner_session_id == "acp-session-uuid"
    assert sup.bind_headless_owner_sessions() == 0


def test_supervisor_defers_headless_owner_bind_until_acp_session_known(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """The bridge hasn't reported its ACP session id back yet -- skip rather
    than binding the ephemeral escrow handle as a placeholder."""
    task = q.create("review turn", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:review-session"
    )
    q.claim_one("headless-owner", task_id=task.id)
    q.start(task.id, "headless-owner")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_activity_fn=lambda _session_id: "ACTIVE",
        local_body_verdict_fn=lambda _session_id: "live",
        local_acp_session_fn=lambda _sid: None,
    )

    assert sup.bind_headless_owner_sessions() == 0
    assert required(q.get(task.id)).owner_session_id is None


def test_failed_cold_stop_keeps_live_process_capacity(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:blocked-session"
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.set_card(
        blocked.id,
        "headless-owner",
        card={"request_input": [{"name": "decision", "type": "text"}]},
    )
    runnable = q.create("next review", labels=["review"])
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        labels=["review"],
        max_concurrent=1,
        local_cold_fn=lambda _session_id: False,
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert required(q.get(runnable.id)).status == Status.QUEUED


def test_cold_stop_exception_does_not_abort_cycle(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    blocked = q.create("needs operator", labels=["review"])
    reservation, _ = q.reserve_spawn(blocked.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:blocked-session"
    )
    q.claim_one("headless-owner", task_id=blocked.id)
    q.start(blocked.id, "headless-owner")
    q.suspend(blocked.id, "headless-owner", reason="waiting")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_cold_fn=lambda _session_id: (_ for _ in ()).throw(
            TimeoutError("stop timed out")
        ),
        local_body_verdict_fn=lambda _session_id: "live",
    )

    assert sup.poll_once() == []


def test_supervisor_settles_suspended_task_completed_by_resolver(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("wait for condition")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="session-1",
        worktree="wt-1",
    )
    q.claim_one("host-a/wt-1", task_id=task.id)
    q.start(task.id, "host-a/wt-1")
    q.suspend(task.id, "host-a/wt-1", reason="condition pending")
    q.complete(
        task.id, "host-a/wt-1", result_ref="condition:satisfied"
    )
    sup = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=1
    )

    assert sup.reconcile() == 1
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SETTLED


def test_supervisor_settles_terminal_cold_reservation(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("wait for condition", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="local-body:session-1",
    )
    q.claim_one("headless-owner", task_id=task.id)
    q.start(task.id, "headless-owner")
    q.suspend(task.id, "headless-owner", reason="condition pending")
    q.record_cold(reservation.key)
    q.complete(
        task.id, "headless-owner", result_ref="condition:satisfied"
    )
    ended: list[str] = []

    def end_session(session_id: str) -> bool:
        assert required(q.get_reservation(reservation.key)).state == SpawnState.SETTLED
        ended.append(session_id)
        return True

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "gone",
        local_end_fn=end_session,
    )

    assert sup.reconcile() == 1
    assert ended == ["session-1"]
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SETTLED


def test_terminal_conclusion_uses_exact_reservation_identity(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one(
        "host-a/worktree-exact",
        task_id=task.id,
        machine="host-a",
        worktree="worktree-exact",
    )
    q.start(task.id, "host-a/worktree-exact")
    q.complete(
        task.id,
        "host-a/worktree-exact",
        result_ref="review:complete",
    )
    calls: list[tuple[str | None, ...]] = []

    def conclude(worktree: str, session: str | None) -> dict[str, str]:
        calls.append((worktree, session))
        return {"action": "primed", "reason": "managed-gc-candidate"}

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        disposable_cli_labels=["review"],
        conclusion_fn=conclude,
    )

    assert sup.reconcile() == 1
    assert calls == [("worktree-exact", "session-exact")]
    settled = required(q.get_reservation(reservation.key))
    assert settled.state == SpawnState.SETTLED
    assert "terminal conclusion primed" in (settled.detail or "")
    assert settled.conclusion_state == "complete"


def test_terminal_refresh_does_not_replace_exact_reserved_session(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="original-session",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    calls: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        liveness_fn=lambda *_args: {
            "session_id": "successor-session",
            "worktree_id": "worktree-exact",
        },
        conclusion_fn=lambda *args: record_result(calls, args, {
            "action": "skipped",
            "reason": "session-mismatch",
        }),
    )

    assert sup.reconcile() == 1
    assert calls == [("worktree-exact", "original-session")]
    settled = required(q.get_reservation(reservation.key))
    assert settled.session_handle == "original-session"
    assert settled.conclusion_state == "held"


def test_terminal_refresh_uses_durable_owner_session_for_mux_placeholder(
    q: OwnerResultQueue,
    supervisor_http: DispatchClient,
) -> None:
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="wt-worktree-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(
        task.id,
        "host-a/worktree-exact",
        owner_session_id="owner-session-exact",
    )
    q.complete(task.id, "host-a/worktree-exact")
    calls: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        liveness_fn=lambda *_args: None,
        conclusion_fn=lambda *args: record_result(calls, args, {
            "action": "primed",
            "reason": "managed-gc-candidate",
        }),
    )

    assert sup.reconcile() == 1
    assert calls == [("worktree-exact", "owner-session-exact")]
    settled = required(q.get_reservation(reservation.key))
    assert settled.session_handle == "owner-session-exact"
    assert settled.conclusion_state == "complete"


def test_terminal_refresh_preserves_headless_session_type_before_end(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="wt-worktree-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(
        task.id,
        "headless-worker",
        owner_session_id="owner-session-exact",
    )
    q.complete(task.id, "headless-worker")
    ended: list[str] = []
    concluded: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda worktree, session: record_result(
            concluded, (worktree, session), {"action": "removed", "reason": "managed-gc-removed"}
        ),
        verdict_fn=lambda *_args: tracking.UNKNOWN,
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "acp-session-exact",
        local_end_fn=lambda sid: record_result(ended, sid, True),
    )

    assert sup.reconcile() == 1
    settled = required(q.get_reservation(reservation.key))
    assert settled.session_handle == "local-body:owner-session-exact"
    assert settled.state == SpawnState.SETTLED
    assert settled.conclusion_state == "complete"
    assert ended == ["owner-session-exact"]
    assert concluded == [("worktree-exact", "acp-session-exact")]
    detail = json.loads(settled.conclusion_detail or "{}")
    assert detail["acp_session_id"] == "acp-session-exact"


def test_terminal_conclusion_is_opt_in_only(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("ordinary", labels=["ordinary"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    calls: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        conclusion_fn=lambda *args: record_result(calls, args, {"action": "primed"}),
    )

    assert sup.reconcile() == 1
    assert calls == []


def test_terminal_conclusion_is_terminal_only(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("still working", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    calls: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *args: record_result(calls, args, {"action": "primed"}),
    )

    assert sup.reconcile() == 0
    assert calls == []
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED


def test_terminal_conclusion_failure_does_not_block_settlement(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *_args: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert sup.reconcile() == 1
    settled = required(q.get_reservation(reservation.key))
    assert settled.state == SpawnState.SETTLED
    assert "terminal conclusion failed (boom)" in (settled.detail or "")
    assert settled.conclusion_state == "pending"


def test_terminal_conclusion_reconcile_is_idempotent(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    calls: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *args: record_result(calls, args, {"action": "primed"}),
    )

    assert sup.reconcile() == 1
    assert sup.reconcile() == 0
    assert len(calls) == 1


@pytest.mark.parametrize("action", ["removed", "already-removed"])
def test_removed_conclusion_actions_are_complete(action: str) -> None:
    assert Supervisor._conclusion_state({"action": action}) == "complete"


def test_live_terminal_conclusion_retries_after_settlement(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    outcomes = iter(
        [
            {"action": "skipped", "reason": "live-session"},
            {"action": "primed", "reason": "managed-gc-candidate"},
        ]
    )
    calls: list[tuple[str | None, ...]] = []
    def conclude(worktree: str, session: str | None) -> dict[str, str]:
        calls.append((worktree, session))
        return next(outcomes)

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=conclude,
        liveness_fn=lambda *_args: None,
    )

    assert sup.reconcile() == 1
    pending = required(q.get_reservation(reservation.key))
    assert pending.state == SpawnState.SETTLED
    assert pending.conclusion_state == "pending"
    _, claimed, claim_token = q.claim_spawn_conclusion_retry(reservation.key)
    assert claimed is True
    q.settle_spawn(
        reservation.key,
        conclusion_state="pending",
        conclusion_detail=json.dumps(
            {
                "action": "skipped",
                "reason": "live-session",
                "attempts": 1,
                "next_attempt_at": 0,
            }
        ),
        claim_token=claim_token,
    )
    assert sup.reconcile() == 0
    complete = required(q.get_reservation(reservation.key))
    assert complete.conclusion_state == "complete"
    assert len(calls) == 2


def test_nonexclusive_disposable_idle_body_ends_before_conclusion(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    ended: list[str] = []
    conclusions = []

    def conclude(worktree: str, session: str | None) -> dict[str, str]:
        conclusions.append((worktree, session))
        return {
            "action": "removed",
            "reason": "managed-gc-removed",
        }

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=conclude,
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "acp-session-exact",
        local_end_fn=lambda sid: record_result(ended, sid, True),
    )

    assert sup.reconcile() == 1
    complete = required(q.get_reservation(reservation.key))
    assert complete.state == SpawnState.SETTLED
    assert complete.conclusion_state == "complete"
    assert ended == ["session-exact"]
    assert conclusions == [("worktree-exact", "acp-session-exact")]


def test_idle_disposable_end_failure_keeps_exclusive_fence(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *_args: pytest.fail(
            "conclusion must wait until the body ends"
        ),
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "acp-session-exact",
        local_end_fn=lambda _sid: False,
    )

    assert sup.reconcile() == 0
    still_active = required(q.get_reservation(reservation.key))
    assert still_active.state == SpawnState.SPAWNED
    assert still_active.conclusion_state == "pending"
    detail = json.loads(still_active.conclusion_detail or "{}")
    assert detail["attempts"] == 1
    assert detail["reason"] == "terminal-body-end-failed"
    assert detail["acp_session_id"] == "acp-session-exact"


def test_failed_disposable_body_ends_reach_durable_held_bound(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")

    for attempt in range(12):
        sup = Supervisor(
            supervisor_http,
            spawn_fn=_ok_spawn(),
            repo=TEST_REPO,
            disposable_cli_labels=["review"],
            conclusion_fn=lambda *_args: pytest.fail(
                "conclusion must wait until the body ends"
            ),
            local_body_verdict_fn=lambda _sid: tracking.LIVE,
            local_body_activity_fn=lambda _sid: "IDLE",
            local_acp_session_fn=lambda _sid: "acp-session-exact",
            local_end_fn=lambda _sid: False,
        )
        assert sup.reconcile() == 0
        current = required(q.get_reservation(reservation.key))
        assert current.state == SpawnState.SPAWNED
        detail = json.loads(current.conclusion_detail or "{}")
        assert detail["attempts"] == attempt + 1
        if attempt < 11:
            q.record_spawn_conclusion(
                reservation.key,
                conclusion_state="pending",
                conclusion_detail=json.dumps(
                    {
                        **detail,
                        "next_attempt_at": 0,
                    }
                ),
            )

    held = required(q.get_reservation(reservation.key))
    assert held.conclusion_state == "held"


def test_successful_disposable_body_end_runs_conclusion(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    ended: list[str] = []
    concluded: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda worktree, session: record_result(
            concluded, (worktree, session), {"action": "removed", "reason": "managed-gc-removed"}
        ),
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "acp-session-exact",
        local_end_fn=lambda sid: record_result(ended, sid, True),
    )

    assert sup.reconcile() == 1
    assert ended == ["session-exact"]
    assert concluded == [("worktree-exact", "acp-session-exact")]
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SETTLED


def test_end_failure_counts_after_legacy_pending_attempts(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.record_spawn_conclusion(
        reservation.key,
        conclusion_state="pending",
        conclusion_detail=json.dumps(
            {
                "action": "failed",
                "reason": "session-identity-unavailable",
                "session": "acp-session-exact",
                "attempts": 3,
                "next_attempt_at": 0,
            }
        ),
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *_args: pytest.fail(
            "conclusion must wait until the body ends"
        ),
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: pytest.fail(
            "legacy checkpoint must avoid a fresh identity lookup"
        ),
        local_end_fn=lambda _sid: False,
    )

    assert sup.reconcile() == 0
    detail = json.loads(
        required(q.get_reservation(reservation.key)).conclusion_detail or "{}"
    )
    assert detail["attempts"] == 4
    assert detail["reason"] == "terminal-body-end-failed"
    assert detail["acp_session_id"] == "acp-session-exact"


def test_nonexclusive_disposable_running_body_is_preserved(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *_args: pytest.fail(
            "running body must not reach conclusion"
        ),
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "RUNNING",
        local_acp_session_fn=lambda _sid: "acp-session-exact",
        local_end_fn=lambda _sid: pytest.fail("running body must not end"),
    )

    assert sup.reconcile() == 0
    current = required(q.get_reservation(reservation.key))
    assert current.state == SpawnState.SPAWNED
    assert current.conclusion_state is None


def test_pending_headless_conclusion_does_not_end_running_session(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")
    q.settle_spawn(
        reservation.key,
        conclusion_state="pending",
        conclusion_detail=json.dumps(
            {
                "action": "skipped",
                "reason": "live-session",
                "attempts": 1,
                "next_attempt_at": 0,
                "same_owner_nudge": "delivered",
            }
        ),
    )
    ended: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_body_activity_fn=lambda _sid: "RUNNING",
        local_end_fn=lambda sid: record_result(ended, sid, True),
    )

    assert sup.reconcile() == 0
    assert ended == []
    assert required(q.get_reservation(reservation.key)).conclusion_state == "pending"


def test_nonidle_headless_conclusion_preserves_without_retry_budget(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("headless-worker", task_id=task.id)
    q.start(task.id, "headless-worker", owner_session_id="session-exact")
    q.complete(task.id, "headless-worker")

    for _attempt in range(3):
        sup = Supervisor(
            supervisor_http,
            spawn_fn=_ok_spawn(),
            repo=TEST_REPO,
            disposable_cli_labels=["review"],
            local_body_verdict_fn=lambda _sid: tracking.LIVE,
            local_body_activity_fn=lambda _sid: "RUNNING",
        )
        assert sup.reconcile() == 0
        current = required(q.get_reservation(reservation.key))
        assert current.state == SpawnState.SPAWNED
        assert current.conclusion_state is None
        assert current.conclusion_detail is None


def test_terminal_conclusion_retries_are_bounded(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("host-a/worktree-exact", task_id=task.id)
    q.start(task.id, "host-a/worktree-exact")
    q.complete(task.id, "host-a/worktree-exact")
    calls: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *args: record_result(calls, args, {
            "action": "skipped",
            "reason": "live-session",
        }),
        liveness_fn=lambda *_args: None,
    )

    assert sup.reconcile() == 1
    for attempt in range(1, 12):
        _, claimed, claim_token = q.claim_spawn_conclusion_retry(
            reservation.key
        )
        assert claimed is True
        q.settle_spawn(
            reservation.key,
            conclusion_state="pending",
            conclusion_detail=json.dumps(
                {
                    "action": "skipped",
                    "reason": "live-session",
                    "attempts": attempt,
                    "next_attempt_at": 0,
                }
            ),
            claim_token=claim_token,
        )
        assert sup.reconcile() == 0

    held = required(q.get_reservation(reservation.key))
    assert held.conclusion_state == "held"
    assert len(calls) == 12


def test_retired_cleanup_exhaustion_transitions_to_held_with_claim(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="session-exact",
        worktree="worktree-exact",
    )
    q.claim_one("worker", task_id=task.id)
    q.start(task.id, "worker")
    q.complete(task.id, "worker", result_ref="result/1")
    q.settle_spawn(
        reservation.key,
        conclusion_state="pending",
        conclusion_detail=json.dumps(
            {
                "action": "failed",
                "reason": "live-session",
                "attempts": _CONCLUSION_MAX_ATTEMPTS,
                "next_attempt_at": 0,
            }
        ),
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *_args: pytest.fail(
            "exhausted cleanup must not run another attempt"
        ),
        nudge=False,
    )

    assert sup.reconcile() == 0
    held = required(q.get_reservation(reservation.key))
    assert held.state == SpawnState.SETTLED
    assert held.conclusion_state == "held"
    assert held.cleanup_claim_token
    assert held.cleanup_claim_expires_at == 0


def test_requeued_task_is_not_double_spawned(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A spawned-but-requeued task (lease expired, embody maybe still alive)
    must never be spawned a second time."""
    t = q.create("work")
    spawn = _ok_spawn({"session": "local-body:sess-1", "worktree": "wt-1"})
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        local_body_verdict_fn=lambda _sid: "live",
    )
    sup.poll_once()  # spawn #1

    # Historical GC left a queued task behind this still-spawned body.
    q.claim_one("m/wt", task_id=t.id, machine="m", worktree="wt")
    q.start(t.id, "m/wt")
    before_retention_gc(q, t.id)
    assert required(q.get(t.id)).status == Status.QUEUED  # back in the queue

    # the supervisor must NOT re-spawn it (reservation still 'spawned')
    assert sup.poll_once() == []
    assert spawn.calls == [t.id]  # still just the one spawn


def test_reconcile_settles_terminal_then_allows_respawn(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()

    # embody works the task to completion
    q.claim_one("m/wt", task_id=t.id, machine="m", worktree="wt")
    q.start(t.id, "m/wt")
    q.complete(t.id, "m/wt")

    settled = sup.reconcile()
    assert settled == 1
    assert required(q.latest_reservation(t.id)).state == SpawnState.SETTLED


def test_reconcile_ends_terminal_local_body_before_settling(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-terminal")
    ended: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: "live",
        local_end_fn=lambda sid: record_result(ended, sid, True),
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")
    q.complete(t.id, "local-o")

    assert sup.reconcile() == 1
    assert ended == ["brg-terminal"]
    assert required(q.latest_reservation(t.id)).state == SpawnState.SETTLED


@pytest.mark.parametrize(
    ("outcome", "conclusion_state"),
    [
        ({"action": "removed", "reason": "managed-gc-removed"}, "complete"),
        ({"action": "skipped", "reason": "not-cli-worktree"}, "held"),
    ],
)
def test_disposable_terminal_local_body_ends_before_conclusion(
    q: OwnerResultQueue,
    supervisor_http: DispatchClient,
    outcome: dict[str, str],
    conclusion_state: str,
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    spawn = _ok_spawn(
        {
            "session": "local-body:brg-disposable",
            "worktree": "worktree-disposable",
        }
    )
    ended: list[str] = []
    concluded: list[tuple[str, str | None]] = []

    def end_body(sid: str) -> bool:
        checkpoint = required(q.get_reservation(required(q.latest_reservation(task.id)).key))
        detail = json.loads(required(checkpoint.conclusion_detail))
        assert checkpoint.conclusion_state == "pending"
        assert detail["acp_session_id"] == "session-exact"
        ended.append(sid)
        return True

    def conclude(worktree: str, session: str | None) -> dict[str, str]:
        assert ended == ["brg-disposable"]
        concluded.append((worktree, session))
        return outcome

    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        disposable_cli_labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "session-exact",
        local_end_fn=end_body,
        conclusion_fn=conclude,
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=task.id)
    q.start(task.id, "local-o", owner_session_id="session-exact")
    q.complete(task.id, "local-o")

    assert sup.reconcile() == 1
    assert ended == ["brg-disposable"]
    assert concluded == [("worktree-disposable", "session-exact")]
    reservation = required(q.latest_reservation(task.id))
    assert reservation.state == SpawnState.SETTLED
    assert reservation.conclusion_state == conclusion_state
    assert json.loads(required(reservation.conclusion_detail))["acp_session_id"] == (
        "session-exact"
    )
    assert sup.reconcile() == 0
    assert ended == ["brg-disposable"]
    assert concluded == [("worktree-disposable", "session-exact")]


def test_stopped_disposable_local_body_is_ended_before_conclusion(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    spawn = _ok_spawn(
        {
            "session": "local-body:brg-stopped",
            "worktree": "worktree-disposable",
        }
    )
    ended: list[str] = []
    concluded: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        local_body_verdict_fn=lambda _sid: tracking.GONE,
        local_acp_session_fn=lambda _sid: "session-exact",
        local_end_fn=lambda sid: record_result(ended, sid, True),
        conclusion_fn=lambda worktree, session: record_result(
            concluded, (worktree, session), {"action": "removed", "reason": "managed-gc-removed"}
        ),
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=task.id)
    q.start(task.id, "local-o", owner_session_id="session-exact")
    q.complete(task.id, "local-o")

    assert sup.reconcile() == 1
    assert ended == ["brg-stopped"]
    assert concluded == [("worktree-disposable", "session-exact")]
    assert required(q.latest_reservation(task.id)).conclusion_state == "complete"


def test_disposable_terminal_retry_keeps_acp_identity_after_end(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    spawn = _ok_spawn(
        {
            "session": "local-body:brg-disposable",
            "worktree": "worktree-disposable",
        }
    )
    outcomes: Iterator[RuntimeError | dict[str, str]] = iter(
        [
            RuntimeError("transient"),
            {"action": "removed", "reason": "managed-gc-removed"},
        ]
    )
    sessions_seen: list[str | None] = []

    def conclude(_worktree: str, session: str | None) -> dict[str, str]:
        sessions_seen.append(session)
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        disposable_cli_labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "session-exact",
        local_end_fn=lambda _sid: True,
        conclusion_fn=conclude,
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=task.id)
    q.start(task.id, "local-o", owner_session_id="session-exact")
    q.complete(task.id, "local-o")

    assert sup.reconcile() == 1
    pending = required(q.latest_reservation(task.id))
    assert pending.conclusion_state == "pending"
    assert json.loads(required(pending.conclusion_detail))["acp_session_id"] == (
        "session-exact"
    )
    _, claimed, claim_token = q.claim_spawn_conclusion_retry(pending.key)
    assert claimed is True
    q.settle_spawn(
        pending.key,
        detail=pending.detail,
        conclusion_state="pending",
        conclusion_detail=json.dumps(
            {
                **json.loads(required(pending.conclusion_detail)),
                "next_attempt_at": 0,
            }
        ),
        claim_token=claim_token,
    )

    assert sup.reconcile() == 0
    assert sessions_seen == ["session-exact", "session-exact"]
    assert required(q.latest_reservation(task.id)).conclusion_state == "complete"


def test_gone_terminal_retry_preserves_terminal_cleanup_envelope(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:brg-disposable",
        worktree="worktree-disposable",
    )
    q.claim_one("local-o", task_id=task.id)
    q.start(task.id, "local-o", owner_session_id="session-exact")
    q.complete(task.id, "local-o")
    outcomes: Iterator[RuntimeError | dict[str, str]] = iter(
        [
            RuntimeError("transient"),
            {"action": "removed", "reason": "managed-gc-removed"},
        ]
    )
    sessions_seen: list[str | None] = []
    ended: list[str] = []

    def conclude(_worktree: str, session: str | None) -> dict[str, str]:
        sessions_seen.append(session)
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        local_body_verdict_fn=lambda _sid: tracking.GONE,
        local_acp_session_fn=lambda _sid: "session-exact",
        local_end_fn=lambda sid: record_result(ended, sid, True),
        conclusion_fn=conclude,
        nudge=False,
    )

    assert sup.reconcile() == 1
    pending = required(q.get_reservation(reservation.key))
    envelope = json.loads(pending.conclusion_detail or "{}")
    assert pending.state == SpawnState.SETTLED
    assert pending.conclusion_state == "pending"
    assert envelope["cleanup_kind"] == "terminal"
    assert envelope["acp_session_id"] == "session-exact"
    assert envelope["session_end"]["state"] == "complete"
    assert envelope["worktree_cleanup"]["state"] == "pending"
    assert ended == ["brg-disposable"]
    assert sessions_seen == ["session-exact"]

    worktree_next_attempt = envelope["worktree_cleanup"]["next_attempt_at"]
    assert sup.release_requested_bodies(
        now=worktree_next_attempt - 0.1
    ) == 0
    assert sessions_seen == ["session-exact"]
    assert sup.release_requested_bodies(
        now=worktree_next_attempt
    ) == 0
    complete = required(q.get_reservation(reservation.key))
    final_envelope = json.loads(complete.conclusion_detail or "{}")
    assert complete.conclusion_state == "complete"
    assert final_envelope["cleanup_kind"] == "terminal"
    assert final_envelope["acp_session_id"] == "session-exact"
    assert final_envelope["session_end"]["state"] == "complete"
    assert final_envelope["worktree_cleanup"]["state"] == "complete"
    assert sessions_seen == ["session-exact", "session-exact"]


def test_disposable_terminal_end_failure_is_bounded_and_retryable(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    spawn = _ok_spawn(
        {
            "session": "local-body:brg-disposable",
            "worktree": "worktree-disposable",
        }
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        disposable_cli_labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: "session-exact",
        local_end_fn=lambda _sid: False,
        conclusion_fn=lambda *_args: pytest.fail(
            "conclusion must wait until the body ends"
        ),
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=task.id)
    q.start(task.id, "local-o", owner_session_id="session-exact")
    q.complete(task.id, "local-o")

    assert sup.reconcile() == 0
    pending = required(q.latest_reservation(task.id))
    assert pending.state == SpawnState.SPAWNED
    assert pending.conclusion_state == "pending"
    detail = json.loads(required(pending.conclusion_detail))
    assert detail["reason"] == "terminal-body-end-failed"
    assert detail["acp_session_id"] == "session-exact"
    assert detail["attempts"] == 1
    assert detail["next_attempt_at"] > 0


def test_disposable_terminal_missing_acp_identity_is_bounded(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    spawn = _ok_spawn(
        {
            "session": "local-body:brg-disposable",
            "worktree": "worktree-disposable",
        }
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        disposable_cli_labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: None,
        local_end_fn=lambda _sid: pytest.fail(
            "body must not end without a durable ACP identity"
        ),
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=task.id)
    q.start(task.id, "local-o")
    q.complete(task.id, "local-o")

    assert sup.reconcile() == 0
    pending = required(q.latest_reservation(task.id))
    assert pending.state == SpawnState.SPAWNED
    assert pending.conclusion_state == "pending"
    detail = json.loads(required(pending.conclusion_detail))
    assert detail["reason"] == "session-identity-unavailable"
    assert detail["attempts"] == 1
    assert detail["next_attempt_at"] > 0


def test_disposable_terminal_acp_identity_error_is_bounded(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create(
        "review",
        labels=["review"],
        exclusive_key="review:repo:42",
    )
    spawn = _ok_spawn(
        {
            "session": "local-body:brg-disposable",
            "worktree": "worktree-disposable",
        }
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        disposable_cli_labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
        local_body_activity_fn=lambda _sid: "IDLE",
        local_acp_session_fn=lambda _sid: (_ for _ in ()).throw(
            RuntimeError("bridge unavailable")
        ),
        local_end_fn=lambda _sid: pytest.fail(
            "body must not end without a durable ACP identity"
        ),
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=task.id)
    q.start(task.id, "local-o")
    q.complete(task.id, "local-o")

    assert sup.reconcile() == 0
    pending = required(q.latest_reservation(task.id))
    assert pending.state == SpawnState.SPAWNED
    assert pending.conclusion_state == "pending"
    detail = json.loads(required(pending.conclusion_detail))
    assert detail["reason"] == "session-identity-unavailable"
    assert detail["attempts"] == 1
    assert detail["next_attempt_at"] > 0


def test_reconcile_retains_terminal_local_reservation_when_end_fails(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-terminal")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        local_body_verdict_fn=lambda _sid: "live",
        local_end_fn=lambda _sid: False,
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")
    q.complete(t.id, "local-o")

    assert sup.reconcile() == 0
    assert required(q.latest_reservation(t.id)).state == SpawnState.SPAWNED


def test_reconcile_ends_cold_terminal_local_body_before_settling(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-cold")
    ended: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        local_body_verdict_fn=lambda _sid: "gone",
        local_end_fn=lambda sid: record_result(ended, sid, True),
    )
    sup.poll_once()
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")
    q.suspend(t.id, "local-o", reason="turn ended")
    q.record_cold(required(q.latest_reservation(t.id)).key)
    q.complete(t.id, "local-o")

    assert sup.reconcile() == 1
    assert ended == ["brg-cold"]
    assert required(q.latest_reservation(t.id)).state == SpawnState.SETTLED


def test_spawn_failure_fails_reservation_and_retries(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = q.create("work")
    attempts = []

    def flaky(task: dict[str, object]) -> tuple[bool, dict[str, object]]:
        attempts.append(1)
        if len(attempts) == 1:
            return False, {"error": "boom"}
        return True, {"session": "s", "worktree": "w"}

    sup = Supervisor(supervisor_http, spawn_fn=flaky, repo=TEST_REPO, max_concurrent=5)
    assert sup.poll_once() == []  # first spawn fails
    assert required(q.latest_reservation(t.id)).state == SpawnState.FAILED

    # next cycle reserves a fresh attempt and succeeds
    assert sup.poll_once() == [t.id]
    assert required(q.latest_reservation(t.id)).attempt == 2
    assert required(q.latest_reservation(t.id)).state == SpawnState.SPAWNED


# -- policy: cap / labels / deferral -----------------------------------------


def test_max_concurrent_caps_spawns(q: OwnerResultQueue, supervisor_http: DispatchClient) -> None:
    a = q.create("a")
    b = q.create("b")
    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=1)

    spawned = sup.poll_once()
    assert len(spawned) == 1  # only one, despite two eligible
    assert {a.id, b.id} & set(spawned)  # spawned one of them


def test_label_opt_in(q: OwnerResultQueue, supervisor_http: DispatchClient) -> None:
    marked = q.create("marked", labels=["nightly-sweep"])
    q.create("unmarked")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, labels=["nightly-sweep"], max_concurrent=5
    )

    spawned = sup.poll_once()
    assert spawned == [marked.id]


def test_not_before_deferral(q: OwnerResultQueue, supervisor_http: DispatchClient) -> None:
    future = q.create("later", not_before=9_999_999_999.0)
    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)

    assert sup.poll_once() == []  # not due yet
    assert q.latest_reservation(future.id) is None


def test_dead_letter_after_max_attempts(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A task that keeps failing to spawn is dead-lettered (not retried forever)."""
    t = q.create("work")

    def always_fail(task: dict[str, object]) -> tuple[bool, dict[str, object]]:
        return False, {"error": "boom"}

    sup = Supervisor(
        supervisor_http, spawn_fn=always_fail, repo=TEST_REPO, max_concurrent=5, max_attempts=3
    )
    # three cycles each burn one attempt (fail_spawn), then it's dead-lettered
    for _ in range(3):
        assert sup.poll_once() == []
    assert len(q.list_reservations(task_id=t.id, state="failed")) == 3

    # a fourth cycle must NOT reserve a 4th attempt
    assert sup.poll_once() == []
    assert required(q.latest_reservation(t.id)).attempt == 3  # still only 3 attempts made


def test_deferred_spawn_never_dead_letters(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A carried session that is confirmed live/busy (not gone) declines the
    spawn -- that's a legitimate deferral, not a failure. Unlike an ordinary
    spawn failure, it must never count toward dead-lettering (#2056): the same
    exclusive-key task should be able to re-check liveness indefinitely while
    its predecessor is genuinely still working, without ever being starved by
    a bound meant for real failures."""
    t = q.create("work")

    def always_busy(task: dict[str, object]) -> tuple[bool, dict[str, object]]:
        return False, {"error": "carried session remains live", "deferred": True}

    sup = Supervisor(
        supervisor_http, spawn_fn=always_busy, repo=TEST_REPO, max_concurrent=5, max_attempts=3
    )
    # Many more cycles than the failure bound: none of them may dead-letter,
    # because none of them actually failed.
    for _ in range(10):
        assert sup.poll_once() == []
    assert len(q.list_reservations(task_id=t.id, state="failed")) == 0
    assert len(q.list_reservations(task_id=t.id, state="deferred")) == 10
    # A fresh attempt is still reserved every cycle (never dead-lettered).
    assert required(q.latest_reservation(t.id)).attempt == 10


def test_bridge_failure_during_spawn_preparation_releases_reservation(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=1,
    )

    def fail_preparation(_task: object, _reservation: object) -> None:
        raise bridge.BridgeUnavailable("registry unavailable")

    monkeypatch.setattr(sup, "_prepare_spawn_task", fail_preparation)

    assert sup.poll_once() == []
    reservation = required(q.latest_reservation(task.id))
    assert reservation.state == SpawnState.FAILED
    assert reservation.detail == (
        "reusable worktree preparation failed: registry unavailable"
    )
    assert spawn.calls == []


def test_dead_letter_summary_is_compact_and_only_repeats_on_change(
    q: OwnerResultQueue, supervisor_http: DispatchClient, caplog: pytest.LogCaptureFixture
) -> None:
    first = q.create("first")
    second = q.create("second")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=lambda _task: (False, {"error": "boom"}),
        repo=TEST_REPO,
        max_concurrent=5,
        max_attempts=1,
    )
    sup.poll_once()
    caplog.clear()

    sup.poll_once()
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    summaries = [m for m in warnings if "spawn-dead-lettered task(s)" in m]
    assert len(summaries) == 1
    assert first.id in summaries[0]
    assert second.id in summaries[0]
    assert "reservations rearm <task> --permit" in summaries[0]

    caplog.clear()
    sup.poll_once()
    assert not [
        r for r in caplog.records if "spawn-dead-lettered task(s)" in r.message
    ]

    third = q.create("third")
    sup.poll_once()
    caplog.clear()
    sup.poll_once()
    summaries = [
        r.message
        for r in caplog.records
        if "spawn-dead-lettered task(s)" in r.message
    ]
    assert len(summaries) == 1
    assert third.id in summaries[0]


def test_max_attempts_zero_retries_forever(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = q.create("work")
    sup = Supervisor(
        supervisor_http, spawn_fn=lambda _t: (False, {"error": "x"}),
        repo=TEST_REPO, max_concurrent=5, max_attempts=0,
    )
    for _ in range(5):
        sup.poll_once()
    assert required(q.latest_reservation(t.id)).attempt == 5  # unbounded retries


def test_label_max_attempts_raises_one_labels_bound(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A per-label override raises one label's dead-letter bound above the
    global default (#3492) -- so its tasks retry longer, independently."""
    t = q.create("work", labels=["code-review"])
    sup = Supervisor(
        supervisor_http, spawn_fn=lambda _t: (False, {"error": "x"}),
        repo=TEST_REPO, max_concurrent=5, max_attempts=1,
        label_max_attempts={"code-review": 3},
    )
    # Global bound is 1, but the label override is 3: it retries up to 3.
    for _ in range(3):
        assert sup.poll_once() == []
    assert len(q.list_reservations(task_id=t.id, state="failed")) == 3
    # A fourth cycle must NOT reserve a 4th attempt -- dead-lettered at 3.
    assert sup.poll_once() == []
    assert required(q.latest_reservation(t.id)).attempt == 3


def test_label_max_attempts_leaves_other_labels_on_global_bound(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A label with no override still uses the global bound -- reviving one
    label's tasks does not revive another's (the decoupling #3492 exists for)."""
    t = q.create("work", labels=["nightly-scan"])
    sup = Supervisor(
        supervisor_http, spawn_fn=lambda _t: (False, {"error": "x"}),
        repo=TEST_REPO, max_concurrent=5, max_attempts=1,
        label_max_attempts={"code-review": 5},
    )
    assert sup.poll_once() == []  # 1 attempt burned
    assert sup.poll_once() == []  # dead-lettered at the global bound of 1
    assert required(q.latest_reservation(t.id)).attempt == 1


# -- liveness-gated heartbeat ------------------------------------------------


def _leased_task_with_spawn(q: OwnerResultQueue) -> Task:
    """A started task with a recorded ``spawned`` reservation (owner m/wt)."""
    t = q.create("work")
    r, _ = q.reserve_spawn(t.id)
    q.record_spawn(r.key, session_handle="sess", worktree="wt")
    q.claim_one("m/wt", task_id=t.id, machine="m", worktree="wt")
    q.start(t.id, "m/wt")
    return t


def test_heartbeat_holds_confirmed_live_lease(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = _leased_task_with_spawn(q)
    probes: list[tuple[str, str | None]] = []

    def alive(worktree: str, machine: str | None) -> dict[str, str]:
        probes.append((worktree, machine))
        return {"liveness": "alive"}

    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, liveness_fn=alive)
    # push the lease into the past so the heartbeat visibly extends it
    before = required(q.get(t.id)).lease_expires_at
    held = sup.hold_live_leases()
    assert held == 1
    assert probes == [("wt", "m")]
    assert required(required(q.get(t.id)).lease_expires_at) >= required(before)


def test_heartbeat_skips_when_not_confirmed_alive(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = _leased_task_with_spawn(q)
    lease_before = required(q.get(t.id)).lease_expires_at

    sup = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, liveness_fn=lambda w, m: None
    )
    assert sup.hold_live_leases() == 0
    # a None probe must never be treated as alive -> no heartbeat written
    assert required(q.get(t.id)).lease_expires_at == lease_before


def test_heartbeat_disabled_skips_liveness(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    _leased_task_with_spawn(q)
    probes: list[int] = []
    sup = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, heartbeat=False,
        liveness_fn=lambda w, m: record_result(probes, 1, {"liveness": "alive"}),
    )
    sup.poll_once()
    assert probes == []  # heartbeat disabled -> liveness never probed


# -- CLI wiring --------------------------------------------------------------


def test_cli_supervise_once(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_api: TestClient
) -> None:

    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod

    t = q.create("work")
    spawn = _ok_spawn()
    monkeypatch.setattr(m, "_client", lambda _args, **_kw: connected(supervisor_api, []))
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)
    monkeypatch.setattr(sup_mod, "make_embody_spawn", lambda **_kw: spawn)

    args = argparse.Namespace(
        all_repos=False, repo=None, url=None, token=None, label=None,
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        embody_backend="cli", cli_label=None, headless_label=None,
    )
    assert _cmd_supervise(args) == 0
    assert spawn.calls == [t.id]
    assert required(q.latest_reservation(t.id)).state == SpawnState.SPAWNED


def test_cli_supervise_all_repos_marks_spawn_claim_as_administrative(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_api: TestClient
) -> None:

    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod

    q.create("work")
    spawn = _ok_spawn()
    seen: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(m, "_client", lambda _args, **_kw: connected(supervisor_api, []))
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(
        m,
        "_scope_repo",
        lambda _args: (_ for _ in ()).throw(AssertionError("must not resolve repo")),
    )
    monkeypatch.setattr(
        sup_mod,
        "make_embody_spawn",
        lambda **kwargs: record_result(seen, ("cli", kwargs), spawn),
    )
    monkeypatch.setattr(
        sup_mod,
        "make_headless_spawn",
        lambda **kwargs: record_result(seen, ("headless", kwargs), spawn),
    )
    args = argparse.Namespace(
        all_repos=True,
        repo=None,
        url=None,
        token=None,
        label=None,
        max_concurrent=5,
        verify_timeout=0,
        once=True,
        interval=30.0,
        no_heartbeat=False,
        max_attempts=3,
        embody_backend=None,
        cli_label=None,
        headless_label=None,
        headless_agent="task-worker",
    )

    assert _cmd_supervise(args) == 0
    assert {kind for kind, _kwargs in seen} == {"cli", "headless"}
    assert all(kwargs["all_repos"] is True for _kind, kwargs in seen)


def test_cli_supervise_refuses_unresolved_repo_without_all_repos(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    from agent_dispatch import __main__ as m

    monkeypatch.setattr(m, "_scope_repo", lambda _args: None)
    args = argparse.Namespace(all_repos=False)

    assert _cmd_supervise(args) == 2
    assert "could not resolve the calling repo" in capsys.readouterr().err


def test_make_embody_spawn_records_handle_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    """The CLI embody backend returns success + a parsed session/worktree handle
    when embody exits 0 (regression: it previously fell through to None, breaking
    record_spawn on the happy path)."""
    import subprocess

    from agent_dispatch import session_acquisition
    from agent_dispatch.spawn_factories import make_embody_spawn

    def fake_spawn_embodied_worker(
        task: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({
                "worktree_id": "wt-9", "session_id": "sess-9",
                "native_session_id": "sess-9", "mode": "cli",
            }),
            stderr="",
        )

    monkeypatch.setattr(session_acquisition, "acquire", fake_spawn_embodied_worker)
    ok, handle = make_embody_spawn()(
        {"id": "t", "repo": "gitea.example/org/widgets"}
    )
    assert ok is True
    assert handle["worktree"] == "wt-9"
    assert handle["session"] == "sess-9"


def test_make_embody_spawn_fails_when_a_zero_exit_yields_no_session_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A zero exit with no recognizable session id (e.g. an embody JSON shape
    # this parser doesn't recognize) must not be reported as a usable
    # success: record_spawn would otherwise persist a SPAWNED reservation
    # with session_handle=None -- indistinguishable from a reservation that
    # never reached spawning anything at all, which release_requested_bodies'
    # absent-worktree shortcut could then wrongly treat as confirmed-gone
    # without ever resolving whether a real, unidentifiable body exists.
    import subprocess

    from agent_dispatch import embody
    from agent_dispatch.spawn_factories import make_embody_spawn

    def fake_spawn_embodied_worker(
        task_id: str, **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="{}", stderr="",
        )

    monkeypatch.setattr(embody, "spawn_embodied_worker", fake_spawn_embodied_worker)
    ok, handle = make_embody_spawn()(
        {"id": "t", "repo": "gitea.example/org/widgets"}
    )
    assert ok is False
    assert "error" in handle


def test_parse_handle_accepts_nested_worktree_object() -> None:
    """Older/newer agent-worktrees JSON shapes both preserve the worktree handle."""
    import subprocess

    from agent_dispatch import embody

    result = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout='{"worktree": {"id": "wt-nested"}, "session": "sess-nested"}',
        stderr="",
    )

    assert embody.parse_handle(result) == {
        "worktree": "wt-nested",
        "session": "sess-nested",
    }


def test_resolving_client_uses_fresh_client_for_each_operation() -> None:
    from agent_dispatch.client import ResolvingDispatchClient

    created: list[int] = []

    def factory() -> DispatchClient:
        generation = len(created) + 1
        created.append(generation)

        def get(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            return httpx.Response(200, json={"generation": generation,
                                             "task_id": request.url.path.removeprefix("/tasks/")})

        return DispatchClient("http://testserver", transport=httpx.MockTransport(get))

    client = ResolvingDispatchClient(factory)

    assert client.get("a") == {"generation": 1, "task_id": "a"}
    assert client.get("b") == {"generation": 2, "task_id": "b"}
    assert created == [1, 2]


def test_resolving_client_rejects_unknown_attribute() -> None:
    from agent_dispatch.client import ResolvingDispatchClient

    def unopened() -> NoReturn:
        pytest.fail("attribute inspection must not open a client")

    client = ResolvingDispatchClient(unopened)
    # Only real DispatchClient methods proxy; a typo/unknown name is an honest
    # AttributeError (hasattr stays truthful) rather than a silent callable.
    assert hasattr(client, "reserve_spawn")
    assert not hasattr(client, "definitely_not_a_method")
    with pytest.raises(AttributeError):
        _ = client.definitely_not_a_method


def test_dispatch_client_skips_tls_setup_only_for_plain_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import client_transport as client_module
    from agent_dispatch.client import DispatchClient

    created: list[dict[str, object]] = []

    class FakeHttpClient:
        def __init__(self, **kwargs: object) -> None:
            created.append(kwargs)

        def close(self) -> None:
            pass

    monkeypatch.setattr(client_module, "ConnectRetryClient", FakeHttpClient)

    DispatchClient("http://127.0.0.1:9847").close()
    DispatchClient("https://dispatch.example.com").close()

    assert created[0]["verify"] is False
    assert created[1]["verify"] is True


# -- headless-ACP embody backend ---------------------------------------------


def _headless_task(task_id: str, **fields: object) -> dict[str, object]:
    return {
        "id": task_id,
        "spawn_reservation_key": "reservation",
        "_spawn_client": SimpleNamespace(get_reservation=lambda key: {"key": key}),
        **fields,
    }


def test_make_headless_spawn_uses_bridge_with_autopilot_seed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headless backend embodies via agent-bridge, delivering the SAME
    autopilot seed the CLI backend uses (parity: identical driving, different
    body) -- and records no worktree handle (a headless body is not a worktree)."""
    from agent_dispatch import embody, ordinary_bridge
    from agent_dispatch.spawn_factories import make_headless_spawn

    calls: dict[str, object] = {}

    def fake_spawn_worker(
        task: dict[str, object], *, agent: str, prompt: str, **_kw: object
    ) -> dict[str, str]:
        calls.update(
            task_id=task["id"], agent=agent, prompt=prompt,
        )
        return {"mode": "acp", "session": "local-body:bridge-1",
                "native_session_id": "native-1", "worktree": "wt-1"}

    monkeypatch.setattr(ordinary_bridge, "start", fake_spawn_worker)
    monkeypatch.setattr(
        embody, "autopilot_worker_prompt",
        lambda task_id, *, worker_id, route="", repo=None, all_repos=False,
        explicit_worker_identity=False: (
            f"SEED::{task_id}"
        ),
    )

    spawn = make_headless_spawn(agent="review-worker")
    ok, handle = spawn(_headless_task("task-1"))

    assert ok is True
    assert handle["worktree"] == "wt-1"
    assert calls["agent"] == "review-worker"
    assert calls["prompt"] == "SEED::task-1"  # the CLI autopilot seed, verbatim


@pytest.mark.parametrize("route,expected_route", [
    (" --shared", "shared"), (" --local", "strict-local"), ("", "local"),
])
def test_make_script_spawn_launches_runtime_python_with_task_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    route: str,
    expected_route: str,
) -> None:
    from agent_dispatch import spawn_script_body
    from agent_dispatch.spawn_script_body import make_script_spawn

    script_path = tmp_path / "worker.py"
    script_path.write_text("print('ok')\n", encoding="utf-8")

    started: list[tuple[list[str], dict[str, str]]] = []

    class FakeProcess:
        pid = 4321

    def popen(argv: list[str], *, env: dict[str, str], **_: object) -> FakeProcess:
        started.append((argv, env))
        return FakeProcess()

    monkeypatch.setattr(
        spawn_script_body, "subprocess", SimpleNamespace(DEVNULL=None, Popen=popen)
    )
    monkeypatch.setattr(
        spawn_script_body,
        "_process_tree_kwargs",
        lambda: {"creationflags": 0},
        raising=False,
    )
    monkeypatch.setattr(
        spawn_script_body,
        "Path",
        Path,
    )
    monkeypatch.setattr(
        "agent_dispatch.procutil.resolve_own_runtime_python",
        lambda: "C:\\runtime\\python.exe",
    )
    monkeypatch.setattr(
        "agent_dispatch.companion.process_start_token",
        lambda pid: f"token-{pid}",
    )

    spawn = make_script_spawn(route=route, all_repos=False)
    ok, handle = spawn(
        {
            "id": "task-1",
            "repo": TEST_REPO,
            "payload_inline": json.dumps({"path": str(script_path)}),
        }
    )

    assert ok is True
    [(argv, env)] = started
    assert argv == ["C:\\runtime\\python.exe", str(script_path)]
    assert env["AGENT_DISPATCH_SCRIPT_TASK_ID"] == "task-1"
    assert env["AGENT_DISPATCH_SCRIPT_WORKER_ID"].startswith("script-")
    assert env["AGENT_DISPATCH_SCRIPT_ROUTE"] == expected_route
    assert env["AGENT_DISPATCH_SCRIPT_REPO"] == TEST_REPO
    assert Path(env["AGENT_DISPATCH_SCRIPT_TASK_FILE"]).is_file()
    parsed = spawn_script_body._parse_script_body_handle(handle["session"])
    assert parsed == (
        env["AGENT_DISPATCH_SCRIPT_WORKER_ID"],
        4321,
        "token-4321",
        env["AGENT_DISPATCH_SCRIPT_TASK_FILE"],
    )
    Path(env["AGENT_DISPATCH_SCRIPT_TASK_FILE"]).unlink(missing_ok=True)


def test_make_script_spawn_rejects_missing_inline_payload() -> None:
    from agent_dispatch.spawn_script_body import make_script_spawn

    ok, handle = make_script_spawn()({"id": "task-1", "repo": TEST_REPO})

    assert ok is False
    assert "payload_inline" in handle["error"]


def test_make_script_spawn_clears_repo_for_all_repos(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agent_dispatch import spawn_script_body
    from agent_dispatch.spawn_script_body import make_script_spawn

    script_path = tmp_path / "worker.py"
    script_path.write_text("print('ok')\n", encoding="utf-8")
    started: list[tuple[list[str], dict[str, str]]] = []

    class FakeProcess:
        pid = 4321

    def popen(argv: list[str], *, env: dict[str, str], **_: object) -> FakeProcess:
        started.append((argv, env))
        return FakeProcess()

    monkeypatch.setattr(
        spawn_script_body, "subprocess", SimpleNamespace(DEVNULL=None, Popen=popen)
    )
    monkeypatch.setattr(
        "agent_dispatch.procutil.resolve_own_runtime_python",
        lambda: "C:\\runtime\\python.exe",
    )
    monkeypatch.setattr(
        "agent_dispatch.companion.process_start_token",
        lambda pid: None,
    )

    ok, handle = make_script_spawn(route="", all_repos=True)(
        {
            "id": "task-1",
            "repo": TEST_REPO,
            "payload_inline": json.dumps({"path": str(script_path)}),
        }
    )

    assert ok is True
    [(_, env)] = started
    assert env["AGENT_DISPATCH_SCRIPT_REPO"] == ""
    task_file = required(
        required(spawn_script_body._parse_script_body_handle(handle["session"]))[3]
    )
    Path(task_file).unlink(missing_ok=True)


def test_make_headless_spawn_resolves_allocation_project_lazily(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import bridge
    from agent_dispatch.spawn_factories import make_headless_spawn

    calls: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(
        bridge,
        "registered_agent_project",
        lambda agent, **kwargs: record_result(calls, (agent, kwargs), "review-harness"),
    )

    spawn = make_headless_spawn(agent="review-worker")

    assert calls == []
    project_for = getattr(spawn, "allocation_project_for", None)
    assert callable(project_for) and project_for({"id": "task-1"}) == "review-harness"
    assert calls == [
        ("review-worker", {"strict": True}),
    ]


def test_make_headless_spawn_exposes_allocation_agent() -> None:
    from agent_dispatch.spawn_factories import make_headless_spawn

    spawn = make_headless_spawn(agent="review-worker")

    assert getattr(spawn, "allocation_agent", None) == "review-worker"


def test_make_headless_spawn_charter_overrides_allocation_agent() -> None:
    """Regression for the venue/charter split: the worktree
    binding (`agent-worktrees create --agent <name>`, exposed here as
    `allocation_agent`) must bind the CHARTER when one is set, not the venue
    -- a pool's venue (`agent=`) is where it spawns, never what persona
    drives it. A pool with no charter still falls back to the venue,
    preserving pre-split behavior."""
    from agent_dispatch.spawn_factories import make_headless_spawn

    with_charter = make_headless_spawn(agent="Atlas-Core-wsl", charter="cab-sweep-reconciler")
    assert getattr(with_charter, "allocation_agent", None) == "cab-sweep-reconciler"

    without_charter = make_headless_spawn(agent="Atlas-Core-wsl")
    assert getattr(without_charter, "allocation_agent", None) == "Atlas-Core-wsl"


@pytest.mark.parametrize(
    "stderr",
    [
        "[FAIL] Session failed-123 entered failed: Connection closed",
        "[FAIL] Session failed-123 entered ended",
        "[FAIL] Session failed-123 entered stopped",
        "[FAIL] Timed out waiting for session failed-123 to become idle",
    ],
)
def test_make_headless_spawn_reports_failure_on_nonzero(
    monkeypatch: pytest.MonkeyPatch, stderr: str
) -> None:
    from agent_dispatch import embody, ordinary_bridge
    from agent_dispatch.spawn_factories import make_headless_spawn

    monkeypatch.setattr(embody, "autopilot_worker_prompt", lambda *a, **k: "seed")
    def fail(*args: object, **kwargs: object) -> NoReturn:
        raise ordinary_bridge.CreatedAgentUnrecorded(stderr)
    monkeypatch.setattr(ordinary_bridge, "start", fail)
    ok, handle = make_headless_spawn()(_headless_task("t"))
    assert ok is False
    assert "failed-123" in handle["error"]
    assert handle["retained"] is True


def test_make_headless_spawn_reuses_carried_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_dispatch import bridge, embody, ordinary_bridge
    from agent_dispatch.spawn_factories import make_headless_spawn

    resumed: list[tuple[str, str]] = []
    created: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(embody, "local_body_verdict", lambda _sid: "live")
    monkeypatch.setattr(
        bridge,
        "resume_worker",
        lambda sid, prompt, **_kwargs: record_result(resumed, (sid, prompt), True),
    )
    monkeypatch.setattr(
        bridge,
        "spawn_worker",
        lambda *args, **kwargs: created.append((args, kwargs)),
    )
    def acquire(task: dict[str, str], *, prompt: str, **kwargs: object) -> dict[str, str]:
        resumed.append((task["spawn_session_handle"].removeprefix("local-body:"), prompt))
        return {"mode": "acp", "session": "local-body:session-old",
                "native_session_id": "native-old", "worktree": "wt-review"}
    monkeypatch.setattr(ordinary_bridge, "start", acquire)

    ok, handle = make_headless_spawn()(
        _headless_task("next-task", **{
            "repo": TEST_REPO,
            "spawn_worktree": "wt-review",
            "spawn_worktree_path": "/fixture/wt-review",
            "spawn_session_handle": "local-body:session-old",
        })
    )

    assert ok is True
    assert handle["session"] == "local-body:session-old"
    assert handle["worktree"] == "wt-review"
    assert handle["native_session_id"] == "native-old"
    assert resumed and resumed[0][0] == "session-old"
    assert "next-task" in resumed[0][1]
    assert created == []


def test_make_headless_spawn_does_not_replace_live_busy_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import bridge, embody, ordinary_bridge
    from agent_dispatch.spawn_factories import make_headless_spawn

    created: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(embody, "local_body_verdict", lambda _sid: "live")
    monkeypatch.setattr(bridge, "resume_worker", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bridge,
        "spawn_worker",
        lambda *args, **kwargs: created.append((args, kwargs)),
    )
    def acquire(*args: object, **kwargs: object) -> NoReturn:
        raise bridge.BridgeCarriedSessionBusy("carried session remains live")
    monkeypatch.setattr(ordinary_bridge, "start", acquire)

    ok, handle = make_headless_spawn()(
        _headless_task("next-task", **{
            "spawn_worktree": "wt-review",
            "spawn_worktree_path": "/fixture/wt-review",
            "spawn_session_handle": "local-body:session-busy",
        })
    )

    assert ok is False
    assert "remains live" in handle["error"]
    assert handle.get("deferred") is True
    assert created == []


def test_make_headless_spawn_does_not_replace_confirmed_gone_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from agent_dispatch import bridge, embody, ordinary_bridge
    from agent_dispatch.spawn_factories import make_headless_spawn

    created: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(embody, "local_body_verdict", lambda _sid: "gone")
    monkeypatch.setattr(bridge, "resume_worker", lambda *_args, **_kwargs: False)

    def create(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        created.append((args, kwargs))
        return subprocess.CompletedProcess(
            [],
            0,
            '{"session_id":"session-new"}',
            "",
        )

    monkeypatch.setattr(bridge, "spawn_worker", create)
    def acquire(*args: object, **kwargs: object) -> NoReturn:
        raise bridge.BridgeUnavailable("strict native resume refused")
    monkeypatch.setattr(ordinary_bridge, "start", acquire)

    ok, handle = make_headless_spawn()(
        _headless_task("next-task", **{
            "spawn_worktree": "wt-review",
            "spawn_worktree_path": "/fixture/wt-review",
            "spawn_session_handle": "local-body:session-gone",
        })
    )

    assert ok is False
    assert "strict native resume refused" in handle["error"]
    assert created == []


def test_make_headless_spawn_holds_on_unknown_carried_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_dispatch import bridge, embody, ordinary_bridge
    from agent_dispatch.spawn_factories import make_headless_spawn

    created: list[tuple[tuple[object, ...], dict[str, object]]] = []
    monkeypatch.setattr(embody, "local_body_verdict", lambda _sid: "unknown")
    monkeypatch.setattr(
        bridge,
        "spawn_worker",
        lambda *args, **kwargs: created.append((args, kwargs)),
    )
    def acquire(*args: object, **kwargs: object) -> NoReturn:
        raise bridge.BridgeCarriedSessionBusy("could not determine native liveness")
    monkeypatch.setattr(ordinary_bridge, "start", acquire)

    ok, handle = make_headless_spawn()(
        _headless_task("next-task", **{
            "spawn_worktree": "wt-review",
            "spawn_worktree_path": "/fixture/wt-review",
            "spawn_session_handle": "local-body:session-unknown",
        })
    )

    assert ok is False
    assert "could not determine" in handle["error"]
    assert created == []


def test_make_headless_spawn_degrades_when_bridge_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_dispatch import bridge, embody, ordinary_bridge
    from agent_dispatch.spawn_factories import make_headless_spawn

    monkeypatch.setattr(embody, "autopilot_worker_prompt", lambda *a, **k: "seed")

    def _boom(*a: object, **k: object) -> NoReturn:
        raise bridge.BridgeUnavailable("no agent-bridge on PATH")

    monkeypatch.setattr(ordinary_bridge, "start", _boom)
    ok, handle = make_headless_spawn()(_headless_task("t"))
    assert ok is False
    assert "agent-bridge" in handle["error"]


def test_make_label_routed_spawn_routes_by_label() -> None:
    from agent_dispatch.spawn_factories import make_label_routed_spawn

    default = _ok_spawn({"session": "cli", "worktree": "wt"})
    headless = _ok_spawn({"session": "headless", "worktree": None})
    default.allocation_project = "default-project"
    headless.allocation_project_for = lambda _task: "headless-project"
    routed = make_label_routed_spawn(default, overrides={"sweep": headless})

    assert routed({"id": "a", "labels": ["sweep"]})[1]["session"] == "headless"
    assert routed({"id": "b", "labels": ["other"]})[1]["session"] == "cli"
    assert routed({"id": "c", "labels": []})[1]["session"] == "cli"
    assert routed({"id": "d"})[1]["session"] == "cli"  # no labels key
    project_for = getattr(routed, "allocation_project_for", None)
    assert callable(project_for)
    assert project_for({"labels": ["sweep"]}) == "headless-project"
    assert project_for({"labels": ["other"]}) == "default-project"


def test_make_label_routed_spawn_routes_script_by_label() -> None:
    from agent_dispatch.spawn_factories import make_label_routed_spawn

    def default(_task: dict[str, object]) -> tuple[bool, dict[str, object]]:
        return True, {"session": "headless", "worktree": None}

    class Script:
        allocation_interface = "script"

        def __call__(self, _task: dict[str, object]) -> tuple[bool, dict[str, object]]:
            return True, {"session": "script", "worktree": None}

    routed = make_label_routed_spawn(default, overrides={"maintenance": Script()})

    assert routed({"labels": ["maintenance"]})[1]["session"] == "script"
    interface_for = getattr(routed, "allocation_interface_for", None)
    assert callable(interface_for) and interface_for({"labels": ["maintenance"]}) == "script"
    assert routed({"labels": ["other"]})[1]["session"] == "headless"


def test_make_label_routed_spawn_no_overrides_returns_default_unwrapped() -> None:
    from agent_dispatch.spawn_factories import make_label_routed_spawn

    def default(_task: dict[str, object]) -> tuple[bool, dict[str, object]]:
        return True, {}

    assert make_label_routed_spawn(default, overrides={}) is default


def test_cli_supervise_headless_is_default(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_api: TestClient
) -> None:
    """Headless is the DEFAULT embody backend: with no per-label flags, every
    watched task embodies headless (no CLI/mux)."""

    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod

    a = q.create("sweep a")
    b = q.create("sweep b")

    embody_calls: list[str] = []
    headless_calls: list[str] = []

    def embody_spawn(task: dict[str, str]) -> tuple[bool, dict[str, str | None]]:
        embody_calls.append(task["id"])
        return True, {"session": "cli", "worktree": "wt"}

    def headless_spawn(task: dict[str, str]) -> tuple[bool, dict[str, str | None]]:
        headless_calls.append(task["id"])
        return True, {"session": "headless", "worktree": None}

    monkeypatch.setattr(m, "_client", lambda _args, **_kw: connected(supervisor_api, []))
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)
    monkeypatch.setattr(sup_mod, "make_embody_spawn", lambda **_kw: embody_spawn)
    monkeypatch.setattr(sup_mod, "make_headless_spawn", lambda **_kw: headless_spawn)

    args = argparse.Namespace(
        all_repos=False, repo=None, url=None, token=None, label=None,
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        embody_backend=None, headless_label=None, cli_label=None,
        headless_agent="task-worker",
    )
    assert _cmd_supervise(args) == 0
    assert set(headless_calls) == {a.id, b.id}  # both headless by default
    assert embody_calls == []


def test_ordinary_headless_task_records_created_worktree_before_launch(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    from agent_dispatch import embody

    task = q.create("ordinary")
    spawn = _ok_spawn({"session": "local-body:s1", "worktree": "wt-created"})
    spawn.requires_reusable_worktree = True
    spawn.allocation_interface = "acp"
    spawn.allocation_project = "worker-harness"
    prepared: dict[str, object] = {}

    def prepare(*_args: object, **kwargs: object) -> dict[str, object]:
        prepared.update(kwargs)
        return {
            "worktree": "wt-created",
            "path": "/fixture/wt-created",
            "project": kwargs["project"],
            "created": True,
            "replaced": False,
            "ownership": "created",
        }

    monkeypatch.setattr(
        embody,
        "prepare_reusable_worktree",
        prepare,
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        machine="host-a",
    )

    assert sup.poll_once() == [task.id]
    reservation = required(q.latest_reservation(task.id))
    assert reservation.worktree == "wt-created"
    assert reservation.worktree_ownership == "created"
    assert reservation.driver == "agent-dispatch"
    assert reservation.creating_host == "host-a"
    assert prepared["project"] == "worker-harness"


def test_exclusive_spawn_records_precreated_worktree_before_launch(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    from agent_dispatch import embody

    task = q.create("review", exclusive_key="review:repo:42")
    observed: list[tuple[SpawnReservation, dict[str, object]]] = []

    class Spawn:
        requires_reusable_worktree = True

        def __call__(self, spawn_task: dict[str, object]) -> tuple[bool, dict[str, object]]:
            observed.append((required(q.latest_reservation(task.id)), spawn_task))
            return True, {
                "session": "local-body:session-new",
                "worktree": spawn_task["spawn_worktree"],
            }

    monkeypatch.setattr(
        embody,
        "prepare_reusable_worktree",
        lambda _task, _reservation, **_kwargs: {
            "worktree": "wt-precreated",
            "path": "/fixture/wt-precreated",
            "project": _kwargs["project"],
            "created": True,
            "replaced": False,
            "ownership": "created",
        },
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=Spawn(),
        repo=TEST_REPO,
        max_concurrent=5,
        machine="host-a",
    )

    assert sup.poll_once() == [task.id]
    [(reservation, spawn_task)] = observed
    assert reservation.state == SpawnState.RESERVING
    assert reservation.worktree == "wt-precreated"
    assert spawn_task["spawn_worktree_path"] == "/fixture/wt-precreated"


def test_created_worktree_bookkeeping_failure_retains_spawn_fence(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    from agent_dispatch import embody

    task = q.create("work")
    spawn = _ok_spawn()
    spawn.requires_reusable_worktree = True
    monkeypatch.setattr(
        embody,
        "prepare_reusable_worktree",
        lambda *_args, **_kwargs: {
            "worktree": "wt-created",
            "path": "/fixture/wt-created",
            "created": True,
            "replaced": False,
            "ownership": "created",
        },
    )
    monkeypatch.setattr(
        supervisor_http,
        "record_spawn_worktree",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DispatchError(503, "coordinator unavailable")
        ),
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert required(q.latest_reservation(task.id)).state == SpawnState.RESERVING


def test_cli_supervise_cli_label_opts_out(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_api: TestClient
) -> None:
    """--cli-label routes a marked task back to CLI/mux while everything else
    stays headless (the opt-out on a headless-by-default lane)."""

    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod

    interactive = q.create("interactive work", labels=["needs-attach"])
    sweep = q.create("sweep work")

    embody_calls: list[str] = []
    headless_calls: list[str] = []

    def embody_spawn(task: dict[str, str]) -> tuple[bool, dict[str, str | None]]:
        embody_calls.append(task["id"])
        return True, {"session": "cli", "worktree": "wt"}

    def headless_spawn(task: dict[str, str]) -> tuple[bool, dict[str, str | None]]:
        headless_calls.append(task["id"])
        return True, {"session": "headless", "worktree": None}

    monkeypatch.setattr(m, "_client", lambda _args, **_kw: connected(supervisor_api, []))
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)
    monkeypatch.setattr(sup_mod, "make_embody_spawn", lambda **_kw: embody_spawn)
    monkeypatch.setattr(sup_mod, "make_headless_spawn", lambda **_kw: headless_spawn)

    args = argparse.Namespace(
        all_repos=False, repo=None, url=None, token=None, label=None,
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        embody_backend=None, headless_label=None, cli_label=["needs-attach"],
        headless_agent="task-worker",
    )
    assert _cmd_supervise(args) == 0
    assert embody_calls == [interactive.id]  # opted out to CLI
    assert sweep.id in headless_calls
    assert interactive.id not in headless_calls


def test_cli_supervise_headless_label_routes(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_api: TestClient
) -> None:
    """--headless-label forces a label headless on an explicit --embody-backend cli
    lane (the opt-in when the default has been set back to CLI)."""

    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod

    marked = q.create("sweep work", labels=["nightly-scan"])
    plain = q.create("interactive work")

    embody_calls: list[str] = []
    headless_calls: list[str] = []

    def embody_spawn(task: dict[str, str]) -> tuple[bool, dict[str, str | None]]:
        embody_calls.append(task["id"])
        return True, {"session": "cli", "worktree": "wt"}

    def headless_spawn(task: dict[str, str]) -> tuple[bool, dict[str, str | None]]:
        headless_calls.append(task["id"])
        return True, {"session": "headless", "worktree": None}

    monkeypatch.setattr(m, "_client", lambda _args, **_kw: connected(supervisor_api, []))
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)
    monkeypatch.setattr(sup_mod, "make_embody_spawn", lambda **_kw: embody_spawn)
    monkeypatch.setattr(sup_mod, "make_headless_spawn", lambda **_kw: headless_spawn)

    args = argparse.Namespace(
        all_repos=False, repo=None, url=None, token=None, label=None,
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        embody_backend="cli", headless_label=["nightly-scan"], cli_label=None,
        headless_agent="task-worker",
    )
    assert _cmd_supervise(args) == 0
    assert headless_calls == [marked.id]
    assert plain.id in embody_calls
    assert marked.id not in embody_calls


def test_cli_supervise_script_label_routes(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_api: TestClient
) -> None:
    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod
    from agent_dispatch.supervise_cli import _cmd_supervise

    marked = q.create("maintenance work", labels=["maintenance"])
    plain = q.create("sweep work")

    headless_calls: list[str] = []
    script_calls: list[str] = []

    def headless_spawn(task: dict[str, str]) -> tuple[bool, dict[str, str | None]]:
        headless_calls.append(task["id"])
        return True, {"session": "headless", "worktree": None}

    def script_spawn(task: dict[str, str]) -> tuple[bool, dict[str, str | None]]:
        script_calls.append(task["id"])
        return True, {"session": "script", "worktree": None}

    monkeypatch.setattr(m, "_client", lambda _args, **_kw: connected(supervisor_api, []))
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)
    monkeypatch.setattr(sup_mod, "make_headless_spawn", lambda **_kw: headless_spawn)
    monkeypatch.setattr(sup_mod, "make_script_spawn", lambda **_kw: script_spawn)

    args = argparse.Namespace(
        all_repos=False,
        repo=None,
        url=None,
        token=None,
        label=None,
        max_concurrent=5,
        verify_timeout=0,
        once=True,
        interval=30.0,
        no_heartbeat=False,
        max_attempts=3,
        embody_backend=None,
        headless_label=None,
        cli_label=None,
        script_label=["maintenance"],
        headless_agent="task-worker",
    )
    assert _cmd_supervise(args) == 0
    assert script_calls == [marked.id]
    assert plain.id in headless_calls


def test_cli_supervise_script_backend_is_lane_default(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_api: TestClient
) -> None:
    from agent_dispatch import __main__ as m
    from agent_dispatch import supervisor as sup_mod
    from agent_dispatch.supervise_cli import _cmd_supervise

    task = q.create("maintenance work")
    script_calls: list[str] = []

    def script_spawn(task: dict[str, str]) -> tuple[bool, dict[str, str | None]]:
        script_calls.append(task["id"])
        return True, {"session": "script", "worktree": None}

    monkeypatch.setattr(m, "_client", lambda _args, **_kw: connected(supervisor_api, []))
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)
    monkeypatch.setattr(sup_mod, "make_script_spawn", lambda **_kw: script_spawn)

    args = argparse.Namespace(
        all_repos=False,
        repo=None,
        url=None,
        token=None,
        label=None,
        max_concurrent=5,
        verify_timeout=0,
        once=True,
        interval=30.0,
        no_heartbeat=False,
        max_attempts=3,
        embody_backend="script",
        headless_label=None,
        cli_label=None,
        script_label=None,
        headless_agent="task-worker",
    )
    assert _cmd_supervise(args) == 0
    assert script_calls == [task.id]


def test_cli_supervise_rejects_script_backend_in_pool_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from agent_dispatch import __main__ as m
    from agent_dispatch.supervise_cli import _cmd_supervise

    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)

    args = argparse.Namespace(
        all_repos=False,
        repo=None,
        url=None,
        token=None,
        label=["maintenance"],
        max_concurrent=5,
        verify_timeout=0,
        once=True,
        interval=30.0,
        no_heartbeat=False,
        max_attempts=3,
        pool="pool-a",
        origin="origin",
        headless=False,
        embody_backend="script",
        headless_label=None,
        cli_label=None,
        script_label=None,
        disposable_cli_label=None,
        headless_agent="task-worker",
        no_pair=False,
    )
    assert _cmd_supervise(args) == 2
    assert "script embodiment is supported only for local" in capsys.readouterr().err


def test_cli_supervise_rejects_idle_nudge_exempt_label_not_watched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    from agent_dispatch import __main__ as m

    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)

    args = argparse.Namespace(
        all_repos=False, repo=None, url=None, token=None, label=["maintenance"],
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        pool=None, origin=None, headless=False,
        embody_backend="headless", headless_label=None, cli_label=None, script_label=None,
        disposable_cli_label=None, idle_nudge_exempt_label=["other-label"],
        headless_agent="task-worker", charter=None, no_pair=False,
    )
    assert _cmd_supervise(args) == 2
    assert "every --idle-nudge-exempt-label must also be watched" in capsys.readouterr().err


def test_cli_supervise_pool_headless_builds_headless_fleet(
    monkeypatch: pytest.MonkeyPatch, q: OwnerResultQueue, supervisor_api: TestClient
) -> None:
    _assert_cli_supervise_pool_headless_fleet(monkeypatch, q, supervisor_api, None, "")


@pytest.mark.parametrize("authority,route", [("central", " --shared"), ("local", " --local")])
def test_cli_supervise_pool_headless_forwards_explicit_authority(
    monkeypatch: pytest.MonkeyPatch,
    q: OwnerResultQueue,
    supervisor_api: TestClient,
    authority: str,
    route: str,
) -> None:
    _assert_cli_supervise_pool_headless_fleet(monkeypatch, q, supervisor_api, authority, route)


def _assert_cli_supervise_pool_headless_fleet(
    monkeypatch: pytest.MonkeyPatch,
    q: OwnerResultQueue,
    supervisor_api: TestClient,
    authority: str | None,
    route: str,
) -> None:
    """--pool --headless constructs a headless FleetSpawner with the configured
    --headless-agent (the headless-fleet embodiment for a remote pool host)."""

    from agent_dispatch import __main__ as m
    from agent_dispatch import fleet as fleet_mod
    from agent_dispatch import remote_dispatch

    q.create("work")
    if authority:
        monkeypatch.setenv("AGENT_DISPATCH_TASK_AUTHORITY", authority)
    captured: dict[str, object] = {}

    class FakeFleet:
        def __init__(
            self,
            pool: list[str],
            *,
            origin: str,
            headless: bool = False,
            agent: str = "task-worker",
            charter: str | None = None,
            all_repos: bool = False,
            verify_timeout: float = 0,
            route: str = "",
        ) -> None:
            self.pool = list(pool)
            captured.update(
                pool=pool, origin=origin, headless=headless, agent=agent,
                charter=charter, all_repos=all_repos, route=route,
            )

        def __call__(self, task: dict[str, object]) -> tuple[bool, dict[str, object]]:
            return True, {
                "session": "s", "worktree": None, "machine": "lc", "owner": "o",
            }

        def can_spawn(self, task: dict[str, object]) -> bool:
            return True

    monkeypatch.setattr(m, "_client", lambda _args, **_kw: connected(supervisor_api, []))
    monkeypatch.setattr(m, "client_url", lambda: "http://coord")
    monkeypatch.setattr(m, "_scope_repo", lambda _args: TEST_REPO)
    monkeypatch.setattr(fleet_mod, "FleetSpawner", FakeFleet)
    monkeypatch.setattr(remote_dispatch, "local_machine", lambda: "mantis-counter")

    args = argparse.Namespace(
        all_repos=False, repo=None, url=None, token=None, label=["review"],
        max_concurrent=5, verify_timeout=0, once=True, interval=30.0,
        no_heartbeat=False, max_attempts=3,
        pool="anomalous-potato-wsl", origin=None, headless=True,
        headless_label=None, headless_agent="review-worker",
    )
    assert _cmd_supervise(args) == 0
    assert captured["headless"] is True
    assert captured["agent"] == "review-worker"
    assert captured["all_repos"] is False
    assert captured["pool"] == ["anomalous-potato-wsl"]
    assert captured["origin"] == "mantis-counter"
    assert captured["route"] == route


def test_hold_live_leases_heartbeats_live_script_body(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("maintenance")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle='script-body:{"pid":4321,"start_token":"tok","worker_id":"script-1"}',
        worktree=None,
    )
    q.claim_one("script-1", repo=TEST_REPO, task_id=task.id)
    q.start(task.id, "script-1")

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        script_body_verdict_fn=lambda pid, token: tracking.LIVE,
    )

    assert sup.hold_live_leases() == 1


def test_unqualified_legacy_script_exit_retains_original_assignment(
    q: OwnerResultQueue, supervisor_http: DispatchClient, tmp_path: Path
) -> None:
    task = q.create("maintenance")
    reservation, _ = q.reserve_spawn(task.id)
    task_file = tmp_path / "task-script.json"
    task_file.write_text("{}", encoding="utf-8")
    q.record_spawn(
        reservation.key,
        session_handle=(
            '{"pid":4321,"start_token":"tok","task_file":"'
            + str(task_file).replace("\\", "\\\\")
            + '","worker_id":"script-1"}'
        ).join(("script-body:", "")),
        worktree=None,
    )
    q.claim_one("script-1", repo=TEST_REPO, task_id=task.id)
    q.start(task.id, "script-1")

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        script_body_verdict_fn=lambda pid, token: tracking.GONE,
    )

    assert sup.recover_gone() == 0
    assert required(q.get(task.id)).status == Status.STARTED
    assert required(q.latest_reservation(task.id)).state == SpawnState.SPAWNED
    assert task_file.exists()


def test_reconcile_settles_terminal_script_body_and_cleans_task_file(
    q: OwnerResultQueue, supervisor_http: DispatchClient, tmp_path: Path
) -> None:
    task = q.create("maintenance")
    reservation, _ = q.reserve_spawn(task.id)
    task_file = tmp_path / "task-terminal.json"
    task_file.write_text("{}", encoding="utf-8")
    handle = (
        '{"pid":4321,"start_token":"tok","task_file":"'
        + str(task_file).replace("\\", "\\\\")
        + '","worker_id":"script-1"}'
    ).join(("script-body:", ""))
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle=handle,
        worktree=None,
    )
    q.claim_one("script-1", repo=TEST_REPO, task_id=task.id)
    q.start(task.id, "script-1")
    q.complete(task.id, "script-1")

    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO)

    assert sup.reconcile() == 1
    assert required(q.latest_reservation(task.id)).state == SpawnState.SETTLED
    assert not task_file.exists()


# -- Slice 2: liveness-gated auto-recovery -----------------------------------


@pytest.mark.parametrize("verdict", ["live", "unknown"])
def test_superseded_task_does_not_release_exclusive_live_reservation(
    q: OwnerResultQueue, supervisor_http: DispatchClient, verdict: str
) -> None:
    old = q.create("old", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(old.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:session-old",
        worktree="wt-review",
    )
    new = before_retention_supersession(q, old)
    ended: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: verdict,
        local_end_fn=lambda sid: record_result(ended, sid, False),
        nudge=False,
    )

    assert required(q.get(old.id)).status == Status.ABANDONED
    assert sup.reconcile() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED
    blocked, acquired = q.reserve_spawn(new.id)
    assert acquired is False
    assert blocked.key == reservation.key
    assert ended == (["session-old"] if verdict == "live" else [])


def test_confirmed_gone_superseded_body_releases_exclusive_key(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    old = q.create("old", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(old.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:session-old",
        worktree="wt-review",
    )
    new = before_retention_supersession(q, old)
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: "gone",
        local_end_fn=lambda _sid: True,
        nudge=False,
    )

    assert sup.reconcile() == 1
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SETTLED
    fresh, acquired = q.reserve_spawn(new.id)
    assert acquired is True
    assert fresh.worktree == "wt-review"


def test_explicit_end_releases_live_exclusive_body(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:session-live",
        worktree="wt-review",
    )
    before_retention_abandon(q, task.id, reason="superseded")
    ended: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: "live",
        local_end_fn=lambda sid: record_result(ended, sid, True),
        nudge=False,
    )

    assert sup.reconcile() == 1
    assert ended == ["session-live"]
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SETTLED


@pytest.mark.parametrize("verdict", ["live", "unknown"])
def test_yielded_fleet_body_is_not_replaced_before_safe_teardown(
    q: OwnerResultQueue, supervisor_http: DispatchClient, verdict: str
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="fleet-body:worker-host:session-live",
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    q.claim_one("fleet-owner", task_id=task.id)
    q.start(task.id, "fleet-owner")
    before_retention_release(q, task.id, "fleet-owner")
    ended: list[tuple[str, str]] = []
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        fleet_verdict_fn=lambda _host, _sid: verdict,
        fleet_end_fn=lambda host, sid: record_result(ended, (host, sid), False),
        nudge=False,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING
    assert ended == (
        [("worker-host", "session-live")] if verdict == "live" else []
    )


def test_yielded_fleet_body_respawns_only_after_explicit_end(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="fleet-body:worker-host:session-live",
        startup_complete=True,
        startup_revision=1,
    )
    q.claim_one("fleet-owner", task_id=task.id)
    q.start(task.id, "fleet-owner")
    before_retention_release(q, task.id, "fleet-owner")
    ended: list[tuple[str, str]] = []
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        fleet_verdict_fn=lambda _host, _sid: "live",
        fleet_end_fn=lambda host, sid: record_result(ended, (host, sid), True),
        nudge=False,
    )

    assert sup.poll_once() == [task.id]
    assert ended == [("worker-host", "session-live")]
    assert spawn.calls == [task.id]
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SETTLED
    assert required(q.latest_reservation(task.id)).attempt == 2


def test_yielded_created_worktree_is_concluded_before_respawn(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="local-body:bridge-1",
        worktree="wt-created", startup_complete=True, startup_revision=1,
    )
    q.claim_one("host-a/wt-created", task_id=task.id)
    q.start(
        task.id,
        "host-a/wt-created",
        owner_session_id="acp-session-1",
    )
    before_retention_release(q, task.id, "host-a/wt-created")
    conclusions: list[tuple[str | None, ...]] = []
    spawn = _ok_spawn({"session": "sess-1", "worktree": "wt-created"})
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: "gone",
        local_acp_session_fn=lambda _sid: "acp-session-1",
        local_end_fn=lambda _sid: True,
        attempt_conclusion_fn=lambda *args: record_result(conclusions, args, {
            "action": "primed",
            "reason": "managed-gc-candidate",
        }),
        nudge=False,
    )

    assert sup.poll_once() == [task.id]
    first = required(q.get_reservation(reservation.key))
    assert first.state == SpawnState.SETTLED
    assert first.conclusion_state == "complete"
    assert conclusions == [
        (
            "wt-created",
            "acp-session-1",
            reservation.key,
            "agent-dispatch",
        )
    ]
    assert required(q.latest_reservation(task.id)).attempt == 2


def test_yielded_attempt_checkpoints_identity_before_bridge_end_and_restart(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key, "wt-created", ownership="created",
        creating_host="host-a", driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="local-body:bridge-1",
        worktree="wt-created",
        startup_complete=True,
        startup_revision=1,
    )
    q.claim_one("host-a/wt-created", task_id=task.id)
    q.start(task.id, "host-a/wt-created", owner_session_id="session-exact")
    before_retention_release(q, task.id, "host-a/wt-created")

    def end_then_exit(_session: str) -> bool:
        checkpoint = json.loads(
            required(q.get_reservation(reservation.key)).conclusion_detail or "{}"
        )
        assert checkpoint["acp_session_id"] == "session-exact"
        raise SystemExit("supervisor exited after bridge teardown")

    first = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, machine="host-a",
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_acp_session_fn=lambda _sid: "session-exact",
        local_end_fn=end_then_exit, nudge=False,
    )
    with pytest.raises(SystemExit, match="after bridge teardown"):
        first.release_requested_bodies()

    assert required(q.get(task.id)).owner_session_id is None
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING
    conclusions: list[tuple[str | None, ...]] = []
    second = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, machine="host-a",
        local_body_verdict_fn=lambda _sid: tracking.GONE,
        local_acp_session_fn=lambda _sid: pytest.fail(
            "persisted identity must survive the bridge record disappearing"
        ),
        local_end_fn=lambda _sid: True,
        attempt_conclusion_fn=lambda *args: record_result(conclusions, args, {
            "action": "primed", "reason": "managed-gc-candidate",
        }),
        nudge=False,
    )
    assert second.release_requested_bodies() == 1
    assert conclusions == [
        ("wt-created", "session-exact", reservation.key, "agent-dispatch"),
    ]
    assert required(q.get_reservation(reservation.key)).conclusion_state == "complete"


def test_native_released_attempt_identity_survives_process_exit(tmp_path: Path) -> None:
    import sys
    import textwrap

    from agent_dispatch.procutil import run_background_capture

    script = textwrap.dedent("""
        import json
        import os
        import sys
        from pathlib import Path
        from agent_dispatch.supervisor import Supervisor
        from tests._legacy_supervisor_history import before_retention_release
        from tests.test_supervisor import OwnerResultQueue, QueueBackedClient, _ok_spawn
        from tests._helpers import TEST_REPO

        q = OwnerResultQueue(Path(sys.argv[1]))
        phase = sys.argv[2]
        if phase == "retire":
            task = q.create("work")
            reservation, _ = q.reserve_spawn(task.id)
            q.record_spawn_worktree(
                reservation.key, "wt-created", ownership="created",
                creating_host="host-a", driver="agent-dispatch",
            )
            q.record_spawn(
                reservation.key, session_handle="local-body:bridge-1",
                worktree="wt-created",
                startup_complete=True, startup_revision=reservation.startup_revision,
            )
            q.claim_one("host-a/wt-created", task_id=task.id)
            q.start(task.id, "host-a/wt-created", owner_session_id="session-exact")
            before_retention_release(q, task.id, "host-a/wt-created")
        else:
            task = q.list(repo=TEST_REPO)[0]
            reservation = q.latest_reservation(task.id)

        def end(_session):
            if phase == "retire":
                checkpoint = json.loads(q.get_reservation(reservation.key).conclusion_detail)
                assert checkpoint["acp_session_id"] == "session-exact"
                os._exit(23)
            return True

        def conclude(worktree, session, key, driver):
            assert (worktree, session, key, driver) == (
                "wt-created", "session-exact", reservation.key, "agent-dispatch",
            )
            return {"action": "primed", "reason": "managed-gc-candidate"}

        sup = Supervisor(
            QueueBackedClient(q), spawn_fn=_ok_spawn(), repo=TEST_REPO, machine="host-a",
            local_body_verdict_fn=lambda _sid: "live" if phase == "retire" else "gone",
            local_acp_session_fn=lambda _sid: "session-exact" if phase == "retire" else None,
            local_end_fn=end, attempt_conclusion_fn=conclude, nudge=False,
        )
        assert sup.release_requested_bodies() == 1
        assert phase == "recover"
        assert q.get_reservation(reservation.key).conclusion_state == "complete"
    """)
    for phase, expected in (("retire", 23), ("recover", 0)):
        result = run_background_capture(
            [sys.executable, "-c", script, str(tmp_path / "queue.db"), phase],
            cwd=Path(__file__).resolve().parents[1],
            timeout=10,
        )
        assert result is not None
        assert result.returncode == expected, result.stdout + result.stderr


def test_yielded_attempt_does_not_end_bridge_when_identity_checkpoint_fails(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key, "wt-created", ownership="created",
        creating_host="host-a", driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key, session_handle="local-body:bridge-1", worktree="wt-created",
    )
    q.request_spawn_release(reservation.key, disposition="settled")
    sup = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, machine="host-a",
        local_body_verdict_fn=lambda _sid: tracking.LIVE,
        local_acp_session_fn=lambda _sid: "session-exact",
        local_end_fn=lambda _sid: pytest.fail("checkpoint must precede bridge teardown"),
        nudge=False,
    )

    def refuse_checkpoint(*_args: object, **_kwargs: object) -> None:
        raise DispatchError(503, "checkpoint unavailable")

    monkeypatch.setattr(sup._conclusion_client, "record_spawn_conclusion", refuse_checkpoint)
    assert sup.release_requested_bodies() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING


def test_failed_attempt_cleanup_retries_after_body_release(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        detail="launch failed",
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    spawn = _ok_spawn()
    first = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "gone",
        worktree_directory_present_fn=lambda *_args: False,
        attempt_conclusion_fn=lambda *_args: {
            "action": "failed",
            "reason": "lifecycle lock busy",
        },
        nudge=False,
    )

    assert first.release_requested_bodies() == 1
    pending = required(q.get_reservation(reservation.key))
    assert pending.state == SpawnState.FAILED
    assert pending.conclusion_state == "pending"
    assert spawn.calls == []
    with pytest.raises(TaskError, match="in-flight cleanup"):
        q.reserve_spawn(task.id)

    second = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "gone",
        attempt_conclusion_fn=lambda *_args: {
            "action": "primed",
            "reason": "managed-gc-candidate",
        },
        nudge=False,
    )

    pending_detail = json.loads(
        required(q.get_reservation(reservation.key)).conclusion_detail or "{}"
    )
    worktree_next_attempt = pending_detail["worktree_cleanup"]["next_attempt_at"]
    assert second.release_requested_bodies(
        now=worktree_next_attempt - 0.1
    ) == 0
    assert required(q.get_reservation(reservation.key)).conclusion_state == "pending"
    assert second.release_requested_bodies(
        now=worktree_next_attempt
    ) == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.FAILED
    assert required(q.get_reservation(reservation.key)).conclusion_state == "complete"
    replacement, acquired = q.reserve_spawn(task.id)
    assert acquired is True
    assert replacement.attempt == 2
    assert replacement.worktree == "wt-created"


# -- unleased worktree-only retirement: no captured owner_session_id --------
#
# A RESERVING-stage spawn failure (worktree preparation failed before any
# claim/session ever existed) leaves the task without an owner_session_id.
# verdict_fn alone can never resolve this to "gone" (identity-keyed, by
# design), so release_requested_bodies additionally cross-checks the local
# agent-worktrees registry directly for this reservation's own recorded
# worktree -- see supervisor.py's worktree_directory_present_fn.


def test_retires_unleased_worktree_reservation_when_directory_confirmed_absent(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    from agent_dispatch import embody

    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-gone",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        detail="worktree preparation failed",
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    assert required(q.get(task.id)).owner is None  # never claimed -> no owner_session_id
    probe_calls: list[tuple[str, str | None]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "unknown",
        worktree_directory_present_fn=lambda wt, project: record_result(
            probe_calls, (wt, project), False
        ),
        attempt_conclusion_fn=lambda *_args: {
            "action": "failed",
            "reason": "lifecycle lock busy",
        },
        nudge=False,
    )

    assert sup.release_requested_bodies() == 1
    assert required(q.get_reservation(reservation.key)).state == SpawnState.FAILED
    # The project (derived from the task's repo, not the CWD) must be threaded
    # through -- this daemon is CWD-neutral.
    assert probe_calls == [("wt-gone", embody.project_for_task({"repo": TEST_REPO}))]


def test_probes_the_spawn_fns_own_allocation_project_when_overridden(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    # A routed/headless spawn_fn can select an allocation project different
    # from plain embody.project_for_task(task) (e.g. via an
    # `allocation_project_for` selector) -- the same one _prepare_spawn_task
    # would have used to actually create this worktree. The absence probe
    # must be scoped to THAT project, not silently re-derive a possibly
    # different one, or it can query the wrong project's registry and
    # wrongly retire a still-present worktree.
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-routed",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
        allocation_project="routed-project",
    )
    q.request_spawn_release(
        reservation.key,
        detail="worktree preparation failed",
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    spawn = _ok_spawn()
    spawn.allocation_project_for = lambda _task: "routed-project"
    probe_calls: list[tuple[str, str | None]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "unknown",
        worktree_directory_present_fn=lambda wt, project: record_result(
            probe_calls, (wt, project), False
        ),
        attempt_conclusion_fn=lambda *_args: {
            "action": "failed",
            "reason": "lifecycle lock busy",
        },
        nudge=False,
    )

    assert sup.release_requested_bodies() == 1
    assert probe_calls == [("wt-routed", "routed-project")]


def test_missing_allocation_project_degrades_only_that_reservation(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    # Missing provenance is unknown, even if current routing offers a project.
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    other_task = q.create("other-work")
    other_reservation, _ = q.reserve_spawn(other_task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-project-resolution-fails",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
        allocation_project=None,
    )
    q.record_spawn_worktree(
        other_reservation.key,
        "wt-other",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        detail="worktree preparation failed",
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    q.request_spawn_release(
        other_reservation.key,
        detail="worktree preparation failed",
        disposition="failed",
        finished_startup_revision=required(
            q.get_reservation(other_reservation.key)
        ).startup_revision,
    )

    def raising_project_selector(task_dict: dict[str, object]) -> str:
        if task_dict.get("id") == task.id:
            raise RuntimeError("bridge registry unavailable")
        return "other-project"

    spawn = _ok_spawn()
    spawn.allocation_project_for = raising_project_selector
    probed: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "unknown",
        worktree_directory_present_fn=lambda wt, project: record_result(probed, wt, False),
        attempt_conclusion_fn=lambda *_args: {
            "action": "failed",
            "reason": "lifecycle lock busy",
        },
        nudge=False,
    )

    # Both reservations get processed this cycle -- the raising selector for
    # the first must not abort the loop before reaching the second.
    assert sup.release_requested_bodies() == 1
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING
    assert required(q.get_reservation(other_reservation.key)).state == SpawnState.FAILED
    assert probed == ["wt-other"]


def test_keeps_unleased_worktree_reservation_when_directory_still_present(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-finalized-but-present",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        detail="worktree preparation failed",
        disposition="failed",
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "unknown",
        worktree_directory_present_fn=lambda _wt, _project: True,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING


def test_keeps_unleased_worktree_reservation_when_directory_probe_unresolved(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-unresolved",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        detail="worktree preparation failed",
        disposition="failed",
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "unknown",
        worktree_directory_present_fn=lambda _wt, _project: None,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING


def test_does_not_confirm_absence_for_a_captured_owner_session(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    # Once owner_session_id IS captured, this exception must not apply --
    # verdict_fn's ordinary identity-keyed contract governs alone, exactly as
    # before this change.
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-claimed",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.claim_one("host-a/wt-claimed", machine="host-a", worktree="wt-claimed",
                task_id=task.id)
    q.start(task.id, "host-a/wt-claimed", owner_session_id="S1")
    q.request_spawn_release(
        reservation.key,
        detail="worktree preparation failed",
        disposition="failed",
    )

    def forbidden(_wt: str, _project: str | None) -> bool:
        raise AssertionError(
            "must not probe worktree_directory_present_fn once owner_session_id "
            "is captured"
        )

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "unknown",
        worktree_directory_present_fn=forbidden,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING


def test_does_not_confirm_absence_for_a_remote_owner(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-remote",
        ownership="created",
        creating_host="peer-box",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        detail="worktree preparation failed",
        disposition="failed",
    )
    # Claim (but never start) the task under a remote-machine owner, leaving
    # owner_session_id uncaptured -- the claim-before-registration race window
    # -- while `_machine_from_owner` still resolves non-None from the stored
    # "<machine>/<worktree>" owner string.
    q.claim_one("peer-box/wt-remote", machine="peer-box", worktree="wt-remote",
                task_id=task.id)

    def forbidden(_wt: str, _project: str | None) -> bool:
        raise AssertionError(
            "must not probe the local agent-worktrees registry for a remote owner"
        )

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "unknown",
        worktree_directory_present_fn=forbidden,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING


def test_does_not_confirm_absence_when_creating_host_is_a_different_machine(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    # The reviewer's exact scenario: the task is never claimed (owner is
    # None, so `_machine_from_owner` alone can't gate this), but the
    # reservation's worktree was created on a DIFFERENT host than this
    # supervisor's own. A supervisor polling a shared cross-machine queue
    # must not probe its own local registry for a worktree that in fact
    # lives elsewhere.
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-on-other-host",
        ownership="created",
        creating_host="peer-box",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        detail="worktree preparation failed",
        disposition="failed",
    )
    assert required(q.get(task.id)).owner is None

    def forbidden(_wt: str, _project: str | None) -> bool:
        raise AssertionError(
            "must not probe the local agent-worktrees registry for a "
            "reservation created on a different host"
        )

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "unknown",
        worktree_directory_present_fn=forbidden,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING


def test_does_not_confirm_absence_for_a_spawned_body_with_a_session_handle(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    # The reviewer's exact scenario: a body was actually spawned (a raw
    # CLI/mux session handle recorded -- not the "fleet-body:"/"local-body:"
    # prefixes the branches above already special-case) but never claimed
    # its task, so owner/owner_session_id are both still None, same as a
    # pure RESERVING-stage failure. Unlike that case, a real body may still
    # be alive under this handle; the worktree-directory-only shortcut must
    # not bypass resolving it and wrongly retire a still-live reservation.
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-spawned-unclaimed",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="cli-session-xyz",
        worktree="wt-spawned-unclaimed",
    )
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED
    q.request_spawn_release(
        reservation.key,
        detail="yielded",
        disposition="failed",
    )
    assert required(q.get(task.id)).owner is None

    def forbidden(_wt: str, _project: str | None) -> bool:
        raise AssertionError(
            "must not probe the local agent-worktrees registry for a "
            "reservation with a recorded session_handle"
        )

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "unknown",
        worktree_directory_present_fn=forbidden,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING


    # claim_one accepts arbitrary worker IDs (e.g. "worker-1", no
    # "<machine>/<worktree>" separator) -- `_machine_from_owner` on such a
    # string returns None just like an actually-unset owner, so the gate must
    # check `task.get("owner") is None` explicitly rather than relying on
    # that parse result alone.
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-malformed-owner",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.claim_one("worker-1", task_id=task.id)
    assert required(q.get(task.id)).owner == "worker-1"
    q.request_spawn_release(
        reservation.key,
        detail="worktree preparation failed",
        disposition="failed",
    )

    def forbidden_owner_probe(_wt: str, _project: str | None) -> bool:
        raise AssertionError(
            "must not probe the local agent-worktrees registry for a claimed "
            "(even if malformed-owner) task"
        )

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "unknown",
        worktree_directory_present_fn=forbidden_owner_probe,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RELEASING


def test_confirms_absence_with_case_insensitive_creating_host_match(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-mixed-case-host",
        ownership="created",
        creating_host="Host-A",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        detail="worktree preparation failed",
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "unknown",
        worktree_directory_present_fn=lambda _wt, _project: False,
        attempt_conclusion_fn=lambda *_args: {
            "action": "failed",
            "reason": "lifecycle lock busy",
        },
        nudge=False,
    )

    assert sup.release_requested_bodies() == 1
    assert required(q.get_reservation(reservation.key)).state == SpawnState.FAILED


def test_held_attempt_cleanup_remains_visible_without_fencing_replacement(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work", exclusive_key="stable:resource")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        reservation.key,
        detail="launch failed",
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    conclusions: list[tuple[str | None, ...]] = []
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: "gone",
        worktree_directory_present_fn=lambda *_args: False,
        attempt_conclusion_fn=lambda *args: record_result(conclusions, args, {
            "action": "preserved",
            "reason": "dirty-worktree",
        }),
        nudge=False,
    )

    assert sup.release_requested_bodies() == 1
    held = required(q.get_reservation(reservation.key))
    assert held.state == SpawnState.FAILED
    assert held.conclusion_state == "held"
    assert "dirty-worktree" in (held.conclusion_detail or "")
    assert len(conclusions) == 1
    assert spawn.calls == []

    replacement_task = q.create(
        "replacement",
        exclusive_key="stable:resource",
    )
    replacement, acquired = q.reserve_spawn(replacement_task.id)
    assert acquired is True
    assert replacement.exclusive_key == "stable:resource"
    assert replacement.worktree == "wt-created"
    assert replacement.session_handle is None
    assert replacement.attempt == 1

    assert sup.release_requested_bodies() == 0
    assert len(conclusions) == 1
    assert required(q.get_reservation(reservation.key)).state == SpawnState.FAILED
    assert spawn.calls == []


def test_pending_cleanup_fences_successor_before_claim(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    old = q.create("old", exclusive_key="stable:resource")
    old_reservation, _ = q.reserve_spawn(old.id)
    q.record_spawn_worktree(
        old_reservation.key,
        "wt-shared",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        old_reservation.key,
        disposition="failed",
        finished_startup_revision=required(
            q.get_reservation(old_reservation.key)
        ).startup_revision,
    )
    q.retire_spawn(
        old_reservation.key,
        exact_absence=True,
        conclusion_state="pending",
        conclusion_detail='{"action":"failed","reason":"lifecycle-lock-busy"}',
    )
    successor = q.create("successor", exclusive_key="stable:resource")
    successor_reservation, acquired = q.reserve_spawn(successor.id)
    assert acquired is True
    assert successor_reservation.state == SpawnState.RESERVING
    assert successor_reservation.worktree is None
    conclusions: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        attempt_conclusion_fn=lambda *args: record_result(conclusions, args, {
            "action": "removed",
            "reason": "must-not-run",
        }),
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    complete = required(q.get_reservation(old_reservation.key))
    assert complete.state == SpawnState.FAILED
    assert complete.conclusion_state == "complete"
    assert conclusions
    assert required(q.get_reservation(successor_reservation.key)).state == SpawnState.RESERVING


def test_terminal_cleanup_retry_does_not_touch_reserving_successor(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    old = q.create(
        "old",
        labels=["review"],
        exclusive_key="stable:resource",
    )
    old_reservation, _ = q.reserve_spawn(old.id)
    q.record_spawn(
        old_reservation.key,
        session_handle="session-old",
        worktree="wt-shared",
    )
    q.claim_one("worker-old", task_id=old.id)
    q.start(old.id, "worker-old")
    q.complete(old.id, "worker-old", result_ref="result/old")
    q.settle_spawn(
        old_reservation.key,
        conclusion_state="pending",
        conclusion_detail='{"action":"failed","reason":"lifecycle-lock-busy"}',
    )
    successor = q.create("successor", exclusive_key="stable:resource")
    successor_reservation, acquired = q.reserve_spawn(successor.id)
    assert acquired is True
    assert successor_reservation.worktree is None
    conclusions: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        disposable_cli_labels=["review"],
        conclusion_fn=lambda *args: record_result(conclusions, args, {
            "action": "removed",
            "reason": "must-not-run",
        }),
        nudge=False,
    )

    assert sup.reconcile() == 0
    complete = required(q.get_reservation(old_reservation.key))
    assert complete.conclusion_state == "complete"
    assert conclusions


def test_cleanup_retry_does_not_touch_settled_successor_worktree(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    old = q.create("old", exclusive_key="stable:resource")
    old_reservation, _ = q.reserve_spawn(old.id)
    q.record_spawn_worktree(
        old_reservation.key,
        "wt-shared",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        old_reservation.key,
        disposition="failed",
        finished_startup_revision=required(
            q.get_reservation(old_reservation.key)
        ).startup_revision,
    )
    q.retire_spawn(
        old_reservation.key,
        exact_absence=True,
        conclusion_state="pending",
        conclusion_detail='{"action":"failed","reason":"lifecycle-lock-busy"}',
    )
    successor = q.create("successor", exclusive_key="stable:resource")
    successor_reservation, acquired = q.reserve_spawn(successor.id)
    assert acquired is True
    with q._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET worktree = ?, "
            "inherited_worktree = ? WHERE key = ?",
            ("wt-shared", "wt-shared", successor_reservation.key),
        )
    q.settle_spawn(successor_reservation.key)
    conclusions: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        attempt_conclusion_fn=lambda *args: record_result(conclusions, args, {
            "action": "removed",
            "reason": "must-not-run",
        }),
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    held = required(q.get_reservation(old_reservation.key))
    assert held.conclusion_state == "held"
    held_detail = json.loads(held.conclusion_detail or "{}")
    assert held_detail["reason"] == "worktree-carried-by-newer-reservation"
    assert held_detail["blocking_reservation_key"] == successor_reservation.key
    assert conclusions == []


def test_cleanup_retry_tracks_successor_inherited_worktree_after_replacement(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    old = q.create("old", exclusive_key="stable:resource")
    old_reservation, _ = q.reserve_spawn(old.id)
    q.record_spawn_worktree(
        old_reservation.key,
        "wt-shared",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.request_spawn_release(
        old_reservation.key,
        disposition="failed",
        finished_startup_revision=required(
            q.get_reservation(old_reservation.key)
        ).startup_revision,
    )
    q.retire_spawn(
        old_reservation.key,
        exact_absence=True,
        conclusion_state="pending",
        conclusion_detail='{"action":"failed","reason":"lifecycle-lock-busy"}',
    )
    successor = q.create("successor", exclusive_key="stable:resource")
    successor_reservation, acquired = q.reserve_spawn(successor.id)
    assert acquired is True
    with q._connect() as conn:
        conn.execute(
            "UPDATE spawn_reservations SET worktree = ?, "
            "inherited_worktree = ? WHERE key = ?",
            ("wt-shared", "wt-shared", successor_reservation.key),
        )
    q.record_spawn_worktree(
        successor_reservation.key,
        "wt-replacement",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    assert required(q.get_reservation(successor_reservation.key)).worktree == (
        "wt-replacement"
    )
    conclusions: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        attempt_conclusion_fn=lambda *args: record_result(conclusions, args, {
            "action": "removed",
            "reason": "must-not-run",
        }),
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    held = required(q.get_reservation(old_reservation.key))
    assert held.conclusion_state == "held"
    assert conclusions == []


def test_unknown_release_liveness_keeps_exclusive_key_fenced(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work", exclusive_key="stable:resource")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-unknown",
        worktree="wt-review",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="settled",
    )
    replacement_task = q.create(
        "replacement",
        exclusive_key="stable:resource",
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: tracking.UNKNOWN,
        local_end_fn=lambda _sid: pytest.fail(
            "unknown liveness must not issue teardown"
        ),
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    blocked, acquired = q.reserve_spawn(replacement_task.id)
    assert acquired is False
    assert blocked.key == reservation.key
    assert blocked.state == SpawnState.RELEASING


def test_absent_local_release_calls_idempotent_end(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-stopped",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="settled",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    ended: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: tracking.GONE,
        local_end_fn=lambda sid: record_result(ended, sid, True),
        nudge=False,
    )

    assert sup.release_requested_bodies() == 1
    assert ended == ["session-stopped"]
    complete = required(q.get_reservation(reservation.key))
    assert complete.state == SpawnState.SETTLED
    assert complete.conclusion_state == "complete"
    assert complete.cleanup_claim_token


def test_retired_pending_without_worktree_claims_before_completion(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.request_spawn_release(
        reservation.key,
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    q.retire_spawn(
        reservation.key,
        exact_absence=True,
        conclusion_state="pending",
        conclusion_detail='{"action":"failed","reason":"legacy-cleanup"}',
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 0
    complete = required(q.get_reservation(reservation.key))
    assert complete.state == SpawnState.FAILED
    assert complete.conclusion_state == "complete"
    assert complete.cleanup_claim_token
    assert "reservation-has-no-worktree" in (
        complete.conclusion_detail or ""
    )


def test_failed_absent_session_end_retries_after_exclusive_release(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work", exclusive_key="stable:resource")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-shared",
        ownership="targeted",
    )
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-stopped",
        worktree="wt-shared",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="settled",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    end_results = iter([False, True])
    ended: list[str] = []

    def end_session(session_id: str) -> bool:
        ended.append(session_id)
        return next(end_results)

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: tracking.GONE,
        local_end_fn=end_session,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 1
    pending = required(q.get_reservation(reservation.key))
    assert pending.state == SpawnState.SETTLED
    assert pending.conclusion_state == "pending"
    pending_detail = json.loads(pending.conclusion_detail or "{}")
    assert pending_detail["reason"] == "retired-body-cleanup"
    assert pending_detail["session_end"]["state"] == "pending"
    assert pending_detail["session_end"]["session_id"] == "session-stopped"
    assert pending_detail["session_end"]["attempts"] == 1
    assert pending_detail["worktree_cleanup"]["state"] == "complete"

    replacement_task = q.create(
        "replacement",
        exclusive_key="stable:resource",
    )
    replacement, acquired = q.reserve_spawn(replacement_task.id)
    assert acquired is True
    assert replacement.worktree is None
    assert replacement.session_handle is None

    session_next_attempt = pending_detail["session_end"]["next_attempt_at"]
    assert sup.release_requested_bodies(
        now=session_next_attempt - 0.1
    ) == 0
    assert ended == ["session-stopped"]
    assert sup.release_requested_bodies(
        now=session_next_attempt
    ) == 0
    complete = required(q.get_reservation(reservation.key))
    assert complete.conclusion_state == "complete"
    assert ended == ["session-stopped", "session-stopped"]
    assert required(q.get_reservation(replacement.key)).state == SpawnState.RESERVING


def test_retired_session_end_failure_backs_off_and_becomes_held(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-stopped",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="settled",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    ended: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: tracking.GONE,
        local_end_fn=lambda sid: record_result(ended, sid, False),
        nudge=False,
    )

    now = 1000.0
    assert sup.release_requested_bodies(now=now) == 1
    while True:
        current = required(q.get_reservation(reservation.key))
        payload = json.loads(current.conclusion_detail or "{}")
        session = payload["session_end"]
        attempts = session["attempts"]
        assert attempts == len(ended)
        if current.conclusion_state == "held":
            assert attempts == _CONCLUSION_MAX_ATTEMPTS
            assert payload["session_end"]["state"] == "held"
            break
        assert current.conclusion_state == "pending"
        next_attempt_at = session["next_attempt_at"]
        assert sup.release_requested_bodies(
            now=next_attempt_at - 0.1
        ) == 0
        assert len(ended) == attempts
        now = next_attempt_at
        assert sup.release_requested_bodies(now=now) == 0


def test_failing_session_end_allows_worktree_cleanup_immediately(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-stopped",
        worktree="wt-created",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    ended: list[str] = []
    conclusions: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: tracking.GONE,
        local_end_fn=lambda sid: record_result(ended, sid, False),
        attempt_conclusion_fn=lambda *args: record_result(conclusions, args, {
            "action": "removed",
            "reason": "managed-gc-removed",
        }),
        nudge=False,
    )

    now = 3000.0
    assert sup.release_requested_bodies(now=now) == 1
    first = required(q.get_reservation(reservation.key))
    first_envelope = json.loads(first.conclusion_detail or "{}")
    assert conclusions
    assert first.conclusion_state == "pending"
    assert first_envelope["session_end"]["state"] == "pending"
    assert first_envelope["worktree_cleanup"]["state"] == "complete"
    while len(ended) < _CONCLUSION_MAX_ATTEMPTS:
        current = required(q.get_reservation(reservation.key))
        payload = json.loads(current.conclusion_detail or "{}")
        now = payload["session_end"]["next_attempt_at"]
        assert sup.release_requested_bodies(now=now) == 0

    pending = required(q.get_reservation(reservation.key))
    envelope = json.loads(pending.conclusion_detail or "{}")
    assert pending.conclusion_state == "held"
    assert envelope["session_end"]["state"] == "held"
    assert envelope["worktree_cleanup"]["state"] == "complete"
    assert len(conclusions) == 1


def test_due_session_cleanup_runs_while_worktree_cleanup_is_backed_off(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-stopped",
        worktree="wt-created",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    q.retire_spawn(
        reservation.key,
        exact_absence=True,
        conclusion_state="pending",
        conclusion_detail=json.dumps(
            {
                "action": "pending",
                "reason": "retired-body-cleanup",
                "cleanup_kind": "attempt",
                "session_end": {
                    "state": "pending",
                    "session_id": "session-stopped",
                    "attempts": 0,
                    "next_attempt_at": 0,
                },
                "worktree_cleanup": {
                    "state": "pending",
                    "attempts": 1,
                    "next_attempt_at": 5000,
                },
            }
        ),
    )
    ended: list[str] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        local_end_fn=lambda sid: record_result(ended, sid, True),
        attempt_conclusion_fn=lambda *_args: pytest.fail(
            "backed-off worktree cleanup must not run"
        ),
        nudge=False,
    )

    assert sup.release_requested_bodies(now=4000) == 0
    pending = required(q.get_reservation(reservation.key))
    envelope = json.loads(pending.conclusion_detail or "{}")
    assert ended == ["session-stopped"]
    assert pending.conclusion_state == "pending"
    assert envelope["session_end"]["state"] == "complete"
    assert envelope["worktree_cleanup"]["state"] == "pending"
    assert envelope["worktree_cleanup"]["next_attempt_at"] == 5000


def test_retired_worktree_cleanup_failure_backs_off_and_becomes_held(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor_http, "attachments", lambda _task: [{
        "session_id": "session-gone", "worktree_id": "wt-created", "machine": "host-a",
    }])
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="session-gone",
        worktree="wt-created",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    conclusions: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: tracking.GONE,
        attempt_conclusion_fn=lambda *args: record_result(conclusions, args, {
            "action": "failed",
            "reason": "lifecycle-lock-busy",
        }),
        nudge=False,
    )

    now = 2000.0
    assert sup.release_requested_bodies(now=now) == 1
    while True:
        current = required(q.get_reservation(reservation.key))
        payload = json.loads(current.conclusion_detail or "{}")
        worktree = payload["worktree_cleanup"]
        attempts = worktree["attempts"]
        assert attempts == len(conclusions)
        if current.conclusion_state == "held":
            assert attempts == _CONCLUSION_MAX_ATTEMPTS
            break
        assert current.conclusion_state == "pending"
        next_attempt_at = worktree["next_attempt_at"]
        assert sup.release_requested_bodies(
            now=next_attempt_at - 0.1
        ) == 0
        assert len(conclusions) == attempts
        now = next_attempt_at
        assert sup.release_requested_bodies(now=now) == 0


@pytest.mark.parametrize("settled", [False, True])
def test_released_attempt_concludes_only_after_relationship_settlement(
    q: OwnerResultQueue,
    supervisor_http: DispatchClient,
    monkeypatch: pytest.MonkeyPatch,
    settled: bool,
) -> None:
    monkeypatch.setattr(supervisor_http, "attachments", lambda _task: [{
        "session_id": "session-gone", "worktree_id": "wt-created", "machine": "host-a",
    }])
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key, "wt-created", ownership="created",
        creating_host="host-a", driver="agent-dispatch",
    )
    q.record_spawn(reservation.key, session_handle="session-gone", worktree="wt-created")
    q.request_spawn_release(
        reservation.key, disposition="settled",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    settlements: list[str] = []
    monkeypatch.setattr(
        supervisor_module, "settle_completed_relationship",
        lambda _sup, res, _task: record_result(settlements, res['key'], settled),
    )
    conclusions: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, machine="host-a",
        verdict_fn=lambda *_args: tracking.GONE,
        attempt_conclusion_fn=lambda *args: record_result(conclusions, args, {
            "action": "removed", "reason": "concluded",
        }),
        nudge=False,
    )

    sup.release_requested_bodies(now=2000.0)

    assert settlements and set(settlements) == {reservation.key}
    assert bool(conclusions) is settled


def test_gone_body_retires_before_session_cleanup_uses_capacity(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work", exclusive_key="stable:resource")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-stopped",
        worktree="wt-shared",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    replacement_task = q.create(
        "replacement",
        exclusive_key="stable:resource",
    )
    replacement_keys: list[str] = []

    def interrupted_end(_session_id: str) -> bool:
        retired = required(q.get_reservation(reservation.key))
        assert retired.state == SpawnState.FAILED
        replacement, acquired = q.reserve_spawn(replacement_task.id)
        assert acquired is True
        replacement_keys.append(replacement.key)
        return False

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: tracking.GONE,
        local_end_fn=interrupted_end,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 1
    pending = required(q.get_reservation(reservation.key))
    assert pending.state == SpawnState.FAILED
    assert pending.conclusion_state == "pending"
    assert replacement_keys


def test_gone_body_retires_before_worktree_cleanup(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="local-body:session-stopped",
        worktree="wt-created",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    conclusions: list[tuple[str | None, ...]] = []

    def conclude(*args: str | None) -> dict[str, str]:
        retired = required(q.get_reservation(reservation.key))
        assert retired.state == SpawnState.FAILED
        conclusions.append(args)
        return {"action": "removed", "reason": "managed-gc-removed"}

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: tracking.GONE,
        local_end_fn=lambda _sid: True,
        attempt_conclusion_fn=conclude,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 1
    assert conclusions
    complete = required(q.get_reservation(reservation.key))
    assert complete.state == SpawnState.FAILED
    assert complete.conclusion_state == "complete"


def test_gone_fleet_body_retires_before_worktree_cleanup(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work", exclusive_key="stable:fleet")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-fleet",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="fleet-body:host-b:session-gone",
        worktree="wt-fleet",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    conclusions: list[tuple[str | None, ...]] = []

    def conclude(*args: str | None) -> dict[str, str]:
        assert required(q.get_reservation(reservation.key)).state == SpawnState.FAILED
        conclusions.append(args)
        return {"action": "removed", "reason": "managed-gc-removed"}

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        fleet_verdict_fn=lambda _host, _sid: tracking.GONE,
        fleet_end_fn=lambda *_args: pytest.fail(
            "confirmed-gone fleet cleanup starts after retirement"
        ),
        attempt_conclusion_fn=conclude,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 1
    assert conclusions
    complete = required(q.get_reservation(reservation.key))
    assert complete.state == SpawnState.FAILED
    assert complete.conclusion_state == "complete"


def test_gone_worktree_body_retires_before_cleanup(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(supervisor_http, "attachments", lambda _task: [{
        "session_id": "session-gone", "worktree_id": "wt-gone", "machine": "host-a",
    }])
    task = q.create("work", exclusive_key="stable:worktree")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-gone",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key,
        session_handle="session-gone",
        worktree="wt-gone",
    )
    q.request_spawn_release(
        reservation.key,
        disposition="failed",
        finished_startup_revision=required(q.get_reservation(reservation.key)).startup_revision,
    )
    conclusions: list[tuple[str | None, ...]] = []

    def conclude(*args: str | None) -> dict[str, str]:
        assert required(q.get_reservation(reservation.key)).state == SpawnState.FAILED
        conclusions.append(args)
        return {"action": "removed", "reason": "managed-gc-removed"}

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        verdict_fn=lambda *_args: tracking.GONE,
        attempt_conclusion_fn=conclude,
        nudge=False,
    )

    assert sup.release_requested_bodies() == 1
    assert conclusions
    complete = required(q.get_reservation(reservation.key))
    assert complete.state == SpawnState.FAILED
    assert complete.conclusion_state == "complete"


def test_terminal_created_worktree_uses_attempt_cleanup_without_label(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(
        reservation.key,
        "wt-created",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:bridge-1",
        worktree="wt-created",
    )
    q.claim_one("host-a/wt-created", task_id=task.id)
    q.start(
        task.id,
        "host-a/wt-created",
        owner_session_id="acp-session-1",
    )
    q.complete(task.id, "host-a/wt-created")
    conclusions: list[tuple[str | None, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: "gone",
        local_acp_session_fn=lambda _sid: "acp-session-1",
        local_end_fn=lambda _sid: True,
        attempt_conclusion_fn=lambda *args: record_result(conclusions, args, {
            "action": "primed",
            "reason": "managed-gc-candidate",
        }),
        nudge=False,
    )

    assert sup.poll_once() == []
    settled = required(q.get_reservation(reservation.key))
    assert settled.state == SpawnState.SETTLED
    assert settled.conclusion_state == "complete"
    assert conclusions


def test_yielded_targeted_worktree_is_preserved_without_conclusion(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work", affinity={"worktree": "wt-target"})
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:bridge-1",
        worktree="wt-target", startup_complete=True, startup_revision=1,
    )
    q.claim_one("host-a/wt-target", task_id=task.id)
    q.start(task.id, "host-a/wt-target")
    before_retention_release(q, task.id, "host-a/wt-target")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        machine="host-a",
        local_body_verdict_fn=lambda _sid: "gone",
        local_end_fn=lambda _sid: True,
        attempt_conclusion_fn=lambda *_args: pytest.fail(
            "targeted worktree must never be concluded as dispatch-owned"
        ),
        nudge=False,
    )

    assert sup.poll_once() == [task.id]
    settled = required(q.get_reservation(reservation.key))
    assert settled.state == SpawnState.SETTLED
    assert settled.conclusion_state == "complete"
    assert "targeted-worktree" in (settled.conclusion_detail or "")


def test_yielded_cli_worker_is_not_redriven_while_release_is_pending(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="session-live",
        worktree="wt-live",
    )
    q.claim_one("host/wt-live", task_id=task.id)
    q.start(task.id, "host/wt-live")
    before_retention_release(q, task.id, "host/wt-live", exclude="worktree:wt-live")
    redriven: list[tuple[object, ...]] = []
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        verdict_fn=lambda *_args: "live",
        liveness_fn=lambda worktree, _machine: {
            "session_id": "session-live",
            "worktree_id": worktree,
        },
        redrive_fn=lambda *args: record_result(redriven, args, True),
        nudge=False,
    )

    assert sup.poll_once() == []
    assert redriven == []
    assert spawn.calls == []
    active = required(q.get_reservation(reservation.key))
    assert active.state == SpawnState.RELEASING
    assert active.release_requested is True


def test_completed_idle_exclusive_body_is_carried_to_next_episode(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("old", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="local-body:session-idle",
        worktree="wt-review",
    )
    q.claim_one("headless-owner", task_id=task.id)
    q.start(task.id, "headless-owner")
    q.complete(task.id, "headless-owner", result_ref="result/1")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: "live",
        local_body_activity_fn=lambda _sid: "IDLE",
        local_end_fn=lambda _sid: pytest.fail(
            "idle completed session should remain reusable"
        ),
        nudge=False,
    )

    assert sup.reconcile() == 1
    next_task = q.create("new", exclusive_key="review:repo:42")
    carried, acquired = q.reserve_spawn(next_task.id)
    assert acquired is True
    assert carried.worktree == "wt-review"
    assert carried.session_handle == "local-body:session-idle"


def test_completed_live_exclusive_fleet_body_is_ended_before_release(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("old", exclusive_key="review:repo:42")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key, startup_complete=True, startup_revision=1,
        session_handle="fleet-body:worker-host:session-live",
    )
    q.claim_one("fleet-owner", task_id=task.id)
    q.start(task.id, "fleet-owner")
    q.complete(task.id, "fleet-owner", result_ref="result/1")
    ended: list[tuple[str,str]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        fleet_verdict_fn=lambda _host, _sid: "live",
        fleet_activity_fn=lambda _host, _sid: "IDLE",
        fleet_end_fn=lambda host, sid: record_result(ended, (host, sid), True),
        nudge=False,
    )

    assert sup.reconcile() == 1
    assert ended == [("worker-host", "session-live")]
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SETTLED


@pytest.mark.parametrize(
    ("verdict", "expected_state"),
    [
        ("gone", SpawnState.FAILED),
        ("unknown", SpawnState.RESERVING),
    ],
)
def test_reconcile_reserving_fails_only_confirmed_gone_worktree(
    q: OwnerResultQueue, supervisor_http: DispatchClient, verdict: str, expected_state: str
) -> None:
    task = q.create("work", exclusive_key="resource:42")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(reservation.key, "wt-precreated")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        verdict_fn=lambda *_args: verdict,
        nudge=False,
    )

    expected_count = 1 if verdict == "gone" else 0
    assert sup.reconcile_reserving() == expected_count
    assert required(q.get_reservation(reservation.key)).state == expected_state


def test_reconcile_reserving_adopts_confirmed_live_worktree(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work", exclusive_key="resource:42")
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn_worktree(reservation.key, "wt-precreated")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        verdict_fn=lambda *_args: "live",
        liveness_fn=lambda worktree, _machine: {
            "session_id": "session-live",
            "worktree_id": worktree,
        },
        nudge=False,
    )

    assert sup.reconcile_reserving() == 1
    adopted = required(q.get_reservation(reservation.key))
    assert adopted.state == SpawnState.SPAWNED
    assert adopted.session_handle == "session-live"
    assert adopted.worktree == "wt-precreated"


def test_reconcile_reserving_rearms_idle_carried_local_session(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    prior_task = q.create("old", exclusive_key="resource:42")
    prior, _ = q.reserve_spawn(prior_task.id)
    q.record_spawn(
        prior.key,
        session_handle="local-body:session-idle",
        worktree="wt-review",
    )
    q.settle_spawn(prior.key)
    task = q.create("new", exclusive_key="resource:42")
    reservation, _ = q.reserve_spawn(task.id)
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        local_body_verdict_fn=lambda _sid: "live",
        local_body_activity_fn=lambda _sid: "IDLE",
        nudge=False,
    )

    assert sup.reconcile_reserving() == 1
    assert required(q.get_reservation(reservation.key)).state == SpawnState.FAILED
    fresh, acquired = q.reserve_spawn(task.id)
    assert acquired is True
    assert fresh.worktree == "wt-review"
    assert fresh.session_handle == "local-body:session-idle"


def test_reconcile_recent_reserving_without_durable_handle_stays_reserved(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(task.id, reserved_by="supervisor-test")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        supervisor_id="supervisor-test",
        verdict_fn=lambda *_args: pytest.fail(
            "unbound reservation cannot be classified"
        ),
        nudge=False,
    )

    assert sup.reconcile_reserving() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RESERVING


def test_reconcile_stale_own_reserving_without_durable_handle_fails(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(
        task.id,
        reserved_by="supervisor-test",
        now=time.time() - 601,
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        supervisor_id="supervisor-test",
        reserving_timeout=600,
        nudge=False,
    )

    assert sup.reconcile_reserving() == 1
    failed = required(q.get_reservation(reservation.key))
    assert failed.state == SpawnState.FAILED
    assert "no durable handle" in required(failed.detail)


def test_reconcile_stale_foreign_reserving_without_durable_handle_stays_reserved(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(
        task.id,
        reserved_by="supervisor-other",
        now=time.time() - 601,
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        supervisor_id="supervisor-test",
        reserving_timeout=600,
        nudge=False,
    )

    assert sup.reconcile_reserving() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RESERVING


def test_reconcile_stale_handleless_reservation_is_idempotent(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(
        task.id,
        reserved_by="supervisor-test",
        now=time.time() - 601,
    )
    first = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        supervisor_id="supervisor-test",
        reserving_timeout=600,
        nudge=False,
    )
    second = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        supervisor_id="supervisor-test",
        reserving_timeout=600,
        nudge=False,
    )

    assert first.reconcile_reserving() == 1
    assert second.reconcile_reserving() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.FAILED


def test_reconcile_stale_own_reserving_worktree_with_no_session_fails(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """Regression for a confirmed field incident: a stale, sessionless
    worktree reservation with a live-owner verdict.

    A reservation whose owner is confirmed LIVE (``verdict_fn`` returns
    ``live``) but whose worktree never actually produces a session (the
    embody/spawn call silently failed or hung) previously had no timeout at
    all in this branch -- unlike the handle-less case above, it could sit
    in ``reserving`` forever. It now shares the same ``reserving_timeout``
    bound.
    """
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(
        task.id,
        reserved_by="supervisor-test",
        now=time.time() - 601,
    )
    q.record_spawn_worktree(reservation.key, "wt-precreated")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        supervisor_id="supervisor-test",
        verdict_fn=lambda *_args: "live",
        liveness_fn=lambda _worktree, _machine: None,
        reserving_timeout=600,
        nudge=False,
    )

    assert sup.reconcile_reserving() == 1
    failed = required(q.get_reservation(reservation.key))
    assert failed.state == SpawnState.FAILED
    assert "no session" in required(failed.detail)
    # A fresh attempt is immediately eligible.
    _, acquired = q.reserve_spawn(task.id)
    assert acquired is True


def test_reconcile_young_own_reserving_worktree_with_no_session_stays_reserved(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(task.id, reserved_by="supervisor-test")
    q.record_spawn_worktree(reservation.key, "wt-precreated")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        supervisor_id="supervisor-test",
        verdict_fn=lambda *_args: "live",
        liveness_fn=lambda _worktree, _machine: None,
        reserving_timeout=600,
        nudge=False,
    )

    assert sup.reconcile_reserving() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RESERVING


def test_reconcile_stale_foreign_reserving_worktree_with_no_session_stays_reserved(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(
        task.id,
        reserved_by="supervisor-other",
        now=time.time() - 601,
    )
    q.record_spawn_worktree(reservation.key, "wt-precreated")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        supervisor_id="supervisor-test",
        verdict_fn=lambda *_args: "live",
        liveness_fn=lambda _worktree, _machine: None,
        reserving_timeout=600,
        nudge=False,
    )

    assert sup.reconcile_reserving() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RESERVING


def test_reconcile_reserving_worktree_confirmed_absent_fails(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """Regression for a confirmed field incident (PR #8133, 2026-10-05): a
    worktree-backed reservation whose target worktree never materialized on
    disk at all left ``verdict_fn`` unable to ever resolve to ``live`` or
    ``gone`` -- it stayed ``unknown`` forever, and a bare elapsed-time bound
    cannot safely treat that as permission to release, since ``UNKNOWN`` also
    covers an ordinary resolver timeout where the original worker may still
    be live. Only a **positive** absence signal -- the local
    ``agent-worktrees`` registry confirming the directory itself never
    existed, the same escalation :meth:`release_requested_bodies` already
    uses for this identical gap -- may release it, never elapsed time alone.
    """
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(task.id, reserved_by="supervisor-test")
    q.record_spawn_worktree(
        reservation.key,
        "wt-never-materialized",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        supervisor_id="supervisor-test",
        verdict_fn=lambda *_args: tracking.UNKNOWN,
        worktree_directory_present_fn=lambda _wt, _project: False,
        nudge=False,
    )

    assert sup.reconcile_reserving() == 1
    failed = required(q.get_reservation(reservation.key))
    assert failed.state == SpawnState.RELEASING
    assert "confirmed absent" in required(failed.detail)


@pytest.mark.parametrize("present", [True, None])
def test_reconcile_reserving_worktree_unresolved_absence_stays_reserved(
    q: OwnerResultQueue, supervisor_http: DispatchClient, present: bool | None
) -> None:
    """Neither a confirmed-PRESENT directory nor a still-unresolvable probe
    (``None``, e.g. the registry itself is unreachable) authorizes a
    release -- only a confirmed-absent (``False``) verdict ever does."""
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(task.id, reserved_by="supervisor-test")
    q.record_spawn_worktree(
        reservation.key,
        "wt-maybe-present",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        supervisor_id="supervisor-test",
        verdict_fn=lambda *_args: tracking.UNKNOWN,
        worktree_directory_present_fn=lambda _wt, _project: present,
        nudge=False,
    )

    assert sup.reconcile_reserving() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RESERVING


def test_reconcile_reserving_worktree_unknown_verdict_on_foreign_host_stays_reserved(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A reservation's worktree created on a DIFFERENT host must never be
    checked against this supervisor's own local registry -- that would
    probe the wrong machine's filesystem entirely and could wrongly
    conclude a still-live remote worktree is absent."""
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(task.id, reserved_by="supervisor-test")
    q.record_spawn_worktree(
        reservation.key,
        "wt-on-other-host",
        ownership="created",
        creating_host="host-b",
        driver="agent-dispatch",
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        supervisor_id="supervisor-test",
        verdict_fn=lambda *_args: tracking.UNKNOWN,
        worktree_directory_present_fn=lambda _wt, _project: pytest.fail(
            "must never probe a worktree created on a different host"
        ),
        nudge=False,
    )

    assert sup.reconcile_reserving() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RESERVING


def test_reconcile_reserving_worktree_confirmed_absent_but_owner_claimed_stays_reserved(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A task may already carry a captured owner/session even while its OWN
    reservation still sits ``reserving`` (e.g. a suspended task's own owner
    survives into a fresh re-reservation) -- confirmed absence of the
    worktree directory must never release a reservation whose task already
    has a live owner captured."""
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(task.id, reserved_by="supervisor-test")
    q.record_spawn_worktree(
        reservation.key,
        "wt-confirmed-absent-but-owned",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    q.claim_one("some-owner", task_id=task.id)
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        supervisor_id="supervisor-test",
        verdict_fn=lambda *_args: tracking.UNKNOWN,
        worktree_directory_present_fn=lambda _wt, _project: False,
        nudge=False,
    )

    assert sup.reconcile_reserving() == 0
    assert required(q.get_reservation(reservation.key)).state == SpawnState.RESERVING


def test_reconcile_reserving_worktree_replaced_between_probe_and_release_is_fenced(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """``request_spawn_release``'s atomic worktree-mismatch check only fires
    when the probed worktree is passed through -- fence the release to the
    exact worktree that was actually confirmed absent, since
    ``record_spawn_worktree`` may replace a ``reserving`` reservation's
    worktree between the probe and the subsequent release call. The real
    ``DispatchClient`` surfaces this server-side mismatch as a
    ``DispatchError`` (an HTTP error response)."""
    task = q.create("ordinary")
    reservation, _ = q.reserve_spawn(task.id, reserved_by="supervisor-test")
    q.record_spawn_worktree(
        reservation.key,
        "wt-original",
        ownership="created",
        creating_host="host-a",
        driver="agent-dispatch",
    )
    def present_fn(_wt: str, _project: str | None) -> bool:
        # Simulate a concurrent replacement landing between the probe and
        # the subsequent release call.
        q.record_spawn_worktree(
            reservation.key,
            "wt-replaced",
            ownership="created",
            creating_host="host-a",
            driver="agent-dispatch",
        )
        return False

    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        machine="host-a",
        supervisor_id="supervisor-test",
        verdict_fn=lambda *_args: tracking.UNKNOWN,
        worktree_directory_present_fn=present_fn,
        nudge=False,
    )


    assert sup.reconcile_reserving() == 0
    unchanged = required(q.get_reservation(reservation.key))
    assert unchanged.state == SpawnState.RESERVING
    assert unchanged.worktree == "wt-original"


@pytest.mark.parametrize(
    ("session_handle", "body_kind"),
    [
        ("worktree-session", "worktree"),
        ("fleet-body:host-a:fleet-session", "fleet"),
        ("local-body:local-session", "local"),
    ],
)
def test_recover_gone_ignores_suspended_bodies(
    q: OwnerResultQueue, supervisor_http: DispatchClient, session_handle: str, body_kind: str
) -> None:
    task = q.create("wait")
    reservation, acquired = q.reserve_spawn(task.id, reserved_by="supervisor")
    assert acquired is True
    q.record_spawn(
        reservation.key,
        session_handle=session_handle,
        worktree="wt-1" if body_kind == "worktree" else None,
    )
    owner = "host-a/wt-1"
    q.claim_one(owner, task_id=task.id)
    q.start(task.id, owner, owner_session_id="owner-session")
    q.suspend(task.id, owner, reason="awaiting review")
    before = required(q.get(task.id))
    probes: list[tuple[str, tuple[object, ...]]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        verdict_fn=lambda *args: record_result(probes, ('worktree', args), "gone"),
        fleet_verdict_fn=lambda *args: record_result(probes, ('fleet', args), "gone"),
        local_body_verdict_fn=lambda *args: record_result(probes, ('local', args), "gone"),
        nudge=False,
    )

    assert sup.recover_gone() == 0
    after_reservation = required(q.latest_reservation(task.id))
    assert probes == []
    assert after_reservation.state == SpawnState.SPAWNED
    assert after_reservation.attempt == 1
    assert required(q.get(task.id)).attempts == before.attempts


def test_recover_gone_releases_stale_reservation_and_respawns(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A *confirmed-gone* embody's stale reservation is released so the task is
    re-embodied (the replacement resumes from progress_log)."""
    t = q.create("work")
    spawn = _ok_spawn({"session": "local-body:sess-1", "worktree": "wt-1"})
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        verdict_fn=lambda wt, mc, sid: "gone",
        local_body_verdict_fn=lambda sid: "gone",
    )
    sup.poll_once()  # spawn #1 -> reservation SPAWNED
    assert spawn.calls == [t.id]

    # Historical GC cleared the owner before the supervisor observed the body.
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    before_retention_gc(q, t.id)
    assert required(q.get(t.id)).status == Status.QUEUED

    # next cycle: recover_gone releases the stale reservation, then re-embodies
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]  # re-spawned
    assert required(q.latest_reservation(t.id)).state == SpawnState.SPAWNED
    assert required(q.latest_reservation(t.id)).attempt == 2


def test_recover_gone_worktree_started_requeues_then_reembodies(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A worktree embody that died *while holding the task started* is requeued
    (yield on the gone owner's behalf, preserving goal + progress_log) AND its
    reservation released -- so re-embody is prompt, NOT lease-bound. Without the
    on-behalf yield the confirmed-gone owner's task would linger STARTED until the
    coordinator's lease-expiry GC requeued it (the liveness-not-lease gap this
    closes, matching the fleet/local body paths)."""
    t = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        verdict_fn=lambda wt, mc, sid: "gone", nudge=False,
    )
    assert sup.poll_once() == [t.id]  # spawn #1 -> SPAWNED (worktree wt-1)
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    assert required(q.get(t.id)).status == Status.STARTED  # leased, NOT requeued by GC

    # recover_gone: GONE -> yield (requeue) + release reservation -> re-embody,
    # all in one cycle, without waiting out the lease.
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]
    assert required(q.latest_reservation(t.id)).attempt == 2


def test_make_redrive_sender_builds_concise_seed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A re-drive targets an already-embodied worker, so its seed should pull
    the charter on demand (``concise=True``) rather than re-inline the whole
    behavioral essay a second time."""
    from agent_dispatch import bridge, embody

    captured: dict[str, object] = {}

    def fake_autopilot_worker_prompt(
        task_id: str, *, worker_id: str, route: str, **kwargs: object
    ) -> str:
        captured["task_id"] = task_id
        captured["route"] = route
        captured["kwargs"] = kwargs
        return "concise-seed"

    def fake_redrive_embodied_worker(worktree: str, prompt: str, **kwargs: object) -> bool:
        captured["worktree"] = worktree
        captured["prompt"] = prompt
        return True

    monkeypatch.setattr(embody, "autopilot_worker_prompt", fake_autopilot_worker_prompt)
    monkeypatch.setattr(bridge, "redrive_embodied_worker", fake_redrive_embodied_worker)

    redrive = make_redrive_sender(route=" --shared")
    result = redrive(
        "wt-1", None, {"id": "task-1"}, {"session_id": "s1"}, {"key": "r1"}
    )

    assert result is True
    assert captured["task_id"] == "task-1"
    assert captured["route"] == " --shared"
    assert captured["kwargs"] == {"concise": True}
    assert captured["prompt"] == "concise-seed"
    assert captured["worktree"] == "wt-1"


def test_redrive_live_spawned_worker_that_never_claimed(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A live spawned worker with a queued task is re-prompted, not duplicated."""
    task = q.create("work")
    reservation, acquired = q.reserve_spawn(task.id, reserved_by="supervisor")
    assert acquired is True
    q.record_spawn(
        reservation.key, session_handle="wt-wt-1", worktree=None,
        startup_complete=True, startup_revision=reservation.startup_revision,
    )
    redriven: list[tuple[object, ...]] = []
    spawn = _ok_spawn({"session": "new", "worktree": "new-wt"})
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        liveness_fn=lambda wt, mc: {
            "session_id": "live-session-1",
            "worktree_id": wt,
            "liveness": "idle",
        },
        redrive_fn=lambda *args: record_result(redriven, args, True),
        nudge=False,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert len(redriven) == 1
    assert redriven[0][0] == "wt-1"
    updated = required(q.get_reservation(reservation.key))
    assert updated.state == SpawnState.SPAWNED
    assert updated.worktree == "wt-1"
    assert updated.session_handle == "live-session-1"


def test_redrive_unknown_spawned_worker_is_left_reserved(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """Unknown bridge liveness is not treated as death and is not re-driven."""
    task = q.create("work")
    reservation, acquired = q.reserve_spawn(task.id, reserved_by="supervisor")
    assert acquired is True
    q.record_spawn(reservation.key, session_handle="wt-wt-1", worktree=None)
    redriven: list[tuple[object, ...]] = []
    spawn = _ok_spawn({"session": "new", "worktree": "new-wt"})
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        liveness_fn=lambda wt, mc: None,
        redrive_fn=lambda *args: record_result(redriven, args, True),
        nudge=False,
    )

    assert sup.poll_once() == []
    assert spawn.calls == []
    assert redriven == []
    assert required(q.get_reservation(reservation.key)).state == SpawnState.SPAWNED


def test_redrive_is_once_per_supervisor_process(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    task = q.create("work")
    reservation, acquired = q.reserve_spawn(task.id, reserved_by="supervisor")
    assert acquired is True
    q.record_spawn(
        reservation.key,
        startup_complete=True,
        startup_revision=1,
        session_handle="s",
        worktree="wt-1",
    )
    redriven: list[tuple[object, ...]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        max_concurrent=5,
        liveness_fn=lambda wt, mc: {"session_id": "live-session-1", "worktree_id": wt},
        redrive_fn=lambda *args: record_result(redriven, args, True),
        nudge=False,
    )

    assert sup.redrive_unclaimed_spawns() == 1
    assert sup.redrive_unclaimed_spawns() == 0
    assert len(redriven) == 1


@pytest.mark.parametrize("verdict", ["live", "unknown"])
def test_recover_leaves_live_or_unknown(
    q: OwnerResultQueue, supervisor_http: DispatchClient, verdict: str
) -> None:
    """Recovery never fires on a live or can't-tell verdict (the safety guarantee
    behind liveness-not-lease): the reservation is held, no re-spawn."""
    t = q.create("work")
    spawn = _ok_spawn({"session": "local-body:sess-1", "worktree": "wt-1"})
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        verdict_fn=lambda *_: verdict,
        local_body_verdict_fn=lambda *_: verdict,
    )
    sup.poll_once()  # spawn #1
    assert spawn.calls == [t.id]

    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    before_retention_gc(q, t.id)
    assert required(q.get(t.id)).status == Status.QUEUED

    # supervisor's OWN verdict is not 'gone' -> hold, never re-spawn on ignorance
    assert sup.poll_once() == []
    assert spawn.calls == [t.id]
    assert required(q.latest_reservation(t.id)).state == SpawnState.SPAWNED


def test_recover_disabled_holds_for_human(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """recover=False restores the old hold-for-a-human default even on a gone
    verdict."""
    t = q.create("work")
    spawn = _ok_spawn()
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        recover=False, verdict_fn=lambda *_: "gone",
    )
    sup.poll_once()  # spawn #1
    assert sup.poll_once() == []  # gone, but recovery disabled -> no re-spawn
    assert spawn.calls == [t.id]
    assert required(q.latest_reservation(t.id)).state == SpawnState.SPAWNED


# -- Slice 2: completion-claim verification ----------------------------------


def test_completion_verify_flags_empty_goal_completion(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A goal-bearing task completed with no result-ref and no progress is
    flagged in the reservation detail (held for review), not silently accepted."""
    t = q.create("goal work", goal="reach X", done_criteria="X is done")
    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.complete(t.id, "m/wt-1")  # no result-ref, no progress
    assert sup.reconcile() == 1
    res = required(q.latest_reservation(t.id))
    assert res.state == SpawnState.SETTLED
    assert "UNVERIFIED" in (res.detail or "")


def test_completion_verify_accepts_goal_with_progress(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A goal completed after recording progress verifies cleanly."""
    t = q.create("goal work", goal="reach X", done_criteria="X is done")
    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.record_progress(t.id, "m/wt-1", phase="p1", summary="did a unit of work")
    q.complete(t.id, "m/wt-1")
    assert sup.reconcile() == 1
    res = required(q.latest_reservation(t.id))
    assert res.state == SpawnState.SETTLED
    assert "UNVERIFIED" not in (res.detail or "")
    assert "progress" in (res.detail or "")


def test_completion_verify_accepts_goal_with_structured_result(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A non-null structured result is recorded completion evidence."""
    t = q.create("goal work", goal="reach X", done_criteria="X is done")
    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.complete(t.id, "m/wt-1", result={"outcome": "complete"})

    assert sup.reconcile() == 1
    detail = required(q.latest_reservation(t.id)).detail or ""
    assert "UNVERIFIED" not in detail
    assert "structured result" in detail


def test_completion_verify_ignores_one_shot_task(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A plain one-shot task (no goal) is never flagged, even with no evidence."""
    t = q.create("plain work")  # no goal
    spawn = _ok_spawn()
    sup = Supervisor(supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5)
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.complete(t.id, "m/wt-1")
    sup.reconcile()
    assert "UNVERIFIED" not in (required(q.latest_reservation(t.id)).detail or "")


# -- Slice 2: nudge-before-recover (stalled-but-live) ------------------------


def test_nudge_stalled_live_worker_once(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A confirmed-alive worker with no progress within the window is nudged
    once (cooldown prevents re-nudging the same window)."""
    t = q.create("work")
    spawn = _ok_spawn()
    sent: list[str] = []
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        stall_seconds=1.0,
        liveness_fn=lambda wt, mc: {"session": "s"},  # confirmed alive
        nudge_fn=lambda wt, mc, task: (record_result(sent, task['id'], True)),
    )
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    future = (required(q.get(t.id)).started_at or 0) + 100
    assert sup.nudge_stalled(now=future) == 1
    assert sent == [t.id]
    # cooldown: another check within the window does not re-nudge
    assert sup.nudge_stalled(now=future + 0.5) == 0


def test_nudge_skips_when_not_confirmed_alive(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A worker that is not confirmed alive is left to recovery, never nudged."""
    t = q.create("work")
    spawn = _ok_spawn()
    sent: list[int] = []
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        stall_seconds=1.0,
        liveness_fn=lambda wt, mc: None,  # not confirmed alive
        nudge_fn=lambda wt, mc, task: (record_result(sent, 1, True)),
    )
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    assert sup.nudge_stalled(now=(required(q.get(t.id)).started_at or 0) + 100) == 0
    assert sent == []


# -- push-driven turn-state wake ---------------------------------------------


def test_wait_for_turn_end_uses_push_client_without_polling(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reactive path waits on one aggregate client and never probes workers."""
    class _Wake(SupervisorEventWake):
        def __init__(self) -> None:
            self.waits: list[float] = []

        def wait(self, timeout: float) -> bool:
            self.waits.append(timeout)
            return True

        def update(self, subscriptions: Iterable[BridgeSubscription]) -> None:
            pass

        def acknowledge(self) -> None:
            pass

        def close(self) -> None:
            pass

    event_wake = _Wake()
    task = q.create("work")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        max_concurrent=5,
        reactive=True,
        event_wake=event_wake,
    )
    sup.poll_once()
    q.claim_one("m/wt-1", task_id=task.id, machine="m", worktree="wt-1")
    q.start(task.id, "m/wt-1")
    monkeypatch.setattr(
        tracking,
        "resolve_live_session",
        lambda *_args, **_kwargs: pytest.fail("turn-state polling is forbidden"),
    )
    assert sup.wait_for_turn_end(30.0) is True
    assert event_wake.waits == [30.0]


def test_wait_for_turn_end_ignores_retired_reactive_flag(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """reactive=False performs exactly one fixed-interval sleep."""
    sup = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5, reactive=False
    )
    slept: list[float] = []
    assert sup.wait_for_turn_end(30.0, sleep=slept.append) is False
    assert slept == [30.0]


def test_wait_for_turn_end_zero_interval_yields(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO)
    slept: list[float] = []

    assert sup.wait_for_turn_end(0.0, sleep=slept.append) is False
    assert slept == [0.0]


def test_wait_for_turn_end_rejects_negative_interval(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO)
    slept: list[float] = []

    with pytest.raises(ValueError, match="interval must be non-negative"):
        sup.wait_for_turn_end(-0.1, sleep=slept.append)
    assert slept == []


def test_serve_uses_one_full_interval_wait(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5)
    polls = 0
    waits: list[float] = []

    def poll_once() -> list[str]:
        nonlocal polls
        polls += 1
        if polls == 2:
            raise KeyboardInterrupt
        return []

    monkeypatch.setattr(sup, "poll_once", poll_once)
    monkeypatch.setattr(
        sup, "wait_for_turn_end", lambda timeout: record_result(waits, timeout, False)
    )

    sup.serve(interval=30.0)

    assert waits == [30.0]


def test_supervisor_syncs_fleet_subscriptions_and_acks_after_cycle(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    class _Wake(SupervisorEventWake):
        def __init__(self) -> None:
            self.calls: list[tuple[str] | tuple[str, tuple[BridgeSubscription, ...]]] = []

        def wait(self, timeout: float) -> bool:
            return False

        def update(self, subscriptions: Iterable[BridgeSubscription]) -> None:
            self.calls.append(("update", tuple(subscriptions)))

        def acknowledge(self) -> None:
            self.calls.append(("ack",))

        def close(self) -> None:
            pass

    wake = _Wake()
    task = q.create("work")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_fleet_spawn("fleet-body:host-a:session-a"),
        repo=TEST_REPO,
        max_concurrent=5,
        reactive=True,
        supervisor_id="registration-a",
        event_wake=wake,
    )

    assert sup.poll_once() == [task.id]

    assert wake.calls[0] == ("ack",)
    update = wake.calls[1]
    assert len(update) == 2
    subscriptions = update[1]
    assert len(subscriptions) == 1
    assert subscriptions[0].host == "host-a"
    assert subscriptions[0].session_id == "session-a"
    assert subscriptions[0].caller_id.startswith("agent-dispatch:")

    q.claim_one("host-a/wt-1", task_id=task.id, machine="host-a", worktree="wt-1")
    q.start(task.id, "host-a/wt-1")
    q.complete(task.id, "host-a/wt-1")
    sup.poll_once()

    assert wake.calls[-2:] == [("ack",), ("update", ())]


# -- Slice 5: headless fleet-body recovery (confirmed-gone over SSH) ----------


def _fleet_spawn(handle_session: str) -> RecordingSpawn:
    return _ok_spawn({"session": handle_session, "worktree": None})


def test_parse_fleet_body_handle_decodes_host_and_session() -> None:
    from agent_dispatch.spawn_factories import _parse_fleet_body_handle

    assert _parse_fleet_body_handle("fleet-body:anomalous-potato-wsl:brg-9") == (
        "anomalous-potato-wsl", "brg-9",
    )
    # non-fleet handles (worktree embody, synthetic owner, empty) -> None
    assert _parse_fleet_body_handle("wt-1") is None
    assert _parse_fleet_body_handle("fleet-t1-abc123") is None
    assert _parse_fleet_body_handle(None) is None
    assert _parse_fleet_body_handle("fleet-body:onlyhost") is None


def test_recover_gone_fleet_body_releases_for_reembody(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A *confirmed-gone* headless fleet body (no worktree; a bridge-session
    recovery handle) is recovered via the fleet verdict probe: its reservation is
    released so the next cycle re-embodies it (resuming from progress_log)."""
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:anomalous-potato-wsl:brg-1")
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        fleet_verdict_fn=lambda host, sid: "gone",
        # the worktree verdict path must NOT be consulted for a fleet handle:
        verdict_fn=lambda *a: "live",
        nudge=False,
    )
    assert sup.poll_once() == [t.id]  # spawn #1 -> SPAWNED w/ fleet-body handle
    assert required(q.latest_reservation(t.id)).state == SpawnState.SPAWNED
    assert (
        required(q.latest_reservation(t.id)).session_handle
        == "fleet-body:anomalous-potato-wsl:brg-1"
    )

    # next cycle: recover_gone sees the fleet body GONE -> releases -> re-embodies
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]
    assert required(q.latest_reservation(t.id)).attempt == 2


@pytest.mark.parametrize("gc_first", [False, True])
def test_recover_gone_fleet_body_started_requeues_then_reembodies(
    q: OwnerResultQueue, supervisor_http: DispatchClient, gc_first: bool
) -> None:
    """A fleet body that died *while holding the task started* is requeued (yield
    on the dead owner's behalf, preserving goal + progress_log) AND its reservation
    released -- so re-embody is prompt, not lease-bound."""
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:h:brg-5")
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        fleet_verdict_fn=lambda host, sid: "gone", nudge=False,
    )
    assert sup.poll_once() == [t.id]                 # spawn #1
    q.claim_one("fleet-o", task_id=t.id)             # body claimed + started it
    started = q.start(t.id, "fleet-o", owner_session_id="original-native" if gc_first else None)
    assert required(q.get(t.id)).status == Status.STARTED

    before = required(q.latest_reservation(t.id))
    if gc_first:
        counts = q.reconcile_liveness(headless_fleet_verdict=lambda host, sid: "gone")
        _assert_gc_left_recovery_to_supervisor(q, started, before, counts)

    assert sup.poll_once() == [t.id]
    assert required(q.get_reservation(before.key)).state == SpawnState.FAILED
    assert spawn.calls == [t.id, t.id]
    assert required(q.latest_reservation(t.id)).attempt == 2


@pytest.mark.parametrize("verdict", ["live", "unknown"])
def test_recover_fleet_body_leaves_live_or_unknown(
    q: OwnerResultQueue, supervisor_http: DispatchClient, verdict: str
) -> None:
    """A fleet body that is live or can't-tell is never recovered (no double-spawn
    of an alive body): the reservation is held, no re-spawn."""
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:h:brg-2")
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        fleet_verdict_fn=lambda host, sid: verdict, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    assert sup.poll_once() == []  # held -> not re-spawned
    assert spawn.calls == [t.id]
    assert required(q.latest_reservation(t.id)).state == SpawnState.SPAWNED
    assert required(q.latest_reservation(t.id)).attempt == 1


def test_hold_live_leases_heartbeats_confirmed_live_fleet_body(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed-live fleet body's origin lease is heartbeated so a live-but-quiet
    body isn't wrongly re-embodied (its lease can't expire under it)."""
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:h:brg-3")
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        fleet_verdict_fn=lambda host, sid: "live", recover=False, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    # the fleet body claimed + started the task under its synthetic owner
    q.claim_one("fleet-o", task_id=t.id)
    q.start(t.id, "fleet-o")

    beats: list[tuple[str, str]] = []
    real_hb = supervisor_http.heartbeat
    def heartbeat(tid: str, wid: str) -> dict[str, object]:
        beats.append((tid, wid))
        return real_hb(tid, wid)

    monkeypatch.setattr(supervisor_http, "heartbeat", heartbeat)
    assert sup.hold_live_leases() == 1
    assert beats == [(t.id, "fleet-o")]


def test_hold_live_leases_skips_unknown_fleet_body(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A can't-tell fleet body is NOT heartbeated (its lease rides its course; a
    genuinely-dead body's lease then expires for recovery)."""
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:h:brg-4")
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        fleet_verdict_fn=lambda host, sid: "unknown", recover=False, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    q.claim_one("fleet-o", task_id=t.id)
    q.start(t.id, "fleet-o")

    beats: list[tuple[str, str]] = []
    monkeypatch.setattr(supervisor_http, "heartbeat", lambda tid, wid: beats.append((tid, wid)))
    assert sup.hold_live_leases() == 0
    assert beats == []


def test_supervisor_publishes_fleet_body_activity(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = q.create("work")
    spawn = _fleet_spawn("fleet-body:h:brg-activity")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        heartbeat=False,
        publish_activity=True,
        fleet_activity_fn=lambda host, sid: "STALLED",
        fleet_verdict_fn=lambda host, sid: "live",
        recover=False,
        nudge=False,
    )
    assert sup.poll_once() == [t.id]
    assert required(q.get(t.id)).activity == "ACTIVE"  # immediate spawn observation
    assert sup.hold_live_leases() == 0
    assert required(q.get(t.id)).activity == "STALLED"


def test_hold_live_leases_does_not_observe_suspended_fleet_body(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    t = q.create("dormant")
    spawn = _fleet_spawn("fleet-body:h:brg-dormant")
    activity_calls: list[tuple[str, str]] = []
    verdict_calls: list[tuple[str, str]] = []
    heartbeat_calls: list[tuple[str, str]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        publish_activity=True,
        fleet_activity_fn=lambda host, sid: activity_calls.append((host, sid)),
        fleet_verdict_fn=lambda host, sid: record_result(
            verdict_calls, (host, sid), tracking.UNKNOWN
        ),
        recover=False,
        nudge=False,
    )
    assert sup.poll_once() == [t.id]
    q.claim_one("fleet-o", task_id=t.id)
    q.start(t.id, "fleet-o")
    q.suspend(t.id, "fleet-o", reason="waiting")
    monkeypatch.setattr(
        supervisor_http,
        "heartbeat",
        lambda tid, wid: heartbeat_calls.append((tid, wid)),
    )

    assert sup.hold_live_leases() == 0
    assert activity_calls == []
    assert verdict_calls == []
    assert heartbeat_calls == []
    assert required(q.get(t.id)).activity is None


# -- local headless-body recovery (confirmed-gone on THIS host, no SSH) --------
#
# The local analog of the fleet-body slice above: a headless body embodied on
# this machine records a `local-body:<bridge-session-id>` recovery handle, so an
# ended/cancelled body is liveness-recovered instead of orphaning its `spawned`
# reservation and starving the label's concurrency slot (the #4433 fix).


def _local_spawn(handle_session: str) -> RecordingSpawn:
    return _ok_spawn({"session": handle_session, "worktree": None})


def test_parse_local_body_handle_decodes_session() -> None:
    from agent_dispatch.spawn_factories import _parse_local_body_handle

    assert _parse_local_body_handle("local-body:brg-9") == "brg-9"
    # non-local handles (worktree embody, fleet body, synthetic owner, empty)
    assert _parse_local_body_handle("wt-1") is None
    assert _parse_local_body_handle("fleet-body:h:brg-1") is None
    assert _parse_local_body_handle("headless-abc123") is None
    assert _parse_local_body_handle(None) is None
    assert _parse_local_body_handle("local-body:") is None


def test_recover_gone_local_body_releases_for_reembody(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A *confirmed-gone* local headless body (no worktree; a `local-body:` bridge
    handle) is recovered via the local verdict probe: its reservation is released
    so the next cycle re-embodies it -- freeing the slot instead of orphaning it."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-1")
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        local_body_verdict_fn=lambda sid: "gone",
        # neither the fleet nor the worktree verdict path applies to a local handle:
        fleet_verdict_fn=lambda host, sid: "live",
        verdict_fn=lambda *a: "live",
        nudge=False,
    )
    assert sup.poll_once() == [t.id]  # spawn #1 -> SPAWNED w/ local-body handle
    assert required(q.latest_reservation(t.id)).state == SpawnState.SPAWNED
    assert required(q.latest_reservation(t.id)).session_handle == "local-body:brg-1"

    # next cycle: recover_gone sees the local body GONE -> releases -> re-embodies
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]
    assert required(q.latest_reservation(t.id)).attempt == 2


@pytest.mark.parametrize("gc_first", [False, True])
def test_recover_gone_local_body_started_requeues_then_reembodies(
    q: OwnerResultQueue, supervisor_http: DispatchClient, gc_first: bool
) -> None:
    """A local body that died *while holding the task started* is requeued (yield
    on its behalf, preserving goal + progress_log) AND its reservation released --
    so re-embody is prompt, not lease-bound."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-5")
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        local_body_verdict_fn=lambda sid: "gone", nudge=False,
    )
    assert sup.poll_once() == [t.id]                 # spawn #1
    q.claim_one("local-o", task_id=t.id)             # body claimed + started it
    started = q.start(t.id, "local-o", owner_session_id="original-native" if gc_first else None)
    assert required(q.get(t.id)).status == Status.STARTED

    before = required(q.latest_reservation(t.id))
    if gc_first:
        counts = q.reconcile_liveness(headless_local_verdict=lambda sid: "gone")
        _assert_gc_left_recovery_to_supervisor(q, started, before, counts)

    assert sup.poll_once() == [t.id]
    assert required(q.get_reservation(before.key)).state == SpawnState.FAILED
    assert spawn.calls == [t.id, t.id]
    assert required(q.latest_reservation(t.id)).attempt == 2


def _assert_gc_left_recovery_to_supervisor(
    q: OwnerResultQueue, started: Task, before: SpawnReservation, counts: dict[str, int],
) -> None:
    current = required(q.get(started.id))
    assert counts["checked"] == counts["gone"] == 1
    assert counts["suspended"] == counts["requeued"] == counts["dead_lettered"] == 0
    assert current.last_liveness == "gone" and current.status == Status.STARTED
    assert (current.owner, current.owner_session_id, current.generation) == (
        started.owner, "original-native", started.generation,
    )
    assert before.state == SpawnState.SPAWNED and q.get_reservation(before.key) == before


def test_productive_gone_local_body_does_not_burn_spawn_failure_budget(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A one-turn body that durably posted progress ended successfully. Its gone
    reservation is settled, not failed, so a later steer can re-embody even when
    max_attempts=1."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-productive")
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        max_attempts=1,
        local_body_verdict_fn=lambda sid: "gone",
        nudge=False,
    )
    assert sup.poll_once() == [t.id]
    first = required(q.latest_reservation(t.id))
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")
    q.record_progress(
        t.id,
        "local-o",
        phase="awaiting steer",
        summary="posted review card",
        now=first.reserved_at + 1,
    )

    assert sup.recover_gone() == 1
    assert required(q.get(t.id)).status == Status.QUEUED
    rows: list[SpawnReservation] = q.list_reservations(task_id=t.id)
    first = rows[0]
    assert first.state == SpawnState.SETTLED
    assert q.list_reservations(task_id=t.id, state=SpawnState.FAILED) == []

    # max_attempts=1 applies to failed spawns, not successful one-turn rounds.
    assert sup.poll_once() == [t.id]
    assert spawn.calls == [t.id, t.id]


def test_interactive_awaiting_steer_stays_suspended_until_submit(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    """A blocking card parks an interactive task without spawning a replacement;
    the operator answer resumes its existing owner."""
    from agent_dispatch import steering

    t = q.create("review")
    q.claim_one("reviewer", task_id=t.id)
    q.start(t.id, "reviewer")
    q.set_card(
        t.id,
        "reviewer",
        card=steering.build_card(
            request_input=steering.parse_request_input("feedback:textarea")
        ),
    )
    q.yield_task(t.id, "reviewer")
    spawn = _ok_spawn({"session": "replacement", "worktree": None})
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        nudge=False,
    )

    assert required(q.get(t.id)).status == Status.SUSPENDED
    assert required(q.get(t.id)).awaiting_steer is True
    assert sup.poll_once() == []
    assert spawn.calls == []

    q.submit_steer(t.id, fields={"feedback": ""}, sender="operator")
    assert required(q.get(t.id)).awaiting_steer is False
    assert required(q.get(t.id)).status == Status.STARTED
    assert sup.poll_once() == []
    assert spawn.calls == []


@pytest.mark.parametrize("verdict", ["live", "unknown"])
def test_recover_local_body_leaves_live_or_unknown(
    q: OwnerResultQueue, supervisor_http: DispatchClient, verdict: str
) -> None:
    """A local body that is live or can't-tell is never recovered (no double-spawn
    of an alive body): the reservation is held, no re-spawn."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-2")
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        local_body_verdict_fn=lambda sid: verdict, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    assert sup.poll_once() == []  # held -> not re-spawned
    assert spawn.calls == [t.id]
    assert required(q.latest_reservation(t.id)).state == SpawnState.SPAWNED
    assert required(q.latest_reservation(t.id)).attempt == 1


def test_hold_live_leases_heartbeats_confirmed_live_local_body(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A confirmed-live local body's lease is heartbeated so a live-but-quiet body
    isn't wrongly re-embodied (its lease can't expire under it)."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-3")
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        local_body_verdict_fn=lambda sid: "live", recover=False, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")

    beats: list[tuple[str, str]] = []
    real_hb = supervisor_http.heartbeat
    def heartbeat(tid: str, wid: str) -> dict[str, object]:
        beats.append((tid, wid))
        return real_hb(tid, wid)

    monkeypatch.setattr(supervisor_http, "heartbeat", heartbeat)
    assert sup.hold_live_leases() == 1
    assert beats == [(t.id, "local-o")]


def test_supervisor_publishes_local_body_activity_while_task_is_queued(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Activity is independent from phase: a spawned body may execute before it
    claims, so a queued task can legitimately read ACTIVE."""
    from agent_dispatch import tracking

    t = q.create("work")
    spawn = _local_spawn("local-body:brg-activity")
    sessions = [{
        "session_id": "brg-activity",
        "status": "running",
        "liveness": "active",
    }]
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda: sessions)
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        heartbeat=False,
        publish_activity=True,
        recover=False,
        nudge=False,
    )
    assert sup.poll_once() == [t.id]
    assert required(q.get(t.id)).status == Status.QUEUED
    assert required(q.get(t.id)).activity == "ACTIVE"

    assert sup.hold_live_leases() == 0
    assert required(q.get(t.id)).activity == "ACTIVE"
    sessions[0] = {
        "session_id": "brg-activity",
        "status": "idle",
        "liveness": "idle",
    }
    assert sup.hold_live_leases() == 0
    assert required(q.get(t.id)).activity == "IDLE"


def test_cli_idle_snapshot_before_start_cannot_restore_stale_activity(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = q.create("review", now=10)
    reservation, _ = q.reserve_spawn(task.id, now=11)
    q.record_spawn(
        reservation.key, session_handle="native-cli", worktree="wt-cli", now=12,
    )
    q.claim_one("machine/wt-cli", task_id=task.id, machine="machine", worktree="wt-cli", now=13)
    q.set_activity(task.id, "IDLE", reservation_key=reservation.key, now=14)
    published: list[tuple[str | None, float | None]] = []
    set_activity = supervisor_http.set_activity
    def record(
        task_id: str,
        activity: str | None,
        *,
        reservation_key: str,
        observed_at: float | None = None,
        admission_fence: dict[str, object] | None = None,
    ) -> dict[str, object]:
        published.append((activity, observed_at))
        return set_activity(task_id, activity, reservation_key=reservation_key,
                            observed_at=observed_at, admission_fence=admission_fence)
    monkeypatch.setattr(supervisor_http, "set_activity", record)
    monkeypatch.setattr(time, "time", lambda: 15)
    def liveness(worktree: str, machine: str | None) -> dict[str, str]:
        assert worktree == "wt-cli"
        # The query started at 15 and captured idle, but start happens before
        # its response reaches the supervisor and establishes new admission.
        q.start(task.id, "machine/wt-cli", owner_session_id="native-cli", now=16)
        return {"session_id": "native-cli", "status": "idle", "liveness": "idle"}
    supervisor = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO,
        liveness_fn=liveness, publish_activity=True, heartbeat=False,
        recover=False, nudge=False,
    )
    supervisor.hold_live_leases()
    assert published == [("IDLE", 15)]
    current = required(q.get(task.id))
    assert current.status == Status.STARTED
    assert current.owner_session_id == "native-cli"
    assert current.activity is None


def test_hold_live_leases_degrades_when_local_session_listing_fails(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_dispatch import tracking

    t = q.create("work")
    spawn = _local_spawn("local-body:brg-unavailable")
    monkeypatch.setattr(tracking, "list_local_body_sessions", lambda: [])
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        heartbeat=False,
        publish_activity=True,
        recover=False,
        nudge=False,
    )
    assert sup.poll_once() == [t.id]

    assert sup.hold_live_leases() == 0
    assert required(q.get(t.id)).activity is None


def test_hold_live_leases_does_not_observe_suspended_local_body(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agent_dispatch import tracking

    t = q.create("dormant")
    spawn = _local_spawn("local-body:brg-dormant")
    session_list_calls: list[bool] = []
    verdict_calls: list[str] = []
    heartbeat_calls: list[tuple[str, str]] = []
    sup = Supervisor(
        supervisor_http,
        spawn_fn=spawn,
        repo=TEST_REPO,
        max_concurrent=5,
        publish_activity=True,
        local_body_verdict_fn=lambda sid: record_result(verdict_calls, sid, tracking.UNKNOWN),
        recover=False,
        nudge=False,
    )
    assert sup.poll_once() == [t.id]
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")
    q.suspend(t.id, "local-o", reason="waiting")
    monkeypatch.setattr(
        tracking,
        "list_local_body_sessions",
        lambda: record_result(session_list_calls, True, []),
    )
    monkeypatch.setattr(
        supervisor_http,
        "heartbeat",
        lambda tid, wid: heartbeat_calls.append((tid, wid)),
    )

    assert sup.hold_live_leases() == 0
    assert session_list_calls == []
    assert verdict_calls == []
    assert heartbeat_calls == []
    assert required(q.get(t.id)).activity is None


def test_hold_live_leases_skips_unknown_local_body(
    q: OwnerResultQueue, supervisor_http: DispatchClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A can't-tell local body is NOT heartbeated (its lease rides its course; a
    genuinely-dead body's lease then expires for recovery)."""
    t = q.create("work")
    spawn = _local_spawn("local-body:brg-4")
    sup = Supervisor(
        supervisor_http, spawn_fn=spawn, repo=TEST_REPO, max_concurrent=5,
        local_body_verdict_fn=lambda sid: "unknown", recover=False, nudge=False,
    )
    assert sup.poll_once() == [t.id]
    q.claim_one("local-o", task_id=t.id)
    q.start(t.id, "local-o")

    beats: list[tuple[str, str]] = []
    monkeypatch.setattr(supervisor_http, "heartbeat", lambda tid, wid: beats.append((tid, wid)))
    assert sup.hold_live_leases() == 0
    assert beats == []


# -- evaluator pass (service-driven loop advancement) -----------------------


def _spec_evaluator(rules: list[dict[str, object]]) -> SpecEvaluator:

    return SpecEvaluator({"rules": rules})


_REVIEWER_DONE_RULE: dict[str, object] = {
    "on": "task.submitted",
    "when": {"labels_any": ["recipe:reviewer"]},
    "emit": {
        "title_template": "unstick follow-up for {title}",
        "labels": ["recipe:conflict-resolution"],
        "dedup_template": "eval:conflict:{task_id}",
    },
}


def _complete(q: OwnerResultQueue, title: str, *, labels: list[str] | None = None,
              evaluator_ref: str | None = None) -> Task:
    t = q.create(
        title, labels=labels or [], require_verification=True, evaluator_ref=evaluator_ref
    )
    q.claim_one("m/wt-1", task_id=t.id, machine="m", worktree="wt-1")
    q.start(t.id, "m/wt-1")
    q.complete(t.id, "m/wt-1")
    with sqlite3.connect(q.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET require_verification = 0 WHERE id = ?",
            (t.id,),
        )
    return t


def _conflict_followups(q: OwnerResultQueue) -> list[Task]:
    return [
        t for t in q.list(repo=TEST_REPO, status="queued")
        if "recipe:conflict-resolution" in (t.labels or [])
    ]


def test_no_evaluator_pass_is_noop(q: OwnerResultQueue, supervisor_http: DispatchClient) -> None:
    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO)
    assert sup.advance_via_evaluator() == 0


def test_evaluator_emits_followup_on_completed(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = _complete(q, "review o/n#42", labels=["recipe:reviewer"])
    sup = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
    )
    assert sup.advance_via_evaluator() == 1
    fus = _conflict_followups(q)
    assert len(fus) == 1
    assert fus[0].title == "unstick follow-up for review o/n#42"
    assert fus[0].dedup_key == f"eval:conflict:{t.id}"
    assert fus[0].source == "evaluator"


def test_evaluator_ignores_non_matching_terminal(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    _complete(q, "some other task", labels=["kind:misc"])  # no recipe:reviewer
    sup = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
    )
    assert sup.advance_via_evaluator() == 0
    assert _conflict_followups(q) == []


def test_evaluator_ref_consumes_only_producer_associated_tasks(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    _complete(
        q, "mine", labels=["recipe:reviewer"], evaluator_ref="review-loop"
    )
    _complete(
        q, "other", labels=["recipe:reviewer"], evaluator_ref="other-loop"
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
        evaluator_ref="review-loop",
    )
    assert sup.advance_via_evaluator() == 1
    assert [t.title for t in _conflict_followups(q)] == [
        "unstick follow-up for mine"
    ]


def test_unscoped_evaluator_does_not_consume_associated_tasks(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    _complete(
        q, "associated", labels=["recipe:reviewer"], evaluator_ref="review-loop"
    )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
    )
    assert sup.advance_via_evaluator() == 0
    assert _conflict_followups(q) == []


def test_evaluator_filter_applies_before_terminal_result_limit(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    mine = _complete(
        q, "mine", labels=["recipe:reviewer"], evaluator_ref="review-loop"
    )
    for index in range(3):
        _complete(
            q,
            f"other-{index}",
            labels=["recipe:reviewer"],
            evaluator_ref="other-loop",
        )
    sup = Supervisor(
        supervisor_http,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
        evaluator_ref="review-loop",
        evaluate_limit=1,
    )
    assert sup.advance_via_evaluator() == 1
    followup = _conflict_followups(q)[0]
    assert followup.dedup_key == f"eval:conflict:{mine.id}"


def test_evaluator_fires_each_task_once_per_process(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    _complete(q, "review o/n#7", labels=["recipe:reviewer"])
    sup = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
    )
    assert sup.advance_via_evaluator() == 1
    # second pass: the in-process guard skips the already-seen terminal task
    assert sup.advance_via_evaluator() == 0
    assert len(_conflict_followups(q)) == 1


def test_evaluator_dedup_guards_a_fresh_supervisor(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    _complete(q, "review o/n#9", labels=["recipe:reviewer"])
    ev = [_REVIEWER_DONE_RULE]
    Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO,
               evaluator=_spec_evaluator(ev)).advance_via_evaluator()
    # a brand-new supervisor (empty in-process guard) re-emits, but the emit's
    # dedup_key collides -> no duplicate row is created.
    Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO,
               evaluator=_spec_evaluator(ev)).advance_via_evaluator()
    assert len(_conflict_followups(q)) == 1


def test_evaluator_handles_abandoned_event(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    t = q.create("drive something", labels=["recipe:goal"])
    q.abandon(t.id, permitted=True, reason="withdrawn")
    sup = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO,
        evaluator=_spec_evaluator([{
            "on": "task.abandoned",
            "emit": {"title_template": "re-carve {title}",
                     "dedup_template": "eval:recarve:{task_id}"},
        }]),
    )
    assert sup.advance_via_evaluator() == 1
    recarves = [t for t in q.list(repo=TEST_REPO, status="queued")
                if t.title == "re-carve drive something"]
    assert len(recarves) == 1


def test_evaluator_error_does_not_crash_the_cycle(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    _complete(q, "review o/n#11", labels=["recipe:reviewer"])

    class _Boom:
        def evaluate(self, event: object) -> None:
            raise RuntimeError("evaluator blew up")

    sup = Supervisor(supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, evaluator=_Boom())
    # the pass swallows the error (returns 0) and still marks the task seen
    assert sup.advance_via_evaluator() == 0
    assert sup.advance_via_evaluator() == 0  # not retried into a crash loop


def test_poll_once_runs_the_evaluator_pass(
    q: OwnerResultQueue, supervisor_http: DispatchClient
) -> None:
    _complete(q, "review o/n#13", labels=["recipe:reviewer"])
    sup = Supervisor(
        supervisor_http, spawn_fn=_ok_spawn(), repo=TEST_REPO, max_concurrent=5,
        evaluator=_spec_evaluator([_REVIEWER_DONE_RULE]),
    )
    sup.poll_once()  # the evaluator pass runs inside poll_once
    assert len(_conflict_followups(q)) == 1
