"""Light tests for the agent-dispatch CLI argument layer."""

from __future__ import annotations

from agent_dispatch.__main__ import _parse_affinity, build_parser


def test_parse_affinity():
    assert _parse_affinity(["agent=w1", "worktree=wt-2"]) == {"agent": "w1", "worktree": "wt-2"}
    assert _parse_affinity(None) == {}


def test_parser_create_flags():
    args = build_parser().parse_args(
        ["create", "do it", "--require", "logger", "--affinity", "agent=w1", "--proposed"]
    )
    assert args.command == "create"
    assert args.title == "do it"
    assert args.require == ["logger"]
    assert args.affinity == ["agent=w1"]
    assert args.proposed is True


def test_parser_claim_flags():
    args = build_parser().parse_args(
        ["claim", "w1", "--capability", "review", "--lease-seconds", "60"]
    )
    assert args.worker_id == "w1"
    assert args.capability == ["review"]
    assert args.lease_seconds == 60


def test_parser_requires_subcommand():
    import pytest

    with pytest.raises(SystemExit):
        build_parser().parse_args([])


def test_parser_create_spawn_flags():
    args = build_parser().parse_args(
        ["create", "x", "--spawn", "--spawn-agent", "w", "--async"]
    )
    assert args.spawn is True
    assert args.spawn_agent == "w"
    assert args.run_async is True


def test_parser_claim_task_flag():
    args = build_parser().parse_args(["claim", "w1", "--task", "t9"])
    assert args.task == "t9"


def test_spawn_helper_degrades_gracefully(monkeypatch, capsys):
    import argparse

    from agent_dispatch import __main__, bridge

    def boom(*_a, **_k):
        raise bridge.BridgeUnavailable("no bridge")

    monkeypatch.setattr(bridge, "spawn_worker", boom)
    args = argparse.Namespace(spawn_agent="task-worker", run_async=False, url=None)
    __main__._spawn_worker_for(args, {"id": "t1"})
    err = capsys.readouterr().err
    assert "--spawn skipped" in err
    assert "t1" in err


def test_parser_worktree_status():
    args = build_parser().parse_args(["worktree-status"])
    assert args.command == "worktree-status"


def test_identity_flags_take_precedence(monkeypatch):
    import argparse

    from agent_dispatch import __main__, identity

    # If both flags are present, no resolution subprocess is attempted.
    def boom():
        raise AssertionError("resolve_identity should not be called when both flags given")

    monkeypatch.setattr(identity, "resolve_identity", boom)
    args = argparse.Namespace(machine="m1", worktree="w1")
    assert __main__._identity(args) == ("m1", "w1")


def test_identity_falls_back_to_resolution(monkeypatch):
    import argparse

    from agent_dispatch import __main__, identity

    monkeypatch.setattr(identity, "resolve_identity", lambda: ("host-a", "wt-7"))
    args = argparse.Namespace(machine=None, worktree=None)
    assert __main__._identity(args) == ("host-a", "wt-7")


def test_parser_inbox_defaults():
    args = build_parser().parse_args(["inbox"])
    assert args.command == "inbox"
    assert args.status == "proposed"
    assert args.machine is None
    assert args.limit == 200


def test_parser_inbox_flags():
    args = build_parser().parse_args(
        ["inbox", "--machine", "host-a", "--status", "proposed,queued", "--limit", "5"]
    )
    assert args.machine == "host-a"
    assert args.status == "proposed,queued"
    assert args.limit == 5


class _FakeClient:
    """A stand-in DispatchClient capturing the params passed to ``list``."""

    def __init__(self, tasks):
        self._tasks = tasks
        self.calls: list[dict] = []

    def list(self, **params):
        self.calls.append(params)
        return list(self._tasks)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None


def test_inbox_scopes_cross_lane_to_this_machine(monkeypatch, capsys):
    import json

    from agent_dispatch import __main__, identity

    tasks = [
        {"id": "t1", "target_machine": "host-a", "status": "proposed"},
        {"id": "t2", "target_machine": None, "status": "proposed"},
        {"id": "t3", "target_machine": "host-b", "status": "proposed"},
    ]
    fake = _FakeClient(tasks)
    monkeypatch.setattr(__main__, "_client", lambda args: fake)
    monkeypatch.setattr(identity, "resolve_identity", lambda: ("host-a", "wt-1"))

    args = build_parser().parse_args(["inbox"])
    rc = args.func(args)
    assert rc == 0

    # Cross-lane query: repo is None (all lanes), status defaulted to proposed.
    assert fake.calls == [{"repo": None, "status": "proposed", "label": None, "limit": 200}]

    emitted = json.loads(capsys.readouterr().out)
    ids = {t["id"] for t in emitted}
    # host-a match + machine-agnostic kept; host-b dropped.
    assert ids == {"t1", "t2"}


def test_inbox_requires_a_machine(monkeypatch, capsys):
    from agent_dispatch import __main__, identity

    monkeypatch.setattr(__main__, "_client", lambda args: _FakeClient([]))
    monkeypatch.setattr(identity, "resolve_identity", lambda: (None, None))

    args = build_parser().parse_args(["inbox"])
    assert args.func(args) == 2
    assert "could not resolve this machine" in capsys.readouterr().err
