"""Tests for the top-level ``--project`` override (command-surface effort).

A top-level ``--project`` (injected by the ``<repo> <slug>`` router, e.g.
``<repo> bridge send <machine>``) pins the target project for the remote
worktree resolve instead of the caller's cwd project -- so a dispatch from
inside an unrelated repo's worktree still resolves the intended repo.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import agent_bridge.__main__ as m


def _reset() -> None:
    m._set_project_override(None)
    m._PROJECT_ROUTED = False


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
    args = parser.parse_args(["--project", "foo", "sessions"])
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


@pytest.mark.parametrize("verb", ["agents", "machines", "send", "create"])
def test_guard_explicit_project_on_consuming_verb_ok(monkeypatch, verb):
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    parser = m.build_parser()
    argv = ["--project", "foo", verb]
    if verb in {"send", "create"}:
        argv.append("target")
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


def test_agents_parser_accepts_all_projects():
    args = m.build_parser().parse_args(["agents", "--all-projects"])
    assert args.all_projects is True


def test_explicit_project_conflicts_with_all_projects(monkeypatch, capsys):
    m._set_project_override("project-a")
    m._PROJECT_ROUTED = False
    try:
        with pytest.raises(SystemExit) as exc:
            m._listing_project(SimpleNamespace(all_projects=True))
        assert exc.value.code == 2
        assert "mutually exclusive" in capsys.readouterr().err
    finally:
        _reset()


def test_routed_project_allows_all_projects():
    m._set_project_override("project-a")
    m._PROJECT_ROUTED = True
    try:
        assert m._listing_project(
            SimpleNamespace(all_projects=True, json=False)
        ) is None
    finally:
        _reset()


def test_json_listing_is_fleet_global_without_explicit_project(monkeypatch):
    monkeypatch.setattr(
        m, "_sender_repo",
        lambda: pytest.fail("JSON fleet listing should not resolve CWD project"),
    )
    assert m._listing_project(
        SimpleNamespace(all_projects=False, json=True)
    ) is None


def test_agents_uses_cwd_project_and_all_projects_escape(
    monkeypatch, capsys,
):
    class Client:
        def list_agents_with_diagnostics(self):
            return [
                {"name": "a", "display_name": "a", "project": "project-a"},
                {"name": "b", "display_name": "b", "project": "project-b"},
                {"name": "global", "display_name": "global", "project": None},
            ], []

    monkeypatch.setattr(m, "_get_client", lambda: Client())
    monkeypatch.setattr(m, "_sender_repo", lambda: "project-a")
    m._set_project_override("project-a")
    m._cmd_agents(SimpleNamespace(json=True, all_projects=False))
    scoped = capsys.readouterr().out
    assert '"a"' in scoped
    assert '"b"' not in scoped
    _reset()

    m._cmd_agents(SimpleNamespace(json=True, all_projects=True))
    fleet = capsys.readouterr().out
    assert '"a"' in fleet and '"b"' in fleet and '"global"' in fleet


def test_agents_reports_partial_topology_failure(monkeypatch, capsys):
    class Client:
        def list_agents_with_diagnostics(self):
            return [{"name": "a", "display_name": "a", "project": None}], [
                "broken: machines.yaml not found",
            ]

    monkeypatch.setattr(m, "_get_client", lambda: Client())
    with pytest.raises(SystemExit) as exc:
        m._cmd_agents(SimpleNamespace(json=True, all_projects=True))
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert '"a"' in captured.out
    assert "broken: machines.yaml not found" in captured.err


def test_machines_filters_with_normalized_machine_keys(monkeypatch, capsys):
    class Client:
        def list_machines_with_diagnostics(self):
            return [
                {"key": "host-a"},
                {"key": "host-b"},
                {"key": "host-global"},
            ], []

        def list_agents_with_diagnostics(self):
            return [
                {
                    "name": "a",
                    "project": "project-a",
                    "machine_key": "host-a",
                },
                {
                    "name": "b",
                    "project": "project-b",
                    "machine_key": "host-b",
                },
                {
                    "name": "global",
                    "project": None,
                    "machine_key": "host-global",
                },
            ], []

    monkeypatch.setattr(m, "_get_client", lambda: Client())
    monkeypatch.setattr(m, "_sender_repo", lambda: "project-a")
    m._set_project_override("project-a")
    m._cmd_machines(SimpleNamespace(json=True, all_projects=False))
    output = capsys.readouterr().out
    assert '"host-a"' in output
    assert '"host-global"' in output
    assert '"host-b"' not in output
    _reset()
