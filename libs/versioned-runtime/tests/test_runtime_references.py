"""Durable lifecycle references remain deletion barriers after owner exit."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime as runtime


def case(tmp_path):
    root = tmp_path / "runtime"
    for name in ("old", "new", "spare"):
        (root / "versions" / name).mkdir(parents=True)
    return root


def replace(root, versions, *, expected=(), owner="fixture/component"):
    runtime._leases.replace_runtime_references(root, owner, versions, expected=list(expected))


def test_references_survive_process_exit_and_protect_all_cleanup_paths(tmp_path):
    root = case(tmp_path)
    code = """
import os, pathlib, sys
sys.path.insert(0, sys.argv[1])
import versioned_runtime as runtime
runtime._leases.replace_runtime_references(
    pathlib.Path(sys.argv[2]), 'fixture/component', ['old', 'new'], expected=[],
)
os._exit(23)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", code, str(LIBRARY), str(root)],
        timeout=15, capture_output=True,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    assert result.returncode == 23, result.stderr
    assert runtime.gc(root) == ["spare"]
    with pytest.raises(RuntimeError, match="selected or in use"):
        runtime.remove_owned_candidate(root, "new", remove=lambda candidate: candidate.rmdir())
    with runtime.construction_lease(root, "new"):
        with pytest.raises(RuntimeError, match="selected or in use"):
            runtime.discard_failed_candidate(root, "new")
    replace(root, ["old"], expected=["old", "new"])
    assert runtime.gc(root) == ["new"]
    replace(root, [], expected=["old"])
    assert runtime.gc(root) == ["old"]


def test_update_is_owner_scoped_and_compare_bound(tmp_path):
    root = case(tmp_path)
    replace(root, ["old"])
    replace(root, ["new"], owner="other/component")
    before = (root / "runtime-references.json").read_bytes()
    with pytest.raises(RuntimeError, match="changed before publication"):
        replace(root, ["spare"], expected=["new"])
    assert (root / "runtime-references.json").read_bytes() == before
    replace(root, [], expected=["old"])
    assert runtime._leases.read_runtime_references(root) == {"other/component": ["new"]}
    assert set(runtime.gc(root)) == {"old", "spare"}


@pytest.mark.parametrize("versions", [["missing"], ["../old"], ["old."], [True], "old"])
def test_invalid_or_missing_generation_cannot_be_published(tmp_path, versions):
    root = case(tmp_path)
    with pytest.raises((ValueError, FileNotFoundError)):
        replace(root, versions)
    assert not (root / "runtime-references.json").exists()


@pytest.mark.parametrize("raw", [
    b"broken",
    b'{"schema":"copilot-extensions.runtime-references","version":1,"owners":{},"owners":{}}',
    b'{"schema":"copilot-extensions.runtime-references","version":true,"owners":{}}',
    b'{"schema":"copilot-extensions.runtime-references","version":1,"owners":{"a":["../new"]}}',
    b'{"schema":"copilot-extensions.runtime-references","version":1,"owners":{"a":[]}}',
    b"x" * 65537,
], ids=["broken", "duplicate", "wrong-version", "escape", "empty", "oversized"])
def test_unreadable_reference_evidence_refuses_deletion_and_overwrite(tmp_path, raw):
    root = case(tmp_path)
    path = root / "runtime-references.json"
    path.write_bytes(raw)
    with pytest.raises(ValueError):
        runtime.gc(root)
    with pytest.raises(ValueError):
        replace(root, ["old"])
    assert path.read_bytes() == raw
    assert set(runtime.list_versions(root)) == {"old", "new", "spare"}


def test_hardlinked_reference_evidence_is_not_authority(tmp_path):
    root = case(tmp_path)
    replace(root, ["old"])
    os.link(root / "runtime-references.json", root / "alias.json")
    with pytest.raises(ValueError, match="ordinary file"):
        runtime.gc(root)


def test_failed_reference_publication_preserves_previous_evidence(tmp_path, monkeypatch):
    root = case(tmp_path)
    replace(root, ["old"])
    before = json.loads((root / "runtime-references.json").read_bytes())
    original = Path.replace

    def fail(path, target):
        if Path(target).name == "runtime-references.json":
            raise OSError("injected reference publication failure")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="publication failure"):
        replace(root, ["new", "old"], expected=["old"])
    assert json.loads((root / "runtime-references.json").read_bytes()) == before
    assert not list(root.glob("runtime-references.json.*.tmp"))


def test_publication_holds_retention_before_selector_custody(tmp_path, monkeypatch):
    root = case(tmp_path)
    publish = runtime._leases._publication_lease
    observed = []

    def check(root, **kwargs):
        with runtime._cleanup_lease(root, "new") as admitted:
            observed.append(admitted)
        return publish(root, **kwargs)

    monkeypatch.setattr(runtime._leases, "_publication_lease", check)
    replace(root, ["new"])
    assert observed == [False]
