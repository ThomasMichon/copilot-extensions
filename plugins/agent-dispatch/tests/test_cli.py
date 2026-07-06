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
