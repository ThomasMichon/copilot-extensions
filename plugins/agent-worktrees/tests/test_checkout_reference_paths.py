"""Bounded reference classification preserves aliases without repeated root walks."""

import sqlite3
import os
import time
from pathlib import Path
from collections.abc import Callable, Iterable, Sequence
from typing import cast

import pytest

from agent_worktrees import checkout_reference_paths as paths
from agent_worktrees import checkout_relocation as relocation
from agent_worktrees import checkout_transfer


def _database(path: Path, values: Iterable[str | bytes | None]) -> None:
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE sessions(id TEXT PRIMARY KEY,cwd TEXT)")
    connection.execute("CREATE TABLE session_files(id INTEGER PRIMARY KEY,session_id TEXT,file_path TEXT)")
    connection.executemany("INSERT INTO session_files(file_path) VALUES (?)", ((value,) for value in values))
    connection.commit()
    connection.close()


@pytest.mark.parametrize("enumerating_containment", [False, True])
def test_real_metadata_layout_resolves_shared_root_once_not_per_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enumerating_containment: bool,
) -> None:
    target = tmp_path / "candidate"
    target.mkdir()
    directories = [tmp_path / "unrelated" / str(index) for index in range(32)]
    for directory in directories:
        directory.mkdir(parents=True)
    values = [str(directories[index % 32] / f"retained-{index}.py") for index in range(8201)]
    database = tmp_path / "session-store.db"
    _database(database, values)
    calls = 0
    ancestor_walks = 0
    original = Path.resolve
    original_parents = cast(Callable[[Path], Sequence[Path]], getattr(Path.parents, "fget"))

    def resolve(path: Path, strict: bool = False) -> Path:
        nonlocal calls
        calls += int(path == target)
        return original(path, strict=strict)

    def parents(path: Path) -> Sequence[Path]:
        nonlocal ancestor_walks
        ancestor_walks += 1
        return original_parents(path)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "parents", property(parents))
    if enumerating_containment:
        monkeypatch.setattr(Path, "is_relative_to", lambda path, other: path == other or other in path.parents)
    before = database.read_bytes()
    assert not relocation._references(database, [
        ("SELECT 1 FROM sessions WHERE checkout_reference_under(cwd, ?) LIMIT 1", (str(target),)),
        ("SELECT 1 FROM session_files WHERE checkout_reference_under(file_path, ?) LIMIT 1", (str(target),)),
    ])
    assert calls == 2  # Initial root snapshot and negative-result revalidation.
    assert ancestor_walks <= 2 * len(directories) + 2
    assert database.read_bytes() == before


@pytest.mark.parametrize("suffix, expected", [
    ("", True),
    ("child/retained.py", True),
    ("../candidate-neighbor/retained.py", False),
    ("../retained.py", False),
])
def test_prepared_containment_keeps_component_boundaries(tmp_path: Path, suffix: str, expected: bool) -> None:
    target = tmp_path / "candidate"
    target.mkdir()
    value = target / suffix
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert snapshot.under(str(value), str(target)) is expected
    snapshot.verify()


@pytest.mark.skipif(os.name != "nt", reason="native Windows case-insensitive paths")
def test_prepared_containment_keeps_windows_case_matching(tmp_path: Path) -> None:
    target = tmp_path / "Candidate"
    target.mkdir()
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert snapshot.under(str(target / "retained.py").swapcase(), str(target))
    snapshot.verify()


def test_missing_shared_ancestors_fit_original_metadata_read_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "candidate"
    target.mkdir()
    values = [
        str(tmp_path / "absent" / f"retained-{index % 1500}" / "nested" / f"file-{index}.py")
        for index in range(9000)
    ]
    database = tmp_path / "session-store.db"
    _database(database, values)
    calls = 0
    original = Path.resolve

    def resolve(path: Path, strict: bool = False) -> Path:
        nonlocal calls
        calls += int(str(path).startswith(str(tmp_path / "absent")))
        return original(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve)
    before = database.read_bytes()
    assert not relocation._references(database, [
        ("SELECT 1 FROM session_files WHERE checkout_reference_under(file_path, ?) LIMIT 1", (str(target),)),
    ])
    assert calls <= 2
    assert database.read_bytes() == before


def test_missing_ancestor_appearing_refuses_negative_snapshot(tmp_path: Path) -> None:
    target, missing = tmp_path / "candidate", tmp_path / "missing"
    target.mkdir()
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert not snapshot.under(str(missing / "nested" / "retained.py"), str(target))
    missing.mkdir()
    with pytest.raises(ValueError, match="changed during inspection"):
        snapshot.verify()


@pytest.mark.parametrize("parent", ["missing.", "missing ", "missing. /nested", "missing/../other", "missing/..."])
def test_missing_parent_normalization_keeps_canonical_result(tmp_path: Path, parent: str) -> None:
    value = tmp_path / parent / "retained.py"
    target = paths.canonical(value.parent)
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert snapshot.under(str(value), str(target)) == paths.under(value, target)
    snapshot.verify()


def test_prepared_reference_preserves_parent_alias_containment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    target, alias = tmp_path / "candidate", tmp_path / "alias"
    target.mkdir()
    alias.mkdir()
    original = paths.canonical
    monkeypatch.setattr(paths, "canonical", lambda value: target if Path(value) == alias else original(value))
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert snapshot.under(str(alias / "historical.py"), str(target))
    snapshot.verify()


def test_changed_directory_incarnation_refuses_negative_snapshot(tmp_path: Path) -> None:
    target, parent = tmp_path / "candidate", tmp_path / "references"
    target.mkdir()
    parent.mkdir()
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert not snapshot.under(str(parent / "historical.py"), str(target))
    parent.rename(tmp_path / "original-references")
    parent.mkdir()
    with pytest.raises(ValueError, match="changed during inspection"):
        snapshot.verify()


def test_unrelated_directory_entry_activity_is_not_a_quietness_gate(tmp_path: Path) -> None:
    target, parent = tmp_path / "candidate", tmp_path / "references"
    target.mkdir()
    parent.mkdir()
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert not snapshot.under(str(parent / "historical.py"), str(target))
    (parent / "unrelated-new-file").write_text("ordinary activity")
    snapshot.verify()


def test_retargeted_parent_alias_refuses_even_with_unchanged_named_signature(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
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
def test_original_unsupported_reference_inputs_remain_held(tmp_path: Path, value: str | bytes) -> None:
    database = tmp_path / "session-store.db"
    _database(database, [value])
    with pytest.raises(ValueError, match="operational reference schema"):
        relocation._references(database, [
            ("SELECT 1 FROM session_files WHERE checkout_reference_under(file_path, ?) LIMIT 1", (str(tmp_path),)),
        ])


def test_null_rows_still_consume_original_entry_budget(tmp_path: Path) -> None:
    database = tmp_path / "session-store.db"
    _database(database, [None] * (checkout_transfer.MAX_ENTRIES + 1))
    with pytest.raises(ValueError, match="operational reference schema"):
        relocation._references(database, [
            ("SELECT 1 FROM session_files WHERE checkout_reference_under(file_path, ?) LIMIT 1", (str(tmp_path),)),
        ])


def test_negative_snapshot_verification_keeps_original_deadline(tmp_path: Path) -> None:
    snapshot = paths.ReferenceSnapshot(time.monotonic() - 1)
    with pytest.raises(ValueError, match="exceeds its budget"):
        snapshot.verify()


@pytest.mark.parametrize("parents", [1, 2, 3])
def test_missing_terminal_parent_components_keep_full_canonical_containment(tmp_path: Path, parents: int) -> None:
    target = tmp_path / "candidate"
    target.mkdir()
    value = target / "missing"
    for _ in range(parents):
        value /= ".."
    snapshot = paths.ReferenceSnapshot(time.monotonic() + 3)
    assert snapshot.under(str(value), str(target)) == paths.under(value, target)
    snapshot.verify()


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction")
@pytest.mark.parametrize("suffix", ["historical.py", "missing/nested/historical.py"])
def test_native_parent_junction_still_finds_candidate_reference(tmp_path: Path, suffix: str) -> None:
    import _winapi

    target, alias = tmp_path / "candidate", tmp_path / "alias"
    target.mkdir()
    create_junction = cast(Callable[[str, str], None], getattr(_winapi, "CreateJunction"))
    create_junction(str(target), str(alias))
    try:
        database = tmp_path / "session-store.db"
        _database(database, [str(alias / suffix)])
        assert relocation._references(database, [
            ("SELECT 1 FROM session_files WHERE checkout_reference_under(file_path, ?) LIMIT 1", (str(target),)),
        ])
    finally:
        alias.rmdir()
