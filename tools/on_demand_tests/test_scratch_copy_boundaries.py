"""Explicit native filesystem regressions; not part of promotion's unit-only portfolio."""
from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

import pytest


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SYNC = _load("scratch_boundary_sync", REPO / "tools/sync-vendored-libs.py")
TRUSTED = _load(
    "scratch_boundary_trusted",
    REPO / "worktree-manager/src/worktree_manager/_trusted_pointer_materializer.py",
)


def _link(path, target):
    try:
        path.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("native directory symlink privilege unavailable")
        raise


@contextmanager
def _no_external_reads(monkeypatch, external):
    def outside(path):
        return not isinstance(path, int) and Path(path).resolve().is_relative_to(external)

    original_copy = shutil.copyfile
    original_open = Path.open
    original_scandir = os.scandir

    def copy(source, *args, **kwargs):
        assert not outside(source), "external source entered copyfile"
        return original_copy(source, *args, **kwargs)

    def open_file(path, *args, **kwargs):
        assert not outside(path), "external source entered Path.open"
        return original_open(path, *args, **kwargs)

    def scandir(path):
        assert not outside(path), "external directory entered traversal"
        return original_scandir(path)

    with monkeypatch.context() as local:
        local.setattr(shutil, "copyfile", copy)
        local.setattr(Path, "open", open_file)
        local.setattr(os, "scandir", scandir)
        yield


def _fixture(tmp_path):
    root = tmp_path / "repo"
    canonical = root / "libs" / "shared"
    for subtree in ("src", "tests"):
        (canonical / subtree / "ordinary").mkdir(parents=True)
        (canonical / subtree / "ordinary" / "source.py").write_bytes(b"ordinary source\n")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "shared"\nversion = "1.0.0"\n',
    )
    checkout = tmp_path / "checkout"
    destination = checkout / "libs" / "shared"
    (destination / "tests").mkdir(parents=True)
    pointer = destination / "VENDOR_POINTER.json"
    pointer.write_text(json.dumps({"source": "libs/shared"}))
    external = tmp_path / "synthetic-external"
    external.mkdir()
    (external / "sentinel.py").write_bytes(b"synthetic external sentinel\n")
    return root, canonical, checkout, destination, pointer, external


@pytest.mark.parametrize("consumer", ("sync", "trusted"))
@pytest.mark.parametrize("subtree", ("src", "tests"))
@pytest.mark.parametrize("layout", ("scratch-link", "nested-link", "ordinary-scratch"))
@pytest.mark.parametrize("placement", ("root", "nested"))
def test_copy_omits_scratch_before_any_external_read(
    tmp_path, monkeypatch, consumer, subtree, layout, placement,
):
    root, canonical, checkout, destination, pointer, external = _fixture(tmp_path)
    parent = canonical / subtree
    if placement == "nested":
        parent /= "ordinary"
    scratch = parent / ".test-runs"
    if layout == "scratch-link":
        _link(scratch, external)
    else:
        scratch.mkdir()
        (scratch / "private.py").write_bytes(b"ordinary scratch must not ship\n")
        if layout == "nested-link":
            _link(scratch / "linked", external)
    with _no_external_reads(monkeypatch, external):
        if consumer == "sync":
            SYNC._safe_replace_tree(
                canonical / subtree, destination / subtree, label=f"shared/{subtree}",
            )
        else:
            result = TRUSTED._materialize_one_pointer(
                pointer, checkout_root=checkout, canonical_root=root,
            )
            assert result.startswith("OK"), result
    assert (destination / subtree / "ordinary/source.py").read_bytes() == b"ordinary source\n"
    assert not list((destination / subtree).rglob(".test-runs"))
    assert not list(destination.rglob("sentinel.py"))
    assert (external / "sentinel.py").read_bytes() == b"synthetic external sentinel\n"


@pytest.mark.parametrize("consumer", ("sync", "trusted"))
@pytest.mark.parametrize("subtree", ("src", "tests"))
def test_non_scratch_links_remain_refused_before_external_reads(tmp_path, monkeypatch, consumer, subtree):
    root, canonical, checkout, destination, pointer, external = _fixture(tmp_path)
    _link(canonical / subtree / "ordinary/link", external)
    with _no_external_reads(monkeypatch, external):
        if consumer == "sync":
            with pytest.raises(SystemExit, match="symlink"):
                SYNC._safe_replace_tree(
                    canonical / subtree, destination / subtree, label=f"shared/{subtree}",
                )
        else:
            result = TRUSTED._materialize_one_pointer(
                pointer, checkout_root=checkout, canonical_root=root,
            )
            assert result.startswith("SKIP") and "symlink" in result
    assert pointer.exists()
    assert not list(destination.rglob("sentinel.py"))


@pytest.mark.parametrize("consumer", ("in-tree", "editable", "plugin"))
def test_unfiltered_build_inputs_reject_scratch_links(tmp_path, monkeypatch, consumer):
    import build_python_artifacts as build

    root, canonical, _checkout, _destination, _pointer, external = _fixture(tmp_path)
    _link(canonical / "src/ordinary/.test-runs", external)
    with _no_external_reads(monkeypatch, external):
        with pytest.raises(build.ArtifactBuildError, match="symlink"):
            if consumer == "in-tree":
                build._validate_in_tree_lib_dir("shared", canonical)
            elif consumer == "editable":
                monkeypatch.setattr(build, "REPO", root)
                monkeypatch.setattr(build, "LIBS_DIR", root / "libs")
                build._validate_editable_canonical_ref(
                    consumer_dir=root / "plugins/fixture", name="shared",
                    raw_path="../../libs/shared", lib="shared",
                )
            else:
                monkeypatch.setattr(build, "PLUGINS_DIR", root / "libs")
                build.build_plugin_artifacts("shared", out_dir=tmp_path / "output")


@pytest.mark.parametrize("consumer", ("editable-hash", "trusted-hash", "build-hash"))
def test_hash_walkers_omit_scratch_before_external_reads(tmp_path, monkeypatch, consumer):
    import build_python_artifacts as build
    import uv_editable_ref as editable

    _root, canonical, _checkout, _destination, _pointer, external = _fixture(tmp_path)
    calculate = {
        "editable-hash": editable._file_hashes,
        "trusted-hash": TRUSTED._file_hashes,
        "build-hash": build.directory_content_hash,
    }[consumer]
    before = calculate(canonical)
    _link(canonical / "src/ordinary/.test-runs", external)
    with _no_external_reads(monkeypatch, external):
        assert calculate(canonical) == before


@pytest.mark.parametrize("consumer", ("standalone", "binding"))
def test_unfiltered_release_closure_rejects_scratch_links(tmp_path, monkeypatch, consumer):
    import build_python_artifacts as build
    import promotion_python_artifacts as promotion
    import standalone_python_artifacts as standalone

    root, _canonical, _checkout, _destination, _pointer, external = _fixture(tmp_path)
    source = root / ("fleet-manager" if consumer == "standalone" else "plugins/fixture")
    source.mkdir(parents=True)
    (source / "pyproject.toml").write_text('[project]\nname="fixture"\nversion="1.0.0"\n')
    _link(source / ".test-runs", external)
    with _no_external_reads(monkeypatch, external):
        if consumer == "standalone":
            monkeypatch.setattr(standalone, "standalone_build_resources", lambda *_args, **_kw: ())
            with pytest.raises(build.ArtifactBuildError, match="linked"):
                standalone.source_closure("fleet-manager", root, build)
        else:
            with pytest.raises(promotion.PromotionArtifactError, match="symlink"):
                promotion.validate_source_binding({"plugin": "fixture"}, root)
