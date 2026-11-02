"""The exact pre-managed inspector must fail after explicitly adopting its lane."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from typing import Any
import zlib

import pytest

from test_agenda_store import AGENDA, STORE, SKILL, documents as documents, request
import agenda_migration as MIGRATION

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX directory-fsync migration path; native Windows is not qualified")


@pytest.fixture
def old_inspector() -> Any:
    fixture = Path(__file__).parent / "fixtures" / "agenda_pre_managed.b64"
    source = zlib.decompress(base64.b64decode(fixture.read_bytes()))
    assert len(source) == 18138
    assert hashlib.sha256(source).hexdigest() == (
        "11f245e0fb8981ef27e254e69fc03840bcf4d88487e884ba82d8f27a2af7d761")
    assert b"agenda.sqlite3" not in source
    spec = importlib.util.spec_from_loader("captured_pre_managed_agenda", loader=None)
    assert spec
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    # Execute the hash-pinned historical source, not an approximation of its reader.
    exec(compile(source, str(fixture), "exec"), module.__dict__)
    return module


@pytest.fixture
def legacy(tmp_path: Path, documents: dict[str, Any]) -> Path:
    # Preserve BOM, formatting and unknown evidence bytes, not just a parsed copy.
    raw = b"\xef\xbb\xbf" + json.dumps(documents["cursor"], indent=2).encode() + b"\n"
    (tmp_path / "cursor.json").write_bytes(raw)
    for name, board in documents["boards"].items():
        (tmp_path / name).write_text(json.dumps(board))
    return tmp_path


def adoption(home: Path) -> tuple[dict[str, Any], bool]:
    return STORE.mutate(home, request({"operation": "init"}, 0, "adopt"), import_home=home)


@pytest.mark.parametrize("version", [1, 2])
def test_actual_old_reader_succeeds_before_and_refuses_after_adoption(
    legacy: Path, old_inspector: Any, capsys: pytest.CaptureFixture[str], version: int
) -> None:
    if version == 2:
        cursor = AGENDA._read(legacy / "cursor.json")
        cursor.update(version=2, deferred=[], max_deferred=8)
        (legacy / "cursor.json").write_text(json.dumps(cursor, indent=2))
    before = (legacy / "cursor.json").read_bytes()
    boards = (legacy / "agenda.json").read_bytes()
    assert old_inspector.main(["next", "--state-home", str(legacy), "--json"]) == 0
    capsys.readouterr()
    result, accepted = adoption(legacy)
    assert accepted and result["persisted"]
    assert old_inspector.main(["next", "--state-home", str(legacy), "--json"]) == 2
    assert "unsupported cursor version" in capsys.readouterr().err
    state = STORE.read(legacy)
    assert state["snapshot"]["revision"] == 1
    assert base64.b64decode(state["metadata"]["legacy_fence"]["original_cursor"]) == before
    recovery = AGENDA.decode((legacy / MIGRATION.RECOVERY).read_bytes(), limit=MIGRATION.LIMIT)
    assert base64.b64decode(recovery["original_cursor"]) == before
    assert (legacy / "agenda.json").read_bytes() == boards
    assert adoption(legacy) == (result, False)


def test_sqlite_acceptance_failure_leaves_old_reader_fenced_and_explicit_retry(
    legacy: Path, old_inspector: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = STORE.encode

    def fail(value: Any) -> str:
        if isinstance(value, dict) and value.get("schema") == "efforts.agenda-result":
            raise AGENDA.AgendaError("injected after durable fence, before SQLite acceptance")
        return original(value)

    monkeypatch.setattr(STORE, "encode", fail)
    with pytest.raises(AGENDA.AgendaError, match="injected after durable fence"):
        adoption(legacy)
    assert old_inspector.main(["check", "--state-home", str(legacy)]) == 2
    with pytest.raises(AGENDA.AgendaError, match="initialization interrupted"):
        STORE.read(legacy)
    monkeypatch.setattr(STORE, "encode", original)
    assert adoption(legacy)[0]["persisted"]
    assert len(STORE.history(legacy)) == 1


def test_backup_is_durable_before_marker_replacement(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = (legacy / "cursor.json").read_bytes()
    atomic = MIGRATION._atomic_write

    def fail(home: Path, name: str, raw: bytes) -> None:
        if name == "cursor.json":
            saved = AGENDA.decode((home / MIGRATION.RECOVERY).read_bytes(), limit=MIGRATION.LIMIT)
            assert base64.b64decode(saved["original_cursor"]) == before
            raise OSError("injected fence replacement failure")
        atomic(home, name, raw)

    monkeypatch.setattr(MIGRATION, "_atomic_write", fail)
    with pytest.raises(AGENDA.AgendaError, match="fence replacement failure"):
        adoption(legacy)
    assert (legacy / "cursor.json").read_bytes() == before
    monkeypatch.setattr(MIGRATION, "_atomic_write", atomic)
    assert adoption(legacy)[0]["persisted"]


def test_actual_caller_death_after_fsync_fence_has_no_stale_success(
    legacy: Path, old_inspector: Any
) -> None:
    code = """
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import agenda_store as store
import agenda_migration as migration
original = migration.prepare
def interrupted(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(17)
migration.prepare = interrupted
home = Path(sys.argv[2])
store.mutate(home, {"schema":"efforts.agenda-request","version":1,"expected_revision":0,
    "operation_id":"adopt","change":{"operation":"init"}}, import_home=home)
"""
    result = subprocess.run([sys.executable, "-B", "-c", code, str(SKILL / "scripts"), str(legacy)],
                            capture_output=True, timeout=10)
    assert result.returncode == 17, result.stderr
    assert old_inspector.main(["next", "--state-home", str(legacy)]) == 2
    with pytest.raises(AGENDA.AgendaError, match="initialization interrupted"):
        STORE.read(legacy)
    assert adoption(legacy)[0]["persisted"]
    assert len(STORE.history(legacy)) == 1


@pytest.mark.parametrize("fault", ["missing", "corrupt", "wrong-request", "symlink"])
def test_fenced_recovery_never_resets_or_discards_input(
    legacy: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    # First complete the durable fence but abort the one accepted SQLite transaction.
    original = STORE.encode

    def fail(value: Any) -> str:
        if isinstance(value, dict) and value.get("schema") == "efforts.agenda-result":
            raise AGENDA.AgendaError("injected")
        return original(value)

    monkeypatch.setattr(STORE, "encode", fail)
    with pytest.raises(AGENDA.AgendaError, match="injected"):
        adoption(legacy)
    monkeypatch.setattr(STORE, "encode", original)
    path = legacy / MIGRATION.RECOVERY
    if fault == "missing":
        path.rename(legacy / "retained-recovery.json")
    elif fault == "corrupt":
        path.write_text("corrupt")
    elif fault == "symlink":
        path.rename(legacy / "retained-recovery.json")
        path.symlink_to(legacy / "retained-recovery.json")
    before = (legacy / "cursor.json").read_bytes()
    with pytest.raises(AGENDA.AgendaError):
        if fault == "wrong-request":
            STORE.mutate(legacy, request({"operation": "init"}, 0, "different"), import_home=legacy)
        else:
            adoption(legacy)
    assert (legacy / "cursor.json").read_bytes() == before


def test_concurrent_legacy_change_during_preparation_is_not_overwritten(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    atomic = MIGRATION._atomic_write

    def fail(home: Path, name: str, raw: bytes) -> None:
        atomic(home, name, raw)
        if name == MIGRATION.RECOVERY:
            (home / "cursor.json").write_text('{"version":1,"newer":"preserve"}')
            raise OSError("injected intervening legacy write")

    monkeypatch.setattr(MIGRATION, "_atomic_write", fail)
    with pytest.raises(AGENDA.AgendaError):
        adoption(legacy)
    monkeypatch.setattr(MIGRATION, "_atomic_write", atomic)
    newer = (legacy / "cursor.json").read_bytes()
    with pytest.raises(AGENDA.AgendaError, match="cursor changed"):
        adoption(legacy)
    assert (legacy / "cursor.json").read_bytes() == newer


def test_unqualified_platform_refuses_legacy_migration_without_acceptance(
    legacy: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = (legacy / "cursor.json").read_bytes()
    monkeypatch.setattr(MIGRATION, "os", SimpleNamespace(name="nt"))
    with pytest.raises(AGENDA.AgendaError, match="platform unsupported"):
        adoption(legacy)
    assert (legacy / "cursor.json").read_bytes() == before
    assert not (legacy / MIGRATION.RECOVERY).exists()


def test_inline_adoption_cannot_loosen_original_definition_limits(
    legacy: Path, documents: dict[str, Any]
) -> None:
    before = (legacy / "cursor.json").read_bytes()
    documents["boards"]["agenda.json"]["nodes"]["implement"]["max_visits"] += 1
    with pytest.raises(AGENDA.AgendaError, match="tighter limits"):
        STORE.mutate(legacy, request({"operation": "init", "documents": documents}, 0, "inline"))
    assert (legacy / "cursor.json").read_bytes() == before
    assert not (legacy / MIGRATION.RECOVERY).exists()


def test_large_original_cursor_recovery_survives_metadata_roundtrip(
    legacy: Path, old_inspector: Any
) -> None:
    cursor = AGENDA._read(legacy / "cursor.json")
    cursor["original_extension"] = "x" * 110000
    original = json.dumps(cursor).encode()
    (legacy / "cursor.json").write_bytes(original)
    assert adoption(legacy)[0]["persisted"]
    state = STORE.read(legacy)
    assert base64.b64decode(state["metadata"]["legacy_fence"]["original_cursor"]) == original
    assert old_inspector.main(["next", "--state-home", str(legacy)]) == 2
