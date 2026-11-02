"""Fresh-only store-v2 acceptance with immutable records and the shared barrier."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from agenda import AgendaError, _number, _object, _text, decode
from agenda_operations import fields
from agenda_context import context_body
from agenda_queue import QueueState, init_queue
from agenda_v2_operations import apply
from agenda_v2_schema import MODE, board, project, validate, CAPS


def schema(db: sqlite3.Connection) -> None:
    import agenda_store as legacy

    legacy._schema(db)
    db.execute("CREATE TABLE IF NOT EXISTS agenda_records (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
    db.execute("PRAGMA user_version=2")


def state(db: sqlite3.Connection, home: Path) -> dict[str, Any]:
    import agenda_store as common

    row = db.execute("SELECT store_id, revision, committed_at, documents, metadata FROM state").fetchone()
    if row is None:
        raise AgendaError("context-qualified store has no accepted initialization")
    documents = decode(row[3].encode(), "stored documents", limit=1048576)
    records = {identity: decode(body.encode(), "immutable record")
               for identity, body in db.execute("SELECT id, body FROM agenda_records")}
    validate(documents, records)
    common._revision(documents["cursor"]["revision"], "current revision")
    if documents["cursor"]["revision"] != row[1]:
        raise AgendaError("stored cursor and commit revision disagree")
    for name in documents["boards"]:
        common.board_name(home, name)
    delivery = db.execute("SELECT status FROM delivery").fetchone()
    return {"store_id": row[0], "revision": row[1], "committed_at": row[2],
        "documents": documents, "metadata": decode(row[4].encode(), limit=262144),
        "records": records, "snapshot": asdict(project(documents, records)),
        "delivery": decode(delivery[0].encode()) if delivery else None}


def mutate(
    home: Path, request: dict[str, Any], *, session: str | None = None,
    actor: str | None = None, publisher_configured: bool = False,
) -> tuple[dict[str, Any], bool]:
    import agenda_store as common

    home = common.safe_path(home, directory=True)
    fields(request, {"schema", "version", "mode", "expected_revision", "operation_id", "change"})
    if request["schema"] != common.SCHEMA or type(request["version"]) is not int or request["version"] != 2:
        raise AgendaError("unsupported context-qualified request schema/version")
    if request["mode"] != MODE:
        raise AgendaError("request v2 requires mode=context-qualified")
    expected = common._revision(request["expected_revision"], "expected revision")
    identity = _text(request["operation_id"], "operation identity")
    if len(identity.encode()) > 1024:
        raise AgendaError("operation identity exceeds 1 KiB")
    for value in (actor, session):
        if value is not None and len(_text(value, "caller metadata").encode()) > 1024:
            raise AgendaError("caller metadata exceeds 1 KiB")
    asserted = {"actor": actor, "session": session,
                "qualification": "caller-asserted" if actor or session else "unknown"}
    common.decode(common.encode(request).encode(), "request")
    canonical = common.encode({"request": request, "identity": asserted})
    change = _object(request["change"], "semantic change")
    _text(change.get("operation"), "semantic operation")
    initializing = change["operation"] == "init"
    if initializing:
        fields(change, {"operation", "worktree", "board", "baseline_context", "resource_caps"})
        if expected != 0:
            raise AgendaError("new-mode init requires expected revision 0")
        for filename in ("cursor.json", "agenda.init-recovery.json"):
            if common.safe_path(home / filename).exists():
                raise AgendaError("context-qualified init requires a fresh home; no active-lane migration")
        board(change["board"])
        fields(change["resource_caps"], CAPS)
        for value in change["resource_caps"].values():
            _number(value, "resource cap", 1)
        _text(change["worktree"], "worktree identity")
    with common.transaction(home, create=initializing, write=True, store_version=2) as db:
        prior = db.execute("SELECT request, result FROM operations WHERE operation_id=?", (identity,)).fetchone()
        if prior:
            if prior[0] != canonical:
                raise AgendaError("operation identity reused with a mismatched request or caller")
            state(db, home)
            return decode(prior[1].encode()), False
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        if initializing:
            if db.execute("SELECT 1 FROM state").fetchone():
                raise AgendaError("new-mode store is already initialized")
            subjects = {subject for item in change["board"]["items"].values()
                        for subject in item["relevant_subjects"]}
            baseline = context_body(change["baseline_context"], subjects)
            queue = init_queue(change["board"], change["worktree"], change["resource_caps"],
                               baseline, now, asserted)
            store_id, revision, previous_records = uuid4().hex, 1, set()
            frame_id = queue.cursor["frames"][0]
            item_ids = list(queue.cursor["frame_state"][frame_id]["pending"])
            transition = {"decision": "initialized", "frame_id": frame_id, "item_ids": item_ids,
                "item_id": item_ids[0], "attempt_id": queue.cursor["item_state"][item_ids[0]]["current_attempt_id"],
                "context_id": queue.cursor["baseline_context_id"]}
        else:
            current = state(db, home)
            if current["revision"] != expected:
                raise AgendaError("stale expected revision")
            revision = common._revision(expected, "current revision", increment=True) + 1
            store_id = current["store_id"]
            queue = QueueState(current["documents"], current["records"], now, asserted)
            previous_records = set(queue.records)
            transition = apply(queue, change)
        queue.cursor["revision"] = revision
        validate(queue.documents, queue.records)
        documents = common.encode(queue.documents)
        decode(documents.encode(), "current structured state", limit=1048576)
        additions = [value for key, value in queue.records.items() if key not in previous_records]
        for addition in additions:
            decode(common.encode(addition).encode(), "immutable record")
        observation = {"schema": "efforts.agenda-observation", "version": 2,
            "source": {"worktree": queue.cursor["worktree"], "session": session, "store_id": store_id},
            "revision": revision, "committed_at": now, "snapshot": asdict(project(queue.documents, queue.records))}
        observation_text = common.encode(observation)
        oversized = len(observation_text.encode()) + 1 > 32768
        if oversized:
            observation_text = common.encode({"schema": "efforts.agenda-undeliverable", "version": 1,
                "revision": revision, "error": {"code": "observation-too-large",
                    "message": "Observation exceeds 32 KiB; no argv invoked."}})
        delivery = {"state": "pending" if publisher_configured else "not-configured",
                    "error": {"code": "awaiting-publish" if publisher_configured else "not-configured",
                              "message": "No delivery has been confirmed."}}
        if oversized and publisher_configured:
            delivery["error"] = decode(observation_text.encode())["error"]
        result = {"schema": "efforts.agenda-result", "version": 2, "mode": MODE,
            "persisted": True, "store_id": store_id, "revision": revision, "operation_id": identity,
            "operation": change["operation"], "source": observation["source"], "committed_at": now,
            "actor": asserted, "transition": transition, "record_ids": [value["id"] for value in additions],
            "records": additions, "delivery": delivery}
        result_text = common.encode(result)
        decode(result_text.encode(), "accepted operation result")
        metadata = common.encode({"schema": "efforts.agenda-store", "version": 2, "mode": MODE,
                                  "actor": asserted, "operation_id": identity, "committed_at": now})
        db.execute("INSERT OR REPLACE INTO state VALUES (1,?,?,?,?,?)",
                   (store_id, revision, now, documents, metadata))
        for addition in additions:
            db.execute("INSERT INTO agenda_records VALUES (?,?)", (addition["id"], common.encode(addition)))
        db.execute("INSERT INTO operations VALUES (?,?,?,?,?)", (identity, revision, canonical, result_text, documents))
        db.execute("INSERT OR REPLACE INTO delivery VALUES (1,?,?,?)",
                   (revision, observation_text, common.encode(delivery)))
        return result, True
