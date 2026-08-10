"""Tests for the ``--project`` scope guard on machine-global verbs (#1080, part b).

agent-worktrees is the primary ``--project`` consumer, but on machine-global
verbs (``repos``/``accounts``/``picker``/``--version``/``--help``) an explicit
``--project`` is meaningless. A *hand-typed* one now bounces (fail loud) instead
of being silently accepted; a *binstub/router-injected* one
(``AGENT_WORKTREES_PROJECT_ROUTED=1``) stays a silent no-op so ``<repo> repos``
etc. keep working. The binstub sets that marker.
"""

from __future__ import annotations

import os

from agent_worktrees import __main__ as m
from agent_worktrees import installer

# -- _guard_project_scope unit behavior ---------------------------------------

def test_guard_hand_typed_project_on_global_verb_bounces(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    assert m._guard_project_scope("foo", "repos") == 2
    assert m._guard_project_scope("foo", "accounts") == 2
    assert m._guard_project_scope("foo", "picker") == 2
    assert m._guard_project_scope("foo", "--version") == 2


def test_guard_routed_project_on_global_verb_is_silent(monkeypatch):
    # A binstub/router-injected --project (marked routed) stays a silent no-op
    # so `<repo> repos` keeps working; the marker is consumed.
    monkeypatch.setenv("AGENT_WORKTREES_PROJECT_ROUTED", "1")
    assert m._guard_project_scope("foo", "repos") is None
    assert "AGENT_WORKTREES_PROJECT_ROUTED" not in os.environ


def test_guard_project_on_scoped_verb_never_bounces(monkeypatch):
    # A project-scoped verb (create/push-changes/…) consumes --project; never bounce.
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    assert m._guard_project_scope("foo", "create") is None
    assert m._guard_project_scope("foo", "push-changes") is None


def test_guard_no_project_is_noop(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    assert m._guard_project_scope(None, "repos") is None


def test_guard_bare_project_no_command_is_noop(monkeypatch):
    # Bare `--project X` (no verb) means "act on project X" -- valid, never bounce.
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    assert m._guard_project_scope("foo", None) is None


def test_guard_consumes_routed_marker_even_without_project(monkeypatch):
    # Defensive: the marker is popped regardless, so a stale routed marker never
    # lingers in the environment of this process or its children.
    monkeypatch.setenv("AGENT_WORKTREES_PROJECT_ROUTED", "1")
    assert m._guard_project_scope(None, "repos") is None
    assert "AGENT_WORKTREES_PROJECT_ROUTED" not in os.environ


# -- main() wiring ------------------------------------------------------------

def test_main_hand_typed_project_on_repos_bounces(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    rc = m.main(["--project", "foo", "repos"])
    assert rc == 2
    assert "not meaningful" in capsys.readouterr().err


def test_main_routed_project_on_repos_does_not_bounce(monkeypatch):
    # With the routed marker, the guard passes through and the real `repos`
    # handler runs (stubbed here to isolate the guard from registry I/O).
    monkeypatch.setenv("AGENT_WORKTREES_PROJECT_ROUTED", "1")
    called = {}
    monkeypatch.setattr(m, "cmd_repos_dispatch",
                        lambda argv: called.__setitem__("ran", True) or 0,
                        raising=False)
    rc = m.main(["--project", "foo", "repos"])
    assert rc != 2  # not bounced
    assert "AGENT_WORKTREES_PROJECT_ROUTED" not in os.environ


# -- binstub carries the routed marker ----------------------------------------

def test_project_binstub_sets_routed_marker():
    # Every generated project binstub (cmd/ps1/sh, platform-dependent) must set
    # AGENT_WORKTREES_PROJECT_ROUTED so `<repo> repos` is treated as routed.
    specs = installer._project_binstub_specs("demo")
    assert specs, "expected at least one binstub spec"
    blob = "\n".join(content for _dst, content in specs)
    assert "AGENT_WORKTREES_PROJECT_ROUTED" in blob
    # The marker must accompany every python-exec path (--project injection).
    for _dst, content in specs:
        if "agent_worktrees --project" in content:
            assert "AGENT_WORKTREES_PROJECT_ROUTED" in content
