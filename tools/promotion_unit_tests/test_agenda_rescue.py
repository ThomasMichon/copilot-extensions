"""In-memory owner composition for complete agenda activation and retries."""

from contextlib import contextmanager
from collections.abc import Iterator
import importlib
from pathlib import Path
import sqlite3
from typing import Any

import pytest

SCRIPTS = (Path(__file__).resolve().parents[2] / "plugins" / "efforts" / "skills" /
           "driving-effort-agendas" / "scripts")
Owners = tuple[Any, sqlite3.Connection, sqlite3.Connection, dict[str, Any]]


@pytest.fixture
def owners(monkeypatch: pytest.MonkeyPatch) -> Iterator[Owners]:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    rescue = importlib.import_module("agenda_rescue")
    owner = importlib.import_module("agenda_store_v2")
    source, target = sqlite3.connect(":memory:"), sqlite3.connect(":memory:")
    owner.schema(source)
    owner.schema(target)
    monkeypatch.setattr(rescue.store, "safe_path", lambda path, **kwargs: path)
    monkeypatch.setattr(rescue, "_members", lambda path: None)
    monkeypatch.setattr(rescue, "_identity", lambda path: [1, 2])
    selected = source

    @contextmanager
    def transaction(*args: Any, **kwargs: Any) -> Any:
        selected.execute("BEGIN")
        try:
            yield selected
            selected.commit()
        finally:
            if selected.in_transaction:
                selected.rollback()

    monkeypatch.setattr(rescue.store, "transaction", transaction)
    home = Path("unit-memory-home")
    request = {"schema": rescue.store.SCHEMA, "version": 2, "mode": "context-qualified",
        "expected_revision": 0, "operation_id": "initial",
        "change": {"operation": "init", "worktree": "fixture",
            "board": {"version": 2, "source": {"kind": "task", "ref": "fixture"},
                "revision": 1, "order": ["one"], "items": {"one": {
                    "source_ref": "fixture/one", "scope_ref": "fixture", "action": "Run one",
                    "exit": ["One recorded"], "on": {"done": "advance"},
                    "relevant_subjects": ["input"]}}},
            "baseline_context": {"facts": [{
                "kind": "input", "subject": "input", "value": "initial", "evidence_ref": "fixture"}]},
            "resource_caps": {"max_depth": 4, "max_pending_items": 32,
                "max_deferred_frames": 4, "max_no_parent_progress": 3}}}
    owner.mutate(home, request, initial_store_id="a" * 32)
    manifest = {"tables": rescue.export._table_hashes(source, rescue.export._Budget())}
    move = {"store_id": "a" * 32, "worktree": "fixture", "revision": 1,
            "destination_directory": [1, 2]}
    rescue._fence(target, {"request": move, "database_identity": [1, 2]})
    target.commit()
    selected = target
    try:
        yield rescue, source, target, {"request": move, "destination_database": [1, 2],
                                       "manifest": manifest, "home": home}
    finally:
        source.close()
        target.close()


def test_activation_copies_original_history_and_retry_never_replays(owners: Owners) -> None:
    rescue, source, target, record = owners
    version, activated = rescue._activate(
        record["home"], source, {key: record[key] for key in ("request", "destination_database")},
        record["manifest"], rescue.export._Budget(),
    )
    assert (version, activated) == (1, True)
    assert rescue.export._table_hashes(target, rescue.export._Budget()) == record["manifest"]["tables"]
    before = list(target.iterdump())
    assert rescue._activate(
        record["home"], source, {key: record[key] for key in ("request", "destination_database")},
        record["manifest"], rescue.export._Budget(),
    ) == (1, False)
    assert list(target.iterdump()) == before


def test_oversized_recovery_refuses_before_changing_the_database(owners: Owners) -> None:
    rescue, source, _target, _record = owners
    before = list(source.iterdump())
    with pytest.raises(rescue.AgendaError, match="128 KiB"):
        rescue._fence(source, {"request": "x" * 131072})
    assert list(source.iterdump()) == before
    assert source.execute("PRAGMA user_version").fetchone()[0] == 2


@pytest.mark.parametrize("relative", [True, False])
def test_relocation_refuses_home_dependent_canonical_references(
    owners: Owners, relative: bool,
) -> None:
    rescue, _source, _target, _record = owners
    documents = {"boards": {
        "root": {"source": {"kind": "task", "ref": "fixture-assignment"}},
        "queued": {"source": {"kind": "effort", "ref":
                             "mission.md" if relative else str(SCRIPTS / "mission.md")}},
    }}
    if relative:
        with pytest.raises(rescue.AgendaError, match="absolute local effort references"):
            rescue._references(documents)
    else:
        rescue._references(documents)


@pytest.mark.parametrize("fault", ["wrong-operation", "history", "copy-failure"])
def test_activation_refuses_foreign_reservation_and_rolls_back_partial_copy(
    owners: Owners, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    rescue, source, target, record = owners
    if fault == "wrong-operation":
        target.execute(f"UPDATE {rescue.TABLE} SET body=?", ('{"request":{"operation_id":"foreign"}}',))
        target.commit()
    elif fault == "history":
        target.execute("INSERT INTO agenda_records VALUES ('foreign','{}')")
        target.commit()
    else:
        def interrupted(*args: Any) -> None:
            raise rescue.AgendaError("copy failed after inserting rows")
        monkeypatch.setattr(rescue.export, "_table_hashes", interrupted)
    before = list(target.iterdump())
    with pytest.raises(rescue.AgendaError):
        rescue._activate(
            record["home"], source, {key: record[key] for key in ("request", "destination_database")},
            record["manifest"], rescue.export._Budget(),
        )
    assert list(target.iterdump()) == before
    assert target.execute("PRAGMA user_version").fetchone()[0] == rescue.store.RELOCATED_VERSION
