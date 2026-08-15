"""Tests for the project-aware, role-routed read transport (transport.py).

The routing decision is host vs client vs no-project, plus the SSH argv/encoded
command a client builds to run the same read subcommand on the indexer host.
"""

from __future__ import annotations

import base64

import pytest

from agent_index import transport


def _patch(monkeypatch, *, root, indexer, machine, role="client"):
    monkeypatch.setattr(transport.config, "repo_root", lambda explicit=None: root)
    monkeypatch.setattr(transport.config, "read_indexer", lambda r: indexer)
    monkeypatch.setattr(transport.config, "machine_id", lambda: machine)
    monkeypatch.setattr(transport.config, "resolve_role", lambda: role)


# -- plan_route ----------------------------------------------------------------

def test_plan_route_host_when_machine_matches_designation(monkeypatch):
    _patch(monkeypatch, root="/repo", indexer={"machine": "tmichon-cloud1", "ssh": "tmichon-cloud1"},
           machine="tmichon-cloud1")
    role, indexer = transport.plan_route()
    assert role == "host"
    assert indexer["ssh"] == "tmichon-cloud1"


def test_plan_route_client_when_machine_differs(monkeypatch):
    _patch(monkeypatch, root="/repo", indexer={"machine": "tmichon-cloud1", "ssh": "tmichon-cloud1"},
           machine="tmichon-book2")
    role, _ = transport.plan_route()
    assert role == "client"


def test_plan_route_designation_compare_is_case_insensitive(monkeypatch):
    _patch(monkeypatch, root="/repo", indexer={"machine": "Tmichon-Cloud1", "ssh": "x"},
           machine="tmichon-cloud1")
    role, _ = transport.plan_route()
    assert role == "host"


def test_plan_route_no_project_falls_back_to_machine_role_host(monkeypatch):
    # No project/indexer (e.g. the delegated command running in the host's home
    # dir over SSH) -> machine-global role decides; host runs local => recursion
    # terminates at the host.
    _patch(monkeypatch, root=None, indexer=None, machine="tmichon-cloud1", role="host")
    role, indexer = transport.plan_route()
    assert role == "host"
    assert indexer is None


def test_plan_route_no_project_falls_back_to_machine_role_client(monkeypatch):
    _patch(monkeypatch, root=None, indexer=None, machine="tmichon-book2", role="client")
    role, indexer = transport.plan_route()
    assert role == "client"
    assert indexer is None


# -- maybe_delegate ------------------------------------------------------------

def test_maybe_delegate_non_delegable_returns_none(monkeypatch):
    _patch(monkeypatch, root=None, indexer=None, machine="x", role="client")
    assert transport.maybe_delegate("index", ["index"]) is None


def test_maybe_delegate_host_runs_local(monkeypatch):
    _patch(monkeypatch, root="/repo", indexer={"machine": "cloud1", "ssh": "cloud1"}, machine="cloud1")
    assert transport.maybe_delegate("search", ["search", "q"]) is None


def test_maybe_delegate_client_no_project_errors(monkeypatch, capsys):
    _patch(monkeypatch, root=None, indexer=None, machine="book2", role="client")
    rc = transport.maybe_delegate("search", ["search", "q"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "no indexer transport" in out


def test_maybe_delegate_in_project_without_indexer_runs_local(monkeypatch):
    # Inside a repo with no indexer designation (e.g. copilot-extensions itself)
    # -> run local; never intercept direct CLI dispatch, even on a client box.
    _patch(monkeypatch, root="/repo", indexer=None, machine="anybox", role="client")
    assert transport.maybe_delegate("search", ["search", "q"]) is None


def test_maybe_delegate_bare_host_runs_local(monkeypatch):
    # Truly bare on a host-role box (also the delegated-over-SSH home-dir case)
    # -> run local, terminating the recursion at the host.
    _patch(monkeypatch, root=None, indexer=None, machine="cloud1", role="host")
    assert transport.maybe_delegate("status", ["status"]) is None


def test_maybe_delegate_client_delegates_over_ssh(monkeypatch):
    _patch(monkeypatch, root="/repo", indexer={"machine": "tmichon-cloud1", "ssh": "tmichon-cloud1"},
           machine="tmichon-book2")
    captured = {}

    class _Proc:
        returncode = 0

    def _run(argv, check=False):
        captured["argv"] = argv
        return _Proc()

    monkeypatch.setattr(transport.subprocess, "run", _run)
    rc = transport.maybe_delegate("search", ["search", "hello world"])
    assert rc == 0
    argv = captured["argv"]
    assert argv[0] == "ssh"
    assert argv[1] == "tmichon-cloud1"
    # pwsh EncodedCommand carries the inner, forcing --json for search.
    assert argv[2].startswith("pwsh -NoProfile -WindowStyle Hidden -EncodedCommand ")
    enc = argv[2].rsplit(" ", 1)[1]
    inner = base64.b64decode(enc).decode("utf-16-le")
    assert "agent-index.ps1" in inner
    assert "'hello world'" in inner
    assert "'--json'" in inner


def test_maybe_delegate_client_ssh_failure_reports_error(monkeypatch, capsys):
    _patch(monkeypatch, root="/repo", indexer={"machine": "c1", "ssh": "c1"}, machine="b2")

    def _boom(argv, check=False):
        raise OSError("ssh: command not found")

    monkeypatch.setattr(transport.subprocess, "run", _boom)
    rc = transport.maybe_delegate("status", ["status"])
    assert rc == 1
    assert "SSH transport" in capsys.readouterr().out


# -- inner command / argv builders --------------------------------------------

def test_build_inner_pwsh_quotes_special_chars():
    inner = transport._build_inner("pwsh", ["search", "it's a test", "--limit", "5"])
    assert inner.startswith('& "$env:USERPROFILE\\.local\\bin\\agent-index.ps1" ')
    # single quote doubled for pwsh literal quoting
    assert "'it''s a test'" in inner
    assert "'--limit' '5'" in inner


def test_build_inner_bash_uses_home_binstub_and_shlex():
    inner = transport._build_inner("bash", ["search", "a b", "--json"])
    assert inner.startswith('"$HOME/.local/bin/agent-index" ')
    assert "'a b'" in inner


def test_build_ssh_argv_bash_wraps_in_login_shell():
    argv = transport.build_ssh_argv({"ssh": "linbox", "shell": "bash"}, ["status"])
    assert argv[0] == "ssh"
    assert argv[1] == "linbox"
    assert argv[2].startswith("bash -lc '")
    assert argv[2].endswith("'")


def test_build_ssh_argv_defaults_to_pwsh():
    argv = transport.build_ssh_argv({"ssh": "winbox"}, ["status"])
    assert "EncodedCommand" in argv[2]


@pytest.mark.parametrize("sub,expect_json", [("search", True), ("status", False),
                                             ("similar", False), ("clusters", False)])
def test_forward_argv_forces_json_only_for_search(sub, expect_json):
    argv = transport._forward_argv(sub, [sub, "x"])
    assert ("--json" in argv) is expect_json


def test_forward_argv_search_no_double_json():
    argv = transport._forward_argv("search", ["search", "x", "--json"])
    assert argv.count("--json") == 1
