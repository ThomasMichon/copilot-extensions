"""Complete, fresh snapshot manifests without repeated relative-path parsing."""

from __future__ import annotations

import hashlib
import importlib.util
import stat
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from .installer_test_support import installer_functional_timeout, snapshot_tree_manifest


@pytest.mark.parametrize("profile,multiplier", [(None, 1), ("standard", 1), ("busy-host-functional", 3)])
@pytest.mark.parametrize("seconds", [45, 60])
def test_installer_functional_profile_is_explicit(monkeypatch, profile, multiplier, seconds):
    monkeypatch.delenv("CODESPACES_INSTALLER_TEST_PROFILE", raising=False)
    if profile is not None:
        monkeypatch.setenv("CODESPACES_INSTALLER_TEST_PROFILE", profile)
    assert installer_functional_timeout(seconds) == seconds * multiplier


@pytest.mark.parametrize("profile", ["", "busy", "STANDARD"])
def test_installer_functional_profile_refuses_unknown_values(monkeypatch, profile):
    monkeypatch.setenv("CODESPACES_INSTALLER_TEST_PROFILE", profile)
    with pytest.raises(ValueError, match="Unknown Codespaces installer test profile"):
        installer_functional_timeout(45)


def _contract_module(module_name, monkeypatch):
    if module_name == "test_snapshot_copy_contract":
        # Synthetic runner collections can cache a stub under this sibling name.
        monkeypatch.setitem(
            sys.modules, f"{__package__}.test_installer_engine_adoption",
            _contract_module("test_installer_engine_adoption", monkeypatch),
        )
    spec = importlib.util.spec_from_file_location(
        f"{__package__}.profile_{module_name}_control",
        Path(__file__).with_name(f"{module_name}.py"),
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("profile,multiplier", [("standard", 1), ("busy-host-functional", 3)])
@pytest.mark.parametrize("module_name", ["test_installer_engine_adoption", "test_snapshot_copy_contract"])
def test_installer_functional_profile_reaches_module_and_native_harness(
    monkeypatch, tmp_path, profile, multiplier, module_name,
):
    monkeypatch.setenv("CODESPACES_INSTALLER_TEST_PROFILE", profile)
    module = _contract_module(module_name, monkeypatch)
    assert module.pytestmark.args == (60 * multiplier,)
    observed = []
    monkeypatch.setattr(
        module.subprocess, "run",
        lambda command, **kwargs: observed.append((command, kwargs)) or "completed",
    )
    if module_name == "test_installer_engine_adoption":
        assert module._run("sh", "exit 0", tmp_path, {}) == "completed"
    else:
        monkeypatch.setattr(module, "_fake_uv", lambda *_args: None)
        monkeypatch.setattr(module, "isolate_installer_environment", lambda *_args: None)
        layout = (tmp_path, tmp_path, tmp_path / "engine.ps1", "")
        assert module._native(layout, tmp_path, "exit 0") == "completed"
    assert observed[0][1]["timeout"] == 45 * multiplier
    assert observed[0][1]["check"] is False


def test_missing_library_fixture_populates_all_declared_sources(monkeypatch, tmp_path):
    module = _contract_module("test_snapshot_copy_contract", monkeypatch)
    canonical = tmp_path / "canonical"
    working = tmp_path / "working"
    payload = working / "plugins" / "fixture"
    payload.mkdir(parents=True)
    names = ("repository-policy", "update-driver", "ssh-manager", "future-library")
    (payload / "pyproject.toml").write_text(
        "[tool.uv.sources]\n" + "".join(
            f'package-{index} = {{ path = "../../libs/{name}" }}\n'
            for index, name in enumerate(names)
        ),
        encoding="utf-8",
    )
    for name in names:
        source = canonical / "libs" / name
        source.mkdir(parents=True)
        (source / "pyproject.toml").write_text(f"name = '{name}'\n", encoding="utf-8")
        (source / ".content").write_bytes(bytes(range(256)))
    engine = working / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "sentinel").write_bytes(b"preserved engine")
    monkeypatch.setattr(module, "REPO", canonical)
    before = snapshot_tree_manifest(canonical)
    module._complete_snapshot_library_fixture(working, payload)
    assert snapshot_tree_manifest(canonical) == before
    for name in names:
        assert snapshot_tree_manifest(working / "libs" / name) == snapshot_tree_manifest(
            canonical / "libs" / name,
        )
    assert (engine / "sentinel").read_bytes() == b"preserved engine"


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory(prefix="installer-manifest-") as directory:
        yield Path(directory)


def test_manifest_records_complete_contents_without_reparsing_each_key(tmp_path, monkeypatch):
    root = tmp_path / "payload"
    (root / "nested" / "empty-directory").mkdir(parents=True)
    binary = bytes(range(256))
    (root / "nested" / ".binary").write_bytes(binary)
    (root / "empty").write_bytes(b"")
    original = Path.relative_to

    def relative_to(path, *args, **kwargs):
        if path == root or root in path.parents:
            raise AssertionError("Manifest keys must be accumulated during the walk")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "relative_to", relative_to)
    assert snapshot_tree_manifest(root) == {
        ".": ("directory", ""),
        "empty": ("file", hashlib.sha256(b"").hexdigest()),
        "nested": ("directory", ""),
        "nested/.binary": ("file", hashlib.sha256(binary).hexdigest()),
        "nested/empty-directory": ("directory", ""),
    }


def test_manifest_rereads_bytes_and_membership_on_every_call(tmp_path):
    root = tmp_path / "payload"
    root.mkdir()
    content = root / "content"
    content.write_bytes(b"original")
    before = snapshot_tree_manifest(root)
    content.write_bytes(b"modified")
    (root / "added").write_bytes(b"new")
    after = snapshot_tree_manifest(root)
    assert after["content"] != before["content"]
    assert after["added"] == ("file", hashlib.sha256(b"new").hexdigest())
    content.unlink()
    assert "content" not in snapshot_tree_manifest(root)


@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_manifest_does_not_walk_a_live_link_root(tmp_path, monkeypatch, kind):
    root = tmp_path / "payload"
    root.mkdir()
    (root / "not-followed").write_bytes(b"private")
    monkeypatch.setattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == root:
            return SimpleNamespace(
                st_mode=stat.S_IFLNK if kind == "symlink" else stat.S_IFDIR,
                st_file_attributes=0 if kind == "symlink" else 0x400,
            )
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr("os.readlink", lambda path: "declared-target")
    assert snapshot_tree_manifest(root) == {".": ("link", "declared-target")}


def test_manifest_checks_live_child_link_metadata_before_reading(tmp_path, monkeypatch):
    root = tmp_path / "payload"
    root.mkdir()
    child = root / "child"
    child.write_bytes(b"original")
    original_lstat = Path.lstat
    original_read = Path.read_bytes
    linked = False

    def lstat(path, *args, **kwargs):
        if linked and path == child:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original_lstat(path, *args, **kwargs)

    def read_bytes(path, *args, **kwargs):
        if linked and path == child:
            raise AssertionError("A changed link must not be followed")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr("os.readlink", lambda path: "changed-target")
    assert snapshot_tree_manifest(root)["child"][0] == "file"
    linked = True
    assert snapshot_tree_manifest(root)["child"] == ("link", "changed-target")
