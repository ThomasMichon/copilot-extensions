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
import local_install_source
import local_runtime_promotion
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
        '[build-system]\nrequires = ["setuptools>=68", "wheel"]\n'
        'build-backend = "setuptools.build_meta"\n'
        '[project]\nname = "demo-plugin"\nversion = "0.1.0-dev1"\n',
        encoding="utf-8",
    )
    source = plugin / "src/demo"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text('VALUE = "initial"\n', encoding="utf-8")


@pytest.mark.parametrize("legacy", [False, True])
def test_local_composition_keeps_main_versions_and_changefiles_on_source(
    repo: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool,
) -> None:
    _runtime(repo)
    _commit(repo, "initial runtime")
    seed = _git(["rev-parse", "HEAD"], repo)
    _git(["branch", "-f", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    _change(repo, "local-change")
    source = _commit(repo, "local source")
    before_refs = _git(["show-ref"], repo)
    before_index = (repo / ".git/index").read_bytes()
    loader = promotion._load_module
    reads = []

    def load(path, name):
        module = loader(path, name)
        if name == "promote_accumulate_bumps":
            reader = module.read_changefiles
            calls = []
            reads.append(calls)

            def read():
                calls.append(True)
                return reader()

            module.read_changefiles = read
            if legacy:
                consume = module._consume_changefiles

                def consume_legacy():
                    consume()

                module._consume_changefiles = consume_legacy
        return module

    monkeypatch.setattr(promotion, "_load_module", load)

    composed = local_runtime_promotion.compose(repo, source_commit=source, main_seed=seed)
    repeated = local_runtime_promotion.compose(repo, source_commit=source, main_seed=seed)

    assert composed == repeated
    assert composed.source_commit == source
    assert composed.main_seed == seed
    assert json.loads(_git([
        "show", f"{composed.tree}:plugins/demo-plugin/plugin.json",
    ], repo))["version"] == "0.1.0-dev1"
    assert ".changefiles/" not in _git(["ls-tree", "-r", "--name-only", composed.tree], repo)
    assert _git(["show-ref"], repo) == before_refs
    assert (repo / ".git/index").read_bytes() == before_index
    assert reads == [[True] * (2 if legacy else 1)] * 2
    assert (repo / ".changefiles/local-change.json").is_file()
    assert _git(["status", "--porcelain"], repo) == ""
    assert len(_git(["worktree", "list"], repo).splitlines()) == 1
    official = promotion.promote(repo=repo, dev_ref=source, main_ref=seed)
    assert official["bumps"]["demo-plugin"] == ("0.1.0-dev1", "0.1.1-dev1")


@pytest.mark.parametrize("selectors", [False, True])
def test_local_composition_does_not_follow_or_mutate_a_foreign_caller_index(
    repo, tmp_path, monkeypatch, selectors,
):
    _runtime(repo)
    seed = _commit(repo, "initial runtime")
    _change(repo, "local-change")
    source = _commit(repo, "local source")
    foreign = tmp_path / "foreign"
    _git(["init", "-q", str(foreign)], repo)
    (foreign / "sentinel.txt").write_text("foreign index and worktree\n", encoding="utf-8")
    foreign_head = _commit(foreign, "foreign source")
    index = foreign / ".git/index"
    before = index.read_bytes(), index.stat().st_mtime_ns
    own_index = (repo / ".git/index").read_bytes()
    with monkeypatch.context() as environment:
        environment.setenv("GIT_INDEX_FILE", str(index))
        if selectors:
            environment.setenv("GIT_DIR", str(foreign / ".git"))
            environment.setenv("GIT_COMMON_DIR", str(foreign / ".git"))
            environment.setenv("GIT_WORK_TREE", str(foreign))
        composed = local_runtime_promotion.compose(repo, source_commit=source, main_seed=seed)
    assert composed.source_commit == source and composed.main_seed == seed
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before
    assert (repo / ".git/index").read_bytes() == own_index
    assert _git(["rev-parse", "HEAD"], foreign) == foreign_head
    assert _git(["status", "--porcelain"], foreign) == ""


@pytest.mark.parametrize("eligible", [True, False, None])
def test_composition_changefile_workers_require_caller_context_admission(
    repo: Path, monkeypatch: pytest.MonkeyPatch, eligible: bool | None,
) -> None:
    seed = _git(["rev-parse", "HEAD"], repo)
    _change(repo, "local-change")
    source = _commit(repo, "source change")
    loader = promotion._load_module
    reads = []

    def load(path, name):
        module = loader(path, name)
        if name == "promote_accumulate_bumps":
            reader = module.read_changefiles

            def read(*, parallel=False):
                reads.append(parallel)
                return reader(parallel=parallel)

            module.read_changefiles = read
        elif name == "promote_materialize_main":
            if eligible is None:
                del module._parallel_pointer_reads
            else:
                module._parallel_pointer_reads = lambda: eligible
        return module

    monkeypatch.setattr(promotion, "_load_module", load)
    composed = local_runtime_promotion.compose(repo, source_commit=source, main_seed=seed)
    assert reads == [eligible is True]
    assert ".changefiles/" not in _git(["ls-tree", "-r", "--name-only", composed.tree], repo)
    assert (repo / ".changefiles/local-change.json").is_file()
    assert _git(["status", "--porcelain"], repo) == ""


def test_local_composition_distinguishes_changed_source_without_version_bump(repo: Path) -> None:
    _runtime(repo)
    seed = _commit(repo, "initial runtime")
    _git(["branch", "-f", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    _change(repo, "local-change")
    source = _commit(repo, "local source")
    first = local_runtime_promotion.compose(repo, source_commit=source, main_seed=seed)
    module = repo / "plugins/demo-plugin/src/demo/__init__.py"
    module.write_text('VALUE = "changed"\n', encoding="utf-8")
    changed = _commit(repo, "change only source")
    second = local_runtime_promotion.compose(repo, source_commit=changed, main_seed=seed)

    assert second.source_commit != first.source_commit
    assert second.tree != first.tree
    assert _git(["show", f"{second.tree}:plugins/demo-plugin/src/demo/__init__.py"], repo) == (
        'VALUE = "changed"'
    )
    for result in (first, second):
        project = tomllib.loads(_git([
            "show", f"{result.tree}:plugins/demo-plugin/pyproject.toml",
        ], repo))
        assert project["project"]["version"] == "0.1.0-dev1"


def test_local_composition_seeds_official_main_not_stale_dev_versions(repo: Path) -> None:
    _runtime(repo)
    baseline = _commit(repo, "initial runtime")
    _git(["branch", "-f", "dev", baseline], repo)
    plugin = repo / "plugins/demo-plugin"
    manifest = json.loads((plugin / "plugin.json").read_text())
    manifest["version"] = "0.1.9-dev1"
    (plugin / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    accumulate_bumps._write_project_version(plugin / "pyproject.toml", "0.1.9-dev1")
    seed = _commit(repo, "official version advance")
    _git(["checkout", "-q", "dev"], repo)
    _change(repo, "local-change")
    source = _commit(repo, "local source with stale version")

    composed = local_runtime_promotion.compose(repo, source_commit=source, main_seed=seed)

    project = tomllib.loads(_git([
        "show", f"{composed.tree}:plugins/demo-plugin/pyproject.toml",
    ], repo))
    assert project["project"]["version"] == "0.1.9-dev1"
    assert json.loads(_git([
        "show", f"{composed.tree}:plugins/demo-plugin/plugin.json",
    ], repo))["version"] == "0.1.9-dev1"
    assert tomllib.loads((plugin / "pyproject.toml").read_text())["project"]["version"] == (
        "0.1.0-dev1"
    )
    assert _git(["rev-parse", "main"], repo) == seed


def test_main_version_inputs_batch_raw_pinned_blobs_without_export_filtering(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import promotion_source_archive as source_io

    standalone = repo / "fleet-manager"
    standalone.mkdir()
    text = (
        '[tool.other]\nversion="99.0.0"\n'
        '[project]\nname="fleet-manager"\nversion="2.0.0"\n'
    )
    (standalone / "pyproject.toml").write_text(text, encoding="utf-8")
    (repo / ".gitattributes").write_text("*.json export-ignore\n*.toml export-ignore\n")
    (repo / "unrelated.bin").write_bytes(b"\xff\x00")
    commit = _commit(repo, "raw version metadata")
    plugin_path = "plugins/demo-plugin/plugin.json"
    expected_plugin = source_io._git(repo, ["show", f"{commit}:{plugin_path}"]).decode("utf-8")
    expected_project = source_io._git(
        repo, ["show", f"{commit}:fleet-manager/pyproject.toml"],
    ).decode("utf-8")
    original = source_io._git
    calls = []

    def git(root, args, **kwargs):
        calls.append(args)
        return original(root, args, **kwargs)

    index = (repo / ".git/index").read_bytes()
    monkeypatch.setattr(source_io, "_git", git)
    observed = promotion._main_version_inputs(
        repo, commit, [plugin_path, "fleet-manager/pyproject.toml", "missing/pyproject.toml"],
    )
    assert observed[plugin_path] == expected_plugin
    assert observed["fleet-manager/pyproject.toml"] == expected_project
    assert accumulate_bumps._project_version_from_text(text) == "2.0.0"
    assert "missing/pyproject.toml" not in observed
    assert [args[:2] for args in calls] == [
        ["ls-tree", "-rz"], ["cat-file", "--batch-check"], ["cat-file", "--batch"],
    ]
    assert (repo / ".git/index").read_bytes() == index


def test_install_snapshot_uses_composed_main_versions_and_retains_git_association(
    repo: Path, tmp_path: Path, monkeypatch,
) -> None:
    _runtime(repo)
    baseline = _commit(repo, "initial runtime")
    _git(["branch", "-f", "dev", baseline], repo)
    plugin = repo / "plugins/demo-plugin"
    manifest = json.loads((plugin / "plugin.json").read_text())
    manifest["version"] = "0.1.9-dev1"
    (plugin / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    accumulate_bumps._write_project_version(plugin / "pyproject.toml", "0.1.9-dev1")
    seed = _commit(repo, "official version advance")
    _git(["checkout", "-q", "dev"], repo)
    _change(repo, "local-change")
    source = _commit(repo, "selected source with stale version")
    expected = local_runtime_promotion.compose(repo, source_commit=source, main_seed=seed)
    before_refs = _git(["show-ref"], repo)
    before_index = (repo / ".git/index").read_bytes()
    monkeypatch.setattr(
        local_install_source.materialization, "materialize",
        lambda *a, **kw: pytest.fail("the canonical composed tree must not be materialized twice"),
    )

    output = tmp_path / "install-inputs"
    record = local_install_source.compose(repo, source, seed, output)

    project = tomllib.loads((output / "source/plugins/demo-plugin/pyproject.toml").read_text())
    assert project["project"]["version"] == "0.1.9-dev1"
    assert record["source_commit"] == source
    assert record["source_tree"] == _git(["rev-parse", f"{source}^{{tree}}"], repo)
    assert record["main_head"] == seed
    assert record["composition"] == {"main_seed": seed, "tree": expected.tree}
    assert not (output / "source/.changefiles/local-change.json").exists()
    assert (repo / ".changefiles/local-change.json").is_file()
    assert _git(["show-ref"], repo) == before_refs
    assert (repo / ".git/index").read_bytes() == before_index


def test_install_snapshot_retains_no_output_when_canonical_composition_refuses(
    repo: Path, tmp_path: Path, monkeypatch,
) -> None:
    source = _git(["rev-parse", "HEAD"], repo)
    output = tmp_path / "refused"

    def refuse(*args, **kwargs):
        raise promotion.PromotionError("selected Main seed cannot be composed")

    monkeypatch.setattr(local_runtime_promotion, "compose", refuse)
    with pytest.raises(promotion.PromotionError, match="Main seed cannot be composed"):
        local_install_source.compose(repo, source, source, output)
    assert not output.exists()


def test_selected_application_export_keeps_full_tree_roster_and_dependency_bytes(
    repo: Path, tmp_path: Path,
) -> None:
    from promotion_python_artifacts import candidate_projects
    from versioned_runtime_source import assembly_hash

    _runtime(repo)
    selected = repo / "plugins" / "demo-plugin"
    with (selected / "pyproject.toml").open("a", encoding="utf-8") as stream:
        stream.write(
            'dependencies = ["helper-lib>=1"]\n'
            '[tool.uv.sources]\nhelper-lib = {path="../../libs/helper-lib", editable=true}\n'
        )
    library = repo / "libs" / "helper-lib"
    (library / "src" / "helper_lib").mkdir(parents=True)
    (library / "src" / "helper_lib" / "__init__.py").write_text("VALUE = 1\n")
    (library / "pyproject.toml").write_text('[project]\nname="helper-lib"\nversion="1.0.0"\n')
    other = repo / "plugins" / "unselected"
    other.mkdir()
    (other / "pyproject.toml").write_text('[project]\nname="unselected"\nversion="2.0.0"\n')
    (other / "unrelated.py").write_text("NOT_SELECTED = True\n")
    (repo / "unrelated.txt").write_text("not an application input\n")
    seed = _commit(repo, "application export inputs")
    _change(repo, "local-change")
    source = _commit(repo, "unallocated local change")
    before_index = (repo / ".git/index").read_bytes()
    before_refs = _git(["show-ref"], repo)
    full, scoped = tmp_path / "full", tmp_path / "scoped"
    expected = local_install_source.compose(repo, source, seed, full)
    observed = local_install_source.compose(repo, source, seed, scoped, projects=("demo-plugin",))
    assert observed == expected
    assert candidate_projects(scoped / "source") == candidate_projects(full / "source")
    assert assembly_hash(scoped / "source/plugins/demo-plugin") == assembly_hash(
        full / "source/plugins/demo-plugin",
    )
    assert (scoped / "source/plugins/demo-plugin/libs/helper-lib/src/helper_lib/__init__.py").read_text() == (
        "VALUE = 1\n"
    )
    assert (scoped / "source/plugins/unselected/pyproject.toml").read_bytes() == (
        full / "source/plugins/unselected/pyproject.toml"
    ).read_bytes()
    assert not (scoped / "source/plugins/unselected/unrelated.py").exists()
    assert not (scoped / "source/unrelated.txt").exists()
    assert _git(["show-ref"], repo) == before_refs
    assert (repo / ".git/index").read_bytes() == before_index
    assert (repo / ".changefiles/local-change.json").is_file()
    refused = tmp_path / "refused"
    with pytest.raises(ValueError, match="declared Python projects"):
        local_install_source.compose(repo, source, seed, refused, projects=("not-declared",))
    assert not refused.exists()
    assert len(_git(["worktree", "list"], repo).splitlines()) == 1


@pytest.mark.parametrize("field", ["source_commit", "main_seed"])
def test_local_composition_requires_exact_commits(repo: Path, field: str) -> None:
    commit = _git(["rev-parse", "HEAD"], repo)
    arguments = dict(source_commit=commit, main_seed=commit)
    arguments[field] = "main"
    with pytest.raises(promotion.PromotionError, match="requires an exact"):
        local_runtime_promotion.compose(repo, **arguments)
    assert len(_git(["worktree", "list"], repo).splitlines()) == 1


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


def test_native_index_five_project_release_closure_uses_library_changefile(repo):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    from packaging.version import Version

    _git(["config", "core.longpaths", "true"], repo)
    source = Path(__file__).resolve().parents[1]
    projects = (
        "plugins/native-index", "services/index", "libs/agent-procutil",
        "libs/zdd", "libs/dropin-registry", "libs/configuration-layers",
        "libs/update-driver", "libs/single-instance-lease",
    )
    for relative in projects:
        target = repo / relative
        target.mkdir(parents=True, exist_ok=True)
        original = "plugins/agent-index" if relative == "plugins/native-index" else relative
        shutil.copy(source / original / "pyproject.toml", target / "pyproject.toml")
        if relative.startswith("libs/"):
            shutil.copytree(source / original / "src", target / "src", ignore=shutil.ignore_patterns(
                "__pycache__", "*.pyc",
            ))
    plugin = repo / "plugins/native-index"
    shutil.copytree(
        source / "plugins/agent-index/libs/dropin-registry",
        plugin / "libs/dropin-registry",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (plugin / "plugin.json").write_text(json.dumps({
        "name": "native-index", "version": "0.10.76-dev1",
    }), encoding="utf-8")
    accumulate_bumps._write_project_version(plugin / "pyproject.toml", "0.10.76-dev1")
    accumulate_bumps._write_project_version(
        repo / "libs/agent-procutil/pyproject.toml", "0.1.0-dev7",
    )
    service = tomllib.loads((repo / "services/index/pyproject.toml").read_text())["project"]
    required = next(
        Requirement(value) for value in service["dependencies"]
        if Requirement(value).name == "agent-procutil"
    )
    assert not required.specifier.contains(Version("0.1.0.dev7"), prereleases=True)
    for relative in (
        "plugins/native-index/src/agent_index/http_auth.py",
    ):
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(source / relative.replace("native-index", "agent-index"), target)
    _commit(repo, "published native index projects with missing library bump")
    _git(["branch", "-f", "dev"], repo)
    _git(["checkout", "-q", "dev"], repo)
    changes = repo / ".changefiles"
    changes.mkdir()
    (changes / "library.json").write_text(json.dumps({
        "comment": "release shared process helpers",
        "changes": [{"plugin": "libs/agent-procutil", "type": "dev"}],
    }), encoding="utf-8")
    _commit(repo, "request canonical library release")
    result = promotion.promote(repo=repo, dev_ref="dev", main_ref="main")
    candidate = promotion.add_scratch_worktree(result["commit"], repo=repo)
    try:
        metadata = [
            tomllib.loads((candidate / relative / "pyproject.toml").read_text())["project"]
            for relative in projects
        ]
        versions = {canonicalize_name(p["name"]): Version(p["version"]) for p in metadata}
        assert versions["agent-procutil"] == Version("0.1.0.dev8")
        checked = []
        for project in metadata:
            for dependency in project.get("dependencies", []):
                requirement = Requirement(dependency)
                name = canonicalize_name(requirement.name)
                if name in versions:
                    assert requirement.specifier.contains(versions[name], prereleases=True)
                    checked.append(name)
        assert set(checked) == {
            "agent-index", "agent-procutil", "agent-zdd", "agent-dropin-registry",
            "agent-configuration-layers", "agent-single-instance-lease",
        }
        canonical = candidate / "libs/agent-procutil"
        embedded = candidate / "plugins/native-index/libs/agent-procutil"
        canonical_project = tomllib.loads((canonical / "pyproject.toml").read_text())
        embedded_project = tomllib.loads((embedded / "pyproject.toml").read_text())
        assert canonical_project["tool"]["uv"]["sources"]["agent-zdd"].pop("editable") is True
        assert embedded_project == canonical_project
        assert (embedded / "src/agent_procutil/owned_process.py").read_bytes() == (
            canonical / "src/agent_procutil/owned_process.py"
        ).read_bytes()
        assert (candidate / "plugins/native-index/src/agent_index/http_auth.py").read_bytes() == (
            source / "plugins/agent-index/src/agent_index/http_auth.py"
        ).read_bytes()
    finally:
        promotion.remove_scratch_worktree(candidate, repo=repo)
    _git(["update-ref", "refs/heads/main", result["commit"]], repo)
    (repo / "README.md").write_text("unrelated later source\n", encoding="utf-8")
    _commit(repo, "later source without another library changefile")
    later = promotion.promote(repo=repo, dev_ref="dev", main_ref="main")
    assert later["bumps"] == {}
    assert tomllib.loads(_git([
        "show", f"{later['commit']}:libs/agent-procutil/pyproject.toml",
    ], repo))["project"]["version"] == "0.1.0-dev8"
