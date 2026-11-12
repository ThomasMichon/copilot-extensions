"""Bounded reference classification preserves aliases without repeated root walks."""

import sqlite3
import os
import time
from pathlib import Path

import pytest

from agent_worktrees import checkout_reference_paths as paths
from agent_worktrees import checkout_relocation as relocation


def _database(path, values):
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY,cwd TEXT)")
    connection.execute("CREATE TABLE session_files(id INTEGER PRIMARY KEY,session_id TEXT,file_path TEXT)")
    connection.executemany("INSERT INTO session_files(file_path) VALUES (?)", ((value,) for value in values))
    connection.commit()
    connection.close()


def test_real_metadata_layout_resolves_shared_root_once_not_per_file(tmp_path, monkeypatch):
    target = tmp_path / "candidate"
    target.mkdir()
    directories = [tmp_path / "unrelated" / str(index) for index in range(32)]
    for directory in directories:
        directory.mkdir(parents=True)
    values = [str(directories[index % 32] / f"retained-{index}.py") for index in range(8201)]
    database = tmp_path / "session-store.db"
    _database(database, values)
    calls = 0
    original = Path.resolve

    def resolve(path, *args, **kwargs):
        nonlocal calls
        calls += int(path == target)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    before = database.read_bytes()
    assert not relocation._references(database, [
        ("SELECT 1 FROM sessions WHERE checkout_reference_under(cwd, ?) LIMIT 1", (str(target),)),
        ("SELECT 1 FROM session_files WHERE checkout_reference_under(file_path, ?) LIMIT 1", (str(target),)),
    ])
    assert calls == 2  # Initial root snapshot and negative-result revalidation.
    assert database.read_bytes() == before


def test_prepared_reference_preserves_parent_alias_containment(tmp_path, monkeypatch):
    target, alias = tmp_path / "candidate", tmp_path / "alias"
    target.mkdir()
    alias.mkdir()
    original = paths.canonical
    monkeypatch.setattr(paths, "canonical", lambda value: target if Path(value) == alias else original(value))
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert snapshot.under(str(alias / "historical.py"), str(target))
    snapshot.verify()


def test_changed_directory_incarnation_refuses_negative_snapshot(tmp_path):
    target, parent = tmp_path / "candidate", tmp_path / "references"
    target.mkdir()
    parent.mkdir()
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert not snapshot.under(str(parent / "historical.py"), str(target))
    parent.rename(tmp_path / "original-references")
    parent.mkdir()
    with pytest.raises(ValueError, match="changed during inspection"):
        snapshot.verify()


def test_unrelated_directory_entry_activity_is_not_a_quietness_gate(tmp_path):
    target, parent = tmp_path / "candidate", tmp_path / "references"
    target.mkdir()
    parent.mkdir()
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert not snapshot.under(str(parent / "historical.py"), str(target))
    (parent / "unrelated-new-file").write_text("ordinary activity")
    snapshot.verify()


def test_retargeted_parent_alias_refuses_even_with_unchanged_named_signature(tmp_path, monkeypatch):
    target, parent = tmp_path / "candidate", tmp_path / "references"
    target.mkdir()
    parent.mkdir()
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert not snapshot.under(str(parent / "historical.py"), str(target))
    original = paths.canonical
    monkeypatch.setattr(paths, "canonical", lambda value: target if Path(value) == parent else original(value))
    with pytest.raises(ValueError, match="changed during inspection"):
        snapshot.verify()


@pytest.mark.parametrize("value", [r"\\example.invalid\share\file", "../retained.py", b"opaque-path"])
def test_original_unsupported_reference_inputs_remain_held(tmp_path, value):
    database = tmp_path / "session-store.db"
    _database(database, [value])
    with pytest.raises(ValueError, match="operational reference schema"):
        relocation._references(database, [
            ("SELECT 1 FROM session_files WHERE checkout_reference_under(file_path, ?) LIMIT 1", (str(tmp_path),)),
        ])


def test_null_rows_still_consume_original_entry_budget(tmp_path):
    database = tmp_path / "session-store.db"
    _database(database, [None] * (relocation.transfer.MAX_ENTRIES + 1))
    with pytest.raises(ValueError, match="operational reference schema"):
        relocation._references(database, [
            ("SELECT 1 FROM session_files WHERE checkout_reference_under(file_path, ?) LIMIT 1", (str(tmp_path),)),
        ])


def test_negative_snapshot_verification_keeps_original_deadline(tmp_path):
    snapshot = paths.ReferenceSnapshot(time.monotonic() - 1)
    with pytest.raises(ValueError, match="exceeds its budget"):
        snapshot.verify()


@pytest.mark.parametrize("parents", [1, 2, 3])
def test_missing_terminal_parent_components_keep_full_canonical_containment(tmp_path, parents):
    target = tmp_path / "candidate"
    target.mkdir()
    value = target / "missing"
    for _ in range(parents):
        value /= ".."
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert snapshot.under(str(value), str(target)) == paths.under(value, target)
    snapshot.verify()


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction")
def test_native_parent_junction_still_finds_candidate_reference(tmp_path):
    import _winapi

    target, alias = tmp_path / "candidate", tmp_path / "alias"
    target.mkdir()
    _winapi.CreateJunction(str(target), str(alias))
    try:
        database = tmp_path / "session-store.db"
        _database(database, [str(alias / "historical.py")])
        assert relocation._references(database, [
            ("SELECT 1 FROM session_files WHERE checkout_reference_under(file_path, ?) LIMIT 1", (str(target),)),
        ])
    finally:
        alias.rmdir()
