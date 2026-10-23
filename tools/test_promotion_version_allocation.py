"""Immutable version reservations survive an unsuccessful Main landing."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

import accumulate_bumps
import promote_release as promotion
from promotion_python_artifacts import PromotionArtifactError, validate_source_binding
from promotion_version_allocation import next_reserved_version
from test_promote_release import (
    _commit, _git, no_ambient_git_identity as _identity_fixture,
    repo as _repo_fixture,
)

repo = _repo_fixture
no_ambient_git_identity = _identity_fixture


def _change(repo, name):
    directory = repo / ".changefiles"
    directory.mkdir(exist_ok=True)
    (directory / f"{name}.json").write_text(json.dumps({
        "comment": name,
        "changes": [{"plugin": "demo-plugin", "type": "patch"}],
    }), encoding="utf-8")


def _runtime(repo):
    plugin = repo / "plugins/demo-plugin"
    (plugin / "pyproject.toml").write_text(
        '[project]\nname = "demo-plugin"\nversion = "0.1.0-dev1"\n',
        encoding="utf-8",
    )
    source = plugin / "src/demo"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text('VALUE = "initial"\n', encoding="utf-8")


@pytest.mark.parametrize("current,expected", [
    ("1.2.3", "1.2.4-dev1"),
    ("1.2.3-dev1", "1.2.3-dev2"),
])
def test_conflicting_reservation_always_advances_version_order(current, expected):
    actual = next_reserved_version(current, accumulator=accumulate_bumps)
    assert actual == expected
    assert accumulate_bumps.version_key(actual) > accumulate_bumps.version_key(current)


def test_changed_dependency_closure_cannot_reuse_an_unlanded_version_tag(repo):
    _runtime(repo)
    _commit(repo, "initial runtime")
    _git(["branch", "-f", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    _change(repo, "first")
    _commit(repo, "first pending source")
    kwargs = dict(
        repo=repo, dev_ref="dev", main_ref="main", promotion_time="2026-10-20T10:00:00Z",
    )
    first = promotion.promote(**kwargs)
    tag = "demo-plugin-v0.1.1-dev1"
    _git(["tag", tag, first["commit"]], repo)

    plugin = repo / "plugins/demo-plugin"
    library = repo / "libs/diagnostic-events"
    (library / "src/diagnostic_events").mkdir(parents=True)
    (library / "src/diagnostic_events/__init__.py").write_text(
        "EVENT = 1\n", encoding="utf-8",
    )
    (library / "pyproject.toml").write_text(
        '[project]\nname = "diagnostic-events"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    with (plugin / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            'dependencies = ["diagnostic-events"]\n'
            '[tool.uv.sources]\n'
            'diagnostic-events = {path = "../../libs/diagnostic-events", editable = true}\n',
        )
    _change(repo, "second")
    _commit(repo, "extend the pending dependency closure")
    second = promotion.promote(**kwargs)

    assert second["bumps"]["demo-plugin"] == ("0.1.0-dev1", "0.1.1-dev2")
    assert _git(["rev-parse", tag], repo) == first["commit"]
    assert _git(["rev-parse", "main"], repo) == first["main_before"]
    manifest = json.loads(_git([
        "show", f"{second['commit']}:plugins/demo-plugin/plugin.json",
    ], repo))
    assert manifest["version"] == "0.1.1-dev2"
    prior = promotion.add_scratch_worktree(first["commit"], repo=repo)
    try:
        with pytest.raises(PromotionArtifactError, match="source closure differs"):
            validate_source_binding({
                "plugin": "demo-plugin",
                "wheels": [
                    {"source": "plugins/demo-plugin"},
                    {"source": "plugins/demo-plugin/libs/diagnostic-events"},
                ],
            }, prior)
    finally:
        promotion.remove_scratch_worktree(prior, repo=repo)
    second_tag = "demo-plugin-v0.1.1-dev2"
    _git(["tag", second_tag, second["commit"]], repo)
    assert promotion.promote(**kwargs)["commit"] == second["commit"]
    (library / "src/diagnostic_events/__init__.py").write_text(
        "EVENT = 2\n", encoding="utf-8",
    )
    _commit(repo, "change a reserved library payload")
    third = promotion.promote(**kwargs)
    assert third["bumps"]["demo-plugin"] == ("0.1.0-dev1", "0.1.1-dev3")
    assert _git(["rev-parse", second_tag], repo) == second["commit"]


@pytest.mark.parametrize("annotated", [False, True])
def test_exact_unlanded_payload_reuses_its_reservation_on_retry(repo, annotated):
    _runtime(repo)
    _commit(repo, "initial runtime")
    _git(["branch", "-f", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    _change(repo, "first")
    _commit(repo, "pending source")
    kwargs = dict(
        repo=repo, dev_ref="dev", main_ref="main", promotion_time="2026-10-20T10:00:00Z",
    )
    first = promotion.promote(**kwargs)
    args = ["tag", "demo-plugin-v0.1.1-dev1", first["commit"]]
    if annotated:
        args += ["-a", "-m", "immutable reservation"]
    _git(args, repo)
    assert promotion.promote(**kwargs)["commit"] == first["commit"]


@pytest.mark.parametrize("change", ["code", "attributes"])
def test_changed_payload_or_checkout_policy_advances_an_occupied_version(repo, change):
    _runtime(repo)
    _commit(repo, "initial runtime")
    _git(["branch", "-f", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    _change(repo, "first")
    _commit(repo, "pending source")
    first = promotion.promote(repo=repo, dev_ref="dev", main_ref="main")
    _git(["tag", "demo-plugin-v0.1.1-dev1", first["commit"]], repo)
    if change == "code":
        (repo / "plugins/demo-plugin/src/demo/__init__.py").write_text(
            'VALUE = "changed"\n', encoding="utf-8",
        )
    else:
        (repo / ".gitattributes").write_text("*.py text eol=lf\n", encoding="utf-8")
    _change(repo, "second")
    _commit(repo, "different pending payload")
    second = promotion.promote(repo=repo, dev_ref="dev", main_ref="main")
    assert second["bumps"]["demo-plugin"] == ("0.1.0-dev1", "0.1.1-dev2")


def test_noncommit_reservation_is_an_explicit_refusal_not_an_unused_version(repo):
    _runtime(repo)
    _commit(repo, "initial runtime")
    _git(["branch", "-f", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    _change(repo, "first")
    _commit(repo, "pending source")
    blob = _git(["rev-parse", "HEAD:README.md"], repo)
    _git(["tag", "demo-plugin-v0.1.1-dev1", blob], repo)
    before = _git(["status", "--porcelain"], repo)
    with pytest.raises(promotion.PromotionError, match="cannot allocate immutable versions"):
        promotion.promote(repo=repo, dev_ref="dev", main_ref="main")
    assert _git(["rev-parse", "demo-plugin-v0.1.1-dev1"], repo) == blob
    assert _git(["status", "--porcelain"], repo) == before
    command = subprocess.run(
        [sys.executable, promotion.__file__, "--repo", str(repo),
         "--dev-ref", "dev", "--main-ref", "main", "--dry-run"],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert command.returncode == 1
    assert "cannot allocate immutable versions" in command.stderr
    assert "Traceback" not in command.stderr


def test_unrelated_source_preserves_an_unchanged_shipped_plugin_version(repo):
    _runtime(repo)
    main = _commit(repo, "shipped runtime")
    tag = "demo-plugin-v0.1.0-dev1"
    _git(["tag", tag, main], repo)
    _git(["branch", "-f", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    (repo / "README.md").write_text("unrelated documentation\n", encoding="utf-8")
    _commit(repo, "unrelated source")
    result = promotion.promote(repo=repo, dev_ref="dev", main_ref="main")
    assert result["bumps"] == {}
    assert _git(["rev-parse", tag], repo) == main
    assert json.loads(_git([
        "show", f"{result['commit']}:plugins/demo-plugin/plugin.json",
    ], repo))["version"] == "0.1.0-dev1"
