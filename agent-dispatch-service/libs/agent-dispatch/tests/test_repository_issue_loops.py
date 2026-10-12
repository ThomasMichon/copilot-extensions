"""Repository issue-loop declaration and producer contracts."""

from __future__ import annotations

import json
import math
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from agent_dispatch.registrar import RegistrarError
from agent_dispatch.registrar_discovery import read_declaration_file_set
from agent_dispatch.queue import TaskError
from agent_dispatch.repository_issue_loops import (
    AzureDevOpsProvider,
    GiteaProvider,
    GitHubProvider,
    Issue,
    _backlog_identifier,
    _forge_provider_for,
    _latest_reservations,
    _marker,
    _resource_key,
    expand_repository_issue_loop,
    occurrence_epoch,
    run_tick,
    validate_config,
)
from tests._helpers import RepoDefaultingQueue


def _config(**overrides):
    config = {
        "name": "backlog",
        "kind": "repository-issue-loop",
        "repo": "example/project",
        "source": "repository-backlog",
        "cadence_seconds": 3600,
        "tick_interval_seconds": 60,
        "quiet_period_seconds": 300,
        "include_labels": ["ready"],
        "exclude_labels": ["bootstrap", "wontfix"],
        "priority_labels": ["priority:high", "priority:medium"],
        "batch_size": 2,
        "task_label": "repository-issue-work",
        "forge": {"provider": "github", "producer_login": "issue-bot"},
        "reservation": {
            "label": "agent-reserved",
            "comment": True,
            "orphan_after_seconds": 600,
        },
        "pool": {
            "max_active_processes": 1,
            "body": {"type": "headless", "agent": "issue-worker"},
        },
    }
    config.update(overrides)
    return config


def _issue(
    number,
    *,
    labels=("ready",),
    created=10,
    updated=10,
    reservations=(),
):
    return Issue(
        number=number,
        title=f"Issue {number}",
        url=f"https://example.com/issues/{number}",
        labels=tuple(labels),
        created_at=created,
        updated_at=updated,
        reservations=tuple(reservations),
    )


def _graphql_issue(number, *, comments=()):
    return {
        "number": number,
        "title": f"Issue {number}",
        "url": f"https://example.com/issues/{number}",
        "labels": {"nodes": [{"name": "ready"}]},
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
        "comments": {"nodes": list(comments)},
    }


def _graphql_page(nodes, *, has_next=False, cursor=None):
    return {
        "data": {
            "repository": {
                "issues": {
                    "nodes": list(nodes),
                    "pageInfo": {
                        "hasNextPage": has_next,
                        "endCursor": cursor,
                    },
                }
            }
        }
    }


class FakeClient:
    def __init__(
        self,
        tasks=(),
        *,
        fail_create=False,
        commit_then_fail=False,
        fail_approve_once=False,
        fail_abandon_once=False,
        fail_bind_at=None,
    ):
        self.tasks = list(tasks)
        self.created = []
        self.fail_create = fail_create
        self.commit_then_fail = commit_then_fail
        self.fail_approve_once = fail_approve_once
        self.fail_abandon_once = fail_abandon_once
        self.fail_bind_at = fail_bind_at
        self.bind_calls = 0
        self.list_calls = []
        self.resource_reservations = {}
        self._lock = threading.Lock()

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        return list(self.tasks)

    def create(self, title, **fields):
        if self.fail_create and not self.commit_then_fail:
            raise RuntimeError("coordinator write failed")
        task = {
            "id": f"task-{len(self.created) + 1}",
            "title": title,
            "status": "proposed" if fields.get("proposed") else "queued",
            **fields,
        }
        self.created.append(task)
        self.tasks.append(task)
        if self.commit_then_fail:
            raise RuntimeError("coordinator response was lost")
        return task

    def approve(self, task_id):
        if self.fail_approve_once:
            self.fail_approve_once = False
            raise RuntimeError("approve response failed")
        task = next(task for task in self.tasks if task["id"] == task_id)
        task["status"] = "queued"
        return dict(task)

    def abandon(self, task_id, *, permitted=False, reason=None, **_kwargs):
        assert permitted
        if self.fail_abandon_once:
            self.fail_abandon_once = False
            raise RuntimeError("abandon response failed")
        task = next(task for task in self.tasks if task["id"] == task_id)
        task["status"] = "abandoned"
        task["abandon_reason"] = reason
        return dict(task)

    def get(self, task_id):
        return dict(
            next(task for task in self.tasks if task["id"] == task_id)
        )

    def acquire_resource_reservation(
        self, key, owner, *, ttl, token=None
    ):
        del ttl
        with self._lock:
            current = self.resource_reservations.get(key)
            if current is None:
                current = {
                    "key": key,
                    "owner": owner,
                    "token": f"token-{len(self.resource_reservations) + 1}",
                    "task_id": None,
                }
                self.resource_reservations[key] = current
                granted = True
            else:
                granted = (
                    current["owner"] == owner
                    and token == current["token"]
                )
            payload = dict(current)
            if not granted:
                payload.pop("token", None)
            return {"granted": granted, "reservation": payload}

    def bind_resource_reservation(self, key, owner, token, task_id):
        with self._lock:
            self.bind_calls += 1
            if self.bind_calls == self.fail_bind_at:
                raise RuntimeError("reservation bind failed")
            current = self.resource_reservations[key]
            assert current["owner"] == owner
            assert current["token"] == token
            current["task_id"] = task_id
            return dict(current)

    def release_resource_reservation(self, key, owner, token):
        with self._lock:
            current = self.resource_reservations.get(key)
            if (
                current is None
                or current["owner"] != owner
                or current["token"] != token
            ):
                return {"released": False, "key": key}
            del self.resource_reservations[key]
            return {"released": True, "key": key}

    def list_resource_reservations(self, *, owner_prefix=None, task_id=None):
        with self._lock:
            values = list(self.resource_reservations.values())
        return [
            dict(value)
            for value in values
            if (owner_prefix is None or value["owner"].startswith(owner_prefix))
            and (task_id is None or value["task_id"] == task_id)
        ]


class FakeProvider:
    def __init__(self, issues, *, fail_reserve=None):
        self.issues = list(issues)
        self.fail_reserve = fail_reserve
        self.reserved = []
        self.claimed = []
        self.released = []
        self.list_calls = 0

    def list_open_issues(self, _repo):
        self.list_calls += 1
        return list(self.issues)

    def reserve(self, _repo, issue, reservation):
        if issue.number == self.fail_reserve:
            raise RuntimeError("forge reservation failed")
        self.reserved.append((issue.number, dict(reservation)))

    def claim(self, _repo, issue, reservation, task_id):
        self.claimed.append((issue.number, task_id, dict(reservation)))

    def release(
        self,
        _repo,
        issue,
        reservation,
        reason,
    ):
        self.released.append((issue.number, reason, dict(reservation)))


class QueueClient:
    def __init__(self, queue):
        self.queue = queue

    def list(self, **kwargs):
        status = kwargs.get("status")
        if isinstance(status, str) and "," in status:
            kwargs = dict(kwargs)
            kwargs["status"] = [part.strip() for part in status.split(",") if part.strip()]
        return [asdict(task) for task in self.queue.list(**kwargs)]

    def get(self, task_id):
        return asdict(self.queue.get(task_id))

    def create(self, title, **fields):
        proposed = fields.pop("proposed", False)
        create = self.queue.propose if proposed else self.queue.create
        return asdict(create(title, **fields))

    def approve(self, task_id):
        return asdict(self.queue.approve(task_id))

    def abandon(self, task_id, **kwargs):
        return asdict(self.queue.abandon(task_id, **kwargs))

    def acquire_resource_reservation(
        self, key, owner, *, ttl, token=None
    ):
        reservation, granted = self.queue.acquire_resource_reservation(
            key, owner, ttl=ttl, token=token
        )
        payload = asdict(reservation)
        if not granted:
            payload.pop("token", None)
        return {"granted": granted, "reservation": payload}

    def bind_resource_reservation(self, key, owner, token, task_id):
        return asdict(
            self.queue.bind_resource_reservation(
                key, owner, token, task_id
            )
        )

    def release_resource_reservation(self, key, owner, token):
        return {
            "released": self.queue.release_resource_reservation(
                key, owner, token
            ),
            "key": key,
        }

    def list_resource_reservations(self, *, owner_prefix=None, task_id=None):
        return [
            asdict(reservation)
            for reservation in self.queue.list_resource_reservations(
                owner_prefix=owner_prefix, task_id=task_id
            )
        ]


class RacingProvider(FakeProvider):
    def __init__(self, issue, barrier):
        super().__init__([issue])
        self.barrier = barrier
        self._lock = threading.Lock()
        self.labels = set()
        self.active = {}

    def reserve(self, repo, issue, reservation):
        with self._lock:
            super().reserve(repo, issue, reservation)
            self.labels.add(reservation["label"])
            self.active[reservation["loop"]] = dict(reservation)
        self.barrier.wait(timeout=5)

    def claim(self, repo, issue, reservation, task_id):
        with self._lock:
            super().claim(repo, issue, reservation, task_id)

    def release(
        self,
        repo,
        issue,
        reservation,
        reason,
    ):
        with self._lock:
            super().release(repo, issue, reservation, reason)
            self.active.pop(reservation["loop"], None)
            if not any(
                item["label"] == reservation["label"]
                for item in self.active.values()
            ):
                self.labels.discard(reservation["label"])


def test_declaration_expands_to_emitter_and_single_headless_lane():
    source, workers = expand_repository_issue_loop(_config())

    assert source.name == "backlog-source"
    assert source.kind == "emitter"
    assert source.spec["lease_scope"] == "repository-issue-loop:backlog"
    assert source.spec["interval_seconds"] == 60
    assert workers.name == "backlog-workers"
    assert workers.concurrency == 1
    assert workers.labels == ("repository-issue-work",)
    assert workers.body.type == "headless"
    assert workers.body.agent == "issue-worker"


def test_discovery_expands_high_level_file(tmp_path):
    path = tmp_path / "loop.json"
    path.write_text(json.dumps(_config()), encoding="utf-8")

    declarations = read_declaration_file_set(path)

    assert [item.kind for item in declarations] == [
        "emitter",
        "supervised-lane",
    ]


def test_occurrence_is_epoch_anchored():
    assert occurrence_epoch(7_399, 3_600) == 7_200
    assert occurrence_epoch(7_200, 3_600) == 7_200


def test_queue_filters_loop_source_origin_and_exclusive_key(tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "queue.db")
    queue.create(
        "match",
        source="repository-backlog",
        origin_ref="backlog/occurrence/7200",
        exclusive_key="repository-issue-loop:backlog",
    )
    queue.create(
        "other",
        source="another-source",
        origin_ref="other",
        exclusive_key="other-loop",
    )

    matches = queue.list(
        source="repository-backlog",
        origin_ref="backlog/occurrence/7200",
        exclusive_key="repository-issue-loop:backlog",
    )

    assert [task.title for task in matches] == ["match"]


def test_overlapping_loops_atomically_elect_one_issue_owner(tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "queue.db")
    client = QueueClient(queue)
    barrier = threading.Barrier(2)
    provider = RacingProvider(_issue(7), barrier)
    configs = [
        _config(name="alpha", source="alpha-source"),
        _config(name="beta", source="beta-source"),
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda config: run_tick(
                    client,
                    config,
                    provider=provider,
                    clock=lambda: 7_500,
                ),
                configs,
            )
        )

    created = [task for result in results for task in result["created"]]
    assert len(created) == 1
    assert sorted(result["lost"] for result in results) == [[], [7]]
    assert len(queue.list()) == 1
    reservations = queue.list_resource_reservations()
    assert len(reservations) == 1
    assert reservations[0].task_id == created[0]["id"]
    assert [item[0] for item in provider.released] == [7]
    assert "won the coordinator election" in provider.released[0][1]
    assert provider.labels == {"agent-reserved"}


def test_overlapping_loops_remove_only_the_losers_distinct_label(tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "queue.db")
    client = QueueClient(queue)
    provider = RacingProvider(_issue(7), threading.Barrier(2))
    configs = [
        _config(
            name="alpha",
            source="alpha-source",
            reservation={
                "label": "reserved-alpha",
                "comment": True,
                "orphan_after_seconds": 600,
            },
        ),
        _config(
            name="beta",
            source="beta-source",
            reservation={
                "label": "reserved-beta",
                "comment": True,
                "orphan_after_seconds": 600,
            },
        ),
    ]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda config: run_tick(
                    client,
                    config,
                    provider=provider,
                    clock=lambda: 7_500,
                ),
                configs,
            )
        )

    winner = next(result for result in results if result["created"])
    winner_loop = (
        "alpha"
        if winner["created"][0]["source"] == "alpha-source"
        else "beta"
    )
    assert provider.labels == {f"reserved-{winner_loop}"}
    assert len(provider.released) == 1
    assert provider.released[0][2]["label"] != f"reserved-{winner_loop}"


def test_post_create_takeover_leaves_only_one_runnable_task(tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "queue.db")
    provider = FakeProvider([_issue(7)])
    alpha_created = threading.Event()
    beta_done = threading.Event()

    class TimedClient(QueueClient):
        def __init__(self, now, *, pause_after_create=False):
            super().__init__(queue)
            self.now = now
            self.pause_after_create = pause_after_create

        def acquire_resource_reservation(
            self, key, owner, *, ttl, token=None
        ):
            reservation, granted = queue.acquire_resource_reservation(
                key, owner, ttl=ttl, token=token, now=self.now
            )
            payload = asdict(reservation)
            if not granted:
                payload.pop("token", None)
            return {"granted": granted, "reservation": payload}

        def create(self, title, **fields):
            task = super().create(title, **fields)
            if self.pause_after_create:
                alpha_created.set()
                assert beta_done.wait(timeout=5)
            return task

    alpha = TimedClient(100, pause_after_create=True)
    beta = TimedClient(161)
    short_reservation = {
        "label": "agent-reserved",
        "comment": True,
        "orphan_after_seconds": 60,
    }

    with ThreadPoolExecutor(max_workers=1) as pool:
        alpha_result = pool.submit(
            run_tick,
            alpha,
            _config(
                name="alpha",
                source="alpha-source",
                reservation=short_reservation,
            ),
            provider=provider,
            clock=lambda: 7_500,
        )
        assert alpha_created.wait(timeout=5)
        try:
            beta_result = run_tick(
                beta,
                _config(
                    name="beta",
                    source="beta-source",
                    reservation=short_reservation,
                ),
                provider=provider,
                clock=lambda: 7_500,
            )
            (current_reservation,) = (
                queue.list_resource_reservations()
            )
            assert ":beta:" in current_reservation.owner
        finally:
            beta_done.set()

        with pytest.raises(TaskError, match="identity does not match"):
            alpha_result.result(timeout=5)

    tasks = queue.list()
    assert [task.status for task in tasks].count("queued") == 1
    assert [task.status for task in tasks].count("abandoned") == 1
    assert beta_result["created"][0]["status"] == "queued"
    assert all(task.status != "proposed" for task in tasks)
    abandoned = next(task for task in tasks if task.status == "abandoned")
    assert any(
        str(event["note"]).startswith("failed-reservation:")
        for event in queue.events(abandoned.id)
    )


def test_unbound_resource_election_expires_but_bound_owner_is_stable(tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "queue.db")

    first, granted = queue.acquire_resource_reservation(
        "forge:github:repository:example/project:issue:7",
        "loop:alpha",
        ttl=60,
        now=100,
    )
    assert granted
    assert first.owner == "loop:alpha"

    current, granted = queue.acquire_resource_reservation(
        first.key, "loop:beta", ttl=60, now=120
    )
    assert not granted
    assert current.owner == "loop:alpha"
    assert not queue.release_resource_reservation(
        first.key, "loop:beta", first.token
    )

    recovered, granted = queue.acquire_resource_reservation(
        first.key, "loop:beta", ttl=60, now=161
    )
    assert granted
    assert recovered.owner == "loop:beta"
    queue.bind_resource_reservation(
        first.key, "loop:beta", recovered.token, "task-7", now=162
    )

    bound, granted = queue.acquire_resource_reservation(
        first.key, "loop:gamma", ttl=60, now=10_000
    )
    assert not granted
    assert bound.owner == "loop:beta"
    assert bound.task_id == "task-7"


def test_stale_same_owner_token_cannot_mutate_reacquired_reservation(tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "queue.db")
    key = "forge:github:repository:example/project:issue:7"
    first, _ = queue.acquire_resource_reservation(
        key, "loop:alpha", ttl=10, now=100
    )
    second, _ = queue.acquire_resource_reservation(
        key, "loop:beta", ttl=10, now=111
    )
    reacquired, granted = queue.acquire_resource_reservation(
        key, "loop:alpha", ttl=10, now=122
    )

    assert granted
    assert len({first.token, second.token, reacquired.token}) == 3
    assert not queue.release_resource_reservation(
        key, "loop:alpha", first.token
    )
    with pytest.raises(TaskError, match="identity does not match"):
        queue.bind_resource_reservation(
            key, "loop:alpha", first.token, "stale-task"
        )
    current = queue.bind_resource_reservation(
        key, "loop:alpha", reacquired.token, "current-task"
    )
    assert current.task_id == "current-task"


def test_expired_reacquire_race_issues_one_new_identity(tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "queue.db")
    key = "forge:github:repository:example/project:issue:7"
    stale, _ = queue.acquire_resource_reservation(
        key, "loop:alpha", ttl=10, now=100
    )
    queue.acquire_resource_reservation(
        key, "loop:beta", ttl=10, now=111
    )
    barrier = threading.Barrier(2)

    def compete(owner):
        barrier.wait(timeout=5)
        return queue.acquire_resource_reservation(
            key, owner, ttl=10, now=122
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(compete, ["loop:alpha", "loop:gamma"]))

    winners = [reservation for reservation, granted in results if granted]
    assert len(winners) == 1
    assert winners[0].token != stale.token
    assert not queue.release_resource_reservation(
        key, "loop:alpha", stale.token
    )


def test_existing_resource_reservation_rows_receive_tokens_on_migration(
    tmp_path,
):
    path = tmp_path / "queue.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE resource_reservations ("
            "key TEXT PRIMARY KEY, owner TEXT NOT NULL, task_id TEXT, "
            "acquired_at REAL NOT NULL, updated_at REAL NOT NULL, "
            "expires_at REAL)"
        )
        conn.execute(
            "INSERT INTO resource_reservations VALUES (?, ?, NULL, ?, ?, ?)",
            ("resource:7", "loop:alpha", 100, 100, 200),
        )

    queue = RepoDefaultingQueue(path)

    (reservation,) = queue.list_resource_reservations()
    assert reservation.token


def test_same_occurrence_is_not_reemitted_after_restart():
    existing = {
        "id": "old",
        "status": "submitted",
        "source": "repository-backlog",
        "origin_ref": "backlog/occurrence/7200",
        "exclusive_key": "repository-issue-loop:backlog",
    }
    client = FakeClient([existing])
    provider = FakeProvider([_issue(1)])

    result = run_tick(
        client, _config(), provider=provider, clock=lambda: 7_500
    )

    assert result["suppressed"] is True
    assert client.created == []
    assert provider.reserved == []
    assert provider.list_calls == 0


def test_same_occurrence_is_not_reemitted_after_restart_with_queue_client(tmp_path):
    queue = RepoDefaultingQueue(tmp_path / "tasks.db")
    repo = "example/project"
    existing = queue.create(
        "old",
        repo=repo,
        source="repository-backlog",
        origin_ref="backlog/occurrence/7200",
        exclusive_key="repository-issue-loop:backlog",
    )
    queue.claim_one("worker-1", repo=repo, task_id=existing.id)
    queue.start(existing.id, "worker-1")
    queue.complete(existing.id, "worker-1")
    client = QueueClient(queue)
    provider = FakeProvider([_issue(1)])

    result = run_tick(
        client, _config(repo=repo), provider=provider, clock=lambda: 7_500
    )

    assert result["suppressed"] is True
    assert result["same_occurrence_tasks"][0]["id"] == existing.id
    assert provider.reserved == []
    assert provider.list_calls == 0


def test_nonterminal_episode_backpressures_later_occurrence():
    existing = {
        "id": "active",
        "status": "suspended",
        "source": "repository-backlog",
        "origin_ref": "backlog/occurrence/3600",
        "exclusive_key": "repository-issue-loop:backlog",
    }

    provider = FakeProvider([_issue(1)])
    result = run_tick(
        FakeClient([existing]),
        _config(),
        provider=provider,
        clock=lambda: 10_900,
    )

    assert result["suppressed"] is True
    assert result["active_tasks"][0]["id"] == "active"
    assert provider.list_calls == 0


def test_source_change_does_not_fork_active_episode():
    existing = {
        "id": "active",
        "status": "started",
        "source": "old-source",
        "origin_ref": "backlog/occurrence/3600",
        "exclusive_key": "repository-issue-loop:backlog",
    }

    client = FakeClient([existing])
    result = run_tick(
        client,
        _config(source="new-source"),
        provider=FakeProvider([_issue(1)]),
        clock=lambda: 10_900,
    )

    assert result["suppressed"] is True
    assert result["active_tasks"][0]["source"] == "old-source"
    assert "source" not in client.list_calls[0]


def test_source_change_does_not_replay_same_terminal_occurrence():
    existing = {
        "id": "old",
        "status": "submitted",
        "source": "old-source",
        "origin_ref": "backlog/occurrence/7200",
        "exclusive_key": "repository-issue-loop:backlog",
    }

    result = run_tick(
        FakeClient([existing]),
        _config(source="new-source"),
        provider=FakeProvider([_issue(1)]),
        clock=lambda: 7_500,
    )

    assert result["suppressed"] is True
    assert result["same_occurrence_tasks"][0]["source"] == "old-source"


def test_quiet_labels_and_priority_produce_deterministic_bounded_order():
    provider = FakeProvider(
        [
            _issue(7, labels=("ready", "priority:medium"), created=1),
            _issue(4, labels=("ready", "priority:high"), created=5),
            _issue(3, labels=("ready", "priority:high"), created=5),
            _issue(2, labels=("ready", "bootstrap"), created=0),
            _issue(1, labels=("ready",), created=0, updated=9_900),
        ]
    )

    result = run_tick(
        FakeClient(), _config(), provider=provider, clock=lambda: 10_000
    )

    assert result["eligible"] == [3, 4]
    assert result["reserved"] == [3, 4]


def test_one_goal_task_carries_loop_exclusivity_and_source():
    client = FakeClient()
    result = run_tick(
        client,
        _config(),
        provider=FakeProvider([_issue(8)]),
        clock=lambda: 10_000,
    )

    task = result["created"][0]
    assert task["source"] == "repository-backlog"
    assert task["exclusive_key"] == "repository-issue-loop:backlog"
    assert task["dedup_key"] == "backlog/occurrence/7200"
    assert task["goal"]
    assert task["done_criteria"]
    assert "Do not force-push" in task["prompt"]
    assert "task-id-based waiter" in task["prompt"]
    assert "clean and synchronized" in task["prompt"]
    assert "Do not change this loop's active declaration" in task["prompt"]


def test_existing_visible_reservation_suppresses_issue():
    reserved = {
        "loop": "another-loop",
        "occurrence": 7200,
        "state": "reserved",
        "at": 9_000,
        "issue": 1,
        "label": "agent-reserved",
    }
    claimed = {
        **reserved,
        "state": "claimed",
        "task_id": "other-task",
    }
    provider = FakeProvider([_issue(1, reservations=(reserved, claimed))])

    result = run_tick(
        FakeClient(), _config(), provider=provider, clock=lambda: 10_000
    )

    assert result["eligible"] == []


def test_partial_reservation_failure_releases_owned_reservations():
    provider = FakeProvider([_issue(1), _issue(2)], fail_reserve=2)

    with pytest.raises(RuntimeError, match="reservation"):
        run_tick(
            FakeClient(), _config(), provider=provider, clock=lambda: 10_000
        )

    assert [item[0] for item in provider.released] == [1]


def test_create_failure_reconciles_all_new_reservations():
    provider = FakeProvider([_issue(1), _issue(2)])

    with pytest.raises(RuntimeError, match="coordinator"):
        run_tick(
            FakeClient(fail_create=True),
            _config(),
            provider=provider,
            clock=lambda: 10_000,
        )

    assert [item[0] for item in provider.released] == [2, 1]


def test_lost_create_response_requeries_and_binds_committed_task():
    provider = FakeProvider([_issue(1)])
    client = FakeClient(commit_then_fail=True)

    result = run_tick(
        client, _config(), provider=provider, clock=lambda: 10_000
    )

    assert [task["id"] for task in result["created"]] == ["task-1"]
    assert provider.released == []
    assert [item[1] for item in provider.claimed] == ["task-1"]
    reservation = next(iter(client.resource_reservations.values()))
    assert reservation["task_id"] == "task-1"


def test_repository_issue_loop_stamps_default_require_verification():
    provider = FakeProvider([_issue(1)])
    client = FakeClient()

    result = run_tick(
        client,
        _config(require_verification=True),
        provider=provider,
        clock=lambda: 10_000,
    )

    assert result["created"][0]["require_verification"] is True


def test_repository_issue_loop_stamps_evaluator_ref():
    provider = FakeProvider([_issue(12)])
    result = run_tick(
        FakeClient(),
        _config(require_verification=True, evaluator_ref="review-loop"),
        provider=provider,
        clock=lambda: 10_000,
    )

    assert result["created"][0]["evaluator_ref"] == "review-loop"


def test_global_backlog_triager_drives_through_the_generic_issue_loop(tmp_path):
    import json as _json

    path = tmp_path / "triager.json"
    path.write_text(
        _json.dumps(
            {
                "extends": "global:backlog-triager",
                "name": "triage-backlog",
                "repo": "example/project",
                "source": "triage-backlog",
                "cadence_seconds": 3600,
                "task_label": "backlog-triage",
                "forge": {"provider": "github", "producer_login": "triage-bot"},
                "reservation": {"label": "triage-reserved", "comment": True},
                "pool": {
                    "max_active_processes": 1,
                    "body": {"agent": "triage-worker"},
                },
            }
        ),
        encoding="utf-8",
    )

    declarations = read_declaration_file_set(path)
    source = next(d for d in declarations if d.name == "triage-backlog-source")
    config = source.spec["repository_issue_loop"]
    provider = FakeProvider([_issue(17, labels=("bug", "needs-triage"))])

    result = run_tick(
        FakeClient(),
        config,
        provider=provider,
        clock=lambda: 10_000,
    )

    task = result["created"][0]
    assert provider.list_calls == 1
    assert task["require_verification"] is True
    assert task["evaluator_ref"] == "backlog-triager"
    assert task["title"] == "Triage repository issues #17"
    assert task["goal"] == "Classify and triage repository issues #17"
    assert "Legitimate active bugs must" in task["prompt"]
    assert "Do not turn this triage task into an implementation lane" in task["prompt"]
    assert "return immediately to triage/dispositioning" in task["prompt"]
    assert "implementation, required checks, review, merge, and issue closure" not in task["prompt"]
    assert "classify it" in task["prompt"]
    assert "attached to tracked effort" in task["prompt"]


def test_global_issue_reproducer_drives_through_the_generic_issue_loop(tmp_path):
    import json as _json

    path = tmp_path / "reproducer.json"
    path.write_text(
        _json.dumps(
            {
                "extends": "global:issue-reproducer",
                "name": "repro-backlog",
                "repo": "example/project",
                "source": "repro-backlog",
                "cadence_seconds": 3600,
                "task_label": "issue-repro",
                "forge": {"provider": "github", "producer_login": "repro-bot"},
                "reservation": {"label": "repro-reserved", "comment": True},
                "pool": {
                    "max_active_processes": 1,
                    "body": {"agent": "repro-worker"},
                },
            }
        ),
        encoding="utf-8",
    )

    declarations = read_declaration_file_set(path)
    source = next(d for d in declarations if d.name == "repro-backlog-source")
    config = source.spec["repository_issue_loop"]
    provider = FakeProvider([_issue(17, labels=("bug", "needs-repro"))])

    result = run_tick(
        FakeClient(),
        config,
        provider=provider,
        clock=lambda: 10_000,
    )

    task = result["created"][0]
    assert provider.list_calls == 1
    assert task["require_verification"] is True
    assert task["evaluator_ref"] == "issue-reproducer"
    assert task["title"] == "Attempt reproduction for repository issues #17"
    assert task["goal"] == "Reproduce and classify repository issues #17"
    assert "relevant reproduction strategies" in task["prompt"]
    assert "durable issue evidence" in task["prompt"]
    assert "strike marker convention" in task["prompt"]
    assert "Do not turn this reproduction task into an implementation lane" in task["prompt"]
    assert "implementation, required checks, review, merge, and issue closure" not in task["prompt"]


def test_global_effort_builder_groups_multiple_issues_into_one_effort_task(tmp_path):
    import json as _json

    path = tmp_path / "effort-builder.json"
    path.write_text(
        _json.dumps(
            {
                "extends": "global:effort-builder",
                "name": "effort-backlog",
                "repo": "example/project",
                "source": "effort-backlog",
                "cadence_seconds": 3600,
                "issue_numbers": [17, 18],
                "task_label": "effort-build",
                "forge": {"provider": "github", "producer_login": "effort-bot"},
                "reservation": {"label": "effort-reserved", "comment": True},
                "pool": {
                    "max_active_processes": 1,
                    "body": {"agent": "effort-worker"},
                },
            }
        ),
        encoding="utf-8",
    )

    declarations = read_declaration_file_set(path)
    source = next(d for d in declarations if d.name == "effort-backlog-source")
    config = source.spec["repository_issue_loop"]
    provider = FakeProvider(
        [
            _issue(17, labels=("bug", "triage:accepted", "ready")),
            _issue(18, labels=("bug", "triage:accepted", "ready")),
            _issue(19, labels=("bug", "triage:accepted", "ready")),
        ]
    )

    result = run_tick(
        FakeClient(),
        config,
        provider=provider,
        clock=lambda: 10_000,
    )

    task = result["created"][0]
    assert provider.list_calls == 1
    assert task["require_verification"] is True
    assert task["evaluator_ref"] == "effort-builder"
    assert task["title"] == "Build tracked effort for repository issues #17, #18"
    assert task["goal"] == "Group repository issues #17, #18 into tracked effort work"
    assert "- #17: Issue 17 (https://example.com/issues/17)" in task["prompt"]
    assert "- #18: Issue 18 (https://example.com/issues/18)" in task["prompt"]
    assert "#19" not in task["title"]
    assert "- #19: Issue 19" not in task["prompt"]
    assert "Group the selected issues into one coherent tracked effort" in task["prompt"]
    assert "Do not turn this effort-building task into an implementation lane" in task["prompt"]
    assert "that execution belongs to a separate worker" in task["prompt"]
    assert "implementation, required checks, review, merge, and issue closure" not in task["prompt"]


def test_explicit_issue_set_suppresses_creation_when_any_configured_issue_is_missing():
    config = _config(issue_numbers=[17, 18], include_labels=["ready"])
    provider = FakeProvider([_issue(17, labels=("ready",)), _issue(19, labels=("ready",))])

    result = run_tick(
        FakeClient(),
        config,
        provider=provider,
        clock=lambda: 10_000,
    )

    assert result["created"] == []
    assert result["eligible"] == []
    assert result["reserved"] == []


def test_explicit_issue_set_releases_partial_reservations_when_one_issue_loses_election():
    config = _config(issue_numbers=[17, 18], include_labels=["ready"])
    provider = FakeProvider([_issue(17, labels=("ready",)), _issue(18, labels=("ready",))])
    client = FakeClient()
    client.resource_reservations[_resource_key(config, 18)] = {
        "key": _resource_key(config, 18),
        "owner": "other-loop",
        "token": "token-existing",
        "task_id": None,
    }

    result = run_tick(
        client,
        config,
        provider=provider,
        clock=lambda: 10_000,
    )

    assert result["created"] == []
    assert result["reserved"] == []
    assert result["lost"] == [18]
    assert 17 in [issue for issue, *_rest in provider.released]
    assert _resource_key(config, 17) not in client.resource_reservations


def test_explicit_issue_set_rollback_releases_through_the_script_backlog_not_repo(
    tmp_path,
):
    """Regression guard: the explicit-`issue_numbers` rollback path (one
    configured issue loses its coordinator election, so the
    already-reserved ones are released) must select `forge.backlog` the
    same way every other `provider.release` call site does -- otherwise
    a script backed by `forge.backlog` receives the task-routing `repo`
    instead and the real backlog reservation is left stranded
    ('Rollback releases reserved issues from the wrong backlog')."""

    class _RecordingProvider(FakeProvider):
        def release(self, repo, issue, reservation, reason):
            self.released_repo = repo
            return super().release(repo, issue, reservation, reason)

    config = _config(
        repo="example/project",
        issue_numbers=[17, 18],
        include_labels=["ready"],
        forge={
            "provider": "script",
            "producer_login": "issue-bot",
            "command": ["./poller.py"],
            "backlog": "pending-work-items",
        },
    )
    provider = _RecordingProvider(
        [_issue(17, labels=("ready",)), _issue(18, labels=("ready",))]
    )
    client = FakeClient()
    # `_resource_key`'s script-provider branch depends on `forge`'s fully
    # resolved markers (namespace, declared command/cwd) -- validate the
    # same config `run_tick` will validate internally to compute the
    # identical key for seeding the pre-existing "other-loop" collision.
    validated = validate_config(config, cwd=tmp_path)
    client.resource_reservations[_resource_key(validated, 18)] = {
        "key": _resource_key(validated, 18),
        "owner": "other-loop",
        "token": "token-existing",
        "task_id": None,
    }

    result = run_tick(client, config, provider=provider, clock=lambda: 10_000, cwd=tmp_path)

    assert result["lost"] == [18]
    assert provider.released_repo == "pending-work-items"


def test_proposed_task_retries_transient_approve_failure():
    provider = FakeProvider([_issue(1)])
    client = FakeClient(fail_approve_once=True)

    with pytest.raises(RuntimeError, match="approve response failed"):
        run_tick(
            client, _config(), provider=provider, clock=lambda: 10_000
        )

    assert client.tasks[0]["status"] == "proposed"
    assert all(
        reservation["task_id"] == "task-1"
        for reservation in client.resource_reservations.values()
    )

    result = run_tick(
        client, _config(), provider=provider, clock=lambda: 10_001
    )

    assert client.tasks[0]["status"] == "queued"
    assert result["reconciled_proposed"] == [
        {"task_id": "task-1", "action": "approved"}
    ]


def test_rehearsal_mode_parks_fully_bound_task_as_proposed():
    provider = FakeProvider([_issue(1)])
    client = FakeClient()

    run_tick(
        client,
        _config(rehearsal_mode=True),
        provider=provider,
        clock=lambda: 10_000,
    )
    assert client.tasks[0]["status"] == "proposed"

    # A second tick re-examines the same fully-bound proposed task and
    # still never auto-approves it -- rehearsal_mode is a standing park,
    # not a one-tick delay.
    result = run_tick(
        client,
        _config(rehearsal_mode=True),
        provider=provider,
        clock=lambda: 10_001,
    )

    assert client.tasks[0]["status"] == "proposed"
    assert result["reconciled_proposed"] == [
        {"task_id": "task-1", "action": "awaiting-manual-approval"}
    ]
    # The forge-side reservation still binds for real (so the rehearsal is
    # visibly real, not a no-op) -- only the task's own queue status parks.
    assert [item[1] for item in provider.claimed] == ["task-1"]
    reservation = next(iter(client.resource_reservations.values()))
    assert reservation["task_id"] == "task-1"

    # A human can still promote it explicitly -- rehearsal_mode only skips
    # the *automatic* approval, never blocks a deliberate one.
    approved = client.approve("task-1")
    assert approved["status"] == "queued"


def test_uncertain_abandon_retains_reservations_until_terminal_reread():
    provider = FakeProvider([_issue(1), _issue(2)])
    client = FakeClient(fail_bind_at=2, fail_abandon_once=True)

    with pytest.raises(RuntimeError, match="abandon response failed"):
        run_tick(
            client, _config(), provider=provider, clock=lambda: 10_000
        )

    assert client.tasks[0]["status"] == "proposed"
    assert len(client.resource_reservations) == 2
    provider.issues = [
        _issue(
            number,
            reservations=(
                {
                    "loop": "backlog",
                    "occurrence": 7200,
                    "state": "reserved",
                    "at": 10_000,
                    "label": "agent-reserved",
                    "issue": number,
                },
            ),
        )
        for number in (1, 2)
    ]

    result = run_tick(
        client, _config(), provider=provider, clock=lambda: 10_001
    )

    assert client.tasks[0]["status"] == "abandoned"
    assert result["reconciled_proposed"] == [
        {"task_id": "task-1", "action": "abandoned"}
    ]
    assert client.resource_reservations == {}
    assert provider.claimed == []
    assert provider.released == []

    run_tick(
        client, _config(), provider=provider, clock=lambda: 10_900
    )
    assert [item[0] for item in provider.released[-2:]] == [1, 2]


def test_stale_owned_unclaimed_reservation_is_reconciled():
    stale = {
        "loop": "backlog",
        "occurrence": 3600,
        "state": "reserved",
        "at": 1_000,
        "label": "agent-reserved",
    }
    provider = FakeProvider([_issue(1, reservations=(stale,))])

    result = run_tick(
        FakeClient(), _config(), provider=provider, clock=lambda: 10_000
    )

    assert provider.released[0][0] == 1
    assert result["reconciled"] == [1]


def test_active_task_suppresses_reservation_promotion_forge_reads():
    reservation = {
        "loop": "backlog",
        "occurrence": 7200,
        "state": "reserved",
        "at": 9_000,
        "label": "agent-reserved",
    }
    task = {
        "id": "task-existing",
        "status": "queued",
        "source": "repository-backlog",
        "origin_ref": "backlog/occurrence/7200",
        "exclusive_key": "repository-issue-loop:backlog",
    }
    provider = FakeProvider([_issue(1, reservations=(reservation,))])

    result = run_tick(
        FakeClient([task]), _config(), provider=provider, clock=lambda: 10_000
    )

    assert result["suppressed"] is True
    assert provider.claimed == []
    assert provider.list_calls == 0


@pytest.mark.parametrize("status", ["submitted", "completed", "abandoned", "dead_letter"])
def test_terminal_task_releases_claim_when_issue_remains_open(status):
    reserved = {
        "loop": "backlog",
        "occurrence": 7200,
        "state": "reserved",
        "at": 9_000,
        "label": "agent-reserved",
        "issue": 1,
    }
    claimed = {
        **reserved,
        "state": "claimed",
        "task_id": "task-existing",
    }
    task = {
        "id": "task-existing",
        "status": status,
        "source": "old-source",
        "origin_ref": "backlog/occurrence/7200",
        "exclusive_key": "repository-issue-loop:backlog",
    }
    provider = FakeProvider([_issue(1, reservations=(reserved, claimed))])

    result = run_tick(
        FakeClient([task]), _config(), provider=provider, clock=lambda: 10_900
    )

    assert provider.released[0][0] == 1
    assert result["reconciled_terminal_claims"] == [1]


def test_completed_task_with_closed_issue_keeps_historical_claim():
    task = {
        "id": "task-existing",
        "status": "completed",
        "source": "repository-backlog",
        "origin_ref": "backlog/occurrence/7200",
        "exclusive_key": "repository-issue-loop:backlog",
    }
    provider = FakeProvider([])

    result = run_tick(
        FakeClient([task]), _config(), provider=provider, clock=lambda: 10_000
    )

    assert result["suppressed"] is True
    assert provider.released == []


def test_claim_task_mismatch_is_not_released():
    reserved = {
        "loop": "backlog",
        "occurrence": 7200,
        "state": "reserved",
        "at": 9_000,
        "label": "agent-reserved",
        "issue": 1,
    }
    claimed = {
        **reserved,
        "state": "claimed",
        "task_id": "another-task",
    }
    task = {
        "id": "task-existing",
        "status": "abandoned",
        "origin_ref": "backlog/occurrence/7200",
        "exclusive_key": "repository-issue-loop:backlog",
    }
    provider = FakeProvider([_issue(1, reservations=(reserved, claimed))])

    run_tick(
        FakeClient([task]), _config(), provider=provider, clock=lambda: 10_000
    )

    assert provider.released == []


def test_github_markers_require_verified_author_and_strict_shape():
    real_reservation = {
        "loop": "backlog",
        "occurrence": 7200,
        "state": "reserved",
        "at": 9_000,
        "label": "agent-reserved",
        "issue": 7,
    }
    real_claim = {
        **real_reservation,
        "state": "claimed",
        "task_id": "task-real",
    }
    forged_release = {
        **real_claim,
        "state": "released",
        "reason": "forged",
    }
    invalid_release = {
        **real_claim,
        "state": "released",
        "task_id": "wrong-task",
        "reason": "wrong task",
    }
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="issue-bot\n", stderr=""),
            SimpleNamespace(
                returncode=0, stdout="example/project\n", stderr=""
            ),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    _graphql_page(
                        [
                            _graphql_issue(
                                7,
                                comments=[
                            {
                                "author": {"login": "issue-bot"},
                                "body": _marker(real_reservation),
                            },
                            {
                                "author": {"login": "issue-bot"},
                                "body": _marker(real_claim),
                            },
                            {
                                "author": {"login": "attacker"},
                                "body": _marker(forged_release),
                            },
                            {
                                "author": {"login": "issue-bot"},
                                "body": _marker(invalid_release),
                            },
                                ],
                            )
                        ]
                    )
                ),
                stderr="",
            ),
        ]
    )
    provider = GitHubProvider(
        "issue-bot", runner=lambda *_args, **_kwargs: next(responses)
    )

    (issue,) = provider.list_open_issues("example/project")

    assert len(issue.reservations) == 3
    assert issue.reservations[0]["comment_author"] == "issue-bot"
    assert _latest_reservations(issue)["backlog"]["state"] == "claimed"
    assert _latest_reservations(issue)["backlog"]["task_id"] == "task-real"


def test_github_issue_discovery_uses_bounded_bulk_pages():
    calls = []
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="issue-bot\n", stderr=""),
            SimpleNamespace(
                returncode=0, stdout="example/project\n", stderr=""
            ),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    _graphql_page(
                        [_graphql_issue(number) for number in range(1, 101)],
                        has_next=True,
                        cursor="page-2",
                    )
                ),
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    _graphql_page(
                        [_graphql_issue(number) for number in range(101, 151)]
                    )
                ),
                stderr="",
            ),
        ]
    )

    def runner(*args, **kwargs):
        del kwargs
        calls.append(args[0])
        return next(responses)

    issues = GitHubProvider("issue-bot", runner=runner).list_open_issues(
        "example/project"
    )

    assert len(issues) == 150
    graphql_calls = [
        args for args in calls if args[:3] == ["gh", "api", "graphql"]
    ]
    assert len(graphql_calls) == 2
    assert len(calls) == 4
    assert not any(args[1:3] == ["issue", "view"] for args in calls)


def test_github_provider_rejects_wrong_authenticated_login():
    provider = GitHubProvider(
        "issue-bot",
        runner=lambda *_args, **_kwargs: SimpleNamespace(
            returncode=0, stdout="other-user\n", stderr=""
        ),
    )

    with pytest.raises(RuntimeError, match="identity mismatch"):
        provider.list_open_issues("example/project")


def test_reserve_rechecks_identity_before_each_mutation():
    calls = []
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="issue-bot\n", stderr=""),
            SimpleNamespace(
                returncode=0, stdout="example/project\n", stderr=""
            ),
            SimpleNamespace(returncode=0, stdout='{"comments":[]}', stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="other-user\n", stderr=""),
        ]
    )

    def runner(*args, **kwargs):
        del kwargs
        calls.append(args[0])
        return next(responses)

    provider = GitHubProvider("issue-bot", runner=runner)
    with pytest.raises(RuntimeError, match="identity mismatch"):
        provider.reserve(
            "example/project",
            _issue(7),
            {
                "loop": "backlog",
                "occurrence": 7200,
                "state": "reserved",
                "at": 7300,
                "label": "agent-reserved",
            },
        )

    assert any("comment" in args for args in calls)
    assert not any("edit" in args for args in calls)


def test_claim_does_not_reuse_a_prior_mutation_identity_check():
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="issue-bot\n", stderr=""),
            SimpleNamespace(
                returncode=0, stdout="example/project\n", stderr=""
            ),
            SimpleNamespace(returncode=0, stdout='{"comments":[]}', stderr=""),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(returncode=0, stdout="other-user\n", stderr=""),
        ]
    )
    provider = GitHubProvider(
        "issue-bot", runner=lambda *_args, **_kwargs: next(responses)
    )
    reservation = {
        "loop": "backlog",
        "occurrence": 7200,
        "state": "reserved",
        "at": 7300,
        "label": "agent-reserved",
    }

    provider.claim(
        "example/project", _issue(7), reservation, "task-first"
    )
    with pytest.raises(RuntimeError, match="identity mismatch"):
        provider.claim(
            "example/project", _issue(7), reservation, "task-second"
        )


def test_loser_release_preserves_another_loops_visible_label():
    calls = []
    winner = {
        "loop": "alpha",
        "occurrence": 7200,
        "state": "reserved",
        "at": 7300,
        "label": "agent-reserved",
        "issue": 7,
    }
    loser_release = {
        "loop": "beta",
        "occurrence": 7200,
        "state": "released",
        "at": 7300,
        "label": "agent-reserved",
        "issue": 7,
        "reason": "another loop won",
    }
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="issue-bot\n", stderr=""),
            SimpleNamespace(
                returncode=0, stdout="example/project\n", stderr=""
            ),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "comments": [
                            {
                                "author": {"login": "issue-bot"},
                                "body": _marker(winner),
                            },
                        ]
                    }
                ),
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "comments": [
                            {
                                "author": {"login": "issue-bot"},
                                "body": _marker(winner),
                            },
                            {
                                "author": {"login": "issue-bot"},
                                "body": _marker(loser_release),
                            },
                        ]
                    }
                ),
                stderr="",
            ),
        ]
    )

    def runner(*args, **kwargs):
        del kwargs
        calls.append(args)
        return next(responses)

    provider = GitHubProvider("issue-bot", runner=runner)
    provider.release(
        "example/project",
        _issue(7),
        {
            "loop": "beta",
            "occurrence": 7200,
            "state": "reserved",
            "at": 7300,
            "label": "agent-reserved",
        },
        "another loop won",
    )

    assert not any(
        "edit" in args[0] and "--remove-label" in args[0]
        for args in calls
    )


def test_loser_release_removes_only_its_distinct_label():
    calls = []
    winner = {
        "loop": "alpha",
        "occurrence": 7200,
        "state": "reserved",
        "at": 7300,
        "label": "reserved-alpha",
        "issue": 7,
    }
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="issue-bot\n", stderr=""),
            SimpleNamespace(
                returncode=0, stdout="example/project\n", stderr=""
            ),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "comments": [
                            {
                                "author": {"login": "issue-bot"},
                                "body": _marker(winner),
                            }
                        ]
                    }
                ),
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "comments": [
                            {
                                "author": {"login": "issue-bot"},
                                "body": _marker(winner),
                            }
                        ]
                    }
                ),
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout="issue-bot\n", stderr=""),
            SimpleNamespace(
                returncode=0, stdout="example/project\n", stderr=""
            ),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
    )

    def runner(*args, **kwargs):
        del kwargs
        calls.append(args)
        return next(responses)

    provider = GitHubProvider("issue-bot", runner=runner)
    provider.release(
        "example/project",
        _issue(7),
        {
            "loop": "beta",
            "occurrence": 7200,
            "state": "reserved",
            "at": 7300,
            "label": "reserved-beta",
        },
        "another loop won",
    )

    edit = next(args[0] for args in calls if "edit" in args[0])
    assert edit[-2:] == ["--remove-label", "reserved-beta"]


def test_claim_reuses_same_loops_comment_across_occurrences():
    """A loop's second (and later) occurrence on the same issue edits its own
    prior marker comment in place instead of posting a fresh one -- the fix
    for the observed "continuous claims and releases" churn where an issue
    (e.g. one stuck at a terminal judgment stage like `stage:needs-vision`)
    keeps getting swept every cadence tick forever."""
    calls = []
    prior_claim = {
        "loop": "backlog",
        "occurrence": 100,
        "state": "claimed",
        "at": 150,
        "label": "agent-reserved",
        "issue": 7,
        "task_id": "task-1",
    }
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="issue-bot\n", stderr=""),
            SimpleNamespace(
                returncode=0, stdout="example/project\n", stderr=""
            ),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "comments": [
                            {
                                "author": {"login": "issue-bot"},
                                "body": _marker(prior_claim),
                                "id": "comment-node-1",
                            },
                        ]
                    }
                ),
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
    )

    def runner(*args, **kwargs):
        del kwargs
        calls.append(args[0])
        return next(responses)

    provider = GitHubProvider("issue-bot", runner=runner)
    provider.claim(
        "example/project",
        _issue(7),
        {
            "loop": "backlog",
            "occurrence": 200,
            "state": "reserved",
            "at": 250,
            "label": "agent-reserved",
        },
        "task-2",
    )

    # No new-comment call was made; the prior occurrence's own comment for
    # this same loop was edited in place via the GraphQL mutation instead.
    assert not any(args[1:3] == ["issue", "comment"] for args in calls)
    graphql_call = next(args for args in calls if args[1:3] == ["api", "graphql"])
    assert "updateIssueComment" in graphql_call[4]


def test_claim_does_not_reuse_a_different_loops_comment():
    """A different loop racing for the same issue must never edit another
    loop's own comment -- each loop keeps its own persistent, evolving
    comment so a losing/finishing loop's transition can't clobber a
    different, still-active loop's claim state."""
    calls = []
    other_loops_claim = {
        "loop": "alpha",
        "occurrence": 100,
        "state": "claimed",
        "at": 150,
        "label": "agent-reserved",
        "issue": 7,
        "task_id": "task-alpha",
    }
    responses = iter(
        [
            SimpleNamespace(returncode=0, stdout="issue-bot\n", stderr=""),
            SimpleNamespace(
                returncode=0, stdout="example/project\n", stderr=""
            ),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {
                        "comments": [
                            {
                                "author": {"login": "issue-bot"},
                                "body": _marker(other_loops_claim),
                                "id": "comment-node-alpha",
                            },
                        ]
                    }
                ),
                stderr="",
            ),
            SimpleNamespace(returncode=0, stdout="", stderr=""),
        ]
    )

    def runner(*args, **kwargs):
        del kwargs
        calls.append(args[0])
        return next(responses)

    provider = GitHubProvider("issue-bot", runner=runner)
    provider.claim(
        "example/project",
        _issue(7),
        {
            "loop": "beta",
            "occurrence": 100,
            "state": "reserved",
            "at": 150,
            "label": "agent-reserved",
        },
        "task-beta",
    )

    # beta has no comment of its own yet, so it must post a new one rather
    # than editing alpha's still-active claim comment.
    assert any(args[1:3] == ["issue", "comment"] for args in calls)
    assert not any(args[1:3] == ["api", "graphql"] for args in calls)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"source": ""}, "source"),
        ({"batch_size": 0}, "batch_size"),
        ({"issue_numbers": [True]}, "issue_numbers: expected a list of positive integers"),
        ({"issue_numbers": ["17"]}, "issue_numbers: expected a list of positive integers"),
        ({"forge": {"provider": "other"}}, "only \\['azure-devops', 'gitea', 'github', 'script'\\]"),
        ({"forge": {"provider": "github"}}, "producer_login"),
        ({"task_contract": False}, "task_contract: expected a mapping"),
        ({"reservation": {"label": "x", "comment": False}}, "must be true"),
        ({"pool": {"max_active_processes": 2}}, "concurrency must be 1"),
        ({"pool": {"body": {"type": "embody"}}}, "must be 'headless'"),
        ({"unknown": True}, "unknown key"),
    ],
)
def test_malformed_config_is_rejected(change, message):
    with pytest.raises(RegistrarError, match=message):
        validate_config(_config(**change))


def test_worker_identity_resolves_rules_into_worker_guidance():
    config = validate_config(
        _config(worker_identity="repository-issue-loop-default")
    )
    assert config["worker_identity"] == "repository-issue-loop-default"
    assert "supersede" in config["worker_guidance"]


def test_worker_identity_resolves_repo_local_override_via_cwd(tmp_path):
    """Regression guard: a declaring repo's own identity resolves when the
    caller's own cwd is NOT that repo (e.g. a supervisor daemon tick),
    provided the repo root is threaded through as ``cwd``."""
    identities_dir = tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities"
    identities_dir.mkdir(parents=True)
    (identities_dir / "custom.identity.md").write_text(
        "---\nname: custom\ndescription: A custom identity.\n---\n\n"
        "Follow the custom rules.\n",
        encoding="utf-8",
    )
    config = validate_config(_config(worker_identity="custom"), cwd=tmp_path)
    assert config["worker_guidance"] == "Follow the custom rules."


def test_expand_repository_issue_loop_stamps_repo_root_as_emitter_cwd(tmp_path):
    """Regression guard for the same issue at the declaration-expansion
    boundary: the materialized emitter spec must carry the declaring repo's
    root as its ``cwd`` so a later out-of-process re-validation (the
    supervisor daemon's own tick, in producers/emitter.py) resolves the same
    repo-local identity rather than the daemon's own working directory."""
    identities_dir = tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities"
    identities_dir.mkdir(parents=True)
    (identities_dir / "custom.identity.md").write_text(
        "---\nname: custom\ndescription: A custom identity.\n---\n\n"
        "Follow the custom rules.\n",
        encoding="utf-8",
    )
    source, _pool = expand_repository_issue_loop(
        _config(worker_identity="custom"), repo_root=tmp_path
    )
    assert source.spec["cwd"] == str(tmp_path.resolve())


def test_expand_repository_issue_loop_normalizes_relative_repo_root(tmp_path, monkeypatch):
    """Regression guard: a relative ``repo_root`` must be stamped onto the
    emitter spec as an absolute path. Left relative, registrar_discovery's
    own path-resolution step would re-interpret it relative to the registrar
    directory (not this repo's root), and an out-of-process daemon has no
    reliable relative base of its own either."""
    identities_dir = tmp_path / ".copilot-extensions" / "agent-dispatch" / "identities"
    identities_dir.mkdir(parents=True)
    (identities_dir / "custom.identity.md").write_text(
        "---\nname: custom\ndescription: A custom identity.\n---\n\n"
        "Follow the custom rules.\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path.parent)
    source, _pool = expand_repository_issue_loop(
        _config(worker_identity="custom"), repo_root=tmp_path.name
    )
    assert source.spec["cwd"] == str(tmp_path.resolve())


def test_worker_identity_and_worker_guidance_are_mutually_exclusive():
    with pytest.raises(RegistrarError, match="mutually exclusive"):
        validate_config(
            _config(
                worker_identity="repository-issue-loop-default",
                worker_guidance="inline prose",
            )
        )


def test_unknown_worker_identity_is_rejected():
    with pytest.raises(RegistrarError, match="no identity file found"):
        validate_config(_config(worker_identity="does-not-exist"))


def test_inline_worker_guidance_still_supported_without_identity():
    config = validate_config(_config(worker_guidance="be nice"))
    assert config["worker_identity"] == ""
    assert config["worker_guidance"] == "be nice"


def test_validate_config_accepts_azure_devops_provider():
    config = validate_config(
        _config(
            repo="example-org/example-project",
            forge={"provider": "azure-devops", "producer_login": "issue-bot"},
        )
    )
    assert config["forge"] == {
        "provider": "azure-devops",
        "producer_login": "issue-bot",
        "discovery_scope": None,
    }


def test_forge_provider_for_selects_github():
    config = validate_config(_config())
    provider = _forge_provider_for(config)
    assert isinstance(provider, GitHubProvider)
    assert provider.expected_login == "issue-bot"


def test_forge_provider_for_selects_azure_devops():
    config = validate_config(
        _config(
            repo="example-org/example-project",
            forge={"provider": "azure-devops", "producer_login": "issue-bot"},
        )
    )
    provider = _forge_provider_for(config)
    assert isinstance(provider, AzureDevOpsProvider)
    assert provider.expected_login == "issue-bot"


def test_validate_config_accepts_azure_devops_discovery_scope():
    config = validate_config(
        _config(
            repo="example-org/example-project",
            forge={
                "provider": "azure-devops",
                "producer_login": "issue-bot",
                "discovery_scope": {
                    "work_item_types": ["Bug", "Task"],
                    "area_path": "example-project\\Team",
                    "max_age_days": 180,
                },
            },
        )
    )
    assert config["forge"]["discovery_scope"] == {
        "work_item_types": ["Bug", "Task"],
        "area_path": "example-project\\Team",
        "max_age_days": 180,
    }


def test_validate_config_defaults_rehearsal_mode_false():
    assert validate_config(_config())["rehearsal_mode"] is False
    assert validate_config(_config(rehearsal_mode=True))["rehearsal_mode"] is True


def test_validate_config_rejects_non_bool_rehearsal_mode():
    with pytest.raises(RegistrarError, match="rehearsal_mode: expected true/false"):
        validate_config(_config(rehearsal_mode="yes"))


def test_validate_config_rejects_discovery_scope_for_github():
    with pytest.raises(RegistrarError, match="only supported"):
        validate_config(
            _config(
                forge={
                    "provider": "github",
                    "producer_login": "issue-bot",
                    "discovery_scope": {"area_path": "x"},
                }
            )
        )


def test_validate_config_rejects_empty_discovery_scope():
    with pytest.raises(RegistrarError, match="must narrow by at least one"):
        validate_config(
            _config(
                repo="example-org/example-project",
                forge={
                    "provider": "azure-devops",
                    "producer_login": "issue-bot",
                    "discovery_scope": {},
                },
            )
        )


def test_forge_provider_for_threads_discovery_scope_to_azure_devops():
    config = validate_config(
        _config(
            repo="example-org/example-project",
            forge={
                "provider": "azure-devops",
                "producer_login": "issue-bot",
                "discovery_scope": {"area_path": "example-project\\Team"},
            },
        )
    )
    provider = _forge_provider_for(config)
    assert isinstance(provider, AzureDevOpsProvider)
    assert provider.discovery_scope == {
        "work_item_types": [],
        "area_path": "example-project\\Team",
        "max_age_days": None,
    }


def test_validate_config_accepts_gitea_provider_with_connection_fields():
    """A well-formed gitea declaration (provider, producer_login, and a
    valid api_base) validates cleanly, with token_env defaulting to
    GITEA_TOKEN when not given."""
    config = validate_config(
        _config(
            repo="example-org/example-project",
            forge={
                "provider": "gitea",
                "producer_login": "issue-bot",
                "api_base": "https://gitea.example.com",
            },
        )
    )
    assert config["forge"]["provider"] == "gitea"
    assert config["forge"]["api_base"] == "https://gitea.example.com"
    assert config["forge"]["token_env"] == "GITEA_TOKEN"


def test_validate_config_requires_api_base_for_gitea_provider():
    with pytest.raises(RegistrarError, match="forge.api_base"):
        validate_config(
            _config(
                repo="example-org/example-project",
                forge={"provider": "gitea", "producer_login": "issue-bot"},
            )
        )


def test_validate_config_rejects_gitea_connection_fields_for_other_providers():
    with pytest.raises(
        RegistrarError, match="only supported for forge.provider 'gitea'"
    ):
        validate_config(
            _config(
                forge={
                    "provider": "github",
                    "producer_login": "issue-bot",
                    "api_base": "https://gitea.example.com",
                },
            )
        )


def test_validate_config_normalizes_gitea_api_base_trailing_slash():
    config = validate_config(
        _config(
            repo="example-org/example-project",
            forge={
                "provider": "gitea",
                "producer_login": "issue-bot",
                "api_base": "https://gitea.example.com/",
            },
        )
    )
    assert config["forge"]["api_base"] == "https://gitea.example.com"


def test_validate_config_normalizes_gitea_api_base_surrounding_whitespace():
    config = validate_config(
        _config(
            repo="example-org/example-project",
            forge={
                "provider": "gitea",
                "producer_login": "issue-bot",
                "api_base": "  https://gitea.example.com/  ",
            },
        )
    )
    assert config["forge"]["api_base"] == "https://gitea.example.com"


def test_validate_config_rejects_gitea_api_base_that_normalizes_to_empty():
    """A bare '/' (or whitespace-only value) looks non-empty before
    normalization but collapses to '' after stripping and trimming the
    trailing slash -- it must be rejected outright, not accepted and left
    to fail forever on the adapter's first real request."""
    with pytest.raises(RegistrarError, match="forge.api_base"):
        validate_config(
            _config(
                repo="example-org/example-project",
                forge={
                    "provider": "gitea",
                    "producer_login": "issue-bot",
                    "api_base": "/",
                },
            )
        )


@pytest.mark.parametrize(
    "bad_api_base",
    [
        "not a URL",
        "ftp://example.com",
        "https://user:pass@example.com",
        "https://example.com/?query=1",
        "https://example.com/#frag",
    ],
)
def test_validate_config_rejects_gitea_api_base_that_is_not_a_usable_url(bad_api_base):
    """A value that is merely non-empty text -- not an absolute http(s)
    authority, or one carrying embedded credentials/query/fragment -- must
    never "validate" successfully and then either fail on every real
    request or target an unintended authority."""
    with pytest.raises(RegistrarError, match="forge.api_base"):
        validate_config(
            _config(
                repo="example-org/example-project",
                forge={
                    "provider": "gitea",
                    "producer_login": "issue-bot",
                    "api_base": bad_api_base,
                },
            )
        )


def test_validate_config_accepts_path_hosted_gitea_api_base():
    config = validate_config(
        _config(
            repo="example-org/example-project",
            forge={
                "provider": "gitea",
                "producer_login": "issue-bot",
                "api_base": "https://example.com/gitea",
            },
        )
    )
    assert config["forge"]["api_base"] == "https://example.com/gitea"


def test_validate_config_normalizes_gitea_token_env_whitespace():
    config = validate_config(
        _config(
            repo="example-org/example-project",
            forge={
                "provider": "gitea",
                "producer_login": "issue-bot",
                "api_base": "https://gitea.example.com",
                "token_env": "  MY_TOKEN  ",
            },
        )
    )
    assert config["forge"]["token_env"] == "MY_TOKEN"


def test_validate_config_accepts_custom_gitea_token_env():
    config = validate_config(
        _config(
            repo="example-org/example-project",
            forge={
                "provider": "gitea",
                "producer_login": "issue-bot",
                "api_base": "https://gitea.example.com",
                "token_env": "MY_GITEA_TOKEN",
            },
        )
    )
    assert config["forge"]["token_env"] == "MY_GITEA_TOKEN"


def test_forge_provider_for_selects_gitea_provider():
    config = {
        "repo": "example-org/example-project",
        "forge": {
            "provider": "gitea",
            "producer_login": "issue-bot",
            "api_base": "https://gitea.example.com",
            "token_env": "GITEA_TOKEN",
        },
    }
    provider = _forge_provider_for(config)
    assert isinstance(provider, GiteaProvider)
    assert provider.expected_login == "issue-bot"
    assert provider.api_base == "https://gitea.example.com"
    assert provider.token_env == "GITEA_TOKEN"


# -- script forge provider (Phase 2 of agent-dispatch-recipe-composability) ---

def test_validate_config_requires_command_for_script_provider(tmp_path):
    with pytest.raises(RegistrarError, match="forge.command: required"):
        validate_config(
            _config(
                repo="my-backlog",
                forge={"provider": "script", "producer_login": "issue-bot"},
            )
        )


def test_validate_config_rejects_script_fields_for_github_provider():
    with pytest.raises(RegistrarError, match="only supported for forge.provider 'script'"):
        validate_config(
            _config(
                forge={
                    "provider": "github",
                    "producer_login": "issue-bot",
                    "command": ["./script.sh"],
                }
            )
        )


def test_validate_config_reports_the_actual_unsupported_script_only_field(tmp_path):
    """Regression guard: the error must name the field(s) actually present,
    not an unconditional `command/cwd/timeout_seconds` list -- a GitHub
    declaration that mistakenly sets `forge.backlog` (a `script`-only
    field added after that original message was written) got a
    misleading diagnostic naming fields it never set at all ('Report
    actual unsupported script-only fields in validation errors')."""
    with pytest.raises(RegistrarError, match="forge.backlog: only supported"):
        validate_config(
            _config(
                forge={
                    "provider": "github",
                    "producer_login": "issue-bot",
                    "backlog": "pending-work-items",
                }
            )
        )
    with pytest.raises(RegistrarError, match="forge.namespace: only supported"):
        validate_config(
            _config(
                forge={
                    "provider": "azure-devops",
                    "producer_login": "issue-bot",
                    "namespace": "shared-backlog",
                }
            )
        )


def test_validate_config_accepts_a_non_owner_name_repo_for_script_provider(tmp_path):
    """The script interprets `repo` itself -- the motivating consumer's
    own case has no real forge-shaped 'owner/name' concept at all."""
    (tmp_path / "script.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./script.sh"],
            },
        ),
        cwd=tmp_path,
    )
    assert config["repo"] == "pending-work-items"


def test_validate_config_resolves_a_relative_script_command_against_repo_root(tmp_path):
    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./tools/poller.py", "--flag"],
            },
        ),
        cwd=tmp_path,
    )
    assert config["forge"]["command"] == [
        sys.executable,
        str((tmp_path / "tools" / "poller.py").resolve()),
        "--flag",
    ]


def test_validate_config_leaves_an_absolute_script_command_path_untouched(tmp_path):
    absolute_script = str((tmp_path / "poller.py").resolve())
    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": [absolute_script],
            },
        ),
        cwd=tmp_path / "unrelated",
    )
    assert config["forge"]["command"] == [sys.executable, absolute_script]


def test_validate_config_rejects_a_relative_script_command_with_no_repo_root(tmp_path):
    """Regression guard: a direct declaration read with no resolvable
    registrar context (no known repo root) must never silently fall back
    to resolving a relative script path against the daemon process's own
    incidental working directory."""
    with pytest.raises(
        RegistrarError, match="requires a known declaring repo root"
    ):
        validate_config(
            _config(
                repo="pending-work-items",
                forge={
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["./poller.py"],
                    "cwd": str(tmp_path),
                },
            ),
        )


def test_validate_config_rejects_an_unset_script_cwd_with_no_repo_root(tmp_path):
    absolute_script = str((tmp_path / "poller.py").resolve())
    with pytest.raises(RegistrarError, match="requires an absolute path"):
        validate_config(
            _config(
                repo="pending-work-items",
                forge={
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": [absolute_script],
                },
            ),
        )


def test_validate_config_rejects_a_relative_script_cwd_with_no_repo_root(tmp_path):
    absolute_script = str((tmp_path / "poller.py").resolve())
    with pytest.raises(RegistrarError, match="requires an absolute path"):
        validate_config(
            _config(
                repo="pending-work-items",
                forge={
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": [absolute_script],
                    "cwd": "./subdir",
                },
            ),
        )


def test_validate_config_accepts_fully_absolute_script_fields_with_no_repo_root(tmp_path):
    """An absolute `command`/`cwd` never depends on a repo root in the
    first place, so a declaration using both is accepted even when no
    repo root is known."""
    absolute_script = str((tmp_path / "poller.py").resolve())
    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": [absolute_script],
                "cwd": str(tmp_path),
            },
        ),
    )
    assert config["forge"]["command"] == [sys.executable, absolute_script]
    assert config["forge"]["cwd"] == str(tmp_path.resolve())


def test_validate_config_converts_a_symlink_loop_into_a_registrar_error(tmp_path, monkeypatch):
    """`Path.resolve()` raises `RuntimeError` for a symlink loop (and
    `OSError` for other resolution failures) -- a malformed declared
    command must surface as the promised configuration `RegistrarError`,
    never an uncaught runtime exception. Mocks the failure directly
    (rather than constructing a real symlink loop, whose exact triggering
    conditions proved filesystem/pytest-tmp-path-layout-dependent) for a
    deterministic, platform-independent regression guard."""
    from pathlib import Path as PathlibPath

    original_resolve = PathlibPath.resolve

    def fake_resolve(self, *args, **kwargs):
        if self.name == "poller.py":
            raise RuntimeError("Symlink loop")
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(PathlibPath, "resolve", fake_resolve)

    with pytest.raises(RegistrarError, match="failed to resolve path"):
        validate_config(
            _config(
                repo="pending-work-items",
                forge={
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["loop_a/poller.py"],
                },
            ),
            cwd=tmp_path,
        )


def test_validate_config_prefixes_a_dot_sh_script_with_bash(tmp_path):
    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.sh"],
            },
        ),
        cwd=tmp_path,
    )
    bash = shutil.which("bash")
    assert bash is not None
    assert config["forge"]["command"] == [bash, str((tmp_path / "poller.sh").resolve())]


def test_validate_config_leaves_a_non_script_suffix_command_unprefixed(tmp_path):
    """An executable with no recognized interpreter-requiring suffix (e.g.
    an already-compiled binary, or a shebang'd extensionless script) is
    invoked directly, matching POSIX's own exec semantics."""
    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller"],
            },
        ),
        cwd=tmp_path,
    )
    assert config["forge"]["command"] == [str((tmp_path / "poller").resolve())]


@pytest.mark.skipif(
    __import__("shutil").which("pwsh") is None,
    reason="pwsh not available on this host to resolve",
)
def test_validate_config_prefixes_a_dot_ps1_script_with_pwsh(tmp_path):
    import shutil as _shutil

    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.ps1"],
            },
        ),
        cwd=tmp_path,
    )
    pwsh = _shutil.which("pwsh")
    assert config["forge"]["command"] == [
        pwsh,
        "-NoProfile",
        "-NonInteractive",
        "-File",
        str((tmp_path / "poller.ps1").resolve()),
    ]


def test_validate_config_rejects_a_dot_sh_script_without_bash(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "agent_dispatch.script_provider.shutil.which", lambda _name: None
    )
    with pytest.raises(RegistrarError, match="requires bash"):
        validate_config(
            _config(
                repo="pending-work-items",
                forge={
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["./poller.sh"],
                },
            ),
            cwd=tmp_path,
        )


def test_validate_config_resolves_a_relative_script_cwd_against_repo_root(tmp_path):
    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
                "cwd": "./subdir",
            },
        ),
        cwd=tmp_path,
    )
    assert config["forge"]["cwd"] == str((tmp_path / "subdir").resolve())


def test_validate_config_defaults_script_cwd_to_the_repo_root(tmp_path):
    """An unset `forge.cwd` must still anchor the subprocess to the
    declaring repo root, never the daemon process's own incidental cwd --
    the same rule `command`'s own resolution already follows."""
    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
            },
        ),
        cwd=tmp_path,
    )
    assert config["forge"]["cwd"] == str(tmp_path.resolve())



def test_validate_config_defaults_script_timeout_and_accepts_an_override(tmp_path):
    from agent_dispatch.script_provider import DEFAULT_TIMEOUT_SECONDS

    default_config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
            },
        ),
        cwd=tmp_path,
    )
    assert default_config["forge"]["timeout_seconds"] == DEFAULT_TIMEOUT_SECONDS

    overridden = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
                "timeout_seconds": 5,
            },
        ),
        cwd=tmp_path,
    )
    assert overridden["forge"]["timeout_seconds"] == 5.0


def test_validate_config_rejects_a_non_positive_script_timeout(tmp_path):
    with pytest.raises(RegistrarError, match="timeout_seconds: expected a finite number > 0"):
        validate_config(
            _config(
                repo="pending-work-items",
                forge={
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["./poller.py"],
                    "timeout_seconds": 0,
                },
            ),
            cwd=tmp_path,
        )


@pytest.mark.parametrize("bad_timeout", [math.inf, -math.inf, math.nan])
def test_validate_config_rejects_a_non_finite_script_timeout(tmp_path, bad_timeout):
    with pytest.raises(RegistrarError, match="timeout_seconds: expected a finite number > 0"):
        validate_config(
            _config(
                repo="pending-work-items",
                forge={
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["./poller.py"],
                    "timeout_seconds": bad_timeout,
                },
            ),
            cwd=tmp_path,
        )


@pytest.mark.parametrize("bad_timeout", [1800.1, 1e308, 3600])
def test_validate_config_rejects_an_excessively_large_script_timeout(tmp_path, bad_timeout):
    """A very large but technically-finite timeout (e.g. `1e308`) still
    reaches `Popen.communicate()` and raises `OverflowError` before
    waiting at all -- finiteness alone is not a valid bound."""
    with pytest.raises(RegistrarError, match=r"<= 1800"):
        validate_config(
            _config(
                repo="pending-work-items",
                forge={
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["./poller.py"],
                    "timeout_seconds": bad_timeout,
                },
            ),
            cwd=tmp_path,
        )


def test_script_provider_init_rejects_an_excessively_large_timeout():
    from agent_dispatch.script_provider import ScriptProvider

    with pytest.raises(ValueError, match=r"<= 1800"):
        ScriptProvider(["./poller.py"], timeout_seconds=1e308)


def test_validate_config_rejects_a_huge_integer_script_timeout(tmp_path):
    """A huge JSON/YAML integer (e.g. far beyond any float's range) raises
    `OverflowError` on conversion to `float` rather than producing a
    non-finite value -- the validator must still classify it as an
    ordinary out-of-range timeout, not crash."""
    with pytest.raises(RegistrarError, match=r"<= 1800"):
        validate_config(
            _config(
                repo="pending-work-items",
                forge={
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["./poller.py"],
                    "timeout_seconds": 10**400,
                },
            ),
            cwd=tmp_path,
        )


def test_script_provider_init_rejects_a_huge_integer_timeout():
    from agent_dispatch.script_provider import ScriptProvider

    with pytest.raises(ValueError, match=r"<= 1800"):
        ScriptProvider(["./poller.py"], timeout_seconds=10**400)


def test_forge_provider_for_selects_script_provider(tmp_path):
    from agent_dispatch.script_provider import ScriptProvider

    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
                "cwd": ".",
                "timeout_seconds": 5,
            },
        ),
        cwd=tmp_path,
    )
    provider = _forge_provider_for(config)
    assert isinstance(provider, ScriptProvider)
    assert provider.command == [sys.executable, str((tmp_path / "poller.py").resolve())]
    assert provider.cwd == str(tmp_path.resolve())
    assert provider.timeout_seconds == 5.0


def test_forge_provider_for_does_not_corrupt_an_already_resolved_script_command(tmp_path):
    """Regression guard: `run_tick` pre-validates `config` via
    `validate_config(config, cwd=cwd)` (resolving + portability-wrapping
    `forge.command` once) and then calls `_forge_provider_for(config,
    cwd=cwd)` on that *already*-wrapped config. A naive re-normalization
    there would treat the wrapped `command[0]` (e.g. `sys.executable`) as
    if it were still the raw script path, double-wrapping it and
    corrupting both the final argv and the resource-key namespace."""
    from agent_dispatch.script_provider import ScriptProvider

    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
            },
        ),
        cwd=tmp_path,
    )
    expected_command = [sys.executable, str((tmp_path / "poller.py").resolve())]
    assert config["forge"]["command"] == expected_command

    # Same call shape `run_tick` itself uses after its own validate_config.
    provider = _forge_provider_for(config, cwd=tmp_path)
    assert isinstance(provider, ScriptProvider)
    assert provider.command == expected_command
    assert provider.producer_login == "issue-bot"


def test_forge_provider_for_normalizes_a_rootless_absolute_script_command(tmp_path):
    """Regression guard: a direct declaration read outside any recognized
    registrar surface (no declaring repo root known, `cwd=None`) must
    still have its already-absolute `.py` script wrapped with the
    required interpreter prefix -- build_provider previously skipped
    normalization entirely whenever `repo_root` was `None`, reaching the
    subprocess with a bare, unwrapped argv that fails on Windows."""
    from agent_dispatch.script_provider import ScriptProvider

    absolute_script = str((tmp_path / "poller.py").resolve())
    raw_config = {
        "forge": {
            "provider": "script",
            "producer_login": "issue-bot",
            "command": [absolute_script],
            "cwd": str(tmp_path),
        }
    }
    provider = _forge_provider_for(raw_config)  # no cwd -- no root known
    assert isinstance(provider, ScriptProvider)
    assert provider.command == [sys.executable, absolute_script]
    assert provider.cwd == str(tmp_path.resolve())


def test_resource_key_namespaces_script_providers_by_resolved_command(tmp_path):
    """Regression guard: unlike a real `owner/name`/`organization/project`,
    a script backlog's own `repo` has no inherent global-uniqueness
    guarantee -- two unrelated declarations choosing the same `repo` label
    must not collide on the coordinator's resource key merely because they
    share that label."""
    (tmp_path / "poller_a.py").write_text("", encoding="utf-8")
    (tmp_path / "poller_b.py").write_text("", encoding="utf-8")
    config_a = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller_a.py"],
            },
        ),
        cwd=tmp_path,
    )
    config_b = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller_b.py"],
            },
        ),
        cwd=tmp_path,
    )
    assert _resource_key(config_a, 1) != _resource_key(config_b, 1)
    # Same declaration, same issue -> stable/reproducible key.
    assert _resource_key(config_a, 1) == _resource_key(config_a, 1)


def test_resource_key_namespaces_the_same_script_differently_by_args_and_cwd(tmp_path):
    """A reusable script declared with different arguments or working
    directories for two distinct backlogs must not collide just because
    they share both the script path and a `repo` label."""
    (tmp_path / "poller.py").write_text("", encoding="utf-8")
    (tmp_path / "work-a").mkdir()
    (tmp_path / "work-b").mkdir()
    base = {
        "repo": "pending-work-items",
        "forge": {
            "provider": "script",
            "producer_login": "issue-bot",
            "command": ["./poller.py"],
        },
    }
    config_args = validate_config(
        _config(
            repo="pending-work-items",
            forge={**base["forge"], "command": ["./poller.py", "--backlog=a"]},
        ),
        cwd=tmp_path,
    )
    config_args2 = validate_config(
        _config(
            repo="pending-work-items",
            forge={**base["forge"], "command": ["./poller.py", "--backlog=b"]},
        ),
        cwd=tmp_path,
    )
    assert _resource_key(config_args, 1) != _resource_key(config_args2, 1)

    absolute_script = str((tmp_path / "poller.py").resolve())
    config_cwd_a = validate_config(
        _config(
            repo="pending-work-items",
            forge={**base["forge"], "command": [absolute_script], "cwd": "work-a"},
        ),
        cwd=tmp_path,
    )
    config_cwd_b = validate_config(
        _config(
            repo="pending-work-items",
            forge={**base["forge"], "command": [absolute_script], "cwd": "work-b"},
        ),
        cwd=tmp_path,
    )
    assert _resource_key(config_cwd_a, 1) != _resource_key(config_cwd_b, 1)


def _init_git_repo_with_remote(root, remote_url: str) -> None:
    import subprocess as _subprocess

    root.mkdir(parents=True, exist_ok=True)
    _subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    _subprocess.run(
        ["git", "remote", "add", "origin", remote_url], cwd=root, check=True
    )


def test_declaring_repo_identity_caches_the_git_remote_probe_by_root(
    tmp_path, monkeypatch
):
    """Regression guard: `_derive_git_remote_alias` is documented
    (`registrar_lane_aliases.py`) as a registration-time-only probe (two
    subprocess calls, each up to a 5-second timeout) -- `run_tick`
    re-validates a `script` forge config on *every scheduled tick*, so an
    uncached call here would re-run that probe on what is otherwise a hot
    path ('Git remote probing stalls every script-provider validation').
    A second validation of the same declaring repo root must not re-probe
    git at all."""
    from agent_dispatch import script_provider as sp

    repo_root = tmp_path / "repo"
    _init_git_repo_with_remote(repo_root, "git@example.com:org/repo.git")
    (repo_root / "scripts").mkdir(parents=True)
    sp._REPO_IDENTITY_CACHE.clear()
    probe_calls = []

    import agent_dispatch.registrar_lane_aliases as rla

    real_derive = rla._derive_git_remote_alias

    def counting_derive(root):
        probe_calls.append(root)
        return real_derive(root)

    monkeypatch.setattr(rla, "_derive_git_remote_alias", counting_derive)

    forge = {
        "provider": "script",
        "producer_login": "issue-bot",
        "command": ["./scripts/poller.py"],
    }
    first = validate_config(_config(repo="pending-work-items", forge=forge), cwd=repo_root)
    second = validate_config(_config(repo="pending-work-items", forge=forge), cwd=repo_root)

    assert len(probe_calls) == 1
    assert _resource_key(first, 1) == _resource_key(second, 1)


def test_resource_key_is_stable_for_the_same_declaration_across_machine_local_roots(tmp_path):
    """High-severity regression guard: the same logical declaration (the
    same repo checkout, run on a second host for redundancy/failover, or
    under a different local Python installation) must dedup against
    itself through the coordinator. Both machine roots are real git
    checkouts sharing one remote -- the namespace must resolve from that
    canonicalized remote (stable cross-host identity), never the
    machine-resolved absolute script path (which legitimately differs per
    host/checkout)."""
    machine_a_root = tmp_path / "machine-a" / "checkout"
    machine_b_root = tmp_path / "machine-b" / "different-checkout-path"
    remote_url = "git@example.com:org/pending-work-items.git"
    _init_git_repo_with_remote(machine_a_root, remote_url)
    _init_git_repo_with_remote(machine_b_root, remote_url)
    (machine_a_root / "scripts").mkdir(parents=True)
    (machine_b_root / "scripts").mkdir(parents=True)
    forge = {
        "provider": "script",
        "producer_login": "issue-bot",
        "command": ["./scripts/poller.py"],
    }
    config_a = validate_config(
        _config(repo="pending-work-items", forge=forge), cwd=machine_a_root
    )
    config_b = validate_config(
        _config(repo="pending-work-items", forge=forge), cwd=machine_b_root
    )
    # Different hosts resolve to genuinely different absolute script paths...
    assert config_a["forge"]["command"] != config_b["forge"]["command"]
    # ...but the coordinator namespace (and therefore the resource key)
    # must still agree, so the two hosts dedup against the same backlog.
    assert _resource_key(config_a, 1) == _resource_key(config_b, 1)


def test_resource_key_distinguishes_unrelated_repos_sharing_a_script_path(tmp_path):
    """High-severity regression guard: two *unrelated* repositories that
    happen to declare the same relative script path, producer_login, and
    `repo` label must not collide just because the raw declaration
    spelling matches -- the declaring repository's own identity must be
    part of the namespace."""
    repo_a_root = tmp_path / "repo-a"
    repo_b_root = tmp_path / "repo-b"
    _init_git_repo_with_remote(repo_a_root, "git@example.com:org/repo-a.git")
    _init_git_repo_with_remote(repo_b_root, "git@example.com:org/repo-b.git")
    (repo_a_root / "scripts").mkdir(parents=True)
    (repo_b_root / "scripts").mkdir(parents=True)
    forge = {
        "provider": "script",
        "producer_login": "issue-bot",
        "command": ["scripts/poller.py"],
    }
    config_a = validate_config(
        _config(repo="pending-work-items", forge=forge), cwd=repo_a_root
    )
    config_b = validate_config(
        _config(repo="pending-work-items", forge=forge), cwd=repo_b_root
    )
    assert _resource_key(config_a, 1) != _resource_key(config_b, 1)


def test_resource_key_ignores_ambient_git_env_during_identity_probing(tmp_path, monkeypatch):
    """High-severity regression guard: an ambient `GIT_DIR`/`GIT_WORK_TREE`
    pointing at an unrelated repo must not redirect the declaring
    repository's own identity probe -- the probe must resolve `repo_a`'s
    own remote despite the poisoned environment, not silently adopt
    `repo_b`'s."""
    repo_a_root = tmp_path / "repo-a"
    repo_b_root = tmp_path / "repo-b"
    _init_git_repo_with_remote(repo_a_root, "git@example.com:org/repo-a.git")
    _init_git_repo_with_remote(repo_b_root, "git@example.com:org/repo-b.git")
    (repo_a_root / "scripts").mkdir(parents=True)
    forge = {
        "provider": "script",
        "producer_login": "issue-bot",
        "command": ["scripts/poller.py"],
    }
    clean_config = validate_config(
        _config(repo="pending-work-items", forge=forge), cwd=repo_a_root
    )
    monkeypatch.setenv("GIT_DIR", str(repo_b_root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(repo_b_root))
    poisoned_config = validate_config(
        _config(repo="pending-work-items", forge=forge), cwd=repo_a_root
    )
    assert _resource_key(clean_config, 1) == _resource_key(poisoned_config, 1)


def test_resource_key_prefers_an_explicit_namespace_over_derivation(tmp_path):
    """An explicit `forge.namespace` is the only cross-host-stable
    guarantee strong enough for a correctness-critical redundant
    deployment -- it must win outright over the best-effort auto-derived
    identity, even when the auto-derived values would otherwise collide
    or diverge."""
    repo_a_root = tmp_path / "repo-a"
    repo_b_root = tmp_path / "repo-b"
    _init_git_repo_with_remote(repo_a_root, "git@example.com:org/repo-a.git")
    _init_git_repo_with_remote(repo_b_root, "git@example.com:org/repo-b.git")
    (repo_a_root / "scripts").mkdir(parents=True)
    (repo_b_root / "scripts").mkdir(parents=True)
    config_a = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["scripts/poller.py"],
                "namespace": "shared-backlog",
            },
        ),
        cwd=repo_a_root,
    )
    config_b = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["scripts/poller.py"],
                "namespace": "shared-backlog",
            },
        ),
        cwd=repo_b_root,
    )
    assert _resource_key(config_a, 1) == _resource_key(config_b, 1)


def test_cross_repo_extends_rejects_an_inherited_relative_script_command(tmp_path):
    """Regression guard: a leaf declaration in one repo that `extends:` a
    base in a *different* repo and inherits that base's relative
    `forge.command` must be refused outright -- `validate_script_forge_config`
    only ever has the leaf's own repo root to resolve against, so silently
    resolving an inherited relative path would execute (or fail to find) a
    script in the wrong repository ('Resolve script overrides relative to
    their declaration origin' / 'Resolve inherited script paths relative to
    their declaring repository')."""
    base_root = tmp_path / "base-repo"
    leaf_root = tmp_path / "leaf-repo"
    base_root.mkdir()
    leaf_root.mkdir()
    base_file = base_root / "base.json"
    base_file.write_text(
        json.dumps(
            {
                "forge": {
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["./base-poller.py"],
                }
            }
        ),
        encoding="utf-8",
    )
    leaf_file = leaf_root / "issues.json"
    leaf_file.write_text(
        json.dumps(
            {
                "extends": str(base_file),
                "name": "backlog",
                "kind": "repository-issue-loop",
                "repo": "leaf/project",
                "source": "repository-backlog",
                "cadence_seconds": 3600,
                "task_label": "repository-issue-work",
                "reservation": {"label": "agent-reserved", "comment": True},
                "pool": {"max_active_processes": 1, "body": {"agent": "issue-worker"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RegistrarError, match="inherited via `extends:`"):
        read_declaration_file_set(leaf_file, repo_root=leaf_root)


def test_cross_repo_extends_accepts_an_inherited_absolute_script_command(tmp_path):
    """The documented workaround: an inherited `forge.command` that is
    already absolute resolves unambiguously regardless of which repo
    supplied it, so it must not be refused."""
    base_root = tmp_path / "base-repo"
    leaf_root = tmp_path / "leaf-repo"
    base_root.mkdir()
    leaf_root.mkdir()
    poller = base_root / "base-poller.py"
    poller.write_text("", encoding="utf-8")
    base_file = base_root / "base.json"
    base_file.write_text(
        json.dumps(
            {
                "forge": {
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": [str(poller)],
                }
            }
        ),
        encoding="utf-8",
    )
    leaf_file = leaf_root / "issues.json"
    leaf_file.write_text(
        json.dumps(
            {
                "extends": str(base_file),
                "name": "backlog",
                "kind": "repository-issue-loop",
                "repo": "leaf/project",
                "source": "repository-backlog",
                "cadence_seconds": 3600,
                "task_label": "repository-issue-work",
                "reservation": {"label": "agent-reserved", "comment": True},
                "pool": {"max_active_processes": 1, "body": {"agent": "issue-worker"}},
            }
        ),
        encoding="utf-8",
    )
    declarations = read_declaration_file_set(leaf_file, repo_root=leaf_root)
    assert declarations  # no RegistrarError


def test_same_repo_extends_does_not_require_an_absolute_inherited_command(tmp_path):
    """A base and leaf declared in the *same* repo share one unambiguous
    repo root -- inheriting a relative `forge.command` through `extends:`
    there is unaffected by the cross-repo rejection above."""
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "scripts").mkdir()
    (repo_root / "scripts" / "poller.py").write_text("", encoding="utf-8")
    base_file = repo_root / "base.json"
    base_file.write_text(
        json.dumps(
            {
                "forge": {
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["scripts/poller.py"],
                }
            }
        ),
        encoding="utf-8",
    )
    leaf_file = repo_root / "issues.json"
    leaf_file.write_text(
        json.dumps(
            {
                "extends": "base.json",
                "name": "backlog",
                "kind": "repository-issue-loop",
                "repo": "leaf/project",
                "source": "repository-backlog",
                "cadence_seconds": 3600,
                "task_label": "repository-issue-work",
                "reservation": {"label": "agent-reserved", "comment": True},
                "pool": {"max_active_processes": 1, "body": {"agent": "issue-worker"}},
            }
        ),
        encoding="utf-8",
    )
    declarations = read_declaration_file_set(leaf_file, repo_root=repo_root)
    assert declarations  # no RegistrarError


def test_nested_cross_repo_extends_rejects_a_relative_command_from_a_deeper_hop(
    tmp_path,
):
    """Regression guard: `leaf (repo A) -> base (repo A) -> base (repo B)` --
    the leaf's own *immediate* `extends:` target is same-repo, but that
    intermediate base itself inherits `forge.command` from a *third*,
    different repository. Checking only the leaf's first hop would miss
    this entirely and resolve the inherited relative path against repo
    A's root instead of repo B's ('Track cross-repository provenance
    through the full extends chain')."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    deep_base_file = repo_b / "deep-base.json"
    deep_base_file.write_text(
        json.dumps(
            {
                "forge": {
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["./b-poller.py"],
                }
            }
        ),
        encoding="utf-8",
    )
    mid_base_file = repo_a / "mid-base.json"
    mid_base_file.write_text(
        json.dumps({"extends": str(deep_base_file)}), encoding="utf-8"
    )
    leaf_file = repo_a / "issues.json"
    leaf_file.write_text(
        json.dumps(
            {
                "extends": "mid-base.json",
                "name": "backlog",
                "kind": "repository-issue-loop",
                "repo": "leaf/project",
                "source": "repository-backlog",
                "cadence_seconds": 3600,
                "task_label": "repository-issue-work",
                "reservation": {"label": "agent-reserved", "comment": True},
                "pool": {"max_active_processes": 1, "body": {"agent": "issue-worker"}},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RegistrarError, match="inherited via `extends:`"):
        read_declaration_file_set(leaf_file, repo_root=repo_a)


def test_cross_repo_extends_accepts_a_same_repo_defining_hop_despite_a_further_cross_repo_ancestor(
    tmp_path,
):
    """Regression guard: `leaf (repo A) -> mid (repo A, defines
    forge.command) -> base (repo B)` -- `mid` is the *nearest* hop that
    actually defines `forge.command`, and it lives in the same repo as
    the leaf, so this must NOT be rejected even though `base` (which
    `mid` itself extends, purely for some unrelated field) lives in a
    different repository entirely. Checking only "does any hop in the
    chain live outside this repo" (rather than tracking each field's own
    nearest-defining hop) would wrongly reject this valid, same-repo
    resolution ('Track nearest defining hop for inherited path fields')."""
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    (repo_a / "scripts").mkdir()
    (repo_a / "scripts" / "poller.py").write_text("", encoding="utf-8")
    repo_b.mkdir()
    base_file = repo_b / "base.json"
    base_file.write_text(
        json.dumps({"reservation": {"label": "unrelated-base-field"}}),
        encoding="utf-8",
    )
    mid_base_file = repo_a / "mid-base.json"
    mid_base_file.write_text(
        json.dumps(
            {
                "extends": str(base_file),
                "forge": {
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["scripts/poller.py"],
                },
            }
        ),
        encoding="utf-8",
    )
    leaf_file = repo_a / "issues.json"
    leaf_file.write_text(
        json.dumps(
            {
                "extends": "mid-base.json",
                "name": "backlog",
                "kind": "repository-issue-loop",
                "repo": "leaf/project",
                "source": "repository-backlog",
                "cadence_seconds": 3600,
                "task_label": "repository-issue-work",
                "reservation": {"label": "agent-reserved", "comment": True},
                "pool": {"max_active_processes": 1, "body": {"agent": "issue-worker"}},
            }
        ),
        encoding="utf-8",
    )
    declarations = read_declaration_file_set(leaf_file, repo_root=repo_a)
    assert declarations  # no RegistrarError


def test_validate_config_rejects_an_empty_script_namespace(tmp_path):
    with pytest.raises(RegistrarError, match="forge.namespace: expected a non-empty string"):
        validate_config(
            _config(
                repo="pending-work-items",
                forge={
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["./poller.py"],
                    "namespace": "",
                },
            ),
            cwd=tmp_path,
        )


def test_validate_config_rejects_an_empty_script_backlog(tmp_path):
    with pytest.raises(RegistrarError, match="forge.backlog: expected a non-empty string"):
        validate_config(
            _config(
                repo="pending-work-items",
                forge={
                    "provider": "script",
                    "producer_login": "issue-bot",
                    "command": ["./poller.py"],
                    "backlog": "",
                },
            ),
            cwd=tmp_path,
        )


def test_backlog_identifier_defaults_to_repo_when_unset(tmp_path):
    """Regression guard: every existing declaration (and every non-`script`
    provider) never set `forge.backlog`, so it must keep seeing exactly
    `repo` as before."""
    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
            },
        ),
        cwd=tmp_path,
    )
    assert _backlog_identifier(config) == "pending-work-items"


def test_backlog_identifier_prefers_forge_backlog_over_repo(tmp_path):
    """`forge.backlog`, when set, decouples the script's own backlog
    identity from `repo`'s task-routing role -- see
    'Separate script backlog naming from the canonical task repo lane'."""
    config = validate_config(
        _config(
            repo="example/project",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
                "backlog": "pending-work-items",
            },
        ),
        cwd=tmp_path,
    )
    assert _backlog_identifier(config) == "pending-work-items"
    assert config["repo"] == "example/project"


def test_run_tick_sends_forge_backlog_to_the_script_provider_not_repo(tmp_path):
    """The script's own four operations must see `forge.backlog`, never
    `repo`, once `forge.backlog` is set -- otherwise setting it would be a
    no-op and the task-routing/backlog-identity coupling the field exists
    to break would persist."""

    class _RecordingProvider(FakeProvider):
        def list_open_issues(self, repo):
            self.seen_repo = repo
            return super().list_open_issues(repo)

        def reserve(self, repo, issue, reservation):
            self.seen_repo = repo
            return super().reserve(repo, issue, reservation)

        def claim(self, repo, issue, reservation, task_id):
            self.seen_repo = repo
            return super().claim(repo, issue, reservation, task_id)

    config = _config(
        repo="example/project",
        forge={
            "provider": "script",
            "producer_login": "issue-bot",
            "command": ["./poller.py"],
            "backlog": "pending-work-items",
        },
    )
    provider = _RecordingProvider([_issue(1)])
    result = run_tick(
        FakeClient(), config, provider=provider, clock=lambda: 10_000, cwd=tmp_path
    )

    assert provider.seen_repo == "pending-work-items"
    # The created task's own routing lane is untouched by `forge.backlog`.
    task = result["created"][0]
    assert task["repo"] == "example/project"
    assert task["target_repo"] == "example/project"


def test_resource_key_namespaces_script_providers_by_backlog_not_repo(tmp_path):
    """Two declarations sharing `repo` but choosing distinct
    `forge.backlog` values are unrelated backlogs and must not collide on
    the same reservation key -- the key must follow the backlog identity,
    not the (now routing-only) `repo`."""
    (tmp_path / "poller.py").write_text("", encoding="utf-8")
    config_a = validate_config(
        _config(
            repo="example/project",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
                "backlog": "backlog-a",
            },
        ),
        cwd=tmp_path,
    )
    config_b = validate_config(
        _config(
            repo="example/project",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
                "backlog": "backlog-b",
            },
        ),
        cwd=tmp_path,
    )
    assert _resource_key(config_a, 1) != _resource_key(config_b, 1)


def test_resource_key_namespaces_collapse_cosmetic_path_spelling(tmp_path):
    """`./scripts/poller.py` and `scripts/poller.py` are the same backlog
    and must produce the same namespace, or an overlapping-loop election
    between the two spellings would never dedup."""
    repo_root = tmp_path / "repo"
    _init_git_repo_with_remote(repo_root, "git@example.com:org/repo.git")
    (repo_root / "scripts").mkdir(parents=True)
    base_forge = {"provider": "script", "producer_login": "issue-bot"}
    config_dotslash = validate_config(
        _config(
            repo="pending-work-items",
            forge={**base_forge, "command": ["./scripts/poller.py"]},
        ),
        cwd=repo_root,
    )
    config_bare = validate_config(
        _config(
            repo="pending-work-items",
            forge={**base_forge, "command": ["scripts/poller.py"]},
        ),
        cwd=repo_root,
    )
    assert _resource_key(config_dotslash, 1) == _resource_key(config_bare, 1)


def test_windows_style_separators_resolve_the_same_file_as_forward_slashes(tmp_path):
    """Regression guard: `_normalize_declared_path` already treats
    `scripts\\poller.py` (Windows-style separators) as equivalent to
    `scripts/poller.py` for the resource-key *namespace*, but actual path
    *resolution* used the raw, unnormalized string -- POSIX treats a
    literal backslash as an ordinary filename character, not a
    separator, so it would try (and fail) to find one single file named
    `scripts\\poller.py` rather than `scripts/poller.py`, while the
    namespace already disagreed by treating the two as the same backlog
    ('Normalize executable separators before Path resolution')."""
    repo_root = tmp_path / "repo"
    _init_git_repo_with_remote(repo_root, "git@example.com:org/repo.git")
    (repo_root / "scripts").mkdir(parents=True)
    (repo_root / "scripts" / "poller.py").write_text("", encoding="utf-8")
    base_forge = {"provider": "script", "producer_login": "issue-bot"}
    config_backslash = validate_config(
        _config(
            repo="pending-work-items",
            forge={**base_forge, "command": ["scripts\\poller.py"]},
        ),
        cwd=repo_root,
    )
    config_forward_slash = validate_config(
        _config(
            repo="pending-work-items",
            forge={**base_forge, "command": ["scripts/poller.py"]},
        ),
        cwd=repo_root,
    )
    # Resolution must find the real file, not a literal `scripts\poller.py`.
    assert config_backslash["forge"]["command"][-1] == str(
        (repo_root / "scripts" / "poller.py").resolve()
    )
    # And the namespace must agree with what resolution actually found.
    assert _resource_key(config_backslash, 1) == _resource_key(config_forward_slash, 1)


def test_windows_style_cwd_separators_resolve_the_same_directory(tmp_path):
    """Same regression guard as the command-path test above, for
    `forge.cwd`."""
    repo_root = tmp_path / "repo"
    _init_git_repo_with_remote(repo_root, "git@example.com:org/repo.git")
    (repo_root / "nested" / "scripts").mkdir(parents=True)
    (repo_root / "nested" / "scripts" / "poller.py").write_text("", encoding="utf-8")
    config = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["poller.py"],
                "cwd": "nested\\scripts",
            },
        ),
        cwd=repo_root,
    )
    assert config["forge"]["cwd"] == str((repo_root / "nested" / "scripts").resolve())


def test_resource_key_namespaces_script_providers_by_producer_login(tmp_path):
    """Two declarations sharing a `repo` label, script path, and cwd but
    forwarding a different `producer_login` to the script select distinct
    backlogs and must not collide."""
    (tmp_path / "poller.py").write_text("", encoding="utf-8")
    config_a = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "bot-a",
                "command": ["./poller.py"],
            },
        ),
        cwd=tmp_path,
    )
    config_b = validate_config(
        _config(
            repo="pending-work-items",
            forge={
                "provider": "script",
                "producer_login": "bot-b",
                "command": ["./poller.py"],
            },
        ),
        cwd=tmp_path,
    )
    assert _resource_key(config_a, 1) != _resource_key(config_b, 1)


def test_resource_key_leaves_github_namespacing_unchanged(tmp_path):
    config = validate_config(
        _config(repo="example/project", forge={"provider": "github", "producer_login": "bot"})
    )
    assert _resource_key(config, 1) == "forge:github:repository:example/project:issue:1"


def test_resource_key_preserves_case_sensitive_script_repo_labels(tmp_path):
    """Regression guard: a script's own `repo` is arbitrary, case-sensitive
    user data interpreted entirely by the script -- unlike a real forge's
    case-insensitive `owner/name` identifier. Casefolding it would make
    `Queue-A` and `queue-a` collide on the same reservation key even
    though the script treats them as two distinct backlogs."""
    (tmp_path / "poller.py").write_text("", encoding="utf-8")
    config_upper = validate_config(
        _config(
            repo="Queue-A",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
            },
        ),
        cwd=tmp_path,
    )
    config_lower = validate_config(
        _config(
            repo="queue-a",
            forge={
                "provider": "script",
                "producer_login": "issue-bot",
                "command": ["./poller.py"],
            },
        ),
        cwd=tmp_path,
    )
    assert _resource_key(config_upper, 1) != _resource_key(config_lower, 1)
    assert ":repository:Queue-A:" in _resource_key(config_upper, 1)
    assert ":repository:queue-a:" in _resource_key(config_lower, 1)


class _FakePopen:
    """Minimal stand-in for ``subprocess.Popen`` used by ``ScriptProvider``
    tests. ``poll()`` always reports already-exited so a timeout path's
    ``terminate_process_tree(proc)`` call short-circuits immediately
    rather than attempting a real ``taskkill``/``os.killpg``."""

    def __init__(self, argv, *, kwargs=None, returncode=0, stdout="", stderr="", communicate_exc=None):
        self.argv = argv
        self.kwargs = kwargs or {}
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self._communicate_exc = communicate_exc
        self.communicate_calls = []

    def communicate(self, input=None, timeout=None):
        self.communicate_calls.append({"input": input, "timeout": timeout})
        if self._communicate_exc is not None:
            raise self._communicate_exc
        return self._stdout, self._stderr

    def poll(self):
        return self.returncode


@pytest.mark.parametrize("bad_timeout", [0, -1, math.inf, -math.inf, math.nan])
def test_script_provider_init_rejects_a_non_finite_or_non_positive_timeout(bad_timeout):
    """Defense-in-depth guard at the lowest-level constructor itself --
    not every caller is required to route through
    ``validate_script_forge_config`` first."""
    from agent_dispatch.script_provider import ScriptProvider

    with pytest.raises(ValueError, match="timeout_seconds must be a finite number > 0"):
        ScriptProvider(["./poller.py"], timeout_seconds=bad_timeout)


@pytest.mark.parametrize("bad_command", ["./poller.py", b"./poller.py"])
def test_script_provider_init_rejects_string_or_bytes_command(bad_command):
    """A plain string/bytes satisfies `Sequence[str]` character-by-character,
    so without an explicit rejection `ScriptProvider("./poller.py")` would
    be silently accepted and invoked as a nonsensical per-character argv."""
    from agent_dispatch.script_provider import ScriptProvider

    with pytest.raises(ValueError, match="non-empty list of non-empty strings"):
        ScriptProvider(bad_command)


def test_script_provider_list_open_issues_parses_a_well_formed_response():
    from agent_dispatch.script_provider import ScriptProvider

    calls = []

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return _FakePopen(
            argv,
            kwargs=kwargs,
            returncode=0,
            stdout=json.dumps(
                {
                    "issues": [
                        {
                            "number": 1,
                            "title": "Pending item",
                            "url": "https://example/1",
                            "labels": ["ready"],
                            "created_at": 100.0,
                            "updated_at": 200.0,
                        }
                    ]
                }
            ),
        )

    provider = ScriptProvider(["./poller.py"], popen=fake_popen)
    issues = provider.list_open_issues("my-repo")

    assert issues == [Issue(1, "Pending item", "https://example/1", ("ready",), 100.0, 200.0)]
    (argv, kwargs) = calls[0]
    assert argv == ["./poller.py", "--op", "list_open_issues"]
    assert kwargs["stdin"] == subprocess.PIPE
    assert kwargs["shell"] is False
    assert kwargs["encoding"] == "utf-8"


def test_script_provider_tolerates_a_lone_surrogate_in_the_request_payload(tmp_path):
    """Regression guard: a lone UTF-16 surrogate can legally end up in a
    Python string (e.g. decoded from an escaped JSON declaration, or a
    script-reported issue field later echoed back in a `reserve` body).
    With `ensure_ascii=False`, `json.dumps` would pass it through verbatim
    and stdin's own UTF-8 `communicate()` encode would raise
    `UnicodeEncodeError` *after* the real subprocess already started --
    exercised here through a real `Popen`, not a mock, since the encoding
    only actually happens in `communicate()`'s own real UTF-8 codec."""
    from agent_dispatch.script_provider import ScriptProvider

    script = tmp_path / "echo.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.read())\n"
        "print(json.dumps({'issues': []}))\n",
        encoding="utf-8",
    )
    provider = ScriptProvider([sys.executable, str(script)])
    # A lone low surrogate -- not valid UTF-16/UTF-8 on its own.
    issues = provider.list_open_issues("repo-\udcff-name")
    assert issues == []


def test_script_provider_reserve_claim_release_post_the_expected_payload():
    from agent_dispatch.script_provider import ScriptProvider

    calls = []

    def fake_popen(argv, **kwargs):
        proc = _FakePopen(argv, kwargs=kwargs, returncode=0, stdout="")
        original_communicate = proc.communicate

        def communicate(input=None, timeout=None):
            calls.append((argv, json.loads(input)))
            return original_communicate(input=input, timeout=timeout)

        proc.communicate = communicate
        return proc

    provider = ScriptProvider(["./poller.py"], producer_login="issue-bot", popen=fake_popen)
    issue = Issue(1, "t", "url", (), 0.0, 0.0)

    provider.reserve("my-repo", issue, {"k": "v"})
    provider.claim("my-repo", issue, {"k": "v"}, "task-1")
    provider.release("my-repo", issue, {"k": "v"}, "stale")

    ops = [argv[-1] for argv, _ in calls]
    assert ops == ["reserve", "claim", "release"]
    reserve_body = calls[0][1]
    assert reserve_body["repo"] == "my-repo"
    assert reserve_body["issue"]["number"] == 1
    assert reserve_body["reservation"] == {"k": "v"}
    assert reserve_body["producer_login"] == "issue-bot"
    assert calls[1][1]["task_id"] == "task-1"
    assert calls[2][1]["reason"] == "stale"


def test_script_provider_surfaces_a_non_zero_exit_as_a_real_error():
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    def fake_popen(argv, **kwargs):
        return _FakePopen(argv, kwargs=kwargs, returncode=1, stderr="boom")

    provider = ScriptProvider(["./poller.py"], popen=fake_popen)
    with pytest.raises(ScriptProviderError, match="exited 1: boom"):
        provider.list_open_issues("my-repo")


def test_script_provider_surfaces_a_timeout_as_a_distinct_error():
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    def fake_popen(argv, **kwargs):
        return _FakePopen(
            argv,
            kwargs=kwargs,
            communicate_exc=subprocess.TimeoutExpired(cmd=argv, timeout=1.0),
        )

    provider = ScriptProvider(["./poller.py"], timeout_seconds=1.0, popen=fake_popen)
    with pytest.raises(ScriptProviderError, match="timed out after 1.0s"):
        provider.list_open_issues("my-repo")


def test_script_provider_charges_the_start_token_probe_against_the_declared_timeout(
    monkeypatch,
):
    """Regression guard: `process_start_token`'s own `ps` fallback can
    block for a meaningful slice of wall-clock time (up to its own fixed
    timeout) before `communicate()` even starts. Without accounting for
    that against one deadline, `communicate(timeout=self.timeout_seconds)`
    would start a *fresh* countdown afterward, letting a short-timeout
    invocation run several times longer than declared ('Identity lookup
    runs outside the invocation timeout budget')."""
    from agent_dispatch.script_provider import ScriptProvider

    probe_delay = 0.3
    declared_timeout = 1.0

    def slow_probe(_pid):
        time.sleep(probe_delay)
        return "token"

    monkeypatch.setattr(
        "agent_dispatch.companion.process_start_token", slow_probe
    )

    fake_proc = None

    def fake_popen(argv, **kwargs):
        nonlocal fake_proc
        fake_proc = _FakePopen(argv, kwargs=kwargs, stdout=json.dumps({"issues": []}))
        fake_proc.pid = 4242
        return fake_proc

    provider = ScriptProvider(
        ["./poller.py"], timeout_seconds=declared_timeout, popen=fake_popen
    )
    provider.list_open_issues("my-repo")

    (call,) = fake_proc.communicate_calls
    # The probe's own ~0.3s must be deducted from the declared 1.0s
    # budget, not ignored (which would otherwise hand communicate() the
    # full 1.0s again, on top of the 0.3s already spent).
    assert call["timeout"] < declared_timeout
    assert call["timeout"] == pytest.approx(declared_timeout - probe_delay, abs=0.15)


def test_script_provider_terminates_the_whole_process_tree_on_timeout():
    """High-severity regression guard: a bare `subprocess.run(timeout=)`
    kills only the immediate child, leaking descendants (notably a venv
    `python.exe` launcher's re-exec'd grandchild on Windows). The timeout
    path must call `terminate_process_tree`, not just let the exception
    propagate."""
    from agent_dispatch import procutil
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    terminated = []

    def fake_terminate(proc, **kwargs):
        terminated.append(proc)

    def fake_popen(argv, **kwargs):
        return _FakePopen(
            argv,
            kwargs=kwargs,
            communicate_exc=subprocess.TimeoutExpired(cmd=argv, timeout=1.0),
        )

    original = procutil.terminate_process_tree
    procutil.terminate_process_tree = fake_terminate
    try:
        provider = ScriptProvider(["./poller.py"], timeout_seconds=1.0, popen=fake_popen)
        with pytest.raises(ScriptProviderError, match="timed out"):
            provider.list_open_issues("my-repo")
    finally:
        procutil.terminate_process_tree = original
    assert len(terminated) == 1


def test_script_provider_reaps_the_process_on_an_overflow_error():
    """Regression guard: an out-of-range `timeout_seconds` reaching the
    platform wait call raises `OverflowError` before any waiting happens
    (MAX_TIMEOUT_SECONDS should already prevent this through normal
    validation, but a directly-constructed provider or a future platform
    edge case must still reap the still-running process rather than
    leaking it)."""
    from agent_dispatch import procutil
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    terminated = []

    def fake_terminate(proc, **kwargs):
        terminated.append(proc)

    def fake_popen(argv, **kwargs):
        return _FakePopen(
            argv, kwargs=kwargs, communicate_exc=OverflowError("timeout value too large")
        )

    original = procutil.terminate_process_tree
    procutil.terminate_process_tree = fake_terminate
    try:
        provider = ScriptProvider(["./poller.py"], popen=fake_popen)
        with pytest.raises(ScriptProviderError, match="out of range"):
            provider.list_open_issues("my-repo")
    finally:
        procutil.terminate_process_tree = original
    assert len(terminated) == 1


def test_script_provider_rejects_malformed_json_on_stdout():
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    def fake_popen(argv, **kwargs):
        return _FakePopen(argv, kwargs=kwargs, returncode=0, stdout="not json{")

    provider = ScriptProvider(["./poller.py"], popen=fake_popen)
    with pytest.raises(ScriptProviderError, match="invalid JSON"):
        provider.list_open_issues("my-repo")


def test_script_provider_rejects_a_non_object_response():
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    def fake_popen(argv, **kwargs):
        return _FakePopen(argv, kwargs=kwargs, returncode=0, stdout="[1,2,3]")

    provider = ScriptProvider(["./poller.py"], popen=fake_popen)
    with pytest.raises(ScriptProviderError, match="must be a JSON object"):
        provider.list_open_issues("my-repo")


def test_script_provider_rejects_a_malformed_issue_entry():
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    def fake_popen(argv, **kwargs):
        return _FakePopen(
            argv,
            kwargs=kwargs,
            returncode=0,
            stdout=json.dumps({"issues": [{"title": "missing number"}]}),
        )

    provider = ScriptProvider(["./poller.py"], popen=fake_popen)
    with pytest.raises(ScriptProviderError, match="malformed issue entry"):
        provider.list_open_issues("my-repo")


_WELL_FORMED_ISSUE = {
    "number": 1,
    "title": "Pending item",
    "url": "https://example/1",
    "labels": ["ready"],
    "created_at": 100.0,
    "updated_at": 200.0,
    "reservations": [],
}


@pytest.mark.parametrize(
    "override",
    [
        {"number": 1.5},
        {"number": True},
        {"number": "1"},
        {"title": None},
        {"url": 42},
        {"labels": "ready"},
        {"labels": [1, 2]},
        {"created_at": math.inf},
        {"created_at": math.nan},
        {"created_at": "100"},
        {"updated_at": math.inf},
        {"reservations": "not-a-list"},
        {"reservations": ["not-a-mapping"]},
        {"reservations": [{"loop": "backlog", "occurrence": 1, "state": "reserved",
                            "at": 1.0, "label": "l", "issue": 999}]},
        {"reservations": [{"loop": "backlog", "occurrence": 1, "state": "reserved",
                            "at": math.inf, "label": "l", "issue": 1}]},
        {"reservations": [{"loop": "backlog", "occurrence": 1, "state": "reserved",
                            "at": math.nan, "label": "l", "issue": 1}]},
        {"created_at": 10**400},
    ],
)
def test_script_provider_rejects_each_mistyped_issue_field(override):
    """High-severity regression guard: a script response that passes the
    'has these keys' check but fails a *type* check must still raise
    `ScriptProviderError`, never silently coerce (`int(1.5)`, `str(None)`),
    misinterpret a string as a per-character label list, hand a
    non-mapping reservation downstream to `_latest_reservations`'s own
    `.get()` calls, or accept a reservation marker whose own schema
    (loop/occurrence/state/at/label/issue/task_id/reason) doesn't match --
    here, an `issue` field that doesn't match the containing issue's own
    `number`."""
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    issue = {**_WELL_FORMED_ISSUE, **override}

    def fake_popen(argv, **kwargs):
        return _FakePopen(
            argv, kwargs=kwargs, returncode=0, stdout=json.dumps({"issues": [issue]})
        )

    provider = ScriptProvider(["./poller.py"], popen=fake_popen)
    with pytest.raises(ScriptProviderError, match="malformed issue entry"):
        provider.list_open_issues("my-repo")


def test_script_provider_accepts_a_well_formed_reservation_marker():
    from agent_dispatch.script_provider import ScriptProvider

    issue = {
        **_WELL_FORMED_ISSUE,
        "reservations": [
            {
                "loop": "backlog",
                "occurrence": 1,
                "state": "reserved",
                "at": 1000.0,
                "label": "agent-reserved",
                "issue": 1,
            }
        ],
    }

    def fake_popen(argv, **kwargs):
        return _FakePopen(
            argv, kwargs=kwargs, returncode=0, stdout=json.dumps({"issues": [issue]})
        )

    provider = ScriptProvider(["./poller.py"], popen=fake_popen)
    (parsed,) = provider.list_open_issues("my-repo")
    assert parsed.reservations == (
        {
            "loop": "backlog",
            "occurrence": 1,
            "state": "reserved",
            "at": 1000.0,
            "label": "agent-reserved",
            "issue": 1,
        },
    )


def test_script_provider_accepts_an_integer_reservation_timestamp():
    """Regression guard: `at` as a plain JSON integer (not a float) must
    not trip the finiteness check at all -- an int is unconditionally
    finite, and applying `math.isfinite` to one risks `OverflowError` for
    an arbitrarily large value."""
    from agent_dispatch.script_provider import ScriptProvider

    issue = {
        **_WELL_FORMED_ISSUE,
        "reservations": [
            {
                "loop": "backlog",
                "occurrence": 1,
                "state": "reserved",
                "at": 1000,
                "label": "agent-reserved",
                "issue": 1,
            }
        ],
    }

    def fake_popen(argv, **kwargs):
        return _FakePopen(
            argv, kwargs=kwargs, returncode=0, stdout=json.dumps({"issues": [issue]})
        )

    provider = ScriptProvider(["./poller.py"], popen=fake_popen)
    (parsed,) = provider.list_open_issues("my-repo")
    assert parsed.reservations[0]["at"] == 1000


def test_script_provider_rejects_an_integer_reservation_timestamp_that_overflows_float():
    """Regression guard: a plain JSON integer is "finite" by definition,
    but one far outside float range (e.g. `10**400`) still raises
    `OverflowError` the moment `plan()` later does `float(own["at"])`
    (repository_issue_loops.py). A malformed marker that can't survive
    that eventual conversion must be rejected up front, not merely
    accepted because `isinstance(at, int)` skipped the finiteness check."""
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    issue = {
        **_WELL_FORMED_ISSUE,
        "reservations": [
            {
                "loop": "backlog",
                "occurrence": 1,
                "state": "reserved",
                "at": 10**400,
                "label": "agent-reserved",
                "issue": 1,
            }
        ],
    }

    def fake_popen(argv, **kwargs):
        return _FakePopen(
            argv, kwargs=kwargs, returncode=0, stdout=json.dumps({"issues": [issue]})
        )

    provider = ScriptProvider(["./poller.py"], popen=fake_popen)
    with pytest.raises(ScriptProviderError, match="reservations"):
        provider.list_open_issues("my-repo")


def test_script_provider_rejects_duplicate_issue_numbers():
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    issue = dict(_WELL_FORMED_ISSUE)

    def fake_popen(argv, **kwargs):
        return _FakePopen(
            argv,
            kwargs=kwargs,
            returncode=0,
            stdout=json.dumps({"issues": [issue, dict(issue)]}),
        )

    provider = ScriptProvider(["./poller.py"], popen=fake_popen)
    with pytest.raises(ScriptProviderError, match="duplicate issue number"):
        provider.list_open_issues("my-repo")


def test_script_provider_surfaces_a_start_failure_as_a_real_error():
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    def fake_popen(argv, **kwargs):
        raise FileNotFoundError("no such file")

    provider = ScriptProvider(["./missing.py"], popen=fake_popen)
    with pytest.raises(ScriptProviderError, match="failed to start"):
        provider.list_open_issues("my-repo")


def test_script_provider_converts_a_popen_value_error_into_a_real_error():
    """Regression guard: `Popen` raises `ValueError` (not `OSError`) for
    certain malformed argv values -- e.g. an embedded NUL byte -- that
    bypass `_safe_resolve_path`'s own validation entirely (it only ever
    sees the script *path*, not every argument). Every start failure must
    surface consistently as `ScriptProviderError`, never a raw exception
    aborting the tick."""
    from agent_dispatch.script_provider import ScriptProvider, ScriptProviderError

    def fake_popen(argv, **kwargs):
        raise ValueError("embedded null byte")

    provider = ScriptProvider(["./poller.py"], popen=fake_popen)
    with pytest.raises(ScriptProviderError, match="failed to start"):
        provider.list_open_issues("my-repo")


def test_script_provider_round_trips_list_reserve_claim_release_through_a_real_script(
    tmp_path,
):
    """Restores the originally planned stateful fixture-cycle validation:
    a *real*, repo-packaged script (a real subprocess per operation, no
    mocking) persisting its own backlog state to disk across the four
    separate invocations, proving the full contract end to end --
    `reserve`/`claim`/`release` each durably append a marker the next
    `list_open_issues` call reads back, matching the documented
    reservation append/state-transition semantics
    (`docs/repository-issue-loop.md`)."""
    from agent_dispatch.script_provider import ScriptProvider

    db_path = tmp_path / "db.json"
    db_path.write_text(
        json.dumps(
            {
                "issues": [
                    {
                        "number": 1,
                        "title": "Pending item",
                        "url": "https://example/1",
                        "labels": ["ready"],
                        "created_at": 100.0,
                        "updated_at": 100.0,
                        "reservations": [],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    script = tmp_path / "fixture_backlog.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.read())\n"
        f"db_path = {str(db_path)!r}\n"
        "db = json.loads(open(db_path, encoding='utf-8').read())\n"
        "op = sys.argv[sys.argv.index('--op') + 1]\n"
        "if op == 'list_open_issues':\n"
        "    print(json.dumps({'issues': db['issues']}))\n"
        "elif op in ('reserve', 'claim', 'release'):\n"
        "    issue = next(i for i in db['issues'] if i['number'] == request['issue']['number'])\n"
        "    marker = dict(request['reservation'])\n"
        "    marker['issue'] = issue['number']\n"
        "    if op == 'claim':\n"
        "        marker['state'] = 'claimed'\n"
        "        marker['task_id'] = request['task_id']\n"
        "    elif op == 'release':\n"
        "        marker['state'] = 'released'\n"
        "        marker['reason'] = request['reason']\n"
        "    issue['reservations'].append(marker)\n"
        "    open(db_path, 'w', encoding='utf-8').write(json.dumps(db))\n"
        "    print(json.dumps({}))\n",
        encoding="utf-8",
    )

    provider = ScriptProvider([sys.executable, str(script)], producer_login="issue-bot")

    (issue,) = provider.list_open_issues("pending-work-items")
    assert issue.number == 1
    assert issue.reservations == ()

    reservation = {
        "loop": "backlog",
        "occurrence": 1,
        "state": "reserved",
        "at": 1000.0,
        "label": "agent-reserved",
    }
    provider.reserve("pending-work-items", issue, reservation)
    (after_reserve,) = provider.list_open_issues("pending-work-items")
    assert after_reserve.reservations == (
        {**reservation, "issue": 1},
    )

    provider.claim("pending-work-items", issue, reservation, "task-1")
    (after_claim,) = provider.list_open_issues("pending-work-items")
    assert after_claim.reservations[-1] == {
        **reservation,
        "issue": 1,
        "state": "claimed",
        "task_id": "task-1",
    }

    provider.release("pending-work-items", issue, reservation, "stale")
    (after_release,) = provider.list_open_issues("pending-work-items")
    assert after_release.reservations[-1] == {
        **reservation,
        "issue": 1,
        "state": "released",
        "reason": "stale",
    }
    assert len(after_release.reservations) == 3  # reserved, claimed, released


def _ado_work_item(
    number,
    *,
    tags="ready",
    created="2026-01-01T00:00:00Z",
    updated="2026-01-01T00:00:00Z",
):
    return {
        "fields": {
            "System.Title": f"Work item {number}",
            "System.Tags": tags,
            "System.CreatedDate": created,
            "System.ChangedDate": updated,
        }
    }


class TestAzureDevOpsProvider:
    def test_list_open_issues_applies_discovery_scope_to_wiql(self):
        captured_wiql = {}

        def runner(args, **kwargs):
            del kwargs
            if len(args) > 2 and args[1] == "boards" and args[2] == "query":
                captured_wiql["value"] = args[args.index("--wiql") + 1]
                return SimpleNamespace(returncode=0, stdout="[]", stderr="")
            if len(args) > 3 and args[1:4] == ["devops", "project", "show"]:
                return SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"name": "example-project"}),
                    stderr="",
                )
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"authenticatedUser": {"providerDisplayName": "issue-bot"}}
                ),
                stderr="",
            )

        provider = AzureDevOpsProvider(
            "issue-bot",
            runner=runner,
            discovery_scope={"work_item_types": ["Bug"]},
        )
        provider.list_open_issues("example-org/example-project")
        assert "[System.WorkItemType] = 'Bug'" in captured_wiql["value"]

    def test_list_open_issues_resolves_tags_and_comments(self):
        responses = iter(
            [
                SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "authenticatedUser": {
                                "providerDisplayName": "issue-bot"
                            }
                        }
                    ),
                    stderr="",
                ),
                SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps({"name": "example-project"}),
                    stderr="",
                ),
                SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps([{"id": 42}]),
                    stderr="",
                ),
                SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(_ado_work_item(42)),
                    stderr="",
                ),
                SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "comments": [
                                {
                                    "text": (
                                        "marked reserved\n\n"
                                        + _marker(
                                            {
                                                "loop": "backlog",
                                                "occurrence": 100,
                                                "state": "reserved",
                                                "at": 100,
                                                "label": "agent-reserved",
                                                "issue": 42,
                                            }
                                        )
                                    ),
                                    "createdBy": {
                                        "displayName": "issue-bot"
                                    },
                                }
                            ]
                        }
                    ),
                    stderr="",
                ),
            ]
        )

        def runner(*args, **kwargs):
            del args, kwargs
            return next(responses)

        provider = AzureDevOpsProvider("issue-bot", runner=runner)
        (issue,) = provider.list_open_issues("example-org/example-project")

        assert issue.number == 42
        assert issue.title == "Work item 42"
        assert issue.labels == ("ready",)
        assert len(issue.reservations) == 1
        assert _latest_reservations(issue)["backlog"]["state"] == "reserved"

    def test_rejects_wrong_authenticated_identity(self):
        responses = iter(
            [
                SimpleNamespace(
                    returncode=0,
                    stdout=json.dumps(
                        {
                            "authenticatedUser": {
                                "providerDisplayName": "someone-else"
                            }
                        }
                    ),
                    stderr="",
                ),
            ]
        )

        def runner(*args, **kwargs):
            del args, kwargs
            return next(responses)

        provider = AzureDevOpsProvider("issue-bot", runner=runner)
        with pytest.raises(RuntimeError, match="identity mismatch"):
            provider.list_open_issues("example-org/example-project")

    def test_reserve_comments_and_tags_the_work_item(self):
        calls = []
        identity_responses = [
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps(
                    {"authenticatedUser": {"providerDisplayName": "issue-bot"}}
                ),
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"name": "example-project"}),
                stderr="",
            ),
        ]
        responses = iter(
            [
                *identity_responses,
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                *identity_responses,
                SimpleNamespace(returncode=0, stdout="", stderr=""),
            ]
        )

        def runner(*args, **kwargs):
            calls.append(args[0])
            return next(responses)

        provider = AzureDevOpsProvider("issue-bot", runner=runner)
        issue = Issue(
            number=42,
            title="Work item 42",
            url="https://dev.azure.com/example-org/_workitems/edit/42",
            labels=("ready",),
            created_at=100,
            updated_at=100,
        )
        provider.reserve(
            "example-org/example-project",
            issue,
            {
                "loop": "backlog",
                "occurrence": 100,
                "state": "reserved",
                "at": 100,
                "label": "agent-reserved",
            },
        )

        tag_call = next(args for args in calls if "update" in args)
        fields_arg = tag_call[tag_call.index("--fields") + 1]
        assert "agent-reserved" in fields_arg
        assert "ready" in fields_arg
