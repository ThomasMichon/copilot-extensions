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
    def __init__(self, task, attachments=()):
        self._task = task
        self._attachments = list(attachments)

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


def _args(argv):
    return m.build_parser().parse_args(argv)


def test_namespace_list_is_empty(capsys):
    rc = m.main(["namespace-list"])
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == []


def test_namespace_resolve_claimed_task_returns_worktree_spec(monkeypatch, capsys):
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "started", "owner": "lambda-core/wt-42"},
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
                "status": "completed",
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
            {"status": "completed", "owner": None},
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
        lambda: "lambda-core",
    )
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {
                "status": "completed",
                "owner": None,
                "owner_session_id": "s-1",
                "target_machine": "wheatley",
            },
        ),
    )
    rc = m.main(["namespace-resolve", "task-6"])
    assert rc == 4
    assert "cross-machine" in capsys.readouterr().err


def test_namespace_resolve_cross_machine_task_exits_bad_state(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_dispatch.bridge_namespace_cli._local_machine_name",
        lambda: "lambda-core",
    )
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "started", "owner": "wheatley/wt-99"},
        ),
    )
    rc = m.main(["namespace-resolve", "task-4"])
    assert rc == 4
    assert "cross-machine" in capsys.readouterr().err


def test_namespace_resolve_venue_suffix_mismatch_exits_bad_state(monkeypatch, capsys):
    monkeypatch.setattr(
        "agent_dispatch.bridge_namespace_cli._local_machine_name",
        lambda: "lambda-core",
    )
    monkeypatch.setattr(
        m, "_client",
        lambda _args, **_kw: _FakeClient(
            {"status": "started", "owner": "lambda-core/wt-1"},
        ),
    )
    rc = m.main(["namespace-resolve", "task-5@wheatley"])
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
