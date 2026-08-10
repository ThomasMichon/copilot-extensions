"""Tests for the soft ``--project`` scope note on machine-global verbs.

``--project`` has no effect on a machine-global verb
(``repos``/``accounts``/``picker``/``--version``/``--help``). The guard emits a
**soft, non-fatal** stderr note for a likely-mistaken explicit one -- but ONLY
when the project name is *unregistered*, because a real project binstub always
injects a registered project. It never blocks and requires no binstub/env
cooperation, so older deployed binstubs keep working unchanged (the earlier
hard-bounce + ``AGENT_WORKTREES_PROJECT_ROUTED`` marker approach broke stale
binstubs -- follow-up to #1108).
"""

from __future__ import annotations

from agent_worktrees import __main__ as m
from agent_worktrees import installer

# -- _guard_project_scope: soft, registration-gated, never blocks --------------

def test_unregistered_project_on_global_verb_warns(monkeypatch, capsys):
    monkeypatch.setattr(m, "_is_registered_project", lambda name: False)
    assert m._guard_project_scope("zzz-bogus", "repos") is None  # never blocks
    err = capsys.readouterr().err
    assert "zzz-bogus" in err and "no effect" in err


def test_registered_project_on_global_verb_is_silent(monkeypatch, capsys):
    # Backward-compat: a real (registered) project -- what any binstub injects --
    # is accepted silently. No note, no block. This is the case that broke
    # stale binstubs under the old hard-bounce design.
    monkeypatch.setattr(m, "_is_registered_project", lambda name: True)
    assert m._guard_project_scope("dotfiles", "repos") is None
    assert capsys.readouterr().err == ""


def test_project_on_scoped_verb_is_silent(monkeypatch, capsys):
    # A project-scoped verb (create/push-changes/…) consumes --project; no note.
    monkeypatch.setattr(m, "_is_registered_project", lambda name: False)
    assert m._guard_project_scope("zzz-bogus", "create") is None
    assert capsys.readouterr().err == ""


def test_no_project_is_silent(capsys):
    assert m._guard_project_scope(None, "repos") is None
    assert capsys.readouterr().err == ""


def test_bare_project_no_command_is_silent(capsys):
    # Bare `--project X` (no verb) means "act on project X" -- valid.
    assert m._guard_project_scope("zzz-bogus", None) is None
    assert capsys.readouterr().err == ""


def test_guard_pops_stray_routed_marker(monkeypatch):
    # Defensive hygiene: a stray marker (set by the sibling router in other
    # flows) is popped so it never leaks to child processes of this verb.
    import os
    monkeypatch.setenv("AGENT_WORKTREES_PROJECT_ROUTED", "1")
    monkeypatch.setattr(m, "_is_registered_project", lambda name: True)
    m._guard_project_scope("dotfiles", "repos")
    assert "AGENT_WORKTREES_PROJECT_ROUTED" not in os.environ


# -- main() never bounces on a global-verb --project (backward compat) ---------

def test_main_unregistered_project_on_repos_warns_but_proceeds(monkeypatch):
    # The whole point of the softening: this must NOT return exit 2. The `repos`
    # handler is stubbed to isolate the guard from registry I/O.
    monkeypatch.setattr(m, "_is_registered_project", lambda name: False)
    monkeypatch.setattr(m, "cmd_repos_dispatch", lambda argv: 0, raising=False)
    rc = m.main(["--project", "zzz-bogus", "repos"])
    assert rc != 2


def test_main_registered_project_on_repos_proceeds(monkeypatch):
    # A stale binstub injects a registered project -> must just work, no note.
    monkeypatch.setattr(m, "_is_registered_project", lambda name: True)
    monkeypatch.setattr(m, "cmd_repos_dispatch", lambda argv: 0, raising=False)
    rc = m.main(["--project", "dotfiles", "repos"])
    assert rc != 2


# -- binstub carries NO routed marker (the coupling was removed) ---------------

def test_project_binstub_has_no_routed_marker():
    # The binstub must NOT set AGENT_WORKTREES_PROJECT_ROUTED: relying on it broke
    # stale binstubs. The guard no longer depends on any binstub/env cooperation.
    specs = installer._project_binstub_specs("demo")
    assert specs, "expected at least one binstub spec"
    for _dst, content in specs:
        assert "AGENT_WORKTREES_PROJECT_ROUTED" not in content
