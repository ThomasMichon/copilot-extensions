"""Main-derived versions are independent of unlanded plugin-version aliases."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

import pytest

import accumulate_bumps
import promote_release as promotion
from promotion_python_artifacts import PromotionArtifactError, validate_source_binding
from candidate_qualification_receipt import UnitQualificationError, input_equivalence
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


@pytest.mark.parametrize(("main_version", "expected"), [
    ("0.1.0-dev1", "0.1.1-dev1"),
    ("0.1.19-dev1", "0.1.20-dev1"),
])
def test_standalone_version_allocation_preserves_enrolled_python_source(
    repo, main_version, expected,
):
    source = Path(__file__).resolve().parents[1]
    project = source / "services/chat"
    component = repo / "services/chat"
    version = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]["version"]

    def copy_source(value):
        shutil.copytree(
            project / "src", component / "src", dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        (component / "pyproject.toml").write_text(
            f'[project]\nname = "agent-chat-service"\nversion = "{value}"\n',
            encoding="utf-8",
        )

    copy_source(main_version)
    accumulate_bumps._write_source_fallbacks(component, version, main_version)
    _commit(repo, "land initial standalone package")
    _git(["branch", "-f", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    copy_source(version)
    changes = repo / ".changefiles"
    changes.mkdir()
    (changes / "chat.json").write_text(json.dumps({
        "comment": "standalone source change",
        "changes": [{"plugin": "services/chat", "type": "patch"}],
    }), encoding="utf-8")
    _commit(repo, "prepare standalone source")
    manifest = json.loads((source / "tools/promotion-unit-manifest.json").read_text())
    inputs = {
        name: hashlib.sha256((repo / name).read_bytes()).hexdigest()
        for name in manifest["sources"]
        if name.startswith("services/chat/src/")
    }
    initializer = "services/chat/src/agent_chat_service/__init__.py"
    assert initializer in inputs
    report = promotion.promote(
        repo=repo, dev_ref="dev", main_ref="main", promotion_time="2026-10-24T02:00:00Z",
    )
    candidate = promotion.add_scratch_worktree(report["commit"], repo=repo)
    try:
        assert report["bumps"]["services/chat"][1] == expected
        assert tomllib.loads((candidate / "services/chat/pyproject.toml").read_text())[
            "project"
        ]["version"] == expected
        input_equivalence(repo, candidate, {"sources": inputs})
        assert (candidate / initializer).read_bytes() == (repo / initializer).read_bytes()
        with (candidate / initializer).open("a", encoding="utf-8") as stream:
            stream.write("\nSOURCE_CHANGED = True\n")
        with pytest.raises(UnitQualificationError, match="candidate unit source differs"):
            input_equivalence(repo, candidate, {"sources": inputs})
    finally:
        promotion.remove_scratch_worktree(candidate, repo=repo)
    assert tomllib.loads((component / "pyproject.toml").read_text())["project"]["version"] == version


def test_unlanded_alias_does_not_change_main_derived_dependency_versions(repo):
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

    assert second["bumps"]["demo-plugin"] == ("0.1.0-dev1", "0.1.1-dev1")
    assert _git(["rev-parse", tag], repo) == first["commit"]
    assert _git(["rev-parse", "main"], repo) == first["main_before"]
    manifest = json.loads(_git([
        "show", f"{second['commit']}:plugins/demo-plugin/plugin.json",
    ], repo))
    assert manifest["version"] == "0.1.1-dev1"
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
    assert promotion.promote(**kwargs)["commit"] == second["commit"]
    (library / "src/diagnostic_events/__init__.py").write_text(
        "EVENT = 2\n", encoding="utf-8",
    )
    _commit(repo, "change a reserved library payload")
    third = promotion.promote(**kwargs)
    assert third["bumps"]["demo-plugin"] == ("0.1.0-dev1", "0.1.1-dev1")
    assert _git(["rev-parse", tag], repo) == first["commit"]


@pytest.mark.parametrize("annotated", [False, True])
def test_exact_source_retry_is_independent_of_unlanded_aliases(repo, annotated):
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
def test_changed_source_or_checkout_policy_does_not_consume_a_version_before_main(repo, change):
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
    assert second["bumps"]["demo-plugin"] == ("0.1.0-dev1", "0.1.1-dev1")
    assert promotion._tree_excluding_state(
        _git(["rev-parse", first["commit"] + "^{tree}"], repo), repo=repo,
    ) != promotion._tree_excluding_state(
        _git(["rev-parse", second["commit"] + "^{tree}"], repo), repo=repo,
    )
    assert _git(["rev-parse", "demo-plugin-v0.1.1-dev1"], repo) == first["commit"]


def test_noncommit_plugin_version_alias_does_not_block_source_composition(repo):
    _runtime(repo)
    _commit(repo, "initial runtime")
    _git(["branch", "-f", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    _change(repo, "first")
    _commit(repo, "pending source")
    blob = _git(["rev-parse", "HEAD:README.md"], repo)
    _git(["tag", "demo-plugin-v0.1.1-dev1", blob], repo)
    before = _git(["status", "--porcelain"], repo)
    result = promotion.promote(repo=repo, dev_ref="dev", main_ref="main")
    assert result["bumps"]["demo-plugin"] == ("0.1.0-dev1", "0.1.1-dev1")
    assert _git(["rev-parse", "demo-plugin-v0.1.1-dev1"], repo) == blob
    assert _git(["status", "--porcelain"], repo) == before
    command = subprocess.run(
        [sys.executable, promotion.__file__, "--repo", str(repo),
         "--dev-ref", "dev", "--main-ref", "main", "--dry-run"],
        capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert command.returncode == 0, command.stderr
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


def test_landed_main_advances_the_next_changed_plugin_version(repo):
    _runtime(repo)
    _commit(repo, "initial runtime")
    _git(["branch", "-f", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    _change(repo, "first")
    _commit(repo, "first pending source")
    first = promotion.promote(repo=repo, dev_ref="dev", main_ref="main")
    _git(["update-ref", "refs/heads/main", first["commit"]], repo)
    (repo / "plugins/demo-plugin/src/demo/__init__.py").write_text(
        'VALUE = "next released source"\n', encoding="utf-8",
    )
    _change(repo, "second")
    _commit(repo, "second pending source")

    second = promotion.promote(repo=repo, dev_ref="dev", main_ref="main")

    old, new = second["bumps"]["demo-plugin"]
    assert (old, new) == ("0.1.1-dev1", "0.1.2-dev1")
    assert accumulate_bumps.version_key(new) > accumulate_bumps.version_key(old)
    assert _git(["rev-parse", "main"], repo) == first["commit"]
