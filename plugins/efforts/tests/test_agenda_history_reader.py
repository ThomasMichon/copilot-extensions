"""Passive rich owner pages over actual accepted SQLite operations."""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
import hashlib
import importlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest
from test_agenda_store import documents as documents, home as home, proof
from test_agenda_v2_core import SKILL, STORE, current, lane as lane, update

HISTORY = importlib.import_module("agenda_history")
WIRE = importlib.import_module("agenda_history_contract")


def info(lane: Path):
    return HISTORY.reader_info(lane, "fixture-lane")


def wait(lane: Path, detail="Await explicit evidence"):
    frame, item, _attempt = current(lane)
    return update(lane, {
        "operation": "wait", "frame_id": frame, "item_id": item,
        "reason_kind": "external", "reason_ref": "fixture-event",
        "next_action": detail, "evidence": proof("waiting evidence"),
    })


def page(lane: Path, **kwargs):
    return HISTORY.history_page(lane, "fixture-lane", info(lane)["source"]["store_id"], **kwargs)


def bytes_in(home: Path):
    return {path.relative_to(home).as_posix(): path.read_bytes()
            for path in home.rglob("*") if path.is_file()}


def test_actual_accepted_records_and_asserted_actor_are_retained_without_state_writes(lane):
    accepted = wait(lane)
    before = bytes_in(lane)
    selected = info(lane)
    result = page(lane)
    assert selected["capabilities"] == [WIRE.CAPABILITY]
    assert selected["source"]["store_version"] == 2
    assert result["type"] == "history-page" and result["completeness"] == "partial"
    assert len(result["entries"]) == 2 and not result["has_more"]
    entry = result["entries"][-1]
    assert entry["kind"] == "agenda-transition" and entry["evidence_kind"] == "retained-event"
    assert entry["result"] == accepted and entry["omission"] is None
    assert entry["actor"]["qualification"] == "caller-asserted"
    assert entry["actor"]["session"] == "fixture-head"
    assert entry["result"]["records"][0]["body"]["next_action"] == "Await explicit evidence"
    assert bytes_in(lane) == before


def test_long_history_pages_are_byte_bounded_contiguous_and_do_not_scan_all_records(lane, monkeypatch):
    for index in range(70):
        wait(lane, f"wait {index}")
    statements = []
    native = STORE.transaction

    @contextmanager
    def transaction(*args, **kwargs):
        with native(*args, **kwargs) as db:
            db.set_trace_callback(statements.append)
            yield db

    monkeypatch.setattr(STORE, "transaction", transaction)
    monkeypatch.setattr(STORE, "_state", lambda *args: pytest.fail("reader loaded the full record graph"))
    selected = info(lane)
    source = WIRE.Source(**{key: selected["source"][key] for key in ("worktree", "store_id", "store_version")})
    before, found = None, []
    while True:
        result = HISTORY.history_page(lane, "fixture-lane", source.store_id,
                                      before_cursor=before, limit=64)
        assert len(WIRE.encoded(result)) <= WIRE.FRAME_BYTES
        body = {key: value for key, value in result.items()
                if key not in {"schema", "contract_version", "type", "source", "cursor"}}
        assert len(WIRE.encoded(body)) <= WIRE.BODY_BYTES
        assert len(result["entries"]) <= 64
        revisions = [WIRE.revision(item["cursor"], source) for item in result["entries"]]
        assert revisions == sorted(revisions)
        assert not before or all(value < WIRE.revision(before, source) for value in revisions)
        found.extend(revisions)
        if not result["has_more"]:
            break
        assert result["next_before"] == result["entries"][0]["cursor"]
        before = result["next_before"]
    assert sorted(found) == list(range(1, 72)) and len(found) == len(set(found))
    assert not any("FROM agenda_records" in statement for statement in statements)
    selects = [statement for statement in statements if "ORDER BY revision DESC" in statement]
    assert selects and all("LIMIT 65" in statement for statement in selects)


def test_large_accepted_operation_keeps_integrity_and_a_typed_owner_payload_reference(lane):
    accepted = wait(lane, "\u6f22" * 10000)
    result = page(lane, limit=1)
    entry = result["entries"][0]
    assert entry["result"] is None and entry["omission"] is not None
    raw = STORE.encode(accepted).encode("utf-8")
    assert entry["omission"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert entry["omission"]["utf8_bytes"] == len(raw)
    ref = entry["omission"]["payload_ref"]
    assert ref["kind"] == "accepted-operation" and ref["operation_id"] == accepted["operation_id"]
    assert STORE.history(lane, 1, ref["revision"] + 1)[0] == accepted
    assert result["has_more"] and len(WIRE.encoded(result)) <= WIRE.FRAME_BYTES


@pytest.mark.parametrize("fault", ["worktree", "store", "cursor-worktree", "cursor-store", "future"])
def test_reader_selection_and_before_position_are_source_qualified(lane, fault):
    selected = info(lane)
    source = WIRE.Source("fixture-lane", selected["source"]["store_id"], 2)
    args = {"home": lane, "worktree": source.worktree, "expected_store_id": source.store_id}
    if fault == "worktree":
        args["worktree"] = "another-worktree"
    elif fault == "store":
        args["expected_store_id"] = "a" * 32 if source.store_id != "a" * 32 else "b" * 32
    elif fault == "cursor-worktree":
        args["before_cursor"] = WIRE.position(replace(source, worktree="another-worktree"), 1)
    elif fault == "cursor-store":
        args["before_cursor"] = WIRE.position(replace(source, store_id="d" * 32), 1)
    else:
        args["before_cursor"] = WIRE.position(source, 5)
    with pytest.raises(WIRE.HistoryError) as error:
        HISTORY.history_page(**args)
    assert error.value.code in {"source-replaced", "cursor-unavailable"}


@pytest.mark.parametrize("limit", [True, False, 0, -1, 65, 1.5, None])
def test_page_limit_rejects_noninteger_and_out_of_contract_values(lane, limit):
    with pytest.raises(WIRE.HistoryError):
        page(lane, limit=limit)


@pytest.mark.parametrize("version", [True, False, None, "1", 2])
def test_exact_contract_version_is_not_a_boolean_or_silent_fallback(lane, version):
    with pytest.raises(WIRE.HistoryError):
        HISTORY.reader_info(lane, "fixture-lane", contract_version=version)


def test_retention_boundary_is_an_explicit_source_gap(lane):
    selected = info(lane)
    source = WIRE.Source("fixture-lane", selected["source"]["store_id"], 2)
    before = WIRE.position(source, 0)
    result = page(lane, before_cursor=before)
    assert result["type"] == "gap" and result["reason"] == "retention"
    assert result["base_cursor"] == result["history_floor"] == before
    assert result["reconcile"] == "reader-info-and-history"


def test_legacy_store_is_observed_only_and_is_not_silently_migrated(home):
    before = bytes_in(home)
    selected = HISTORY.reader_info(home, "fixture-lane")
    assert selected["capabilities"] == [] and selected["availability"] == "observed-only"
    with pytest.raises(WIRE.HistoryError) as error:
        HISTORY.history_page(home, "fixture-lane", selected["source"]["store_id"])
    assert error.value.code == "unsupported" and bytes_in(home) == before


@pytest.mark.parametrize("fault", ["boards", "cursor-version", "header-time", "documents-blob"])
def test_reader_info_does_not_advertise_a_malformed_or_unaccepted_current_header(lane, fault):
    with sqlite3.connect(lane / STORE.DATABASE) as db:
        if fault == "header-time":
            db.execute("UPDATE state SET committed_at='2001-01-01T00:00:00Z'")
        elif fault == "documents-blob":
            raw = db.execute("SELECT documents FROM state").fetchone()[0]
            db.execute("UPDATE state SET documents=?", (sqlite3.Binary(raw.encode()),))
        else:
            documents = json.loads(db.execute("SELECT documents FROM state").fetchone()[0])
            if fault == "boards":
                documents["boards"] = []
            else:
                documents["cursor"]["version"] = True
            db.execute("UPDATE state SET documents=?", (json.dumps(documents),))
    with pytest.raises(WIRE.HistoryError) as error:
        info(lane)
    assert error.value.code == "invalid-store"


@pytest.mark.parametrize("fault", [
    "missing-operation", "wrong-result", "bad-record-list", "bad-version", "record-actor", "record-time",
])
def test_corrupt_selected_store_is_not_an_empty_or_partial_success(lane, fault):
    accepted = wait(lane)
    with sqlite3.connect(lane / STORE.DATABASE) as db:
        if fault == "missing-operation":
            db.execute("DELETE FROM operations WHERE revision=2")
        else:
            value = deepcopy(accepted)
            if fault == "wrong-result":
                value["source"]["worktree"] = "foreign"
            elif fault == "bad-record-list":
                value["records"] = [42]
                value["record_ids"] = []
            elif fault == "bad-version":
                value["version"] = True
            elif fault == "record-actor":
                value["records"][0]["actor"]["qualification"] = "authoritative"
            else:
                value["records"][0]["created_at"] = "2001-01-01T00:00:00Z"
            db.execute("UPDATE operations SET result=? WHERE revision=2", (json.dumps(value),))
    with pytest.raises(AGENDA_ERROR):
        page(lane)


AGENDA_ERROR = importlib.import_module("agenda").AgendaError


def test_readonly_transaction_cannot_mutate_or_create_a_store(lane, tmp_path):
    before = bytes_in(lane)
    with pytest.raises(AGENDA_ERROR, match="read.?only"):
        with STORE.transaction(lane, readonly=True) as db:
            db.execute("CREATE TABLE forbidden (value)")
    assert bytes_in(lane) == before
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(AGENDA_ERROR):
        HISTORY.reader_info(empty, "fixture-lane")
    assert list(empty.iterdir()) == []


@pytest.mark.parametrize("command", ["reader-info", "history-page"])
def test_actual_owner_cli_is_passive_and_matches_the_selected_store(lane, command):
    selected = info(lane)
    before = bytes_in(lane)
    argv = [sys.executable, "-B", str(SKILL / "scripts" / "agenda_cli.py"),
            command, "--state-home", str(lane), "--worktree", "fixture-lane",
            "--store-id", selected["source"]["store_id"], "--contract-version", "1", "--json"]
    result = subprocess.run(
        argv, capture_output=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode == 0, result.stderr
    output = WIRE.encoded(json.loads(result.stdout))
    assert len(output) <= WIRE.FRAME_BYTES and result.stderr == b""
    assert json.loads(result.stdout)["source"] == selected["source"]
    assert bytes_in(lane) == before


@pytest.mark.parametrize(("extra", "code"), [
    ([], "invalid-request"),
    (["--store-id", "a" * 32, "--contract-version", "2"], "unsupported"),
    (["--store-id", "not-a-store"], "invalid-request"),
])
def test_actual_history_cli_failure_is_bounded_structured_and_write_free(lane, extra, code):
    before = bytes_in(lane)
    result = subprocess.run(
        [sys.executable, "-B", str(SKILL / "scripts" / "agenda_cli.py"),
         "history-page", "--state-home", str(lane), "--worktree", "fixture-lane", *extra],
        capture_output=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    assert result.returncode == 2 and result.stderr == b""
    failure = json.loads(result.stdout)
    assert failure["type"] == "error" and failure["error"]["code"] == code
    assert len(result.stdout) <= WIRE.FRAME_BYTES and bytes_in(lane) == before
