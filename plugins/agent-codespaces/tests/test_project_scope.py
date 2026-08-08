"""Tests for the top-level ``--project`` scope guard (command-surface effort).

Mirrors agent-bridge #1080 for agent-codespaces: ``--project`` is meaningful
only for the project-consuming verbs (``config``, whose ``init``/``adopt``
read the repo-root/``codespaces.yaml`` from the cwd). On a name/CodeSpace-
addressed verb an *explicit* (user-typed) ``--project`` bounces instead of
silently no-op'ing, while a router-injected one (marked
``AGENT_WORKTREES_PROJECT_ROUTED=1``) stays a silent no-op so the uniform
``<repo> codespaces …`` surface keeps working.
"""

from __future__ import annotations

import argparse
import os

import pytest

import agent_codespaces.__main__ as m


def _parser() -> argparse.ArgumentParser:
    # A tiny stand-in parser matching the real top-level shape (a --project
    # option + a subparser dest="command"); the guard only reads args.project /
    # args.command and calls parser.error, so this avoids building the full CLI.
    p = argparse.ArgumentParser(prog="agent-codespaces")
    p.add_argument("--project", "-p", dest="project", default=None)
    sub = p.add_subparsers(dest="command")
    for verb in ("config", "ssh", "list", "create"):
        sp = sub.add_parser(verb)
        sp.add_argument("rest", nargs="*")
    return p


def test_guard_explicit_project_on_name_addressed_errors(monkeypatch):
    # An explicit (user-typed, not router-injected) --project on a name-addressed
    # verb (ssh) must bounce, not silently no-op.
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    p = _parser()
    args = p.parse_args(["--project", "foo", "ssh", "some-codespace"])
    with pytest.raises(SystemExit) as ei:
        m._guard_project_scope(p, args)
    assert ei.value.code == 2  # argparse error exit


def test_guard_routed_project_on_name_addressed_is_silent(monkeypatch):
    # Router-injected --project (marked routed) stays a silent no-op so the
    # uniform `<repo> codespaces ssh <name>` surface keeps working.
    monkeypatch.setenv("AGENT_WORKTREES_PROJECT_ROUTED", "1")
    p = _parser()
    args = p.parse_args(["--project", "foo", "ssh", "some-codespace"])
    assert m._guard_project_scope(p, args) is False  # no raise, no chdir
    # The marker is consumed so it never leaks to child processes.
    assert "AGENT_WORKTREES_PROJECT_ROUTED" not in os.environ


def test_guard_explicit_project_on_config_applies(monkeypatch):
    # config is project-consuming (config init/adopt read the cwd repo-root) ->
    # an explicit --project applies (True: caller chdirs).
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    p = _parser()
    args = p.parse_args(["--project", "foo", "config", "adopt"])
    assert m._guard_project_scope(p, args) is True


def test_guard_routed_project_on_config_applies(monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_PROJECT_ROUTED", "1")
    p = _parser()
    args = p.parse_args(["--project", "foo", "config", "init"])
    assert m._guard_project_scope(p, args) is True
    assert "AGENT_WORKTREES_PROJECT_ROUTED" not in os.environ


def test_guard_no_project_is_noop(monkeypatch):
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    p = _parser()
    args = p.parse_args(["ssh", "some-codespace"])
    assert m._guard_project_scope(p, args) is False  # no --project -> no raise


def test_guard_consumes_routed_marker_even_without_project(monkeypatch):
    # Defensive: the marker is popped regardless, so a stale routed marker never
    # lingers in the environment of this process or its children.
    monkeypatch.setenv("AGENT_WORKTREES_PROJECT_ROUTED", "1")
    p = _parser()
    args = p.parse_args(["ssh", "some-codespace"])
    assert m._guard_project_scope(p, args) is False
    assert "AGENT_WORKTREES_PROJECT_ROUTED" not in os.environ


def test_config_is_the_only_consuming_verb():
    # Guards the taxonomy decision: only `config` reads the cwd repo-root
    # (_resolve_repo_root); every other verb is name-addressed or uses the
    # merged adopted-repo config.
    assert m._PROJECT_CONSUMING_VERBS == frozenset({"config"})
