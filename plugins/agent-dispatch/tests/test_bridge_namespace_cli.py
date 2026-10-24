"""Tests for the `dispatch:` namespace-provider CLI seam (#3389).

Mirrors ``test_cli.py``'s monkeypatch-``_client`` convention: a fake client
stands in for the coordinator so these tests exercise only
``bridge_namespace_cli``'s own resolution/exit-code logic, not HTTP.
"""

from __future__ import annotations

import json

import pytest

from agent_dispatch import __main__ as m
from agent_dispatch.client import DispatchError


@pytest.fixture(autouse=True)
def _isolate_discovery(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_DISPATCH_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("AGENT_DISPATCH_ROUTING_DIR", str(tmp_path / "routing"))
    monkeypatch.delenv("AGENT_DISPATCH_ENDPOINT", raising=False)
    monkeypatch.delenv("AGENT_DISPATCH_FAILOVER_MACHINE", raising=False)


@pytest.fixture(autouse=True)
def _no_local_machine(monkeypatch):
    """Cross-machine validation is a no-op unless a local machine name
    resolves -- pin it explicitly per test instead of depending on CWD."""
    monkeypatch.setattr(
        "agent_dispatch.bridge_namespace_cli._local_machine_name",
        lambda: None,
    )


class _FakeClient:
    def __init__(self, task, attachments=(), reservations=()):
        self._task = task
        self._attachments = list(attachments)
        self._reservations = list(reservations)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, task_id):
        if self._task is None:
            raise DispatchError(404, f"no such task {task_id}")
        return self._task

    def attachments(self, task_id):
        return self._attachments

    def list_reservations(self, **kwargs):
        return self._reservations[:1] if kwargs.get("latest_only") else self._reservations


def _args(argv):
    return m.build_parser().parse_args(argv)


def test_namespace_list_is_empty(capsys):
    rc = m.main(["namespace-list"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_namespace_resolve_supplies_bounded_retry_reservation_history(monkeypatch, capsys):
    current = {
        "key": "reservation-b", "task_id": "task-a", "attempt": 2,
        "worktree": "wt-active", "state": "reserving", "worktree_ownership": "reused",
    }
    previous = {**current, "key": "reservation-a", "attempt": 1, "state": "deferred",
                "worktree_ownership": "created"}
    calls = []
    client = _FakeClient({"id": "task-a", "status": "queued"}, reservations=[current, previous])
    original = client.list_reservations
    def list_rows(**kwargs):
        calls.append(kwargs)
        return original(**kwargs)
    client.list_reservations = list_rows
    monkeypatch.setattr(m, "_client", lambda *_args, **_kwargs: client)
    assert m.main(["namespace-resolve", "task-a"]) == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["venue"]["acquisition_reservation"] == current
    assert spec["venue"]["acquisition_reservation_history"] == {
        "version": 1, "reservations": [current, previous],
    }
    assert calls == [
        {"task_id": "task-a", "latest_only": True, "limit": 1},
        {"task_id": "task-a", "latest_only": False, "limit": 256},
    ]


def test_namespace_resolve_claimed_task_returns_worktree_spec(monkeypatch, capsys):
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "started", "owner": "atlas-core/wt-42"},
            attachments=[{"session_id": "s-old"}],
        ),
    )
    rc = m.main(["namespace-resolve", "task-1"])
    assert rc == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["type"] == "worktree"
    assert spec["worktree_id"] == "wt-42"
    assert spec["venue"]["provider"] == "agent-dispatch"
    assert spec["venue"]["target_id"] == "task-1"
    assert spec["venue"]["task"]["status"] == "started"
    assert spec["venue"]["attachments"] == [{"session_id": "s-old"}]


def test_namespace_resolve_unclaimed_pinned_task_uses_target_worktree(
    monkeypatch, capsys,
):
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "queued", "owner": None, "target_worktree": "wt-pinned"},
        ),
    )
    rc = m.main(["namespace-resolve", "task-2"])
    assert rc == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["worktree_id"] == "wt-pinned"


@pytest.mark.parametrize("state", ["reserving", "spawned"])
def test_namespace_preserves_active_execution_project_not_queue_lane(
    monkeypatch, capsys, state,
):
    reservation = {
        "worktree": "wt-active", "state": state, "release_requested": False,
        "allocation_project": "execution-project", "worktree_path": "/actual/execution",
    }
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "queued", "repo": "github.com/example/producing-lane",
             "target_worktree": "wt-active"},
            reservations=[reservation],
        ),
    )
    assert m.main(["namespace-resolve", "task-active"]) == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["project"] == "execution-project"
    assert spec["worktree_id"] == "wt-active"
    assert "cwd" not in spec


@pytest.mark.parametrize("changes", [
    {"state": "deferred"},
    {"state": "failed"},
    {"release_requested": True},
    {"worktree": "another-workspace"},
    {"allocation_project": None},
])
def test_namespace_does_not_infer_project_from_inactive_or_foreign_allocation(
    monkeypatch, capsys, changes,
):
    reservation = {
        "worktree": "wt-active", "state": "reserving", "release_requested": False,
        "allocation_project": "execution-project", **changes,
    }
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "queued", "repo": "github.com/example/producing-lane",
             "target_worktree": "wt-active"},
            reservations=[reservation],
        ),
    )
    assert m.main(["namespace-resolve", "task-active"]) == 0
    assert "project" not in json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("project", ["", " ", True, []])
def test_namespace_refuses_malformed_active_execution_project(monkeypatch, capsys, project):
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "queued", "target_worktree": "wt-active"},
            reservations=[{
                "worktree": "wt-active", "state": "reserving",
                "allocation_project": project,
            }],
        ),
    )
    assert m.main(["namespace-resolve", "task-active"]) == 4
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid execution project" in captured.err


@pytest.mark.parametrize("status", ["queued", "claimed", "started", "suspended"])
@pytest.mark.parametrize("state", ["reserving", "spawned"])
def test_namespace_resolve_active_reservation_supplies_acquisition_worktree(
    monkeypatch, capsys, status, state,
):
    reservation = {"worktree": "wt-active", "state": state, "release_requested": False}
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": status, "owner": None, "owner_session_id": "s-current"},
            reservations=[reservation],
        ),
    )
    assert m.main(["namespace-resolve", "task-1"]) == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["type"] == "worktree"
    assert spec["worktree_id"] == "wt-active"
    assert spec["venue"]["acquisition_reservation"] == reservation


@pytest.mark.parametrize("status", ["submitted", "completed", "abandoned"])
@pytest.mark.parametrize("session_source", ["owner", "attachment"])
def test_namespace_resolve_terminal_task_ignores_historical_reservation(
    monkeypatch, capsys, status, session_source,
):
    reservation = {"worktree": "wt-released", "state": "spawned", "release_requested": False}
    task = {"status": status, "owner": None}
    if session_source == "owner":
        task["owner_session_id"] = "s-durable"
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            task, attachments=[{"session_id": "s-durable"}],
            reservations=[reservation],
        ),
    )
    assert m.main(["namespace-resolve", "task-1"]) == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["type"] == "session"
    assert "worktree_id" not in spec
    assert spec["venue"]["acquisition_reservation"] == reservation


@pytest.mark.parametrize(
    "state,release_requested",
    [("released", False), ("failed", False), ("spawned", True), (None, False)],
)
def test_namespace_resolve_inactive_reservation_cannot_redirect_session(
    monkeypatch, capsys, state, release_requested,
):
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "started", "owner": None, "owner_session_id": "s-current"},
            reservations=[{
                "worktree": "wt-released", "state": state,
                "release_requested": release_requested,
            }],
        ),
    )
    assert m.main(["namespace-resolve", "task-1"]) == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["type"] == "session"
    assert "worktree_id" not in spec


def test_namespace_resolve_unknown_task_exits_not_found(monkeypatch, capsys):
    monkeypatch.setattr(m, "_client", lambda _args, **_kw: _FakeClient(None))
    rc = m.main(["namespace-resolve", "does-not-exist"])
    assert rc == 3
    assert "not found" in capsys.readouterr().err


def test_namespace_resolve_unbound_task_exits_bad_state(monkeypatch, capsys):
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient({"status": "queued", "owner": None}),
    )
    rc = m.main(["namespace-resolve", "task-3"])
    assert rc == 4
    assert "not yet bound" in capsys.readouterr().err


def test_namespace_resolve_completed_headless_task_returns_session_spec(
    monkeypatch, capsys,
):
    # A headless body (board-sweep/review worker) clears `owner` on
    # completion -- never bound a worktree at all -- but the durable
    # `owner_session_id` survives independently.
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {
                "status": "submitted",
                "owner": None,
                "owner_session_id": "s-completed",
            },
            attachments=[{"session_id": "s-old"}],
        ),
    )
    rc = m.main(["namespace-resolve", "task-4"])
    assert rc == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["type"] == "session"
    assert "worktree_id" not in spec
    assert spec["venue"]["provider"] == "agent-dispatch"
    assert spec["venue"]["target_id"] == "task-4"
    assert spec["venue"]["task"]["owner_session_id"] == "s-completed"
    assert spec["venue"]["attachments"] == [{"session_id": "s-old"}]


def test_namespace_resolve_completed_headless_task_falls_back_to_attachment_session(
    monkeypatch, capsys,
):
    # No owner_session_id recorded on the task itself, but attachment
    # history still carries a resolvable session id.
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "submitted", "owner": None},
            attachments=[{"session_id": "s-from-history"}],
        ),
    )
    rc = m.main(["namespace-resolve", "task-5"])
    assert rc == 0
    spec = json.loads(capsys.readouterr().out)
    assert spec["type"] == "session"


def test_namespace_resolve_headless_task_cross_machine_exits_bad_state(
    monkeypatch, capsys,
):
    monkeypatch.setattr(
        "agent_dispatch.bridge_namespace_cli._local_machine_name",
        lambda: "atlas-core",
    )
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {
                "status": "submitted",
                "owner": None,
                "owner_session_id": "s-1",
                "target_machine": "ember",
            },
        ),
    )
    rc = m.main(["namespace-resolve", "task-6"])
    assert rc == 4
    assert "cross-machine" in capsys.readouterr().err


def test_namespace_resolve_cross_machine_task_exits_bad_state(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_dispatch.bridge_namespace_cli._local_machine_name",
        lambda: "atlas-core",
    )
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "started", "owner": "ember/wt-99"},
        ),
    )
    rc = m.main(["namespace-resolve", "task-4"])
    assert rc == 4
    assert "cross-machine" in capsys.readouterr().err


def test_namespace_resolve_venue_suffix_mismatch_exits_bad_state(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_dispatch.bridge_namespace_cli._local_machine_name",
        lambda: "atlas-core",
    )
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "started", "owner": "atlas-core/wt-1"},
        ),
    )
    rc = m.main(["namespace-resolve", "task-5@ember"])
    assert rc == 4


def test_namespace_resolve_other_dispatch_error_exits_bad_state(monkeypatch, capsys):
    class _BrokenClient:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def get(self, _task_id):
            raise DispatchError(500, "coordinator on fire")

    monkeypatch.setattr(m, "_client", lambda _args, **_kw: _BrokenClient())
    rc = m.main(["namespace-resolve", "task-6"])
    assert rc == 4
