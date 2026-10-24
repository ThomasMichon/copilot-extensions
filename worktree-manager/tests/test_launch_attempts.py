"""Protected evidence survives failed launches without becoming retry authority."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from worktree_manager import launch_attempts as journal


def record(store: journal.LaunchAttempts, **overrides) -> str:
    attempt_id = overrides.pop("attempt_id", uuid4().hex)
    fields = {
        "action": "new", "project": "example", "surface": "new-dialog",
        "is_local": True, "prompt": "  exact 'private' text\nsecond line\n",
        "options": {"no_mux": True, "ahp": False},
    }
    store.record_intent(attempt_id, **{**fields, **overrides})
    return attempt_id


def test_missing_reads_and_maintenance_do_not_create_storage(tmp_path):
    store = journal.LaunchAttempts(tmp_path)
    assert store.entries() == []
    assert store.expire() == 0
    with pytest.raises(journal.LaunchAttemptError, match="not found"):
        store.detail(uuid4().hex)
    assert list(tmp_path.iterdir()) == []


def test_existing_reads_verify_permissions_without_repair_or_database_writes(tmp_path, monkeypatch):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    before = store.path.read_bytes()
    connect = sqlite3.connect
    def readonly_connect(*args, **kwargs):
        assert kwargs.get("uri") is True and str(args[0]).endswith("?mode=ro")
        return connect(*args, **kwargs)
    def no_repair(*_args, **_kwargs):
        pytest.fail("read-only evidence access must not repair permissions")
    monkeypatch.setattr(journal, "protect", no_repair)
    monkeypatch.setattr(journal.sqlite3, "connect", readonly_connect)
    assert store.entries()[0]["attempt_id"] == attempt_id
    assert store.detail(attempt_id, include_input=True)["input"]["prompt"]
    assert store.path.read_bytes() == before


@pytest.mark.skipif(os.name != "nt", reason="Native Windows read-only DACL refusal")
def test_broad_windows_acl_blocks_input_reads_without_repair(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes

    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    before = store.path.read_bytes()
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID),
        ctypes.POINTER(wintypes.DWORD),
    ]
    convert.restype = wintypes.BOOL
    apply = advapi.SetFileSecurityW
    apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
    apply.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    descriptor = wintypes.LPVOID()
    assert convert("D:P(A;;FA;;;WD)", 1, ctypes.byref(descriptor), None)
    try:
        assert apply(str(store.path), 0x80000004, descriptor)
    finally:
        kernel.LocalFree(descriptor)
    def no_repair(*_args, **_kwargs):
        pytest.fail("evidence reads must not replace a broad DACL")
    monkeypatch.setattr(journal, "protect", no_repair)
    with pytest.raises(journal.LaunchAttemptError, match="metadata"):
        store.entries()
    with pytest.raises(journal.LaunchAttemptError, match="detail"):
        store.detail(attempt_id, include_input=True)
    assert store.path.read_bytes() == before


def test_exact_private_input_requires_explicit_detail_and_metadata_never_selects_it(
    tmp_path, monkeypatch,
):
    store = journal.LaunchAttempts(tmp_path)
    prompt = "  exact 'private' text\nsecond line\n"
    attempt_id = record(store)
    statements = []
    connect = sqlite3.connect

    class TracedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            statements.append(sql)
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        journal.sqlite3, "connect",
        lambda *args, **kwargs: connect(*args, **kwargs, factory=TracedConnection),
    )
    metadata = store.entries()
    detail = store.detail(attempt_id)
    assert "private" not in json.dumps([metadata, detail])
    assert "input" not in detail and "prompt" not in metadata[0]
    assert metadata[0]["attempt_id"] == attempt_id
    assert detail["events"][0]["phase"] == "confirmed"
    assert not any("SELECT inputs FROM" in statement for statement in statements)
    assert store.detail(attempt_id, include_input=True)["input"] == {
        "prompt": prompt, "options": {"no_mux": True, "ahp": False},
    }
    assert sum("SELECT inputs FROM" in statement for statement in statements) == 1


def test_same_identity_is_idempotent_only_for_identical_unexpired_intent(tmp_path):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    record(store, attempt_id=attempt_id)
    assert len(store.entries()) == 1
    assert len(store.detail(attempt_id)["events"]) == 1
    for change in ({"prompt": "changed"}, {"options": {}}, {"action": "resume"}):
        with pytest.raises(journal.LaunchAttemptError, match="reused"):
            record(store, attempt_id=attempt_id, **change)
    assert len(store.entries()) == 1


def test_unicode_multiline_input_and_option_text_round_trip_without_normalization(tmp_path):
    store = journal.LaunchAttempts(tmp_path)
    prompt = "  r\u00e9sum\u00e9 \u4e2d\u6587 \U0001f9ea e\u0301\n'quoted' $literal; & text\n"
    options = {"title": "\u03b4 title\nsecond line", "no_mux": False}
    attempt_id = record(store, prompt=prompt, options=options)
    assert store.detail(attempt_id, include_input=True)["input"] == {
        "prompt": prompt, "options": options,
    }


@pytest.mark.parametrize("locality", [True, False, None])
def test_locality_is_an_explicit_boolean_or_unknown_in_v2(tmp_path, locality):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store, is_local=locality)
    assert store.entries()[0]["is_local"] is locality
    assert store.detail(attempt_id)["is_local"] is locality
    assert int.from_bytes(store.path.read_bytes()[60:64], "big") == 2
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT is_local FROM attempts").fetchone()[0] == (
            -1 if locality is None else int(locality)
        )
    record(store, attempt_id=attempt_id, is_local=locality)
    with pytest.raises(journal.LaunchAttemptError, match="reused"):
        record(store, attempt_id=attempt_id, is_local=not locality)


@pytest.mark.parametrize("locality", [-1, 0, 1, "unknown", [], {}])
def test_locality_api_refuses_non_boolean_values_without_publishing(tmp_path, locality):
    store = journal.LaunchAttempts(tmp_path)
    with pytest.raises(journal.LaunchAttemptError):
        record(store, is_local=locality)
    assert not store.path.exists()


def legacy_store(tmp_path):
    """Build the exact released V1 shape, independently of the current writer."""
    store = journal.LaunchAttempts(tmp_path)
    store.path.parent.mkdir()
    journal.protect(store.path.parent, directory=True)
    attempt_id = uuid4().hex
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "CREATE TABLE attempts (attempt_id TEXT PRIMARY KEY, submitted_at REAL NOT NULL, "
            "action TEXT NOT NULL, project TEXT NOT NULL, surface TEXT NOT NULL, "
            "machine TEXT, environment TEXT, provider TEXT, venue_id TEXT, "
            "is_local INTEGER NOT NULL, worktree_id TEXT, inputs TEXT)"
        )
        connection.execute(
            "CREATE TABLE events (sequence INTEGER PRIMARY KEY, attempt_id TEXT NOT NULL "
            "REFERENCES attempts(attempt_id), observed_at REAL NOT NULL, "
            "owner TEXT NOT NULL, phase TEXT NOT NULL, details TEXT NOT NULL)"
        )
        connection.execute("CREATE INDEX attempts_time ON attempts(submitted_at)")
        connection.execute("CREATE INDEX events_attempt ON events(attempt_id, sequence)")
        connection.execute(f"PRAGMA application_id={journal._APPLICATION_ID}")
        connection.execute("PRAGMA user_version=1")
        connection.execute(
            "INSERT INTO attempts VALUES (?, ?, 'new', 'example', 'new-dialog', "
            "NULL, NULL, NULL, NULL, 1, NULL, ?)",
            (attempt_id, journal.time.time(), json.dumps({"prompt": "exact legacy\n", "options": {}})),
        )
        connection.execute(
            "INSERT INTO events VALUES (1, ?, ?, 'manager', 'confirmed', '{}')",
            (attempt_id, journal.time.time()),
        )
    journal.protect(store.path)
    return store, attempt_id


def test_v1_reads_preserve_bytes_version_and_input_without_migration(tmp_path):
    store, attempt_id = legacy_store(tmp_path)
    before = store.path.read_bytes()
    assert store.entries()[0]["is_local"] is True
    assert store.detail(attempt_id, include_input=True)["input"]["prompt"] == "exact legacy\n"
    assert store.path.read_bytes() == before
    assert int.from_bytes(before[60:64], "big") == 1


@pytest.mark.parametrize("operation", ["record", "append", "expire"])
def test_first_v1_write_upgrades_without_replacing_prior_evidence(tmp_path, operation):
    store, attempt_id = legacy_store(tmp_path)
    original = store.detail(attempt_id, include_input=True)
    if operation == "record":
        added = record(store, is_local=None)
        assert store.detail(added)["is_local"] is None
    elif operation == "append":
        store.append(attempt_id, owner="manager", phase="unknown")
    else:
        assert store.expire() == 0
    assert int.from_bytes(store.path.read_bytes()[60:64], "big") == 2
    retained = store.detail(attempt_id, include_input=True)
    events = retained.pop("events")
    assert events[:1] == original.pop("events")
    assert retained == original


@pytest.mark.parametrize("invalid", [-1, 3])
def test_corrupt_v1_locality_cannot_acquire_valid_unknown_meaning(tmp_path, invalid):
    store, attempt_id = legacy_store(tmp_path)
    with sqlite3.connect(store.path) as connection:
        connection.execute("UPDATE attempts SET is_local=?", (invalid,))
    before = store.path.read_bytes()
    for operation in (
        store.entries, lambda: store.detail(attempt_id), lambda: record(store, is_local=None),
        lambda: store.append(attempt_id, owner="manager", phase="unknown"), store.expire,
    ):
        with pytest.raises(journal.LaunchAttemptError):
            operation()
        assert store.path.read_bytes() == before


def test_v1_migration_and_first_mutation_share_one_rollback_boundary(tmp_path, monkeypatch):
    store, attempt_id = legacy_store(tmp_path)
    original = store.detail(attempt_id, include_input=True)
    connect = sqlite3.connect

    class FailedFirstMutation(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if sql.startswith("INSERT INTO events"):
                raise sqlite3.OperationalError("disk full")
            return super().execute(sql, parameters)

    with monkeypatch.context() as patch:
        patch.setattr(
            journal.sqlite3, "connect",
            lambda *args, **kwargs: connect(*args, **kwargs, factory=FailedFirstMutation),
        )
        with pytest.raises(journal.LaunchAttemptError):
            record(store, is_local=None)
    assert int.from_bytes(store.path.read_bytes()[60:64], "big") == 1
    assert store.detail(attempt_id, include_input=True) == original
    assert len(store.entries()) == 1
    assert list(store.path.parent.iterdir()) == [store.path]


def test_concurrent_v1_upgrades_preserve_original_and_all_confirmations(tmp_path):
    store, attempt_id = legacy_store(tmp_path)

    def submit(_):
        return record(journal.LaunchAttempts(tmp_path), is_local=None)

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = set(pool.map(submit, range(8)))
    assert {row["attempt_id"] for row in store.entries()} == ids | {attempt_id}
    assert store.detail(attempt_id, include_input=True)["input"]["prompt"] == "exact legacy\n"
    assert all(store.detail(ident)["is_local"] is None for ident in ids)


def test_concurrent_first_writers_publish_one_complete_database_and_all_intents(tmp_path):
    def submit(index):
        return record(journal.LaunchAttempts(tmp_path), prompt=f"private-{index}")
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = set(pool.map(submit, range(12)))
    store = journal.LaunchAttempts(tmp_path)
    assert {row["attempt_id"] for row in store.entries()} == ids
    assert all(len(store.detail(attempt_id)["events"]) == 1 for attempt_id in ids)
    assert list(store.path.parent.iterdir()) == [store.path]


def test_initial_existence_observation_can_lag_a_concurrent_publish_and_journal(tmp_path, monkeypatch):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    exists = type(store.path).exists
    stale = []
    def stale_first_observation(path):
        if path == store.path and not stale:
            stale.append(path)
            return False
        return exists(path)
    with sqlite3.connect(store.path) as writer:
        writer.execute(
            "INSERT INTO events(attempt_id, observed_at, owner, phase, details) "
            "VALUES (?, ?, 'manager', 'unknown', '{}')", (attempt_id, journal.time.time()),
        )
        assert store.path.with_name(store.path.name + "-journal").exists()
        with monkeypatch.context() as patch:
            patch.setattr(type(store.path), "exists", stale_first_observation)
            assert store._exists() is True
    assert stale == [store.path]
    assert len(store.entries()) == 1


@pytest.mark.parametrize("action", [
    "new", "resume", "open", "restore", "base", "bare-resume", "open-venue",
])
def test_empty_prompts_and_complete_options_are_still_attempts(tmp_path, action):
    store = journal.LaunchAttempts(tmp_path)
    options = {"anchor": False, "bare": False, "no_mux": True, "ahp": False, "local_model": False}
    attempt_id = record(
        store, action=action, prompt="", options=options, is_local=False,
        machine="example-host", environment="linux", worktree_id="example-worktree",
        provider="example-provider", venue_id="example-venue",
    )
    detail = store.detail(attempt_id, include_input=True)
    assert detail["input"] == {"prompt": "", "options": options}
    assert detail["is_local"] == 0
    assert detail["machine"] == "example-host" and detail["environment"] == "linux"
    assert detail["worktree_id"] == "example-worktree"
    assert detail["provider"] == "example-provider" and detail["venue_id"] == "example-venue"


def test_observations_preserve_owner_meaning_and_do_not_infer_success(tmp_path):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    store.append(attempt_id, owner="engine", phase="resolved", worktree_id="example-worktree")
    store.append(attempt_id, owner="manager", phase="launcher-returned", exit_code=0)
    detail = store.detail(attempt_id)
    assert [item["phase"] for item in detail["events"]] == [
        "confirmed", "resolved", "launcher-returned",
    ]
    assert "success" not in detail and "automatic_eligible" not in detail
    assert detail["worktree_id"] is None  # A resolved observation is not allocated metadata.
    assert detail["events"][-1]["details"] == {"exit_code": 0}


@pytest.mark.parametrize("value", [
    "d\u00e8sktop-wsl-20261021-010203-abcd",
    "de\u0301sktop-wsl-20261021-010203-abcd",
    "\u65e5\u672c-\U0001f5a5-wsl-20261021-010203-abcd",
])
def test_worktree_observation_identity_preserves_unicode_without_normalization(tmp_path, value):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    store.append(attempt_id, owner="manager", phase="resolved", worktree_id=value)
    assert store.detail(attempt_id)["events"][-1]["details"] == {"worktree_id": value}


@pytest.mark.parametrize("value", ["", "a" * 513, "line\nbreak", "tab\there", "null\0byte", "del\x7f"])
def test_worktree_observation_identity_stays_bounded_and_ascii_control_free(tmp_path, value):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    with pytest.raises(journal.LaunchAttemptError, match="observation"):
        store.append(attempt_id, owner="manager", phase="resolved", worktree_id=value)
    assert [event["phase"] for event in store.detail(attempt_id)["events"]] == ["confirmed"]


def test_large_accepted_history_does_not_hide_private_input_and_is_fully_pageable(tmp_path):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    with sqlite3.connect(store.path) as connection:
        connection.executemany(
            "INSERT INTO events(attempt_id, observed_at, owner, phase, details) "
            "VALUES (?, ?, 'manager', 'unknown', '{}')",
            [(attempt_id, journal.time.time())] * 1000,
        )
    store.append(attempt_id, owner="manager", phase="launcher-returned", exit_code=0)
    first = store.detail(attempt_id, include_input=True)
    assert first["input"]["prompt"] == "  exact 'private' text\nsecond line\n"
    assert len(first["events"]) == 1000
    assert first["events_has_more"] is True
    assert first["next_event_sequence"] == first["events"][-1]["sequence"]
    second = store.detail(attempt_id, after_sequence=first["next_event_sequence"])
    assert len(second["events"]) == 2
    assert second["events_has_more"] is False and second["next_event_sequence"] is None
    sequences = [event["sequence"] for event in first["events"] + second["events"]]
    assert sequences == sorted(set(sequences)) and len(sequences) == 1002
    assert second["events"][-1]["phase"] == "launcher-returned"


@pytest.mark.parametrize("page", [
    {"event_limit": 0}, {"event_limit": 1001}, {"event_limit": True},
    {"after_sequence": -1}, {"after_sequence": True},
])
def test_event_page_arguments_refuse_invalid_or_unbounded_reads(tmp_path, page):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    with pytest.raises(journal.LaunchAttemptError, match="detail"):
        store.detail(attempt_id, **page)


@pytest.mark.parametrize("details", [
    {"prompt": "private"}, {"stderr": "private"}, {"code": "private"},
    {"worktree_id": "private\ntext"}, {"exit_code": True},
])
def test_observation_payload_refuses_free_text_without_echoing_it(tmp_path, details):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    with pytest.raises(journal.LaunchAttemptError) as error:
        store.append(attempt_id, owner="manager", phase="refused", **details)
    assert "private" not in str(error.value)
    assert len(store.detail(attempt_id)["events"]) == 1


def test_unknown_observation_cannot_create_an_attempt(tmp_path):
    store = journal.LaunchAttempts(tmp_path)
    record(store)
    with pytest.raises(journal.LaunchAttemptError):
        store.append(uuid4().hex, owner="manager", phase="unknown")
    assert len(store.entries()) == 1


def test_wal_mode_is_refused_instead_of_retaining_a_second_prompt_copy(tmp_path):
    store = journal.LaunchAttempts(tmp_path)
    record(store)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    with pytest.raises(journal.LaunchAttemptError, match="DELETE"):
        record(store)
    with pytest.raises(journal.LaunchAttemptError, match="DELETE"):
        store.expire()


def test_refused_wal_reads_do_not_initialize_sidecars_or_change_storage(tmp_path):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    before = {path.name: path.read_bytes() for path in store.path.parent.iterdir()}
    assert set(before) == {store.path.name}
    for operation in (
        store.entries, lambda: store.detail(attempt_id),
        lambda: store.detail(attempt_id, include_input=True),
    ):
        with pytest.raises(journal.LaunchAttemptError, match="DELETE"):
            operation()
        assert {path.name: path.read_bytes() for path in store.path.parent.iterdir()} == before


@pytest.mark.skipif(os.name != "nt", reason="Native Windows inherited sidecar ACL")
def test_private_inherited_sidecar_allows_concurrent_committed_reads_without_acl_repair(
    tmp_path, monkeypatch,
):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    def no_repair(*_args, **_kwargs):
        pytest.fail("concurrent evidence reads must not repair the writer's sidecar ACL")
    with sqlite3.connect(store.path) as writer:
        writer.execute(
            "INSERT INTO events(attempt_id, observed_at, owner, phase, details) "
            "VALUES (?, ?, 'manager', 'unknown', '{}')", (attempt_id, journal.time.time()),
        )
        sidecar = store.path.with_name(store.path.name + "-journal")
        assert sidecar.exists()
        with monkeypatch.context() as patch:
            patch.setattr(journal, "protect", no_repair)
            assert store.entries()[0]["attempt_id"] == attempt_id
            assert len(store.detail(attempt_id)["events"]) == 1
            assert sidecar.exists()
    assert len(store.detail(attempt_id)["events"]) == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX read-only permission verification")
def test_unsafe_permissions_refuse_read_without_repairing_them(tmp_path):
    store = journal.LaunchAttempts(tmp_path)
    record(store)
    store.path.chmod(0o644)
    before = store.path.read_bytes()
    with pytest.raises(journal.LaunchAttemptError, match="metadata"):
        store.entries()
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o644
    assert store.path.read_bytes() == before


def test_expiry_is_based_on_confirmation_not_reads_or_later_events_and_erases_bytes(
    tmp_path, monkeypatch,
):
    store = journal.LaunchAttempts(tmp_path)
    clock = 2_000_000_000.0
    monkeypatch.setattr(journal.time, "time", lambda: clock)
    private = "private-expiry-marker-" * 200
    attempt_id = record(store, prompt=private)
    assert b"private-expiry-marker-" in store.path.read_bytes()
    clock += journal.RETENTION_SECONDS - 1
    store.append(attempt_id, owner="manager", phase="unknown", code="interrupted")
    assert store.detail(attempt_id, include_input=True)["input"]["prompt"] == private
    clock += 1
    assert store.entries()[0]["inputs_available"] == 0
    with pytest.raises(journal.LaunchAttemptError, match="expired"):
        store.detail(attempt_id, include_input=True)
    with pytest.raises(journal.LaunchAttemptError, match="expired"):
        record(store, attempt_id=attempt_id, prompt=private)
    assert store.expire() == 1 and store.expire() == 0
    assert b"private-expiry-marker-" not in store.path.read_bytes()
    assert list(store.path.parent.iterdir()) == [store.path]
    assert len(store.detail(attempt_id)["events"]) == 2
    with pytest.raises(journal.LaunchAttemptError, match="expired"):
        record(store, attempt_id=attempt_id, prompt=private)


@pytest.mark.parametrize("kind", ["corrupt", "foreign", "uninitialized", "marked-foreign", "trigger"])
def test_corrupt_or_foreign_state_is_never_adopted_or_repaired(tmp_path, kind):
    store = journal.LaunchAttempts(tmp_path)
    if kind in {"marked-foreign", "trigger"}:
        record(store)
        with sqlite3.connect(store.path) as connection:
            if kind == "marked-foreign":
                connection.execute("PRAGMA user_version=42")
            else:
                connection.execute(
                    "CREATE TRIGGER foreign_trigger AFTER INSERT ON events BEGIN SELECT 1; END"
                )
    else:
        store.path.parent.mkdir()
        if kind == "corrupt":
            store.path.write_bytes(b"not a SQLite database")
        elif kind == "uninitialized":
            store.path.touch()
        else:
            with sqlite3.connect(store.path) as connection:
                connection.execute("CREATE TABLE unrelated(value TEXT)")
    before = store.path.read_bytes()
    for operation in (store.entries, lambda: record(store), store.expire):
        with pytest.raises(journal.LaunchAttemptError):
            operation()
        assert store.path.read_bytes() == before


@pytest.mark.parametrize("statement", [
    "CREATE TABLE sqlitex_hidden(value TEXT)",
    "CREATE VIEW sqlitex_hidden AS SELECT 'example' AS value",
    "CREATE INDEX sqlitex_hidden ON attempts(project)",
    "CREATE TRIGGER sqlitex_hidden BEFORE UPDATE ON attempts BEGIN SELECT 1; END",
])
def test_schema_rejects_sqlite_prefix_lookalike_objects(tmp_path, statement):
    store = journal.LaunchAttempts(tmp_path)
    record(store)
    with sqlite3.connect(store.path) as connection:
        connection.execute(statement)
    before = store.path.read_bytes()
    for operation in (store.entries, lambda: record(store), store.expire):
        with pytest.raises(journal.LaunchAttemptError, match="schema"):
            operation()
        assert store.path.read_bytes() == before


def test_lookalike_archive_trigger_cannot_copy_expiring_private_input(tmp_path, monkeypatch):
    store = journal.LaunchAttempts(tmp_path)
    clock = 2_000_000_000.0
    monkeypatch.setattr(journal.time, "time", lambda: clock)
    attempt_id = record(store)
    with sqlite3.connect(store.path) as connection:
        connection.execute("CREATE TABLE sqlitex_archive(value TEXT)")
        connection.execute(
            "CREATE TRIGGER sqlitex_keep AFTER UPDATE OF inputs ON attempts "
            "BEGIN INSERT INTO sqlitex_archive VALUES (OLD.inputs); END"
        )
    clock += journal.RETENTION_SECONDS
    with pytest.raises(journal.LaunchAttemptError, match="schema"):
        store.expire()
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM sqlitex_archive").fetchone()[0] == 0
        assert connection.execute(
            "SELECT inputs IS NOT NULL FROM attempts WHERE attempt_id=?", (attempt_id,),
        ).fetchone()[0] == 1


def test_trigger_cannot_hide_behind_a_recreated_index_with_the_same_name(tmp_path, monkeypatch):
    store = journal.LaunchAttempts(tmp_path)
    clock = 2_000_000_000.0
    monkeypatch.setattr(journal.time, "time", lambda: clock)
    attempt_id = record(store)
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP INDEX attempts_time")
        connection.execute(
            "CREATE TRIGGER attempts_time AFTER UPDATE OF inputs ON attempts BEGIN "
            "INSERT INTO events(attempt_id, observed_at, owner, phase, details) "
            "VALUES (OLD.attempt_id, 1, 'manager', 'unknown', OLD.inputs); END"
        )
        connection.execute("CREATE INDEX attempts_time ON attempts(submitted_at)")
    clock += journal.RETENTION_SECONDS
    with pytest.raises(journal.LaunchAttemptError, match="schema"):
        store.expire()
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert connection.execute(
            "SELECT inputs IS NOT NULL FROM attempts WHERE attempt_id=?", (attempt_id,),
        ).fetchone()[0] == 1


@pytest.mark.parametrize("statement,value", [
    ("UPDATE attempts SET action=?", "unexpected"),
    ("UPDATE attempts SET is_local=?", 3),
    ("UPDATE attempts SET inputs=?", "{}"),
    ("UPDATE events SET phase=?", "success-from-zero-exit"),
    ("UPDATE events SET details=?", '{"prompt":"must-not-leak"}'),
    ("UPDATE events SET observed_at=?", "invalid"),
])
def test_malformed_records_are_not_success_shaped_detail(tmp_path, statement, value):
    store = journal.LaunchAttempts(tmp_path)
    attempt_id = record(store)
    with sqlite3.connect(store.path) as connection:
        connection.execute(statement, (value,))
    with pytest.raises(journal.LaunchAttemptError):
        store.detail(attempt_id, include_input=True)


def test_permission_failure_happens_before_database_publication(tmp_path, monkeypatch):
    store = journal.LaunchAttempts(tmp_path)
    def denied(_path, **_kwargs):
        raise PermissionError("denied")
    monkeypatch.setattr(journal, "protect", denied)
    with pytest.raises(journal.LaunchAttemptError, match="not been authorized"):
        record(store)
    assert not store.path.exists()


def test_intent_and_first_event_are_one_rollback_boundary(tmp_path, monkeypatch):
    store = journal.LaunchAttempts(tmp_path)
    connect = sqlite3.connect
    class InterruptedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=(), /):
            if sql.startswith("INSERT INTO events"):
                raise sqlite3.OperationalError("disk full")
            return super().execute(sql, parameters)
    with monkeypatch.context() as patch:
        patch.setattr(
            journal.sqlite3, "connect",
            lambda *args, **kwargs: connect(*args, **kwargs, factory=InterruptedConnection),
        )
        with pytest.raises(journal.LaunchAttemptError):
            record(store)
    assert store.entries() == []
    assert list(store.path.parent.iterdir()) == [store.path]


def test_lock_contention_refuses_without_losing_the_prior_attempt(tmp_path, monkeypatch):
    store = journal.LaunchAttempts(tmp_path)
    original = record(store)
    connect = sqlite3.connect
    observed = []
    def short_contention_probe(*args, **kwargs):
        assert kwargs["timeout"] == 5
        observed.append(kwargs["timeout"])
        return connect(*args, **{**kwargs, "timeout": 0.01})
    with sqlite3.connect(store.path) as lock:
        lock.execute("BEGIN IMMEDIATE")
        with monkeypatch.context() as patch:
            patch.setattr(journal.sqlite3, "connect", short_contention_probe)
            with pytest.raises(journal.LaunchAttemptError, match="not been authorized"):
                record(store)
    assert observed == [5]
    assert [row["attempt_id"] for row in store.entries()] == [original]


@pytest.mark.parametrize("suffix", ["", "-journal", "-wal", "-shm"])
def test_links_and_orphan_sidecars_are_explicit_not_empty(tmp_path, suffix):
    store = journal.LaunchAttempts(tmp_path)
    store.path.parent.mkdir()
    path = store.path.with_name(store.path.name + suffix)
    if suffix:
        path.write_bytes(b"orphan")
    else:
        try:
            path.symlink_to(tmp_path / "unrelated")
        except OSError as exc:
            if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                pytest.skip("Windows symlink privilege unavailable")
            raise
    with pytest.raises(journal.LaunchAttemptError):
        store.entries()
    with pytest.raises(journal.LaunchAttemptError):
        record(store)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="POSIX FIFO file type")
def test_special_database_file_is_refused_before_any_header_open(tmp_path, monkeypatch):
    store = journal.LaunchAttempts(tmp_path)
    store.path.parent.mkdir(mode=0o700)
    os.mkfifo(store.path, 0o600)
    attempt_id = uuid4().hex
    opened = []
    original_open = type(store.path).open
    def no_fifo_open(path, *args, **kwargs):
        if path == store.path:
            opened.append(path)
            pytest.fail("a special database file must be refused before opening its header")
        return original_open(path, *args, **kwargs)
    monkeypatch.setattr(type(store.path), "open", no_fifo_open)
    for operation in (
        store.entries, lambda: store.detail(attempt_id), store.expire,
        lambda: store.append(attempt_id, owner="manager", phase="unknown"),
        lambda: record(store, attempt_id=attempt_id),
    ):
        with pytest.raises(journal.LaunchAttemptError):
            operation()
    assert opened == [] and stat.S_ISFIFO(store.path.lstat().st_mode)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions; Windows uses the shared ACL helper")
def test_private_database_and_directory_have_owner_only_modes(tmp_path):
    store = journal.LaunchAttempts(tmp_path)
    record(store)
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_metadata_filters_and_limits_are_applied_in_sql(tmp_path, monkeypatch):
    store = journal.LaunchAttempts(tmp_path)
    clock = 2_000_000_000.0
    monkeypatch.setattr(journal.time, "time", lambda: clock)
    original = record(store)
    clock += 10
    record(store, project="other")
    clock += 10
    record(store, action="resume")
    assert store.entries(project="example", action="new")[0]["attempt_id"] == original
    assert store.entries(before=clock - 10)[0]["attempt_id"] == original
    assert len(store.entries(limit=1)) == 1
    for limit in (0, 1001, True):
        with pytest.raises(journal.LaunchAttemptError):
            store.entries(limit=limit)
