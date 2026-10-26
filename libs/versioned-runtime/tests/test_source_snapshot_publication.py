"""Immutable source snapshots share publication ownership across native callers."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

LIBRARY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRARY))
import versioned_runtime_source as source


@pytest.fixture
def selected(tmp_path):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text(
        json.dumps({"name": "example-plugin", "version": "1.2.3"}), encoding="utf-8",
    )
    digest = source.assembly_hash(payload)
    identity = source.SourceIdentity(
        "example-plugin", "1.2.3", "a" * 40, "b" * 40, digest,
        source.runtime_version("1.2.3", "a" * 40, digest),
    )
    (payload / source.SOURCE_FILE).write_text(json.dumps(identity.document()), encoding="utf-8")
    return payload, identity


def _contents(root):
    return {path.relative_to(root): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in root.rglob("*") if path.is_file()}


def _change_tree(payload):
    path = payload / source.SOURCE_FILE
    document = json.loads(path.read_text(encoding="utf-8"))
    document["source_tree"] = "c" * 40
    path.write_text(json.dumps(document), encoding="utf-8")


def test_snapshot_reuse_preserves_every_published_byte_and_timestamp(selected, tmp_path):
    payload, identity = selected
    root = tmp_path / "runtime"
    snapshot = source.publish_source_snapshot(payload, root, identity)
    before = _contents(snapshot)
    assert source.publish_source_snapshot(payload, root, identity) == snapshot
    assert _contents(snapshot) == before
    assert source.read_source_identity(snapshot, "1.2.3") == identity
    assert snapshot == root / "snapshots" / identity.runtime_version


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_snapshot_retries_transient_windows_publication_locks(selected, tmp_path, monkeypatch, winerror):
    payload, identity = selected
    rename = Path.rename
    attempts, delays = [], []

    def locked(path, destination):
        if path.name == "payload" and path.parent.name.startswith(".source-"):
            attempts.append(path)
            if len(attempts) < 3:
                error = PermissionError("synthetic Windows publication lock")
                error.winerror = winerror
                raise error
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", locked)
    monkeypatch.setattr(source.time, "sleep", delays.append)
    snapshot = source.publish_source_snapshot(payload, tmp_path / "runtime", identity)
    assert len(attempts) == 3
    assert delays == [.5, 1.0]
    assert source.read_source_identity(snapshot, identity.package_version) == identity
    assert not list(snapshot.parent.glob(".source-*"))


@pytest.mark.parametrize("winerror, expected_attempts", [(5, 4), (87, 1)])
def test_snapshot_publication_lock_failure_is_bounded_and_explicit(
    selected, tmp_path, monkeypatch, winerror, expected_attempts,
):
    payload, identity = selected
    rename = Path.rename
    attempts, delays = [], []

    def denied(path, destination):
        if path.name == "payload" and path.parent.name.startswith(".source-"):
            attempts.append(path)
            error = PermissionError("synthetic permanent publication denial")
            error.winerror = winerror
            raise error
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", denied)
    monkeypatch.setattr(source.time, "sleep", delays.append)
    root = tmp_path / "runtime"
    with pytest.raises(PermissionError, match="permanent publication denial"):
        source.publish_source_snapshot(payload, root, identity)
    assert len(attempts) == expected_attempts
    assert len(delays) == expected_attempts - 1
    assert not (root / "snapshots" / identity.runtime_version).exists()
    assert not list((root / "snapshots").glob(".source-*"))
    assert source.read_source_identity(payload, identity.package_version) == identity


@pytest.mark.parametrize("changed_input", ["source", "candidate"])
def test_snapshot_revalidates_inputs_after_transient_publication_lock(
    selected, tmp_path, monkeypatch, changed_input,
):
    payload, identity = selected
    rename = Path.rename
    attempts = []

    def changed(path, destination):
        if path.name == "payload" and path.parent.name.startswith(".source-"):
            attempts.append(path)
            _change_tree(payload if changed_input == "source" else path)
            error = PermissionError("synthetic Windows publication lock")
            error.winerror = 32
            raise error
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", changed)
    monkeypatch.setattr(source.time, "sleep", lambda _: None)
    root = tmp_path / "runtime"
    with pytest.raises(source.SourceIdentityError, match="identity changed"):
        source.publish_source_snapshot(payload, root, identity)
    assert len(attempts) == 1
    assert not (root / "snapshots" / identity.runtime_version).exists()


def test_conflicting_winner_is_not_repaired_or_nested_into(selected, tmp_path):
    payload, identity = selected
    root = tmp_path / "runtime"
    snapshot = source.publish_source_snapshot(payload, root, identity)
    _change_tree(snapshot)
    changed = source.read_source_identity(snapshot, "1.2.3")
    assert changed.runtime_version == identity.runtime_version
    assert changed != identity
    before = _contents(snapshot)
    with pytest.raises(source.SourceIdentityError, match="identity changed"):
        source.publish_source_snapshot(payload, root, identity)
    assert _contents(snapshot) == before
    assert not (snapshot / "payload").exists()


def test_source_is_revalidated_after_acquiring_real_snapshot_lease(selected, tmp_path, monkeypatch):
    payload, identity = selected
    root = tmp_path / "runtime"
    snapshot = source.publish_source_snapshot(payload, root, identity)
    before = _contents(snapshot)
    runtime = source._runtime()
    lease = runtime.construction_lease

    @contextmanager
    def changed(*args, **kwargs):
        with lease(*args, **kwargs) as held:
            _change_tree(payload)
            yield held

    monkeypatch.setattr(runtime, "construction_lease", changed)
    with pytest.raises(source.SourceIdentityError, match="identity changed"):
        source.publish_source_snapshot(payload, root, identity)
    assert _contents(snapshot) == before


def test_incomplete_copy_is_never_published(selected, tmp_path, monkeypatch):
    payload, identity = selected
    root = tmp_path / "runtime"
    copy = source.shutil.copytree

    def changed(origin, destination, **kwargs):
        result = copy(origin, destination, **kwargs)
        (destination / "unexpected.py").write_text("changed", encoding="utf-8")
        return result

    monkeypatch.setattr(source.shutil, "copytree", changed)
    with pytest.raises(source.SourceIdentityError, match="payload bytes"):
        source.publish_source_snapshot(payload, root, identity)
    assert not (root / "snapshots" / identity.runtime_version).exists()
    assert [path.name for path in (root / "snapshots").iterdir()] == [".construction-leases"]


def test_publication_refuses_to_write_inside_the_source(selected):
    payload, identity = selected
    before = _contents(payload)
    with pytest.raises(source.SourceIdentityError, match="outside the source payload"):
        source.publish_source_snapshot(payload, payload / "runtime", identity)
    assert _contents(payload) == before


@pytest.mark.parametrize("changed", [False, True])
def test_snapshot_cli_preserves_initial_full_identity(selected, tmp_path, changed):
    payload, identity = selected
    root = tmp_path / "runtime"
    expected = json.dumps(source.manifest_fields(identity), sort_keys=True)
    if changed:
        _change_tree(payload)
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(LIBRARY / "versioned_runtime_source.py"),
         "--payload", str(payload), "--package-version", "1.2.3", "--plugin", identity.plugin,
         "--expected-runtime-version", identity.runtime_version,
         "--expected-source-identity", expected, "--snapshot-root", str(root)],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if changed:
        assert result.returncode != 0
        assert "Source identity changed after runtime selection" in result.stderr
        assert not root.exists()
    else:
        assert result.returncode == 0, result.stderr
        snapshot = Path(result.stdout.strip())
        assert snapshot == root / "snapshots" / identity.runtime_version
        assert source.read_source_identity(snapshot, "1.2.3") == identity
