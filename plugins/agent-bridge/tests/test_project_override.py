"""Tests for the top-level ``--project`` override (command-surface effort).

A top-level ``--project`` (injected by the ``<repo> <slug>`` router, e.g.
``<repo> bridge send <machine>``) pins the target project for the remote
worktree resolve instead of the caller's cwd project -- so a dispatch from
inside an unrelated repo's worktree still resolves the intended repo.
"""

from __future__ import annotations

import os

import pytest

import agent_bridge.__main__ as m


def _reset() -> None:
    m._set_project_override(None)


def test_sender_repo_uses_override(monkeypatch):
    monkeypatch.setattr(m, "_worktrees_get", lambda key: "cwd-project")
    m._set_project_override("explicit-repo")
    try:
        assert m._sender_repo() == "explicit-repo"
    finally:
        _reset()


def test_sender_repo_falls_back_to_cwd(monkeypatch):
    monkeypatch.setattr(m, "_worktrees_get", lambda key: "cwd-project")
    _reset()
    assert m._sender_repo() == "cwd-project"


def test_set_project_override_blank_is_none():
    m._set_project_override("   ")
    try:
        assert m._PROJECT_OVERRIDE is None
    finally:
        _reset()


def test_override_does_not_change_caller_id(monkeypatch):
    # caller_id keys cursor/session affinity to the caller's OWN worktree and
    # must NOT be affected by --project (only the target project changes).
    monkeypatch.setattr(m, "_worktrees_get",
                        lambda key: "wt-dir" if key == "worktree-dir" else "p")
    m._set_project_override("other-repo")
    try:
        assert m._get_caller_id() == "wt-dir"
    finally:
        _reset()


def test_build_parser_accepts_project_long():
    parser = m.build_parser()
    args = parser.parse_args(["--project", "demo", "agents"])
    assert args.project == "demo"
    assert args.command == "agents"


def test_build_parser_accepts_project_short():
    parser = m.build_parser()
    args = parser.parse_args(["-p", "demo", "sessions"])
    assert args.project == "demo"


def test_project_absent_defaults_none():
    parser = m.build_parser()
    args = parser.parse_args(["agents"])
    assert args.project is None


# -- _guard_project_scope: bounce explicit --project on non-consuming verbs (#1080)


def test_guard_explicit_project_on_fleet_global_errors(monkeypatch):
    # An explicit (user-typed, not router-injected) --project on a fleet-global
    # verb must bounce, not silently no-op.
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    parser = m.build_parser()
    args = parser.parse_args(["--project", "foo", "agents"])
    with pytest.raises(SystemExit) as ei:
        m._guard_project_scope(parser, args)
    assert ei.value.code == 2  # argparse error exit


def test_guard_routed_project_on_fleet_global_is_silent(monkeypatch):
    # Router-injected --project (marked routed) stays a silent no-op so the
    # uniform `<repo> bridge agents` surface keeps working.
    monkeypatch.setenv("AGENT_WORKTREES_PROJECT_ROUTED", "1")
    parser = m.build_parser()
    args = parser.parse_args(["--project", "foo", "agents"])
    m._guard_project_scope(parser, args)  # no raise
    # The marker is consumed so it never leaks to child processes.
    assert "AGENT_WORKTREES_PROJECT_ROUTED" not in os.environ


@pytest.mark.parametrize("verb", ["send", "create"])
def test_guard_explicit_project_on_consuming_verb_ok(monkeypatch, verb):
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    parser = m.build_parser()
    argv = ["--project", "foo", verb, "target"]
    args = parser.parse_args(argv)
    m._guard_project_scope(parser, args)  # consuming verb -> no raise


def test_guard_no_project_is_noop(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    parser = m.build_parser()
    args = parser.parse_args(["agents"])
    m._guard_project_scope(parser, args)  # no --project -> no raise


def test_guard_consumes_routed_marker_even_without_project(monkeypatch):
    # Defensive: the marker is popped regardless, so a stale routed marker never
    # lingers in the environment of this process or its children.
    monkeypatch.setenv("AGENT_WORKTREES_PROJECT_ROUTED", "1")
    parser = m.build_parser()
    args = parser.parse_args(["agents"])
    m._guard_project_scope(parser, args)
    assert "AGENT_WORKTREES_PROJECT_ROUTED" not in os.environ
