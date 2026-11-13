"""Release-helper decisions using fixture bytes and actor-boundary substitutes."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import artifact_builder_cli as cli
import changefile
import governed_feed_trust as trust
import installer_engine_ref as engine
import launch_wrapper_assets_ref as assets
import materialize_installer_engine as installers
import materialize_launch_wrapper_assets as wrappers
import materialize_main as materializer
import nested_uv_editable_ref as nested
import promote_release as promotion
import promotion_source_alias as aliases
import public_toolchain_source as public
import reuse_promotion_artifacts as reuse
import runtime_profile_release as profiles
import seasoned_public_feed as seasoned
import standalone_consumers as standalone
import tag_promotion as tags
import uv_editable_ref as editable
from runtime_profile import ProfileError, SCHEMA as PROFILE_SCHEMA

DEV, MAIN, TREE = "d" * 40, "a" * 40, "c" * 40


@pytest.fixture(autouse=True)
def _no_process_actors(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("release decision unit reached an unmocked process actor")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize("case", ["same", "changed", "wrong-source", "bad-json", "git-error"])
def test_source_tag_reuse_requires_matching_provenance_and_content(tmp_path, monkeypatch, case):
    calls = []

    def git(argv, **kwargs):
        calls.append(argv)
        assert kwargs["cwd"] == tmp_path
        if argv[0] == "show":
            state = {"last_promotion": {
                "release_id": DEV, "dev_head": MAIN if case == "wrong-source" else DEV,
            }}
            return subprocess.CompletedProcess(
                argv, 0, "{" if case == "bad-json" else json.dumps(state), "",
            )
        assert argv == [
            "diff", "--quiet", "prior", "landed", "--", ".",
            f":(exclude){tags.PIPELINE_STATE_PATH}",
            f":(exclude){tags.COVERAGE_BASELINES_DIR}",
        ]
        return subprocess.CompletedProcess(
            argv, {"changed": 1, "git-error": 128}.get(case, 0), "", "comparison failed",
        )

    monkeypatch.setattr(tags, "_git", git)
    if case in {"bad-json", "git-error"}:
        with pytest.raises(tags.TagError, match="unreadable|cannot compare"):
            tags._same_source_release("prior", "landed", DEV, repo=tmp_path)
    else:
        assert tags._same_source_release("prior", "landed", DEV, repo=tmp_path) == (case == "same")
    assert len(calls) == (1 if case in {"wrong-source", "bad-json"} else 2)


@pytest.mark.parametrize("case", ["valid", "wrong-tree", "wrong-release-id", "unrelated-history"])
def test_prior_alias_requires_frozen_source_and_main_history(tmp_path, monkeypatch, case):
    previous = {
        "dev_head": DEV, "release_id": MAIN if case == "wrong-release-id" else DEV,
        "tag": f"promote-dev-{DEV}", "main_before": MAIN,
    }
    monkeypatch.setattr(aliases, "_git", lambda *a: (MAIN + "\n").encode())
    monkeypatch.setattr(aliases, "_tree", lambda *a: {aliases._STATE: "entry"})
    monkeypatch.setattr(aliases, "_blob", lambda *a, **kw: json.dumps({
        "schema": "copilot-extensions.release-pipeline-state", "version": 1,
        "last_promotion": previous,
    }).encode())
    monkeypatch.setattr(promotion, "_is_ancestor", lambda *a, **kw: case != "unrelated-history")
    monkeypatch.setattr(promotion, "_git", lambda *a, **kw: TREE)
    projections = []

    def project_tree(argv, **kwargs):
        assert argv in [
            ["git", "read-tree", TREE],
            ["git", "rm", "--cached", "-f", "-q", "--ignore-unmatch", promotion.PIPELINE_STATE_PATH],
            ["git", "rm", "--cached", "-f", "-q", "-r", "--ignore-unmatch",
             promotion.COVERAGE_BASELINES_DIR],
            ["git", "write-tree"],
        ]
        assert kwargs["cwd"] == str(tmp_path)
        assert Path(kwargs["env"]["GIT_INDEX_FILE"]).name == "index"
        projections.append(argv)
        return subprocess.CompletedProcess(argv, 0, TREE + "\n", "")

    monkeypatch.setattr(subprocess, "run", project_tree)
    cache = {("example/plugins", DEV): {
        "dev_head": DEV, "content_tree": MAIN if case == "wrong-tree" else TREE,
    }}
    kwargs = dict(
        repository="example/plugins", tag_commit="prior", current_commit="current",
        current_index={"dev_head": DEV}, repo=tmp_path, cache=cache,
    )
    if case == "valid":
        aliases.validate_prior_source_alias(**kwargs)
        assert cache[("example/plugins", DEV)]["content_tree"] == TREE
    else:
        with pytest.raises(reuse.PromotionArtifactError, match="history|composition"):
            aliases.validate_prior_source_alias(**kwargs)
    assert len(projections) == (4 if case in {"valid", "wrong-tree"} else 0)


@pytest.mark.parametrize("case", ["valid", "digest", "oversize"])
def test_frozen_artifact_download_checks_bytes_before_writing(tmp_path, monkeypatch, case):
    content = b"frozen source artifact"
    calls = []

    def gh(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, content, b"")

    monkeypatch.setattr(reuse.publisher, "_gh", gh)
    asset = {
        "id": 7, "size": len(content),
        "digest": "sha256:" + hashlib.sha256(content if case != "digest" else b"other").hexdigest(),
    }
    kwargs = dict(
        repository="example/plugins", tag="python-artifacts-fixture",
        name="frozen.zip", asset=asset, limit=1 if case == "oversize" else 100,
    )
    if case == "valid":
        reuse._download(tmp_path, **kwargs)
        assert (tmp_path / "frozen.zip").read_bytes() == content
        assert calls == [([
            "api", "repos/example/plugins/releases/assets/7",
            "-H", "Accept: application/octet-stream",
        ], {"verification": True, "binary": True})]
    else:
        with pytest.raises(reuse.PromotionArtifactError, match="digest mismatch|size limit"):
            reuse._download(tmp_path, **kwargs)
        assert not (tmp_path / "frozen.zip").exists()
        assert len(calls) == (0 if case == "oversize" else 1)


@pytest.mark.parametrize("case", ["absent", "bad-repository", "bad-dev"])
def test_only_genuine_source_release_absence_allows_build(tmp_path, monkeypatch, case):
    reads = []
    monkeypatch.setattr(
        reuse.discovery, "read_release",
        lambda repository, tag, **options: reads.append((repository, tag)),
    )
    kwargs = dict(
        repository="../plugins" if case == "bad-repository" else "example/plugins",
        dev_head="short" if case == "bad-dev" else DEV,
        main_before=MAIN, content_tree=TREE, candidate_root=tmp_path,
        repo=tmp_path, source_policy="governed", min_age_days=None,
    )
    if case == "absent":
        assert reuse.reuse_artifacts(tmp_path, **kwargs) is None
        assert reads == [("example/plugins", f"python-artifacts-{DEV}")]
    else:
        with pytest.raises(reuse.PromotionArtifactError, match="reuse identity"):
            reuse.reuse_artifacts(tmp_path, **kwargs)
        assert reads == []


@pytest.mark.parametrize("case", ["valid", "wrong-tree", "foreign-main", "extra-bundle", "misbound-tag"])
def test_frozen_source_index_requires_exact_composition_and_original_main_admission(
    tmp_path, monkeypatch, case,
):
    filename = "example-1.0-" + "b" * 64 + ".zip"
    index = {
        "schema": reuse.SCHEMA, "schema_version": 2, "dev_head": DEV,
        "content_tree": MAIN if case == "wrong-tree" else TREE,
        "admission": {"signer_digest": MAIN, "source_digest": MAIN, "source_ref": "refs/heads/main"},
        "artifacts": [{"filename": filename, "sha256": "b" * 64}],
    }
    content = json.dumps(index).encode()
    inventory = [
        {"id": 1, "name": reuse.INDEX_NAME, "state": "uploaded", "size": len(content),
         "digest": "sha256:" + hashlib.sha256(content).hexdigest()},
        {"id": 2, "name": filename, "state": "uploaded", "size": 100, "digest": "sha256:" + "b" * 64},
    ]
    if case == "extra-bundle":
        inventory.append({
            "id": 3, "name": "other-1.0-" + "e" * 64 + ".zip",
            "state": "uploaded", "size": 100, "digest": "sha256:" + "e" * 64,
        })
    monkeypatch.setattr(reuse.discovery, "read_release", lambda *a, **kw: {"id": 10})
    monkeypatch.setattr(
        reuse.discovery, "tag_commit", lambda *a, **kw: MAIN if case == "misbound-tag" else DEV,
    )
    monkeypatch.setattr(promotion, "_is_ancestor", lambda *a, **kw: case != "foreign-main")
    verified = []

    def gh(argv, **kwargs):
        if argv[:4] == ["api", "--paginate", "--slurp", "repos/example/plugins/releases/10/assets?per_page=100"]:
            assert kwargs == {"verification": True}
            return subprocess.CompletedProcess(argv, 0, json.dumps([inventory]), "")
        if argv[:2] == ["api", "repos/example/plugins/releases/assets/1"]:
            assert kwargs == {"verification": True, "binary": True}
            return subprocess.CompletedProcess(argv, 0, content, b"")
        assert argv == [
            "attestation", "verify", str(tmp_path / reuse.INDEX_NAME), "--repo", "example/plugins",
            "--signer-workflow", "example/plugins/.github/workflows/validate-and-promote.yml",
            "--signer-digest", MAIN, "--source-digest", MAIN,
            "--source-ref", "refs/heads/main", "--deny-self-hosted-runners",
        ]
        assert kwargs == {"verification": True}
        assert Path(argv[2]).read_bytes() == content
        verified.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(reuse.publisher, "_gh", gh)
    kwargs = dict(
        repository="example/plugins", dev_head=DEV, main_before=MAIN,
        content_tree=TREE, repo=tmp_path,
    )
    if case == "valid":
        admitted, admitted_assets = reuse.read_admitted_source_index(tmp_path, **kwargs)
        assert admitted == index
        assert set(admitted_assets) == {reuse.INDEX_NAME, filename}
        assert len(verified) == 1
        assert not (tmp_path / filename).exists()
    else:
        with pytest.raises(
            reuse.PromotionArtifactError,
            match="composition|trusted Main history|unexpected files|tag is misbound",
        ):
            reuse.read_admitted_source_index(tmp_path, **kwargs)
        assert len(verified) == (1 if case == "extra-bundle" else 0)


@pytest.mark.parametrize("case", ["plugin", "standalone", "unavailable", "age-with-governed"])
def test_builder_cli_selects_builder_and_refuses_invalid_policy(tmp_path, monkeypatch, capsys, case):
    argv = ["builder", "fixture", "--out-dir", str(tmp_path)]
    if case in {"standalone", "unavailable"}:
        argv.append("--standalone")
    if case == "age-with-governed":
        argv += ["--toolchain-min-age-days", "7"]
    monkeypatch.setattr(sys, "argv", argv)
    calls = []

    def builder(name, **kwargs):
        calls.append((name, kwargs))
        return {"kind": "plugin"}

    def standalone_builder(name, **kwargs):
        calls.append((name, kwargs))
        return {"kind": "standalone"}

    if case in {"unavailable", "age-with-governed"}:
        with pytest.raises(SystemExit) as error:
            cli.main(builder, "fixture")
        assert error.value.code == 2
        assert calls == []
        assert ("unavailable" if case == "unavailable" else "requires") in capsys.readouterr().err
    else:
        assert cli.main(builder, "fixture", standalone_builder=standalone_builder) == 0
        assert json.loads(capsys.readouterr().out) == {"kind": case}
        assert calls == [("fixture", {"out_dir": tmp_path, "python": None, "toolchain": None})]


@pytest.mark.parametrize("case", ["valid", "missing-editable", "wrong-canonical"])
def test_uv_materialization_copies_nested_closure_or_refuses_before_copy(tmp_path, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    source, target = canonical / "plugins/example", destination / "plugins/example"
    raw = "../../plugins/core" if case == "wrong-canonical" else "../../libs/core"
    manifest = (
        '[project]\nname="example"\nversion="1.0"\n[tool.uv.sources]\n'
        f'core = {{ path = "{raw}"'
        + ("" if case == "missing-editable" else ", editable = true") + " }\n"
    )
    _write(source / "pyproject.toml", manifest)
    _write(target / "pyproject.toml", manifest)
    _write(canonical / "libs/core/pyproject.toml",
           '[project]\nname="core"\nversion="1.0"\n[tool.uv.sources]\n'
           'leaf = { editable = true, path = "../leaf" }\n')
    _write(canonical / "libs/core/src/core/__init__.py", "VALUE = 1\n")
    _write(canonical / "libs/leaf/pyproject.toml", '[project]\nname="leaf"\nversion="1.0"\n')
    _write(canonical / "libs/leaf/src/leaf/__init__.py", "VALUE = 2\n")
    log = materializer.materialize_uv_editable_ref_into(
        source_consumer_dir=source, dest_consumer_dir=target,
        canonical_root=canonical, dest_root=destination,
    )
    if case == "valid":
        assert len(log) == 2 and all(line.startswith("OK") for line in log)
        assert editable.find_uv_editable_refs(target) == []
        assert 'core = { path = "libs/core" }' in (target / "pyproject.toml").read_text()
        assert 'leaf = { path = "../leaf" }' in (target / "libs/core/pyproject.toml").read_text()
        assert (target / "libs/leaf/src/leaf/__init__.py").read_text() == "VALUE = 2\n"
    else:
        assert len(log) == 1 and log[0].startswith("SKIP")
        assert ("missing editable" if case == "missing-editable" else "not canonical_root") in log[0]
        assert not (target / "libs").exists()
        assert (target / "pyproject.toml").read_text() == manifest


@pytest.mark.parametrize("case", ["matching", "conflicting"])
def test_nested_existing_library_requires_complete_canonical_match(tmp_path, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    parent = destination / "plugins/example/libs/core"
    manifest = '[tool.uv.sources]\nleaf = { path = "../leaf", editable = true }\n'
    _write(parent / "pyproject.toml", manifest)
    for root in (canonical / "libs/leaf", parent.parent / "leaf"):
        _write(root / "pyproject.toml", '[project]\nname="leaf"\nversion="1.0"\n')
        _write(root / "src/leaf.py", "VALUE = 1\n")
    if case == "conflicting":
        _write(parent.parent / "leaf/src/leaf.py", "VALUE = 9\n")
    log, copied = nested.materialize_nested_uv_editable_refs(
        parent, canonical_root=canonical, dest_root=destination,
    )
    assert len(log) == 1
    if case == "matching":
        assert copied == {"leaf"} and log[0].startswith("OK")
        assert 'leaf = { path = "../leaf" }' in (parent / "pyproject.toml").read_text()
    else:
        assert copied == set() and "does not match canonical" in log[0]
        assert (parent / "pyproject.toml").read_text() == manifest
        assert (parent.parent / "leaf/src/leaf.py").read_text() == "VALUE = 9\n"


@pytest.mark.parametrize("case", ["fresh", "identical", "conflicting", "unsafe-name"])
def test_packaged_assets_materialize_only_safe_nonconflicting_bytes(tmp_path, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    source, target = canonical / "plugins/example", destination / "plugins/example"
    target.mkdir(parents=True)
    declaration = {
        "schema": "copilot-extensions.launch-wrapper-assets", "version": 1,
        "canonicalDir": "wrapper/bin", "files": ["../escape" if case == "unsafe-name" else "launch.sh"],
    }
    _write(source / assets.MANIFEST_NAME, json.dumps(declaration))
    _write(canonical / "wrapper/bin/launch.sh", "#!/bin/sh\nexit 0\n")
    output = target / "bin/launch.sh"
    if case in {"identical", "conflicting"}:
        _write(output, "foreign\n" if case == "conflicting" else "#!/bin/sh\nexit 0\n")
    log = wrappers.materialize_into(
        source_consumer_dir=source, dest_consumer_dir=target,
        canonical_root=canonical, dest_root=destination,
        manifest_name=assets.MANIFEST_NAME, reuse_existing=True,
    )
    if case == "fresh":
        assert len(log) == 1 and log[0].startswith("OK")
        assert output.read_bytes() == (canonical / "wrapper/bin/launch.sh").read_bytes()
    elif case == "identical":
        assert log == [] and output.read_text() == "#!/bin/sh\nexit 0\n"
    else:
        assert len(log) == 1 and log[0].startswith("SKIP")
        assert ("basename-only" if case == "unsafe-name" else "already exists") in log[0]
        if case == "conflicting":
            assert output.read_text() == "foreign\n"
        else:
            assert not output.exists()


def _nested_packaged_assets(root: Path, files: list[str]) -> Path:
    consumer = root / "worktree-manager"
    _write(consumer / assets.PACKAGED_MANIFEST_NAME, json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": 2,
        "canonicalDir": ".", "payloadDir": "scripts/provider", "files": files,
    }))
    return consumer


def test_nested_packaged_assets_preserve_explicit_layout(tmp_path):
    root = tmp_path / "source"
    files = ["tools/prepare.py", "libs/example/pyproject.toml", "tools/requirements.txt"]
    consumer = _nested_packaged_assets(root, files)
    for name in files:
        _write(root / name, name)
    _write(root / "tools/unrelated.py", "not declared")
    dest = tmp_path / "release" / "worktree-manager"
    dest.mkdir(parents=True)
    report = wrappers.materialize_into(
        source_consumer_dir=consumer, dest_consumer_dir=dest, canonical_root=root,
    )
    assert len(report) == len(files) and all(line.startswith("OK") for line in report)
    packaged = dest / "scripts" / "provider"
    assert sorted(path.relative_to(packaged).as_posix()
                  for path in packaged.rglob("*") if path.is_file()) == sorted(files)
    assert all((packaged / name).read_bytes() == (root / name).read_bytes() for name in files)
    manifest = assets.load_manifest(consumer, manifest_name=assets.PACKAGED_MANIFEST_NAME)
    assert manifest.source_relative(files[0]) == files[0]


@pytest.mark.parametrize("files", [
    ["../outside.py"], ["tools/../outside.py"], ["C:/outside.py"],
    ["/outside.py"], ["tools\\outside.py"], ["tools/*.py"],
    ["tools/file.py", "tools/FILE.py"], ["tools", "tools/file.py"],
    ["tools/file.py:stream"], ["tools/name."], ["tools/name "], ["tools/CON.py"],
], ids=[
    "traversal", "nested-traversal", "drive", "absolute", "backslash", "glob",
    "case-alias", "file-directory", "stream", "trailing-dot", "trailing-space", "device-name",
])
def test_nested_packaged_assets_reject_unsafe_or_aliased_members(tmp_path, files):
    consumer = _nested_packaged_assets(tmp_path, files)
    with pytest.raises(ValueError, match="files"):
        assets.load_manifest(consumer, manifest_name=assets.PACKAGED_MANIFEST_NAME)


@pytest.mark.parametrize("failure", ["missing", "occupied", "parent-file", "source-link", "dest-link"])
def test_nested_packaged_assets_preflight_all_members(tmp_path, failure, monkeypatch):
    root = tmp_path / "source"
    files = ["tools/first.py", "libs/example/value.py"]
    consumer = _nested_packaged_assets(root, files)
    _write(root / files[0], "first")
    dest = tmp_path / "release" / "worktree-manager"
    packaged = dest / "scripts" / "provider"
    packaged.mkdir(parents=True)
    source = root / files[1]
    target = packaged / files[1]
    if failure != "missing":
        _write(root / files[1], "second")
    if failure == "occupied":
        _write(target, "retained")
    elif failure == "parent-file":
        _write(packaged / "libs", "retained")
    elif failure == "source-link":
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == source.parent)
    elif failure == "dest-link":
        monkeypatch.setattr(Path, "is_symlink", lambda path: path == packaged / "libs")
    report = wrappers.materialize_into(
        source_consumer_dir=consumer, dest_consumer_dir=dest, canonical_root=root,
    )
    assert len(report) == 1 and report[0].startswith("SKIP")
    assert not (packaged / files[0]).exists()


@pytest.mark.parametrize("version,canonical,name", [
    (1, "tools", "prepare.py"),
    (2, ".", "libs/example/pyproject.toml"),
])
def test_standalone_packaged_sources_charge_the_declared_consumer(
    monkeypatch, tmp_path, version, canonical, name,
):
    spec = importlib.util.spec_from_file_location("asset_version_guard", ROOT / "tools/check-version-bump.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = tmp_path / "source"
    (root / "plugins").mkdir(parents=True)
    _write(root / "worktree-manager/packaged-assets.json", json.dumps({
        "schema": "copilot-extensions.packaged-assets", "version": version,
        "canonicalDir": canonical, "payloadDir": "scripts/provider", "files": [name],
    }))
    monkeypatch.setattr(module, "REPO", root)
    monkeypatch.setattr(module, "PLUGINS_DIR", root / "plugins")
    source = (Path(canonical) / name).as_posix()
    assert module._plugins_needing_bump([source], {}) == {
        "worktree-manager": {f"packaged source {source}"},
    }
    assert module._plugins_needing_bump(["tools/unrelated.py"], {}) == {}


def test_application_resource_projection_retains_companions_and_metadata_not_directories(tmp_path):
    from plugin_runtime_resources import application_resources

    spec = importlib.util.spec_from_file_location(
        "application_resource_builder", ROOT / "libs/versioned-runtime/standalone_build.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prefix = "scripts/runtime-source-tools/fixture/"
    expected = {
        "_runtime_assets/" + prefix + name
        for name in ("native.cs", "pyproject.toml", "requirements.txt", "uv.lock")
    }
    for name in ("native.cs", "pyproject.toml", "requirements.txt", "uv.lock"):
        _write(tmp_path / prefix / name, "fixture")
    (tmp_path / prefix / "not-a-file.cs").mkdir()
    assert set(module.application_resources(tmp_path, "worktree_manager")) == expected
    assert set(application_resources(tmp_path, "worktree_manager")) == {
        "worktree_manager/" + name for name in expected
    }


def test_packaged_manager_provider_loads_its_declared_canonical_owners(tmp_path, monkeypatch):
    source = ROOT / "worktree-manager"
    manifests = assets.load_manifest_groups(source, manifest_name=assets.PACKAGED_MANIFEST_NAME)
    destination = tmp_path / "worktree-manager"
    destination.mkdir()
    report = wrappers.materialize_into(
        source_consumer_dir=source, dest_consumer_dir=destination, canonical_root=ROOT,
        manifest_name=assets.PACKAGED_MANIFEST_NAME,
    )
    assert report and all(line.startswith("OK") for line in report), report
    expected = {
        (Path(manifest.payload_dir_raw) / name).as_posix()
        for manifest in manifests for name in manifest.files
    }
    assert {
        path.relative_to(destination).as_posix() for path in destination.rglob("*") if path.is_file()
    } == expected
    from plugin_runtime_resources import application_resources

    assert set(application_resources(destination, "worktree_manager")) == {
        "worktree_manager/_runtime_assets/" + name for name in expected
    }

    bundles = {
        manifest.payload_dir(plugin_dir=destination).parent
        for manifest in manifests if manifest.canonical_dir_raw == "plugins"
    }
    assert len(bundles) == 1
    bundle = bundles.pop()
    declared = {
        ROOT / manifest.source_relative(name) for manifest in manifests for name in manifest.files
    }
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if filename and Path(filename) in declared:
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.delitem(sys.modules, "_local_install_source_provenance", raising=False)
    inherited = [
        path for path in sys.path
        if not Path(path or ".").resolve().is_relative_to(ROOT)
    ]
    monkeypatch.setattr(sys, "path", [str(bundle / "tools"), *inherited])
    monkeypatch.chdir(tmp_path)
    try:
        modules = {
            name: importlib.import_module(name) for name in (
                "local_install_source", "local_install_profile", "local_runtime_promotion",
                "build_python_artifacts", "standalone_python_artifacts",
            )
        }
        assert all(Path(module.__file__).is_relative_to(bundle) for module in modules.values())
        assert modules["local_install_source"].repository_source(
            tmp_path, {"source": "github", "repo": "Example/Suite"}, deadline=1,
        ) == {"kind": "github", "canonical": "github:example/suite", "ref": ""}
        projection = bundle / (
            "plugins/customizing-copilot/skills/reviewing-customizations/scripts"
        )
        monkeypatch.syspath_prepend(str(projection))
        loaded = importlib.import_module("instruction_projections")
        assert Path(loaded.__file__).is_relative_to(bundle)
    finally:
        for name, module in list(sys.modules.items()):
            filename = getattr(module, "__file__", None)
            if filename and Path(filename).is_relative_to(bundle):
                sys.modules.pop(name, None)


@pytest.mark.parametrize("ext", ["ps1", "sh"])
@pytest.mark.parametrize("case", ["valid", "duplicate"])
def test_installer_reference_materialization_preserves_comments_and_refuses_ambiguity(tmp_path, ext, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    source, target = canonical / "plugins/example", destination / "plugins/example"
    text = "  " + engine.canonical_line(ext) + " # retain\n"
    if case == "duplicate":
        text += engine.canonical_line(ext) + "\n"
    _write(source / f"scripts/install.{ext}", text)
    _write(target / f"scripts/install.{ext}", text)
    _write(canonical / f"libs/installer-engine/installer-engine.{ext}", "# fixture engine\n")
    log = installers.materialize_ref_into(
        source_consumer_dir=source, dest_consumer_dir=target,
        canonical_root=canonical, dest_root=destination,
    )
    output = target / f"scripts/installer-engine.{ext}"
    assert len(log) == 1
    if case == "valid":
        assert log[0].startswith("OK") and output.read_text() == "# fixture engine\n"
        script = target / f"scripts/install.{ext}"
        assert script.read_text() == "  " + engine.local_line(ext) + " # retain\n"
        assert engine.is_local_ref(engine.find_engine_ref(script, ext), target)
    else:
        assert "expected exactly one" in log[0] and not output.exists()
        assert (target / f"scripts/install.{ext}").read_text() == text


@pytest.mark.parametrize("ext", ["ps1", "sh"])
@pytest.mark.parametrize("case", ["corruption", "source-drift"])
def test_native_installer_copy_is_verified_before_entrypoint_rewrite(tmp_path, monkeypatch, ext, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    source, target = canonical / "plugins/example", destination / "plugins/example"
    text = engine.canonical_line(ext) + "\n"
    _write(source / f"scripts/install.{ext}", text)
    script = target / f"scripts/install.{ext}"
    _write(script, text)
    canonical_engine = canonical / f"libs/installer-engine/installer-engine.{ext}"
    _write(canonical_engine, "# admitted engine\n")
    output = target / f"scripts/installer-engine.{ext}"
    copyfile = installers.shutil.copyfile
    copied = []

    def copy_then_fault(src, dst, *args, **kwargs):
        result = copyfile(src, dst, *args, **kwargs)
        if Path(src) == canonical_engine and Path(dst) == output:
            copied.append((src, dst))
            (output if case == "corruption" else canonical_engine).write_bytes(b"# changed engine\n")
        return result

    monkeypatch.setattr(installers.shutil, "copyfile", copy_then_fault)
    log = installers.materialize_ref_into(
        source_consumer_dir=source, dest_consumer_dir=target,
        canonical_root=canonical, dest_root=destination,
    )
    assert copied == [(canonical_engine, output)]
    assert len(log) == 1 and log[0].startswith("SKIP"), log
    assert ("postwrite bytes" if case == "corruption" else "canonical source changed") in log[0]
    assert script.read_text() == text


@pytest.mark.parametrize("case", ["stale", "absent", "foreign", "absent-bad-digest"])
def test_declared_human_initializer_refresh_uses_source_preimage(tmp_path, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    source, target = canonical / "plugins/agent-mcp", destination / "plugins/agent-mcp"
    script = source / "scripts/init.ps1"
    text = "$ErrorActionPreference = 'Stop'\n\n" + engine.HUMAN_PROLOGUE + "\n"
    _write(script, text)
    _write(script.parent / engine.HUMAN_HELPER, "# admitted prior initializer\n")
    declaration = engine.human_output_json(script)
    if case == "absent-bad-digest":
        data = json.loads(declaration)
        data["initializer_sha256"] = "invalid"
        declaration = json.dumps(data, indent=2) + "\n"
    _write(script.with_suffix(".human-output.json"), declaration)
    if case.startswith("absent"):
        (script.parent / engine.HUMAN_HELPER).unlink()
    _write(canonical / "libs/installer-engine" / engine.HUMAN_HELPER, "# current initializer\n")
    _write(target / "scripts/init.ps1", text)
    _write(target / "scripts/init.human-output.json", declaration)
    if not case.startswith("absent"):
        _write(target / "scripts" / engine.HUMAN_HELPER,
               "# foreign initializer\n" if case == "foreign" else "# admitted prior initializer\n")
    before = {path.relative_to(canonical): path.read_bytes()
              for path in canonical.rglob("*") if path.is_file()}
    kwargs = dict(
        source_consumer_dir=source, dest_consumer_dir=target,
        canonical_root=canonical, dest_root=destination,
    )
    if case in {"foreign", "absent-bad-digest"}:
        with pytest.raises(ValueError, match="conflicting native human-output assets"):
            installers.materialize_ref_into(**kwargs)
        assert (target / "scripts/init.human-output.json").read_text() == declaration
    else:
        log = installers.materialize_ref_into(**kwargs)
        assert log and all(line.startswith("OK") for line in log), log
        emitted = target / "scripts/init.ps1"
        assert emitted.with_suffix(".human-output.json").read_text() == engine.human_output_json(emitted)
        assert (emitted.parent / engine.HUMAN_HELPER).read_bytes() == (
            canonical / "libs/installer-engine" / engine.HUMAN_HELPER
        ).read_bytes()
    assert before == {path.relative_to(canonical): path.read_bytes()
                      for path in canonical.rglob("*") if path.is_file()}


@pytest.mark.parametrize("case", ["valid", "wrong-name", "conditional", "escape"])
def test_standalone_dependency_source_follows_exact_declared_identity(tmp_path, case):
    project = tmp_path / "services/example"
    path = "../../../outside" if case == "escape" else "../../libs/selected"
    entry = f'{{ path = "{path}", editable = true'
    entry += ', marker = "sys_platform == \'linux\'"' if case == "conditional" else ""
    _write(project / "pyproject.toml", '[tool.uv.sources]\nExample_Lib = ' + entry + " }\n")
    selected = tmp_path / "libs/selected"
    _write(selected / "pyproject.toml",
           '[project]\nname="' + ("different" if case == "wrong-name" else "example-lib") + '"\n')
    if case == "valid":
        assert standalone.local_dependency_source(
            project, "example.lib", source_root=tmp_path,
        ) == selected.resolve()
    else:
        with pytest.raises(ValueError, match="invalid local source mapping"):
            standalone.local_dependency_source(project, "example.lib", source_root=tmp_path)


@pytest.mark.parametrize("case", ["absent", "stale", "foreign"])
def test_single_consumer_wrapper_refreshes_declared_standalone_assets(tmp_path, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    source = canonical / "worktree-manager"
    _write(source / "scripts/source.txt", "retained\n")
    _write(destination / "scripts/source.txt", "retained\n")
    for name in (*engine.PYTHON_FILES, *engine.SUPPORT_FILES):
        _write(canonical / "libs/installer-engine" / name, "# canonical " + name + "\n")
    if case != "absent":
        _write(source / "scripts/installer_engine_commands.py", "# admitted older helper\n")
        _write(destination / "scripts/installer_engine_commands.py",
               "# foreign helper\n" if case == "foreign" else "# admitted older helper\n")
    before = {path.relative_to(canonical): path.read_bytes()
              for path in canonical.rglob("*") if path.is_file()}
    log = materializer.materialize_installer_engine_ref_into(
        source_consumer_dir=source, dest_consumer_dir=destination,
        canonical_root=canonical,
    )
    if case == "foreign":
        assert any(line.startswith("SKIP") and "foreign content" in line for line in log), log
        assert not (destination / "scripts/installer_engine.py").exists()
    else:
        assert log and all(line.startswith("OK") for line in log), log
        for name in (*engine.PYTHON_FILES, *engine.SUPPORT_FILES):
            assert (destination / "scripts" / name).read_bytes() == (
                canonical / "libs/installer-engine" / name
            ).read_bytes()
    assert before == {path.relative_to(canonical): path.read_bytes()
                      for path in canonical.rglob("*") if path.is_file()}


@pytest.mark.parametrize("case", ["absent", "stale", "foreign"])
def test_single_consumer_canonical_reference_refreshes_support_before_conversion(tmp_path, case):
    canonical, destination = tmp_path / "canonical", tmp_path / "release"
    source = canonical / "plugins/agent-vault"
    for ext in ("ps1", "sh"):
        text = engine.canonical_line(ext) + "\n"
        _write(source / f"scripts/install.{ext}", text)
        _write(destination / f"scripts/install.{ext}", text)
        _write(canonical / f"libs/installer-engine/installer-engine.{ext}",
               "# fixture engine uses " + engine.SUPPORT_FILES[0] + "\n")
    for name in (*engine.PYTHON_FILES, *engine.SUPPORT_FILES):
        _write(canonical / "libs/installer-engine" / name, "# canonical " + name + "\n")
    if case != "absent":
        _write(source / "scripts/installer_engine_commands.py", "# admitted older helper\n")
        _write(destination / "scripts/installer_engine_commands.py",
               "# foreign helper\n" if case == "foreign" else "# admitted older helper\n")
    before = {path.relative_to(canonical): path.read_bytes()
              for path in canonical.rglob("*") if path.is_file()}
    candidate_before = {path.relative_to(destination): path.read_bytes()
                        for path in destination.rglob("*") if path.is_file()}
    log = materializer.materialize_installer_engine_ref_into(
        source_consumer_dir=source, dest_consumer_dir=destination, canonical_root=canonical,
    )
    if case == "foreign":
        assert any(line.startswith("SKIP") and "foreign content" in line for line in log), log
        assert candidate_before == {path.relative_to(destination): path.read_bytes()
                                    for path in destination.rglob("*") if path.is_file()}
    else:
        assert log and all(line.startswith("OK") for line in log), log
        for name in (*engine.PYTHON_FILES, *engine.SUPPORT_FILES, "installer-engine.ps1", "installer-engine.sh"):
            assert (destination / "scripts" / name).read_bytes() == (
                canonical / "libs/installer-engine" / name
            ).read_bytes()
        for ext in ("ps1", "sh"):
            assert engine.is_local_ref(
                engine.find_engine_ref(destination / f"scripts/install.{ext}", ext), destination,
            )
    assert before == {path.relative_to(canonical): path.read_bytes()
                      for path in canonical.rglob("*") if path.is_file()}


@pytest.mark.parametrize("case", ["valid", "fresh-file", "yanked-file", "missing-hash"])
def test_public_release_selection_ages_every_file_and_binds_hashes(case):
    def file(digest, timestamp="2026-01-01T00:00:00Z"):
        return {"yanked": False, "upload_time_iso_8601": timestamp, "digests": {"sha256": digest}}

    releases = {
        "1.0": [file("b" * 64)],
        "1.0.post1": [file("A" * 64), file("a" * 64)],
        "2.0rc1": [file("c" * 64)],
    }
    latest = releases["1.0.post1"][1]
    if case == "fresh-file":
        latest["upload_time_iso_8601"] = "2026-01-31T00:00:00Z"
    elif case == "yanked-file":
        latest["yanked"] = True
    elif case == "missing-hash":
        latest["digests"] = {}
    urls = []

    def fetch(url):
        urls.append(url)
        return json.dumps({"releases": releases}).encode()

    kwargs = dict(min_age_days=7, now=datetime(2026, 2, 1, tzinfo=timezone.utc), fetch=fetch)
    if case == "missing-hash":
        with pytest.raises(seasoned.SeasonedVersionError, match="file SHA-256"):
            seasoned.resolve_seasoned_public_release("Example_Pkg", **kwargs)
    else:
        result = seasoned.resolve_seasoned_public_release("Example_Pkg", **kwargs)
        assert (result.version, result.hashes) == (
            ("1.0.post1", ("a" * 64,)) if case == "valid" else ("1.0", ("b" * 64,))
        )
    assert urls == ["https://pypi.org/pypi/example-pkg/json"]


@pytest.mark.parametrize("case", ["valid", "duplicate-hash", "wrong-pin", "boolean-age"])
def test_public_toolchain_requires_canonical_hash_and_pin_provenance(tmp_path, case):
    source = public.PublicToolchainSource(7, {"wheel": "1.0"}, {"wheel": ("a" * 64,)})
    provenance = source.provenance | {"python": "3.11.9"}
    if case == "duplicate-hash":
        provenance["file_hashes"]["wheel"] *= 2
    elif case == "wrong-pin":
        provenance["packages"]["wheel"] = "2.0"
    elif case == "boolean-age":
        provenance["min_age_days"] = True
    if case == "valid":
        public.validate_lock_provenance(provenance, {"wheel": "1.0"})
        source.verify({"wheel": "1.0"})
        args = source.install_args(tmp_path)
        assert args == [
            "--require-hashes", "--requirements", str(tmp_path / "public-toolchain-requirements.txt"),
        ]
        assert Path(args[-1]).read_text() == "wheel==1.0 --hash=sha256:" + "a" * 64 + "\n"
        with pytest.raises(trust.ArtifactBuildError, match="installed versions"):
            source.verify({"wheel": "2.0"})
    else:
        with pytest.raises(trust.ArtifactBuildError, match="provenance"):
            public.validate_lock_provenance(provenance, {"wheel": "1.0"})


@pytest.mark.parametrize("case", ["configured", "plaintext", "public", "other-host"])
def test_governed_feed_requires_configured_https_nonpublic_index(case):
    url = {
        "plaintext": "http://feed.example/simple", "public": "https://pypi.org/simple",
        "other-host": "https://other.example/simple",
    }.get(case, "https://feed.example/simple")
    env = {"UV_DEFAULT_INDEX": url, "UV_INDEX_URL": "https://lower.example/simple"}
    assert trust._validated_trusted_index_url(env) == (
        (url, None) if case in {"configured", "other-host"} else None
    )


@pytest.mark.parametrize("case", ["valid", "unpaired", "invalid-type", "empty"])
def test_changefile_add_writes_paired_intent_only(tmp_path, monkeypatch, capsys, case):
    monkeypatch.setattr(changefile, "CHANGEFILES_DIR", tmp_path / ".changefiles")
    args = argparse.Namespace(
        plugin=[] if case == "empty" else ["example", "other"],
        type=[] if case == "empty" else ["patch", "dev"],
        comment="Preserve paired release intent",
    )
    if case == "unpaired":
        args.type.pop()
    elif case == "invalid-type":
        args.type[0] = "unknown"
    assert changefile.cmd_add(args) == (0 if case == "valid" else 1)
    output = capsys.readouterr()
    if case == "valid":
        pending = changefile.read_changefiles()
        assert len(pending) == 1
        assert pending[0][1] == {
            "comment": args.comment,
            "changes": [{"plugin": "example", "type": "patch"}, {"plugin": "other", "type": "dev"}],
        }
        assert "Wrote" in output.out
    else:
        assert output.err and not (tmp_path / ".changefiles").exists()


def _release_profile():
    return {
        "schema": PROFILE_SCHEMA, "version": 1, "component": "engine", "backend": "cpu",
        "release": {"identity": "engine:1.0", "sha256": "sha256:" + "2" * 64},
        "mode": "artifact", "approval_source": profiles.APPROVAL_SOURCE,
        "artifact": {"artifact_id": "sha256:" + "3" * 64, "manifest_sha256": "sha256:" + "4" * 64},
        "acquisition_policy": "governed-source-policy",
        "target": {
            "device": "cpu", "tags": ["py3-none-any"],
            "environment": {
                "implementation_name": "cpython", "implementation_version": "3.11.9",
                "os_name": "nt", "platform_machine": "AMD64", "platform_release": "11",
                "platform_system": "Windows", "platform_version": "10.0.1",
                "python_full_version": "3.11.9", "platform_python_implementation": "CPython",
                "python_version": "3.11", "sys_platform": "win32",
            },
        },
        "roots": ["engine==1.0"],
        "wheels": [{
            "name": "engine", "version": "1.0", "filename": "engine-1.0-py3-none-any.whl",
            "sha256": "sha256:" + "1" * 64, "requires_python": ">=3.11",
            "provides_extras": [], "selected_extras": [], "requires_dist": [],
        }],
    }


@pytest.mark.parametrize("case", ["valid", "foreign-artifact", "self-approval", "duplicate"])
def test_release_profiles_bind_whole_records_to_promotion_channel(case):
    record = _release_profile()
    identity = record["artifact"]["artifact_id"]
    index = {
        "artifacts": [{"artifact_id": identity}],
        "runtime_profiles": {"schema": profiles.SCHEMA, "version": 1, "profiles": [record]},
    }
    if case == "foreign-artifact":
        index["artifacts"][0]["artifact_id"] = "sha256:" + "9" * 64
    elif case == "self-approval":
        record["approval_source"] = "self"
    elif case == "duplicate":
        index["runtime_profiles"]["profiles"].append(record)
    if case == "valid":
        selected, = profiles.release_profiles(index)
        assert selected.record() == record
        record["backend"] = "cuda12"
        assert selected.record()["backend"] == "cpu"
    else:
        with pytest.raises(ProfileError, match="promotion artifact channel|duplicate release"):
            profiles.release_profiles(index)
