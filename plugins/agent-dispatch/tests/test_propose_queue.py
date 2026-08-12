"""Tests for the propose/queue lifecycle verbs (argument layer + propose guard)."""

from __future__ import annotations

import argparse

from agent_dispatch.__main__ import _cmd_create, _cmd_propose, build_parser


def _args(argv):
    return build_parser().parse_args(argv)


def test_create_still_parses_after_parent_refactor():
    # The parent-parser refactor must not change create's surface.
    ns = _args(["create", "do a thing", "--prompt", "details", "--label", "x"])
    assert ns.func is _cmd_create
    assert ns.title == "do a thing"
    assert ns.prompt == "details"
    assert ns.label == ["x"]
    assert ns.proposed is False


def test_propose_shares_create_surface_and_routes_to_cmd_propose():
    ns = _args(["propose", "plan a thing", "--goal", "ship it", "--require", "checkout"])
    assert ns.func is _cmd_propose
    assert ns.title == "plan a thing"
    assert ns.goal == "ship it"
    assert ns.require == ["checkout"]


def test_queue_is_alias_of_approve():
    ns = _args(["queue", "task-123"])
    assert ns.task_id == "task-123"
    # dispatches through the same approve handler as `approve`
    approve_ns = _args(["approve", "task-123"])
    assert ns.func.__name__ == approve_ns.func.__name__


def test_propose_forces_proposed_and_rejects_execution_flags(capsys):
    # A proposed draft is not claimed/spawned -- the guard returns 2 without a coordinator.
    ns = _args(["propose", "t"])
    ns.claim = True
    assert _cmd_propose(ns) == 2
    err = capsys.readouterr().err
    assert "not claimed or spawned" in err

    ns2 = _args(["propose", "t"])
    ns2.spawn = True
    assert _cmd_propose(ns2) == 2


def test_propose_namespace_has_execution_defaults():
    # propose reuses the create arg set, so spawn/claim default False and are present.
    ns = _args(["propose", "t"])
    assert ns.claim is False
    assert ns.spawn is False
    assert isinstance(ns, argparse.Namespace)
