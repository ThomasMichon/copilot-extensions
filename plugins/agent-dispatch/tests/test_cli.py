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
