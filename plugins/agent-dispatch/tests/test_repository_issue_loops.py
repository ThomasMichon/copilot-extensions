"""Repository issue-loop declaration and producer contracts."""

from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from agent_dispatch.registrar import RegistrarError
from agent_dispatch.registrar_discovery import read_declaration_file_set
from agent_dispatch.queue import TaskError
from agent_dispatch.repository_issue_loops import (
    GitHubProvider,
    Issue,
    _latest_reservations,
    _marker,
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
        "status": "completed",
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
        "status": "completed",
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


@pytest.mark.parametrize("status", ["completed", "abandoned", "dead_letter"])
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


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"source": ""}, "source"),
        ({"batch_size": 0}, "batch_size"),
        ({"forge": {"provider": "other"}}, "only 'github'"),
        ({"forge": {"provider": "github"}}, "producer_login"),
        ({"reservation": {"label": "x", "comment": False}}, "must be true"),
        ({"pool": {"max_active_processes": 2}}, "concurrency must be 1"),
        ({"pool": {"body": {"type": "embody"}}}, "must be 'headless'"),
        ({"unknown": True}, "unknown key"),
    ],
)
def test_malformed_config_is_rejected(change, message):
    with pytest.raises(RegistrarError, match=message):
        validate_config(_config(**change))
