"""Quiescent handoff of a private proposal intake; never a coordinator takeover."""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from .proposal_recovery import ProposalIntake, proposal_row

# Cache compatibility is distinct from the version of the independent logical records.
CACHE_FORMAT = 1


def offer(intake: ProposalIntake) -> None:
    """Advertise a stopped, owner-private generation at its committed journal epoch."""
    with closing(sqlite3.connect(intake.records.as_uri() + "?mode=rw", uri=True)) as db:
        db.execute("BEGIN IMMEDIATE")
        if not intake._usable or db.execute("SELECT epoch FROM writer").fetchall() != [(intake._epoch,)]:
            raise ValueError("generation does not own the current proposal")
        db.execute("CREATE TABLE IF NOT EXISTS cache_offer(singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                   "epoch TEXT NOT NULL, format INTEGER NOT NULL, home TEXT NOT NULL)")
        db.execute("INSERT OR REPLACE INTO cache_offer VALUES(1,?,?,?)",
                   (intake._epoch, CACHE_FORMAT, str(Path(intake.queue.db_path).parent)))
        # A failed journal write could otherwise leave a newer queue than the offer.
        db.execute("UPDATE writer SET epoch=? WHERE singleton=1", ("offered:" + intake._epoch,))
        db.commit()
        intake._usable = False


def receive(records: Path, destination: Path, *, outgoing: Path) -> tuple[ProposalIntake, str]:
    """Use only the explicitly addressed current generation. All roots are caller-private."""
    records, destination, outgoing = (p.absolute() for p in (records, destination, outgoing))
    if destination.exists() or destination.is_relative_to(outgoing) or outgoing.is_relative_to(destination):
        raise ValueError("handoff requires a fresh disjoint destination")
    with closing(sqlite3.connect(records.as_uri() + "?mode=rw", uri=True)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT o.epoch,o.format,o.home FROM cache_offer o JOIN writer w "
                         "ON w.epoch='offered:'||o.epoch WHERE o.singleton=1 AND w.singleton=1").fetchone()
        if row is None or Path(row[2]) != outgoing:
            raise ValueError("outgoing proposal is not the current offer")
        if row[1] == CACHE_FORMAT and (outgoing / "tasks.db").is_file():
            identity, title, _, payload, _, draft = proposal_row(db)
            destination.mkdir(mode=0o700)
            with closing(sqlite3.connect((outgoing / "tasks.db").as_uri() + "?mode=ro", uri=True)) as old:
                with closing(sqlite3.connect(destination / "tasks.db")) as new:
                    old.backup(new)
            blobs = outgoing / "payloads"
            if blobs.exists():
                shutil.copytree(blobs, destination / "payloads")
            incoming = ProposalIntake(records=records, generation=destination, identity=identity)
            task = incoming.queue.get(identity)
            if task is None or task.title != title or task.status != "proposed" or task.owner is not None:
                raise ValueError("cache does not match the offered proposal")
            if draft is not None and task.card_draft != json.loads(draft):
                raise ValueError("cache does not match the acknowledged draft")
            if task.payload_ref:
                # The content address precedes the original writer's text-mode newline translation.
                body = payload.encode("utf-8")
                digest = hashlib.sha256(body).hexdigest()
                if (task.payload_ref != "blob:" + digest or
                        (destination / "payloads" / (digest + ".md")).read_bytes()
                        not in (body, body.replace(b"\n", b"\r\n"))):
                    raise ValueError("cache does not match the offered payload")
            elif (incoming.queue.read_payload(task) or "") != payload:
                raise ValueError("cache does not match the offered payload")
            log = incoming.queue.steer_log(identity)
            accepted = db.execute("SELECT key,fields,receipt FROM steer_v1 ORDER BY seq").fetchall()
            if len(log) != len(accepted) or any(
                item["fields"] != json.loads(fields) or incoming.queue.steer_receipt(identity, key) != json.loads(receipt)
                for item, (key, fields, receipt) in zip(log, accepted)
            ):
                raise ValueError("cache does not match the acknowledged steers")
            db.execute("UPDATE writer SET epoch=? WHERE singleton=1", (incoming._epoch,))
            db.commit()
            return incoming, "compatible-import"
        reason = "incompatible-format" if row[1] != CACHE_FORMAT else "unavailable-cache"
    # recover() checks the offered epoch again inside its own write transaction.
    return ProposalIntake.recover(records, destination, expected_epoch="offered:" + row[0]), reason
