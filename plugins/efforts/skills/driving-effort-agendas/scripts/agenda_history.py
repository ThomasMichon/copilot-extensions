"""Bounded read-only pages of accepted context-qualified agenda operations."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
from typing import Any

from agenda import _time, decode
from agenda_operations import fields
import agenda_history_contract as wire
import agenda_store as store
from agenda_v2_schema import record as validate_record


def _header(
    db: sqlite3.Connection, worktree: str, expected_store_id: str | None,
) -> tuple[wire.Source, int, str]:
    row = db.execute(
        "SELECT store_id,revision,committed_at,length(CAST(documents AS BLOB)) FROM state WHERE singleton=1"
    ).fetchone()
    if row is None:
        raise wire.HistoryError("agenda store has no accepted initialization", "unavailable")
    if type(row[3]) is not int or not 1 <= row[3] <= 1048576:
        raise wire.HistoryError("current agenda documents exceed their bound", "invalid-store")
    raw = db.execute("SELECT documents FROM state WHERE singleton=1").fetchone()[0]
    if not isinstance(raw, str):
        raise wire.HistoryError("current agenda documents are not text", "invalid-store")
    documents = decode(raw.encode("utf-8"), "current agenda documents", limit=1048576)
    fields(documents, {"cursor", "boards"})
    cursor = documents["cursor"]
    if not isinstance(cursor, dict) or not isinstance(documents["boards"], dict):
        raise wire.HistoryError("current agenda cursor is invalid", "invalid-store")
    source = wire.Source(wire.text(cursor.get("worktree"), "stored worktree"),
                         wire.store_id(row[0]), db.execute("PRAGMA user_version").fetchone()[0])
    if source.worktree != worktree:
        raise wire.HistoryError("agenda locator belongs to another worktree", "source-replaced")
    if expected_store_id is not None and source.store_id != expected_store_id:
        raise wire.HistoryError("agenda locator store was replaced", "source-replaced")
    revision = store._revision(row[1], "current agenda revision")
    if revision < 1 or type(cursor.get("revision")) is not int or cursor["revision"] != revision:
        raise wire.HistoryError("agenda state and cursor revision disagree", "invalid-store")
    supported_cursor = (4,) if source.store_version == 2 else (1, 2)
    if type(cursor.get("version")) is not int or cursor["version"] not in supported_cursor or (
        source.store_version == 2 and cursor.get("mode") != "context-qualified"
    ):
        raise wire.HistoryError("agenda store and execution policy disagree", "invalid-store")
    _time(row[2], "accepted commit time")
    if source.store_version == 2:
        latest = db.execute("SELECT operation_id FROM operations WHERE revision=?", (revision,)).fetchone()
        if latest is None or _result(db, source, latest[0], revision)["committed_at"] != row[2]:
            raise wire.HistoryError("current agenda header has no matching acceptance", "invalid-store")
    return source, revision, row[2]


def reader_info(
    home: Path, worktree: str, *, expected_store_id: str | None = None,
    contract_version: int = 1,
) -> dict[str, Any]:
    """Describe actual read support for one explicitly selected existing store."""
    wire.version(contract_version)
    wire.text(worktree, "selected worktree")
    if expected_store_id is not None:
        wire.store_id(expected_store_id)
    with store.transaction(home, readonly=True) as db:
        source, head, committed_at = _header(db, worktree, expected_store_id)
        supported = source.store_version == 2
        result = {
            "schema": "efforts.agenda-reader-info", "contract_version": wire.VERSION,
            "source": source.body(), "cursor": wire.position(source, head),
            "revision": head, "committed_at": committed_at,
            "capabilities": [wire.CAPABILITY] if supported else [],
            "availability": "supported" if supported else "observed-only",
            "history_scope": "accepted-operations",
            "reason": None if supported else "legacy store has no context-qualified rich records",
        }
        wire.encoded(result)
        return result


def _result(db: sqlite3.Connection, source: wire.Source, operation: str, revision: int) -> dict:
    wire.text(operation, "accepted operation identity")
    row = db.execute(
        "SELECT result FROM operations WHERE operation_id=? AND revision=?", (operation, revision),
    ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise wire.HistoryError("accepted agenda operation disappeared", "invalid-store")
    result = decode(row[0].encode("utf-8"), "accepted agenda operation")
    fields(result, {"schema", "version", "mode", "persisted", "store_id", "revision",
                    "operation_id", "operation", "source", "committed_at", "actor",
                    "transition", "record_ids", "records", "delivery"})
    if (
        result["schema"] != "efforts.agenda-result" or type(result["version"]) is not int
        or result["version"] != 2 or result["mode"] != "context-qualified"
        or result["persisted"] is not True or result["store_id"] != source.store_id
        or type(result["revision"]) is not int or result["revision"] != revision
        or result["operation_id"] != operation
    ):
        raise wire.HistoryError("accepted agenda result changed identity", "invalid-store")
    fields(result["source"], {"worktree", "store_id", "session"})
    fields(result["actor"], {"actor", "session", "qualification"})
    if (
        result["source"]["worktree"] != source.worktree
        or result["source"]["store_id"] != source.store_id
        or result["source"]["session"] != result["actor"]["session"]
        or result["actor"]["qualification"] not in {"caller-asserted", "unknown"}
    ):
        raise wire.HistoryError("accepted operation changed its source", "invalid-store")
    for key in ("actor", "session"):
        if result["actor"][key] is not None:
            wire.text(result["actor"][key], "asserted " + key)
    wire.text(result["operation"], "native agenda operation")
    _time(result["committed_at"], "accepted operation time")
    if (
        not isinstance(result["records"], list) or not isinstance(result["record_ids"], list)
        or any(not isinstance(item, dict) for item in result["records"])
        or any(not isinstance(identity, str) for identity in result["record_ids"])
        or not isinstance(result["transition"], dict) or not isinstance(result["delivery"], dict)
    ):
        raise wire.HistoryError("accepted rich records are invalid", "invalid-store")
    if result["record_ids"] != [item.get("id") for item in result["records"]]:
        raise wire.HistoryError("accepted record identities disagree", "invalid-store")
    if len(result["record_ids"]) != len(set(result["record_ids"])):
        raise wire.HistoryError("accepted record identities repeat", "invalid-store")
    for item in result["records"]:
        wire.text(item.get("id"), "immutable agenda record identity")
        validate_record({item["id"]: item}, item["id"])
        if item["actor"] != result["actor"] or item["created_at"] != result["committed_at"]:
            raise wire.HistoryError("accepted record metadata changed its commit", "invalid-store")
    return result


def _entry(source: wire.Source, revision: int, result: dict[str, Any]) -> dict[str, Any]:
    raw = store.encode(result).encode("utf-8")
    identity = hashlib.sha256(
        store.encode([source.body(), result["operation_id"]]).encode("utf-8")
    ).hexdigest()
    omitted = len(raw) > wire.BODY_BYTES - 8192
    reference = {"kind": "accepted-operation", "store_id": source.store_id,
                 "store_version": source.store_version, "revision": revision,
                 "operation_id": result["operation_id"]}
    return {
        "event_id": identity, "cursor": wire.position(source, revision),
        "evidence_kind": "retained-event", "kind": "agenda-transition",
        "operation": result["operation"], "operation_id": result["operation_id"],
        "committed_at": result["committed_at"], "actor": result["actor"],
        "result": None if omitted else result,
        "omission": {"utf8_bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest(),
                     "payload_ref": reference} if omitted else None,
    }


def history_page(
    home: Path, worktree: str, expected_store_id: str, *,
    before_cursor: str | None = None, limit: int = 64, contract_version: int = 1,
) -> dict[str, Any]:
    """Read an immutable accepted-operation range without consuming delivery state."""
    wire.version(contract_version)
    wire.text(worktree, "selected worktree")
    wire.store_id(expected_store_id)
    if type(limit) is not int or not 1 <= limit <= wire.PAGE_ENTRIES:
        raise wire.HistoryError("agenda history limit must be from 1 through 64")
    with store.transaction(home, readonly=True) as db:
        source, head, committed_at = _header(db, worktree, expected_store_id)
        if source.store_version != 2:
            raise wire.HistoryError("rich history requires a context-qualified store", "unsupported")
        before = None if before_cursor is None else wire.revision(before_cursor, source)
        if before is not None and before > head + 1:
            raise wire.HistoryError("agenda cursor is ahead of the current store", "cursor-unavailable")
        common = {"history_scope": "accepted-operations", "completeness": "partial",
                  "committed_at": committed_at, "history_floor": wire.position(source, 0)}
        if before == 0:
            return wire.frame("gap", source, head, {
                **common, "base_cursor": before_cursor, "reason": "retention",
                "reconcile": "reader-info-and-history",
                "details": "No accepted agenda operation precedes store initialization.",
            })
        rows = db.execute(
            "SELECT operation_id,revision,length(CAST(result AS BLOB)) FROM operations "
            "WHERE (? IS NULL OR revision<?) ORDER BY revision DESC LIMIT ?",
            (before, before, limit + 1),
        ).fetchall()
        entries = []
        expected = head if before is None else before - 1
        for operation, revision, size in rows[:limit]:
            if type(revision) is not int or revision != expected:
                raise wire.HistoryError("accepted agenda revision continuity is broken", "invalid-store")
            if type(size) is not int or not 1 <= size <= 131072:
                raise wire.HistoryError("accepted operation exceeds its stored bound", "invalid-store")
            value = _entry(source, revision, _result(db, source, operation, revision))
            candidate = {**common, "before_cursor": before_cursor, "entries": [*reversed(entries + [value])],
                         "has_more": revision > 1, "next_before": wire.position(source, revision) if revision > 1 else None}
            try:
                wire.encoded(candidate, wire.BODY_BYTES)
            except wire.HistoryError as error:
                if error.code != "oversized" or not entries:
                    raise
                break
            entries.append(value)
            expected -= 1
        if not entries and expected > 0:
            raise wire.HistoryError("accepted agenda history is missing", "invalid-store")
        more = bool(entries and wire.revision(entries[-1]["cursor"], source) > 1)
        return wire.frame("history-page", source, head, {
            **common, "before_cursor": before_cursor, "entries": list(reversed(entries)),
            "has_more": more, "next_before": entries[-1]["cursor"] if more else None,
        })
