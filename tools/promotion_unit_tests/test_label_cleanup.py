"""Actor-free qualification of privately configured repository label cleanup."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = "tools/sweep_forbidden_labels.py"


@pytest.fixture
def labels(monkeypatch):
    spec = importlib.util.spec_from_file_location("promotion_label_cleanup", ROOT / SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    return module


def test_exact_casefold_matches_across_pages_delete_only_configured_names(labels, monkeypatch):
    calls = []

    def request(args):
        calls.append(args)
        if "--paginate" in args:
            return json.dumps([
                [{"name": "Private-Origin"}, {"name": "private-origin-extra"}],
                [{"name": "literal*"}, {"name": "literal-other"}, {"name": "path/label"}],
            ])
        return ""

    monkeypatch.setattr(labels, "_gh", request)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    result = labels.sweep(
        "example/repository", '["private-origin", "literal*", "path/label"]', execute=True
    )
    assert result["selected_count"] == result["deleted_count"] == 3
    assert calls == [
        ["--paginate", "--slurp", "repos/example/repository/labels?per_page=100"],
        ["--method", "DELETE", "repos/example/repository/labels/Private-Origin"],
        ["--method", "DELETE", "repos/example/repository/labels/literal%2A"],
        ["--method", "DELETE", "repos/example/repository/labels/path%2Flabel"],
    ]


def test_unconfigured_empty_and_dry_run_never_delete(labels, monkeypatch):
    calls = []
    monkeypatch.setattr(
        labels, "_gh",
        lambda args: calls.append(args) or '[[{"name": "private-origin"}]]',
    )
    assert labels.sweep("", "", execute=True)["configured"] is False
    assert labels.sweep("", "[]", execute=True)["configured"] is True
    assert calls == []
    result = labels.sweep("example/repository", '["private-origin"]')
    assert result["selected_count"] == 1
    assert result["deleted_count"] == 0
    assert len(calls) == 1
    assert "--method" not in calls[0]


def test_invalid_policy_refuses_before_any_request(labels, monkeypatch):
    def forbidden(*args):
        raise AssertionError("invalid policy reached the API")

    monkeypatch.setattr(labels, "_gh", forbidden)
    for raw in ('{', '{}', 'true', '[null]', '[""]', '["   "]', '["bad\\nname"]'):
        with pytest.raises(ValueError, match="Invalid private label policy"):
            labels.sweep("example/repository", raw, execute=True)


def test_execution_requires_explicit_token_and_bounded_repository(labels, monkeypatch):
    def forbidden(*args):
        raise AssertionError("invalid execution reached the API")

    monkeypatch.setattr(labels, "_gh", forbidden)
    with pytest.raises(ValueError, match="explicit GitHub token"):
        labels.sweep("example/repository", '["private-origin"]', execute=True)
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    for repo in ("", "../repository", "owner/..", "owner/repo/other", "owner/repo?x=y"):
        with pytest.raises(ValueError, match="owner/repository"):
            labels.sweep(repo, '["private-origin"]', execute=True)


def test_incomplete_or_duplicate_inventory_never_partially_deletes(labels, monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    for inventory in (
        {},
        [],
        [None],
        [[{"name": "private-origin"}], [{"name": None}]],
        [[{"name": "private-origin"}], [{"name": "PRIVATE-ORIGIN"}]],
    ):
        calls = []

        def request(args):
            calls.append(args)
            return json.dumps(inventory)

        monkeypatch.setattr(labels, "_gh", request)
        with pytest.raises(ValueError, match="Invalid label inventory"):
            labels.sweep("example/repository", '["private-origin"]', execute=True)
        assert len(calls) == 1
        assert "--method" not in calls[0]


def test_cli_uses_explicit_host_and_redacts_backend_failure(labels, monkeypatch, capsys):
    calls = []
    monkeypatch.setenv("GH_TOKEN", "fixture-token")
    monkeypatch.setenv(labels.POLICY_ENV, '["private-origin"]')
    monkeypatch.setenv("GH_DEBUG", "api")

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=1, stdout="private-origin", stderr="private-origin fixture-token"
        )

    monkeypatch.setattr(labels.subprocess, "run", run)
    assert labels.main(["--repo", "example/repository", "--execute"]) == 1
    output = capsys.readouterr()
    assert "private-origin" not in output.out + output.err
    assert "fixture-token" not in output.out + output.err
    assert "failed" in output.err
    argv, kwargs = calls[0]
    assert argv[:4] == ["gh", "api", "--hostname", "github.com"]
    assert labels.POLICY_ENV not in kwargs["env"]
    assert "GH_DEBUG" not in kwargs["env"]
    assert kwargs["env"]["GH_PROMPT_DISABLED"] == "1"


def test_cli_summary_does_not_publish_names(labels, monkeypatch, capsys):
    monkeypatch.setenv(labels.POLICY_ENV, '["private-origin"]')
    monkeypatch.setenv("GH_TOKEN", "fixture-token")

    def run(argv, **kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout="" if "--method" in argv else '[[{"name": "Private-Origin"}]]',
            stderr="",
        )

    monkeypatch.setattr(labels.subprocess, "run", run)
    assert labels.main(["--repo", "example/repository", "--execute"]) == 0
    output = capsys.readouterr()
    assert "origin" not in output.out + output.err
    assert json.loads(output.out)["deleted_count"] == 1
