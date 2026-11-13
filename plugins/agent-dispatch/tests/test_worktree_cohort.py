"""Original allocation evidence crosses only a capable creation boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agent_dispatch import embody, worktree_cohort
from agent_dispatch.queue import TaskQueue, TaskError
from agent_dispatch.spawn_factories import SpawnPreparationRetained
from tests.test_supervisor import QueueBackedClient


def cohort(task="original", key="dispatch-task:original:1"):
    return {
        "schema": "dispatch-assignment-cohort", "version": 1,
        "emitter": {
            "repo": "example", "source": "emitter", "request_id": "original-request",
            "producer_id": "source-reader", "task_id": task, "generation": 1,
        },
        "pool": {
            "task_id": task, "registration_id": "original-pool", "machine": "worker",
            "env": "linux", "reservation_key": key, "task_generation": 1,
            "catalog_revision": 7,
        },
    }


@pytest.fixture(autouse=True)
def clear_capabilities():
    worktree_cohort._supports_cohort.cache_clear()
    yield
    worktree_cohort._supports_cohort.cache_clear()


@pytest.fixture
def provider(tmp_path, monkeypatch):
    """A strict current/prior process boundary, never the installed owner."""
    package = tmp_path / "agent_worktrees"
    package.mkdir()
    (package / "__main__.py").write_text(
        '''import argparse, json, os, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ["CALL_LOG"]).open("a") as out:
    out.write(json.dumps(args) + "\\n")
cap = json.loads(os.environ["CAPABILITIES"])
if args == ["worktree-allocation", "capabilities", "--json"]:
    if cap == "legacy":
        print("unknown command: worktree-allocation", file=sys.stderr)
        sys.exit(2)
    print(json.dumps(cap))
    sys.exit(0)
parser = argparse.ArgumentParser()
parser.add_argument("--project")
parser.add_argument("operation", choices=["create"])
parser.add_argument("--no-owner", action="store_true")
parser.add_argument("--json", action="store_true")
parser.add_argument("--no-pair", action="store_true")
for flag in ("origin", "interface", "dispatch-task-id", "dispatch-reservation-key",
             "dispatch-attempt", "dispatch-driver", "dispatch-supervisor", "creation-key"):
    parser.add_argument("--" + flag)
if isinstance(cap, dict) and cap.get("dispatch_cohort"):
    parser.add_argument("--dispatch-cohort", type=json.loads)
parsed = parser.parse_args()
facts = json.dumps(getattr(parsed, "dispatch_cohort", None), sort_keys=True)
journal = Path(os.environ["CREATION_JOURNAL"])
if journal.exists() and journal.read_text() != facts:
    parser.error("original creation facts changed")
journal.write_text(facts)
if os.environ.get("FAIL_CREATE"):
    print("error: unknown argument after an indeterminate creation", file=sys.stderr)
    sys.exit(2)
print(json.dumps({
    "creation": {"version": 1, "status": "complete"},
    "worktree": {"id": "workspace", "repo": parsed.project, "path": "/fixture/workspace"},
}))
''',
        encoding="utf-8",
    )
    log = tmp_path / "calls.jsonl"
    environment = {
        **os.environ, "PYTHONPATH": str(tmp_path), "CALL_LOG": str(log),
        "CREATION_JOURNAL": str(tmp_path / "creation.json"),
    }
    prefix = [sys.executable, "-m", "agent_worktrees"]
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: prefix)
    monkeypatch.setattr(embody, "agent_worktrees_environment", lambda: environment.copy())
    monkeypatch.setattr(worktree_cohort, "agent_worktrees_environment", lambda: environment.copy())
    capabilities = {
        "schema": "copilot-extensions.worktree-allocation", "version": 1, "role": "origin",
        "dispatch_cohort": {"schema": "dispatch-assignment-cohort", "version": 1},
    }
    environment["CAPABILITIES"] = json.dumps(capabilities)

    def create(**kwargs):
        original = kwargs.pop("allocation_cohort", cohort())
        transport = kwargs.pop("cohort_transport", worktree_cohort.selection(prefix, original))
        return embody.create_worktree(
            project="example", task_id="original", reservation_key="dispatch-task:original:1",
            attempt=1, driver="agent-dispatch", supervisor="supervisor",
            creation_key="original-creation", allocation_cohort=original, cohort_transport=transport,
            **kwargs,
        )

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    return create, calls, environment, prefix, capabilities


@pytest.mark.parametrize("support", ["current", "prior", "legacy", "bad-schema", "future", "bool", "receiver"])
def test_real_parser_selects_capable_or_baseline_before_creation(provider, support, caplog):
    create, calls, env, _, cap = provider
    if support == "prior":
        cap.pop("dispatch_cohort")
    elif support == "legacy":
        cap = "legacy"
    elif support == "bad-schema":
        cap["schema"] = "unrelated"
    elif support == "future":
        cap["dispatch_cohort"]["version"] = 2
    elif support == "bool":
        cap["dispatch_cohort"]["version"] = True
    elif support == "receiver":
        cap["role"] = "receiver"
    env["CAPABILITIES"] = json.dumps(cap)
    assert create()["worktree"] == "workspace"
    discovery, creation = calls()
    assert discovery == ["worktree-allocation", "capabilities", "--json"]
    assert ("--dispatch-cohort" in creation) is (support == "current")
    if support == "current":
        assert json.loads(creation[creation.index("--dispatch-cohort") + 1]) == cohort()
    else:
        assert "legacy unattributed baseline" in caplog.text


def test_cached_selected_runtime_and_provider_rollback(provider):
    create, calls, env, prefix, cap = provider
    create()
    create()
    assert len(calls()) == 3  # One observation, two creations.
    prefix.insert(1, "-B")
    cap.pop("dispatch_cohort")
    env["CAPABILITIES"] = json.dumps(cap)
    with pytest.raises(SpawnPreparationRetained, match="requires a capable"):
        create(cohort_transport="cohort-v1")
    assert len(calls()) == 4
    assert calls()[-1] == ["worktree-allocation", "capabilities", "--json"]


def test_creation_failure_is_not_an_effectful_syntax_probe(provider):
    create, calls, env, _, _ = provider
    env["FAIL_CREATE"] = "1"
    with pytest.raises(embody.EmbodyUnavailable, match="indeterminate creation"):
        create()
    assert len(calls()) == 2
    assert "--dispatch-cohort" in calls()[-1]


def test_legacy_reservation_has_no_discovery_or_guessed_cohort(provider):
    create, calls, _, _, _ = provider
    create(allocation_cohort=None)
    assert len(calls()) == 1
    assert "--dispatch-cohort" not in calls()[0]


@pytest.mark.parametrize("failure", [OSError("unavailable"), subprocess.TimeoutExpired("capabilities", 5)])
def test_unavailable_discovery_preserves_optional_baseline(monkeypatch, failure, caplog):
    def fail(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(worktree_cohort.subprocess, "run", fail)
    assert worktree_cohort.selection(
        [sys.executable, "-m", "agent_worktrees"], cohort(),
    ) == "baseline"
    assert "support is unknown" in caplog.text


def test_mutable_shim_never_authorizes_new_syntax(monkeypatch):
    monkeypatch.setattr(worktree_cohort.subprocess, "run", lambda *_a, **_k: pytest.fail("no shim probe"))
    assert worktree_cohort.selection(["/fixture/bin/worktrees"], cohort()) == "baseline"


def test_new_creation_forwards_original_snapshot_not_current_task(monkeypatch):
    requests = []
    original = cohort()
    monkeypatch.setattr(embody, "create_worktree", lambda **kwargs:
        requests.append(kwargs) or {"worktree": "workspace", "path": "/fixture/workspace", "project": "example"})
    result = embody.prepare_reusable_worktree(
        {"id": "new-episode", "source": "replacement", "cohort": cohort("new-episode")},
        {
            "key": "dispatch-task:new-episode:3", "attempt": 3,
            "allocation_reservation_key": "dispatch-task:original:1",
            "allocation_project": "example", "worktree_path": None,
            "allocation_cohort": original, "creation_key": "original-creation",
        },
        interface="cli", driver="agent-dispatch", supervisor="supervisor",
    )
    assert result["created"] is True
    assert requests[0]["allocation_cohort"] == original
    assert requests[0]["task_id"] == "original"
    assert requests[0]["reservation_key"] == "dispatch-task:original:1"
    assert requests[0]["attempt"] == 1


@pytest.mark.parametrize("ownership", ["targeted", "reused"])
def test_existing_workspace_is_never_restamped(monkeypatch, ownership):
    monkeypatch.setattr(embody, "resolve_worktree", lambda *_a, **_k:
        {"worktree": "workspace", "path": "/fixture/workspace", "project": "example"})
    monkeypatch.setattr(embody, "create_worktree", lambda **_k: pytest.fail("no restamping"))
    monkeypatch.setattr(worktree_cohort, "creation_arguments", lambda *_a: pytest.fail("no discovery"))
    result = embody.prepare_reusable_worktree(
        {"id": "current"}, {
            "key": "dispatch-task:current:1", "attempt": 1, "worktree": "workspace",
            "worktree_ownership": ownership, "allocation_project": "example",
            "worktree_path": "/fixture/workspace", "allocation_cohort": cohort("current"),
        },
        interface="cli", driver="agent-dispatch", supervisor="supervisor",
    )
    assert result["created"] is False


@pytest.mark.parametrize("initial", ["baseline", "cohort-v1"])
def test_lost_creation_response_restart_and_runtime_change_preserve_wire_choice(
    provider, tmp_path, monkeypatch, initial,
):
    _, calls, env, prefix, cap = provider
    if initial == "baseline":
        env["CAPABILITIES"] = json.dumps({key: value for key, value in cap.items() if key != "dispatch_cohort"})
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="example")
    reservation, _ = queue.reserve_spawn(task.id)
    original_run = subprocess.run
    lost = [True]

    def run(argv, **kwargs):
        result = original_run(argv, **kwargs)
        if "create" in argv and lost[0]:
            lost[0] = False
            raise subprocess.TimeoutExpired(argv, 5)
        return result

    monkeypatch.setattr(embody.subprocess, "run", run)

    def prepare(q, reserved):
        from dataclasses import asdict

        return embody.prepare_reusable_worktree(
            asdict(task), asdict(reserved), project="example", interface="cli",
            driver="agent-dispatch", supervisor="supervisor", allocation_client=QueueBackedClient(q),
        )

    with pytest.raises(subprocess.TimeoutExpired):
        prepare(queue, reservation)
    assert queue.get_reservation(reservation.key).allocation_cohort_transport == initial
    queue.defer_spawn(reservation.key, detail="creation outcome unknown")
    restarted = TaskQueue(queue.db_path)
    retry, _ = restarted.reserve_spawn(task.id)
    assert retry.creation_key == reservation.creation_key
    assert retry.allocation_cohort_transport == initial
    prefix.insert(1, "-B")
    env["CAPABILITIES"] = json.dumps(
        cap if initial == "baseline" else {key: value for key, value in cap.items() if key != "dispatch_cohort"},
    )
    if initial == "cohort-v1":
        with pytest.raises(SpawnPreparationRetained, match="requires a capable"):
            prepare(restarted, retry)
        assert sum("create" in call for call in calls()) == 1
        prefix.insert(1, "-u")
        env["CAPABILITIES"] = json.dumps(cap)
    assert prepare(restarted, retry)["worktree"] == "workspace"
    creations = [call for call in calls() if "create" in call]
    assert len(creations) == 2
    assert all(("--dispatch-cohort" in call) is (initial == "cohort-v1") for call in creations)
    assert {call[call.index("--creation-key") + 1] for call in creations} == {reservation.creation_key}
    with pytest.raises(TaskError, match="cannot change"):
        restarted.record_spawn_allocation(
            retry.key, "example", cohort_transport="baseline" if initial == "cohort-v1" else "cohort-v1",
        )


def test_transport_migration_preserves_legacy_baseline_and_new_unselected_intent():
    import sqlite3
    from agent_dispatch.queue_assignment_cohort import migrate_allocation_cohorts

    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE spawn_reservations (key TEXT PRIMARY KEY, allocation_cohort TEXT,"
        "allocation_reservation_key TEXT, creation_key TEXT)",
    )
    conn.execute("INSERT INTO spawn_reservations(key) VALUES ('legacy')")
    migrate_allocation_cohorts(conn)
    assert conn.execute("SELECT allocation_cohort_transport FROM spawn_reservations").fetchone()[0] == "baseline"
    conn.execute("INSERT INTO spawn_reservations(key, allocation_cohort_transport) VALUES ('new', NULL)")
    migrate_allocation_cohorts(conn)
    assert conn.execute(
        "SELECT allocation_cohort_transport FROM spawn_reservations WHERE key='new'",
    ).fetchone()[0] is None
    with pytest.raises(sqlite3.IntegrityError, match="transport is immutable"):
        conn.execute("UPDATE spawn_reservations SET allocation_cohort_transport='cohort-v1' WHERE key='legacy'")
    conn.close()


def test_lost_allocation_binding_reply_never_reaches_creation(provider, tmp_path):
    from dataclasses import asdict
    from agent_dispatch.client import DispatchError

    _, calls, _, _, _ = provider
    queue = TaskQueue(tmp_path / "queue.db")
    task = queue.create(title="work", repo="example")
    reservation, _ = queue.reserve_spawn(task.id)

    class LostReply(QueueBackedClient):
        def record_spawn_allocation(self, *args, **kwargs):
            super().record_spawn_allocation(*args, **kwargs)
            raise DispatchError(503, "binding reply lost")

    with pytest.raises(SpawnPreparationRetained, match="could not be recorded"):
        embody.prepare_reusable_worktree(
            asdict(task), asdict(reservation), project="example", interface="cli",
            driver="agent-dispatch", supervisor="supervisor", allocation_client=LostReply(queue),
        )
    assert queue.get_reservation(reservation.key).allocation_cohort_transport == "cohort-v1"
    assert calls() == [["worktree-allocation", "capabilities", "--json"]]
