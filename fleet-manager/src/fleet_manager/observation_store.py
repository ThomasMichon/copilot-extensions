"""Explicit SQLite current slots and bounded replay, not a service or authority."""

from contextlib import contextmanager
from pathlib import Path
import sqlite3
import uuid

from .observations import (
    MAX_UPDATE_BYTES, CurrentUpdate, ObservationCursor, OriginBinding, binding_dict,
)
from .planning import PlanError, canonical, decode, identifier, integer

MAX_REPLAY_EVENTS = 1024
MAX_PAGE_ROWS = 64
MAX_POINT_READS = 64
APPLICATION_ID = 0x464D4F42
_SCHEMA = {
    "identity": (
        "CREATE TABLE identity(singleton INTEGER PRIMARY KEY CHECK(singleton=1),"
        "authority TEXT NOT NULL,installation TEXT NOT NULL,store TEXT NOT NULL,"
        "position INTEGER NOT NULL,floor INTEGER NOT NULL)"
    ),
    "origins": (
        "CREATE TABLE origins(origin TEXT PRIMARY KEY,installation TEXT NOT NULL,"
        "generation INTEGER NOT NULL,producer TEXT NOT NULL,position INTEGER NOT NULL)"
    ),
    "streams": (
        "CREATE TABLE streams(origin TEXT NOT NULL,project TEXT NOT NULL,"
        "generation INTEGER NOT NULL,sequence INTEGER NOT NULL,source_revision INTEGER NOT NULL,"
        "position INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(origin,project))"
    ),
    "current": (
        "CREATE TABLE current(origin TEXT NOT NULL,project TEXT NOT NULL,entity TEXT NOT NULL,"
        "generation INTEGER NOT NULL,source_revision INTEGER NOT NULL,position INTEGER NOT NULL,"
        "present INTEGER NOT NULL,payload BLOB NOT NULL,PRIMARY KEY(origin,project,entity))"
    ),
    "changes": (
        "CREATE TABLE changes(position INTEGER PRIMARY KEY,origin TEXT NOT NULL,project TEXT,"
        "generation INTEGER NOT NULL,sequence INTEGER,payload BLOB NOT NULL)"
    ),
    "current_page": "CREATE INDEX current_page ON current(origin,project,present,entity)",
    "changes_replay": "CREATE INDEX changes_replay ON changes(origin,position)",
    "changes_retry": (
        "CREATE UNIQUE INDEX changes_retry ON changes(origin,project,generation,sequence)"
    ),
}
_PAYLOAD = (
    f"CASE WHEN typeof(payload)='blob' AND length(payload)<={MAX_UPDATE_BYTES} "
    "THEN payload END"
)


class ObservationStore:
    """Trusted local API; authentication and single-Gateway lifecycle stay outside."""

    def __init__(self, path, *, authority, installation):
        identifier(authority, "observation authority")
        identifier(installation, "observation installation")
        self.path = Path(path).resolve()
        self.authority, self.installation = authority, installation
        with self._transaction(initialize=True):
            pass

    @contextmanager
    def _transaction(self, *, initialize=False, write=True):
        connection = sqlite3.connect(
            str(self.path) if initialize else self.path.as_uri() + "?mode=rw",
            uri=not initialize, timeout=1, isolation_level=None,
        )
        try:
            connection.execute("PRAGMA synchronous=FULL")
            if not write:
                connection.execute("PRAGMA query_only=ON")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            application = connection.execute("PRAGMA application_id").fetchone()[0]
            objects = dict(connection.execute(
                "SELECT CASE WHEN length(CAST(name AS BLOB))<=128 THEN name END,"
                "CASE WHEN length(CAST(sql AS BLOB))<=4096 THEN sql END "
                "FROM sqlite_master WHERE name NOT GLOB 'sqlite_*' LIMIT ?",
                (len(_SCHEMA) + 1,),
            ))
            if initialize and not version and not application and not objects:
                for statement in _SCHEMA.values():
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO identity VALUES(1,?,?,?,?,?)",
                    (self.authority, self.installation, uuid.uuid4().hex, 0, 0),
                )
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute("PRAGMA user_version=1")
            elif version != 1 or application != APPLICATION_ID or objects != _SCHEMA:
                raise PlanError("unrecognized observation store schema")
            identity = connection.execute(
                "SELECT CASE WHEN length(CAST(authority AS BLOB))<=128 THEN authority END,"
                "CASE WHEN length(CAST(installation AS BLOB))<=128 THEN installation END,"
                "CASE WHEN length(CAST(store AS BLOB))<=128 THEN store END,"
                "position,floor FROM identity LIMIT 2"
            ).fetchall()
            if len(identity) != 1 or identity[0][:2] != (self.authority, self.installation):
                raise PlanError("observation store identity mismatch")
            store, position, floor = identity[0][2:]
            identifier(store, "observation store identity")
            integer(position, "observation head")
            integer(floor, "observation replay floor", maximum=position)
            yield connection, store, position, floor
            connection.commit()
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    @staticmethod
    def _binding(connection, origin):
        row = connection.execute(
            "SELECT CASE WHEN length(CAST(installation AS BLOB))<=128 THEN installation END,"
            "generation,CASE WHEN length(CAST(producer AS BLOB))<=128 THEN producer END,"
            "position FROM origins WHERE origin=?",
            (origin,),
        ).fetchone()
        if row is None:
            raise PlanError("observation origin is not bound")
        return OriginBinding(origin, *row[:3]), row[3]

    @staticmethod
    def _append(connection, *, origin, project, generation, sequence, payload):
        previous = connection.execute("SELECT position FROM identity").fetchone()[0]
        integer(previous, "observation head", maximum=2**63 - 2)
        position = previous + 1
        connection.execute(
            "INSERT INTO changes VALUES(?,?,?,?,?,?)",
            (position, origin, project, generation, sequence, payload),
        )
        floor = max(0, position - MAX_REPLAY_EVENTS)
        connection.execute("DELETE FROM changes WHERE position<=?", (floor,))
        connection.execute("UPDATE identity SET position=?,floor=?", (position, floor))
        return position

    def bind_origin(self, binding, *, expected=None):
        """CAS an explicitly admitted producer; never infer enrollment from an update."""
        value = binding_dict(binding)
        if expected is not None:
            binding_dict(expected)
            if expected.origin != binding.origin:
                raise PlanError("origin binding comparison scope mismatch")
        with self._transaction() as (connection, _store, _head, _floor):
            found = connection.execute(
                "SELECT installation,generation,producer,position FROM origins WHERE origin=?",
                (binding.origin,),
            ).fetchone()
            current = None if found is None else OriginBinding(binding.origin, *found[:3])
            if current == binding:
                return found[3]
            if current != expected:
                raise PlanError("origin binding changed")
            if binding.generation != (1 if current is None else current.generation + 1):
                raise PlanError("origin binding requires the next generation")
            position = self._append(
                connection, origin=binding.origin, project=None,
                generation=binding.generation, sequence=None,
                payload=canonical({"kind": "origin-bound", "binding": value}),
            )
            connection.execute(
                "INSERT OR REPLACE INTO origins VALUES(?,?,?,?,?)",
                (binding.origin, binding.installation, binding.generation, binding.producer, position),
            )
            return position

    def apply(self, update):
        if not isinstance(update, CurrentUpdate):
            raise PlanError("an immutable current update is required")
        value = update.to_dict()
        origin, project = value["origin"], value["project"]
        generation, sequence = value["generation"], value["sequence"]
        with self._transaction() as (connection, _store, _head, _floor):
            binding, _position = self._binding(connection, origin)
            if binding != update.binding:
                raise PlanError("current update origin binding mismatch")
            stream = connection.execute(
                f"SELECT generation,sequence,source_revision,position,{_PAYLOAD} "
                "FROM streams WHERE origin=? AND project=?", (origin, project),
            ).fetchone()
            previous = stream if stream is not None and stream[0] == generation else None
            if previous is not None and sequence <= previous[1]:
                if sequence == previous[1]:
                    position, payload = previous[3:]
                else:
                    retry = connection.execute(
                        f"SELECT position,{_PAYLOAD} FROM changes "
                        "WHERE origin=? AND project=? AND generation=? AND sequence=?",
                        (origin, project, generation, sequence),
                    ).fetchone()
                    if retry is None:
                        raise PlanError("current update retry is outside replay retention")
                    position, payload = retry
                if payload != update.encoded:
                    raise PlanError("current update sequence reused with conflicting content")
                return position
            if sequence != (1 if previous is None else previous[1] + 1):
                raise PlanError("current update sequence has a gap")
            if previous is not None and value["source_revision"] < previous[2]:
                raise PlanError("current update source revision regressed")
            if (previous is not None and value["source_revision"] == previous[2]
                    and self._decode_update(previous[4]).to_dict()["observed_at"]
                    != value["observed_at"]):
                raise PlanError("source revision reused with a different observation timestamp")
            existing = connection.execute(
                f"SELECT generation,source_revision,{_PAYLOAD} FROM current "
                "WHERE origin=? AND project=? AND entity=?",
                (origin, project, value["entity"]),
            ).fetchone()
            if (existing is not None and existing[0] == generation
                    and existing[1] == value["source_revision"]):
                if self._decode_update(existing[2]).to_dict()["state"] != value["state"]:
                    raise PlanError("source revision reused with conflicting current state")
            position = self._append(
                connection, origin=origin, project=project, generation=generation,
                sequence=sequence, payload=update.encoded,
            )
            connection.execute(
                "INSERT OR REPLACE INTO current VALUES(?,?,?,?,?,?,?,?)",
                (origin, project, value["entity"], generation, value["source_revision"],
                 position, int(value["state"] is not None), update.encoded),
            )
            connection.execute(
                "INSERT OR REPLACE INTO streams VALUES(?,?,?,?,?,?,?)",
                (origin, project, generation, sequence, value["source_revision"],
                 position, update.encoded),
            )
            return position

    @staticmethod
    def _decode_update(payload):
        if type(payload) is not bytes:
            raise PlanError("invalid stored observation")
        return CurrentUpdate(payload)

    @staticmethod
    def _scope(origin, project):
        identifier(origin, "observation origin")
        identifier(project, "observation project")

    @staticmethod
    def _cursor(cursor, store, origin, project, head):
        if (not isinstance(cursor, ObservationCursor) or cursor.store != store
                or cursor.origin != origin or cursor.project != project):
            raise PlanError("observation cursor scope mismatch")
        if cursor.position > head:
            raise PlanError("observation cursor is ahead of the store")

    def _row(self, stored, binding, project, head):
        if stored is None:
            return None
        entity, generation, revision, position, present, payload = stored
        integer(position, "stored observation position", minimum=1, maximum=head)
        update = self._decode_update(payload)
        value = update.to_dict()
        if (value["origin"] != binding.origin or value["project"] != project
                or value["entity"] != entity or value["generation"] != generation
                or value["source_revision"] != revision
                or present != int(value["state"] is not None)
                or generation > binding.generation
                or (generation == binding.generation
                    and update.binding != binding)):
            raise PlanError("stored observation identity mismatch")
        return {
            "position": position, "update": value,
            "coverage": "point-observation" if value["generation"] == binding.generation
            else "origin-resync-required",
        }

    def _point(self, connection, origin, project, entity, head):
        binding, _position = self._binding(connection, origin)
        row = connection.execute(
            f"SELECT entity,generation,source_revision,position,present,{_PAYLOAD} FROM current "
            "WHERE origin=? AND project=? AND entity=?", (origin, project, entity),
        ).fetchone()
        return {"binding": binding, "row": self._row(row, binding, project, head)}

    def read(self, *, origin, project, entity):
        self._scope(origin, project)
        identifier(entity, "observation entity")
        with self._transaction(write=False) as (connection, store, head, _floor):
            return {
                "cursor": ObservationCursor(store, origin, project, head),
                **self._point(connection, origin, project, entity, head),
            }

    def read_points(self, points):
        if (type(points) is not tuple or not 1 <= len(points) <= MAX_POINT_READS
                or any(type(point) is not tuple or len(point) != 3 for point in points)):
            raise PlanError("an explicit bounded tuple of observation points is required")
        for origin, project, entity in points:
            self._scope(origin, project)
            identifier(entity, "observation entity")
        if len(set(points)) != len(points):
            raise PlanError("duplicate observation points")
        with self._transaction(write=False) as (connection, _store, head, _floor):
            return tuple(self._point(connection, *point, head) for point in points)

    def page(self, *, origin, project, after_entity=None, limit=MAX_PAGE_ROWS, at=None):
        self._scope(origin, project)
        integer(limit, "observation page limit", minimum=1, maximum=MAX_PAGE_ROWS)
        if after_entity is not None:
            identifier(after_entity, "observation page entity")
            if at is None:
                raise PlanError("observation page continuation requires its snapshot cursor")
        with self._transaction(write=False) as (connection, store, head, _floor):
            binding, _position = self._binding(connection, origin)
            if at is not None:
                self._cursor(at, store, origin, project, head)
                if at.position != head:
                    raise PlanError("observation page changed; restart the snapshot")
            found = connection.execute(
                f"SELECT entity,generation,source_revision,position,present,{_PAYLOAD} FROM current "
                "WHERE origin=? AND project=? AND present=1 AND entity>? "
                "ORDER BY entity LIMIT ?",
                (origin, project, after_entity or "", limit + 1),
            ).fetchall()
            selected = found[:limit]
            return {
                "cursor": ObservationCursor(store, origin, project, head),
                "binding": binding,
                "rows": [self._row(row, binding, project, head) for row in selected],
                "next_entity": selected[-1][0] if len(found) > limit else None,
                "coverage": "point-observations-only",
            }

    def replay(self, cursor, *, limit=MAX_PAGE_ROWS):
        if not isinstance(cursor, ObservationCursor):
            raise PlanError("an explicit scoped observation cursor is required")
        integer(limit, "observation replay limit", minimum=1, maximum=MAX_PAGE_ROWS)
        with self._transaction(write=False) as (connection, store, head, floor):
            self._cursor(cursor, store, cursor.origin, cursor.project, head)
            self._binding(connection, cursor.origin)
            if cursor.position < floor:
                raise PlanError("observation cursor expired; a fresh snapshot is required")
            found = connection.execute(
                f"SELECT position,project,generation,sequence,{_PAYLOAD} FROM changes WHERE origin=? "
                "AND position>? AND (project=? OR project IS NULL) ORDER BY position LIMIT ?",
                (cursor.origin, cursor.position, cursor.project, limit + 1),
            ).fetchall()
            selected = found[:limit]
            events = []
            for position, project, generation, sequence, payload in selected:
                if project is None:
                    value = decode(payload)
                    if (not isinstance(value, dict) or set(value) != {"kind", "binding"}
                            or value["kind"] != "origin-bound"
                            or not isinstance(value["binding"], dict)
                            or set(value["binding"]) != {
                                "origin", "installation", "generation", "producer",
                            }):
                        raise PlanError("invalid stored origin-binding event")
                    binding = OriginBinding(**value["binding"])
                    if (binding.origin != cursor.origin or binding.generation != generation
                            or sequence is not None):
                        raise PlanError("stored origin-binding event identity mismatch")
                else:
                    update = self._decode_update(payload).to_dict()
                    if (update["origin"] != cursor.origin or update["project"] != cursor.project
                            or update["generation"] != generation or update["sequence"] != sequence):
                        raise PlanError("stored observation event identity mismatch")
                    value = {"kind": "current", "update": update}
                events.append({"position": position, **value})
            return {
                "events": events,
                "cursor": ObservationCursor(
                    store, cursor.origin, cursor.project,
                    selected[-1][0] if len(found) > limit else head,
                ),
                "more": len(found) > limit,
            }
