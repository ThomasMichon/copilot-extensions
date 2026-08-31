from __future__ import annotations

import argparse
import json

import pytest

from agent_machines import __main__ as cli
from agent_machines.manifest import ManifestError


def _args(*, repo=None, all_projects=False):
    return argparse.Namespace(repo=repo, all_projects=all_projects)


def test_reconcile_defaults_to_cwd_repo(monkeypatch, tmp_path):
    package = object()
    monkeypatch.setattr(
        cli._layout,
        "resolve_cwd_repo",
        lambda: ("current", tmp_path, tmp_path),
    )
    monkeypatch.setattr(
        cli._discover,
        "packages_in_repo",
        lambda path, name, machine, **kwargs: [package],
    )
    monkeypatch.setattr(
        cli,
        "_collect_all_packages",
        lambda machine: pytest.fail("default scope must not collect the machine union"),
    )

    packages, scope = cli._collect_reconcile_packages(_args(), "box")

    assert packages == [package]
    assert scope == "repo:current"


def test_reconcile_all_projects_is_explicit(monkeypatch):
    package = object()
    monkeypatch.setattr(cli, "_collect_all_packages", lambda machine: [package])

    packages, scope = cli._collect_reconcile_packages(
        _args(all_projects=True),
        "box",
    )

    assert packages == [package]
    assert scope == "all-projects"


def test_reconcile_accepts_registered_repo(monkeypatch, tmp_path):
    package = object()
    monkeypatch.setattr(
        cli._discover,
        "resolve_registered_repo",
        lambda name: ("canonical", tmp_path),
    )
    monkeypatch.setattr(
        cli._discover,
        "packages_in_repo",
        lambda path, name, machine, **kwargs: [package],
    )

    packages, scope = cli._collect_reconcile_packages(
        _args(repo="CANONICAL"),
        "box",
    )

    assert packages == [package]
    assert scope == "repo:canonical"


def test_reconcile_accepts_explicit_repo_path(monkeypatch, tmp_path):
    package = object()
    monkeypatch.setattr(cli._discover, "resolve_registered_repo", lambda name: None)
    monkeypatch.setattr(
        cli._layout,
        "resolve_cwd_repo",
        lambda path: ("canonical", path.resolve(), path.resolve()),
    )
    monkeypatch.setattr(
        cli._discover,
        "packages_in_repo",
        lambda path, name, machine, **kwargs: [package],
    )

    packages, scope = cli._collect_reconcile_packages(
        _args(repo=str(tmp_path)),
        "box",
    )

    assert packages == [package]
    assert scope == "repo:canonical"


def test_reconcile_unknown_repo_fails(monkeypatch):
    monkeypatch.setattr(cli._discover, "resolve_registered_repo", lambda name: None)

    with pytest.raises(ManifestError, match="neither a directory nor a registered repo"):
        cli._collect_reconcile_packages(_args(repo="missing"), "box")


def test_reconcile_explicit_non_git_directory_is_specific(monkeypatch, tmp_path):
    monkeypatch.setattr(cli._discover, "resolve_registered_repo", lambda name: None)
    monkeypatch.setattr(
        cli._layout,
        "resolve_cwd_repo",
        lambda path: (_ for _ in ()).throw(
            ManifestError(f"{path} is not inside a Git repository")
        ),
    )

    with pytest.raises(ManifestError, match=r"repo path .* is not a Git repository"):
        cli._collect_reconcile_packages(_args(repo=str(tmp_path)), "box")


def test_scope_flags_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        cli.main(["plan", "--repo", "one", "--all-projects"])


def test_validate_json_preserves_findings_array(monkeypatch, capsys):
    monkeypatch.setattr(
        cli,
        "_collect_reconcile_packages",
        lambda args, machine: ([], "repo:current"),
    )
    monkeypatch.setattr(cli._reconcile, "resolve_union", lambda packages, machine: [])
    monkeypatch.setattr(cli._validator, "validate", lambda resolved, machine: [])

    rc = cli.main(["validate", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert payload == []


def test_validate_human_reports_scope_with_findings(monkeypatch, capsys):
    finding = type(
        "Finding",
        (),
        {"level": "advisory", "code": "example", "message": "detail"},
    )()
    monkeypatch.setattr(
        cli,
        "_collect_reconcile_packages",
        lambda args, machine: ([], "repo:current"),
    )
    monkeypatch.setattr(cli._reconcile, "resolve_union", lambda packages, machine: [])
    monkeypatch.setattr(
        cli._validator,
        "validate",
        lambda resolved, machine: [finding],
    )
    monkeypatch.setattr(cli._validator, "has_errors", lambda findings: False)

    rc = cli.main(["validate"])
    output = capsys.readouterr().out

    assert rc == 0
    assert "validator [repo:current]" in output
    assert "[advisory] example: detail" in output
