"""Real slot retention controls, without provider or native execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

import pytest

from . import test_runtime_profile as fixtures

commands = fixtures.commands
payload = fixtures.payload
profiles = fixtures.profiles


def _selection(
    profiles: ModuleType, payload: Path, root: Path, *, profile: str = "base",
) -> object:
    fixtures._provision(profiles, root, payload, requested=profile)
    return profiles.select_execution(root)


def test_exact_selection_survives_switch_and_rollback_without_reresolution(
    profiles, payload, commands, tmp_path, monkeypatch,
) -> None:
    root = tmp_path / "runtime"
    first = _selection(profiles, payload, root)
    second = _selection(profiles, payload, root, profile="provider-exec")
    commands.clear()

    def unexpected_resolution(root: Path) -> None:
        raise AssertionError("retention must not resolve a mutable selection again")

    monkeypatch.setattr(profiles.runtime, "resolve_python", unexpected_resolution)
    with profiles.retain_execution(root, first, timeout=0) as (retained, descriptor):
        assert retained is first
        assert retained.python != second.python
        assert retained.retained_creation is False
        os.fstat(descriptor)
        profiles.runtime.activate(root, first.runtime_version, link_free=True)
        profiles.runtime.activate(root, second.runtime_version, link_free=True)
        # Neither selected nor last-known-good may hide the retention assertion.
        (root / "last-known-good").write_text(second.runtime_version, encoding="utf-8")
        assert profiles.runtime.gc(root, min_age_days=0) == []
        assert first.python.is_file()
    with pytest.raises(OSError):
        os.fstat(descriptor)
    assert profiles.runtime.gc(root, min_age_days=0) == [first.runtime_version]
    assert commands == []


@pytest.mark.parametrize("change", [
    "declaration", "hash", "marker", "missing", "duplicate", "legacy",
])
def test_changed_slot_refuses_and_releases_retention(
    profiles, payload, commands, tmp_path, change,
) -> None:
    root = tmp_path / "runtime"
    selected = _selection(profiles, payload, root)
    slot = selected.python.parent.parent
    path = slot / "runtime-profile.json"
    value = json.loads(path.read_text())
    if change == "declaration":
        value["executionContract"]["retainedCreation"] = True
    elif change == "hash":
        value["payloadHash"] = "0" * 64
    elif change == "marker":
        (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    elif change == "missing":
        path.rename(slot / "saved-profile.json")
    elif change == "legacy":
        del value["executionContract"]
    if change in {"declaration", "hash", "legacy"}:
        path.write_text(json.dumps(value), encoding="utf-8")
    elif change == "duplicate":
        path.write_text('{"profile":"base","profile":"base"}', encoding="utf-8")
    commands.clear()
    with pytest.raises(RuntimeError):
        with profiles.retain_execution(root, selected, timeout=0):
            pytest.fail("changed evidence must refuse before handing out custody")
    with profiles.runtime._cleanup_lease(root, selected.runtime_version) as admitted:
        assert admitted
    assert commands == []


def test_descriptor_is_reread_after_canonical_lease_admission(
    profiles, payload, commands, tmp_path, monkeypatch,
) -> None:
    from contextlib import contextmanager

    root = tmp_path / "runtime"
    selected = _selection(profiles, payload, root)
    custody = profiles.runtime.retention_custody

    @contextmanager
    def changed(root: Path, version: str, **kwargs):
        with custody(root, version, **kwargs) as owned:
            path = selected.python.parent.parent / "runtime-profile.json"
            value = json.loads(path.read_text())
            value["executionContract"]["retainedCreation"] = True
            path.write_text(json.dumps(value), encoding="utf-8")
            yield owned

    monkeypatch.setattr(profiles.runtime, "retention_custody", changed)
    with pytest.raises(RuntimeError, match="changed after selection"):
        with profiles.retain_execution(root, selected, timeout=0):
            pytest.fail("pre-lease declaration cannot grant retention")


@pytest.mark.parametrize("change", ["root", "version", "python", "profile", "contract"])
def test_selection_cannot_borrow_another_slots_lease(
    profiles, payload, commands, tmp_path, change,
) -> None:
    root = tmp_path / "runtime"
    selected = _selection(profiles, payload, root)
    if change == "root":
        root = tmp_path / "other"
    elif change == "version":
        selected = replace(selected, runtime_version="2.0.0+base")
    elif change == "python":
        selected = replace(selected, python=selected.python.with_name("other-python"))
    elif change == "profile":
        selected = replace(selected, profile="provider-exec")
    else:
        selected = replace(selected, contract_version=2)
    with pytest.raises(RuntimeError):
        with profiles.retain_execution(root, selected, timeout=0):
            pytest.fail("conflicting selection must not acquire usable custody")


def test_body_failure_releases_borrowed_descriptor(profiles, payload, commands, tmp_path) -> None:
    root = tmp_path / "runtime"
    selected = _selection(profiles, payload, root)
    with pytest.raises(RuntimeError, match="caller failed"):
        with profiles.retain_execution(root, selected, timeout=0) as (_, descriptor):
            raise RuntimeError("caller failed")
    with pytest.raises(OSError):
        os.fstat(descriptor)
    with profiles.runtime._cleanup_lease(root, selected.runtime_version) as admitted:
        assert admitted


def test_shared_retention_and_exclusive_cleanup_use_same_kernel_owner(
    profiles, payload, commands, tmp_path,
) -> None:
    root = tmp_path / "runtime"
    selected = _selection(profiles, payload, root)
    with profiles.retain_execution(root, selected, timeout=0):
        with profiles.retain_execution(root, selected, timeout=0):
            with profiles.runtime._cleanup_lease(root, selected.runtime_version) as admitted:
                assert not admitted
    with profiles.runtime._cleanup_lease(root, selected.runtime_version) as admitted:
        assert admitted
        with pytest.raises(profiles.runtime.RetentionTimeout):
            with profiles.retain_execution(root, selected, timeout=0):
                pytest.fail("exclusive cleanup must prevent execution retention")


@pytest.mark.skipif(os.name == "nt", reason="POSIX inherited flock ownership only")
def test_inherited_descriptor_protects_exact_slot_after_parent_context_exits(
    profiles, payload, commands, tmp_path,
) -> None:
    import select

    root = tmp_path / "runtime"
    selected = _selection(profiles, payload, root)
    other = _selection(profiles, payload, root, profile="provider-exec")
    (root / "last-known-good").write_text(other.runtime_version, encoding="utf-8")
    child = None
    try:
        with profiles.retain_execution(root, selected, timeout=0) as (_, descriptor):
            child = subprocess.Popen(
                [sys.executable, "-I", "-c",
                 "import os,sys; os.fstat(int(sys.argv[1])); "
                 "print('held',flush=True); sys.stdin.buffer.read(1)", str(descriptor)],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                pass_fds=(descriptor,),
            )
            assert select.select([child.stdout], [], [], 10)[0], "child did not accept descriptor"
            assert child.stdout.readline() == b"held\n"
        assert profiles.runtime.gc(root, min_age_days=0) == []
        assert selected.python.is_file()
        stdout, stderr = child.communicate(b"x", timeout=10)
        assert child.returncode == 0, stderr
        assert stdout == stderr == b""
        assert profiles.runtime.gc(root, min_age_days=0) == [selected.runtime_version]
    finally:
        if child is not None and child.returncode is None:
            child.communicate(b"", timeout=10)
