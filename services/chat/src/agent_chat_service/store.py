"""Bounded, durable conversation RPC core; metadata is not proof of liveness."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import __version__ as VERSION

MAX_BODY_BYTES = 64 * 1024
MAX_PAGE_BYTES = 1024 * 1024
ACTOR_KEYS = {
    "codename", "machine", "environment", "repo", "worktree_id",
    "session_id", "bridge_handle",
}
IDENTITY_KEYS = {"machine", "environment", "repo", "worktree_id"}
IDENTIFIER = re.compile(r"[a-z0-9._-]{1,64}\Z")
FIELDS = {
    "join": ({"channel", "actor"}, set()),
    "leave": ({"channel", "codename"}, {"actor"}),
    "post": ({"channel", "codename", "text", "request_id"}, set()),
    "read": ({"channel", "codename"}, {"limit"}),
    "cursor": ({"channel", "codename", "expected_cursor", "next_cursor"}, set()),
    "wait": ({"channel", "codename"}, {"timeout", "batch_ms"}),
    "scan": ({"codename", "epoch", "after"}, {"limit", "timeout", "batch_ms"}),
    "members": ({"channel"}, {"limit", "after"}),
    "resolve": ({"codename"}, set()),
}
STATUS = {
    "invalid": 400, "missing": 404, "conflict": 409,
    "notjoined": 409, "capacity": 409, "busy": 503, "draining": 503,
    "chat_owner_mismatch": 409,
    "scan_epoch_mismatch": 409, "scan_gap": 409,
}


class ChatError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = STATUS[code]

    def as_dict(self):
        return {"error": self.code, "message": self.message}


def json_bytes(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


def _unicode(value):
    if isinstance(value, str):
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ChatError("invalid", "Invalid Unicode.") from None
    elif isinstance(value, dict):
        for key, item in value.items():
            _unicode(key)
            _unicode(item)
    elif isinstance(value, list):
        for item in value:
            _unicode(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ChatError("invalid", "Nonfinite numbers are not allowed.")


def strict_json(data: bytes):
    """Decode one bounded UTF-8 JSON value, without ambiguous extensions."""
    if len(data) > MAX_BODY_BYTES:
        raise ChatError("invalid", "Request body is too large.")

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("nonfinite number")

    try:
        value = json.loads(
            data.decode("utf-8"), object_pairs_hook=pairs,
            parse_constant=constant,
        )
        _unicode(value)
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise ChatError("invalid", "Invalid JSON.") from None


def _identifier(value, field):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ChatError("invalid", f"Invalid {field}.")
    return value


def _printable(value, field, maximum=1024):
    if not isinstance(value, str) or not value or not value.isprintable():
        raise ChatError("invalid", f"Invalid {field}.")
    if len(value.encode("utf-8")) > maximum:
        raise ChatError("invalid", f"{field} is too large.")
    return value


def _integer(value, field, minimum, maximum=None):
    if type(value) is not int or value < minimum or (
        maximum is not None and value > maximum
    ):
        raise ChatError("invalid", f"Invalid {field}.")
    return value


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def _identity_collision(recorded, expected):
    return any(
        key in recorded and key in expected and recorded[key] != expected[key]
        for key in IDENTITY_KEYS
    )


def validate_request(request):
    if type(request) is not dict or type(request.get("op")) is not str:
        raise ChatError("invalid", "An operation object is required.")
    op = request["op"]
    if op not in FIELDS:
        raise ChatError("invalid", "Unknown operation.")
    required, optional = FIELDS[op]
    if not required <= request.keys() or request.keys() - (
        required | optional | {"op"}
    ):
        raise ChatError("invalid", "Invalid operation fields.")
    try:
        _unicode(request)
    except RecursionError:
        raise ChatError("invalid", "Invalid JSON nesting.") from None
    if "channel" in request:
        _identifier(request["channel"], "channel")
    if "codename" in request:
        _identifier(request["codename"], "codename")
    if op == "join" or (op == "leave" and "actor" in request):
        actor = request["actor"]
        if type(actor) is not dict or "codename" not in actor or (
            actor.keys() - ACTOR_KEYS
        ):
            raise ChatError("invalid", "Invalid actor fields.")
        _identifier(actor["codename"], "codename")
        for key, value in actor.items():
            _printable(value, key)
        if len(json_bytes(actor)) > 4096:
            raise ChatError("invalid", "Actor metadata is too large.")
        if op == "leave" and actor["codename"] != request["codename"]:
            raise ChatError("invalid", "Actor codename must match leave codename.")
    elif op == "post":
        text = request["text"]
        if not isinstance(text, str) or not text.strip() or "\0" in text:
            raise ChatError("invalid", "Invalid text.")
        if len(text.encode("utf-8")) > 8192:
            raise ChatError("invalid", "Text is too large.")
        _printable(request["request_id"], "request_id", 128)
    elif op == "read":
        _integer(request.get("limit", 50), "limit", 1, 100)
    elif op == "members":
        if "limit" in request:
            _integer(request["limit"], "limit", 1, 100)
        if "after" in request:
            _identifier(request["after"], "after")
    elif op == "cursor":
        for name in ("expected_cursor", "next_cursor"):
            _integer(request[name], name, 0, 2**63 - 1)
    elif op in {"wait", "scan"}:
        timeout = request.get("timeout", 30)
        if type(timeout) not in (int, float) or not 0 <= timeout <= 300 or (
            not math.isfinite(timeout)
        ):
            raise ChatError("invalid", "Invalid timeout.")
        _integer(request.get("batch_ms", 0), "batch_ms", 0, 5000)
        if op == "scan":
            epoch, positions = request["epoch"], request["after"]
            if epoch is not None and (
                not isinstance(epoch, str) or not re.fullmatch(r"[a-f0-9]{32}", epoch)
            ):
                raise ChatError("invalid", "Invalid epoch.")
            if type(positions) is not list or not 1 <= len(positions) <= 16:
                raise ChatError("invalid", "Invalid scan channels.")
            channels = set()
            for position in positions:
                if type(position) is not dict or set(position) != {"channel", "event_id"}:
                    raise ChatError("invalid", "Invalid scan position.")
                channel = _identifier(position["channel"], "channel")
                _integer(position["event_id"], "event_id", 0, 2**63 - 1)
                if channel in channels or epoch is None and position["event_id"] != 0:
                    raise ChatError("invalid", "Duplicate channel or invalid discovery position.")
                channels.add(channel)
            _integer(request.get("limit", 100), "limit", 1, 100)
    return op


class ChatStore:
    """One conversation owner, with short SQLite transactions and bounded waits.

    Inactive memberships still occupy capacity. Text quota counts UTF-8 text
    bytes, not SQLite/file overhead. Nothing prunes unread history.
    """

    def __init__(
        self, data_dir, installation_id, *, max_events=100000,
        max_message_bytes=64 * 1024 * 1024, max_channels=256,
        max_actors=4096, max_memberships=4096, max_channel_members=256,
        max_waiters=16,
    ):
        _unicode(installation_id)
        _printable(installation_id, "installation_id", 128)
        for name, value in (
            ("max_events", max_events), ("max_message_bytes", max_message_bytes),
            ("max_channels", max_channels), ("max_actors", max_actors),
            ("max_memberships", max_memberships),
            ("max_channel_members", max_channel_members),
            ("max_waiters", max_waiters),
        ):
            _integer(value, name, 1)
        self.installation_id = installation_id
        self.max_events = max_events
        self.max_message_bytes = max_message_bytes
        self.max_channels = max_channels
        self.max_actors = max_actors
        self.max_memberships = max_memberships
        self.max_channel_members = max_channel_members
        self.max_waiters = max_waiters
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._waiters = threading.BoundedSemaphore(max_waiters)
        self._membership_generations = {}
        self._closed = False
        self._draining = False
        self._service_generation = object()
        self._active_waiters = 0
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(
            directory / "chat.sqlite3", timeout=0.25, check_same_thread=False,
            isolation_level=None,
        )
        self._db.row_factory = sqlite3.Row
        try:
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=FULL")
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS channels (name TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS actors (
                    codename TEXT PRIMARY KEY, metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memberships (
                    channel TEXT NOT NULL REFERENCES channels(name),
                    codename TEXT NOT NULL REFERENCES actors(codename),
                    active INTEGER NOT NULL, joined_at TEXT NOT NULL,
                    cursor INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(channel, codename)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel TEXT NOT NULL REFERENCES channels(name),
                    codename TEXT NOT NULL REFERENCES actors(codename),
                    timestamp TEXT NOT NULL, author TEXT NOT NULL,
                    text TEXT NOT NULL, request_id TEXT NOT NULL,
                    text_bytes INTEGER NOT NULL,
                    UNIQUE(codename, request_id)
                );
                CREATE INDEX IF NOT EXISTS events_channel_id
                    ON events(channel, id);
            """)
            with self._transaction():
                self._db.execute(
                    "INSERT OR IGNORE INTO settings VALUES ('schema', '1')"
                )
                self._db.execute(
                    "INSERT OR IGNORE INTO settings VALUES ('installation_id', ?)",
                    (installation_id,),
                )
                settings = dict(self._db.execute("SELECT key, value FROM settings"))
                if settings["installation_id"] != installation_id:
                    raise ChatError("conflict", "Installation identity mismatch.")
                if settings["schema"] != "1":
                    raise ChatError("conflict", "Unsupported store schema.")
                self._db.execute(
                    "INSERT OR IGNORE INTO settings VALUES ('log_epoch', ?)",
                    (uuid.uuid4().hex,),
                )
                self.log_epoch = self._db.execute(
                    "SELECT value FROM settings WHERE key='log_epoch'"
                ).fetchone()[0]
                if not re.fullmatch(r"[a-f0-9]{32}", self.log_epoch):
                    raise ChatError("conflict", "Invalid log epoch.")
                self._db.execute(
                    """INSERT OR IGNORE INTO settings VALUES
                    ('event_count', (SELECT count(*) FROM events))"""
                )
                self._db.execute(
                    """INSERT OR IGNORE INTO settings VALUES
                    ('message_bytes', (SELECT coalesce(sum(text_bytes), 0) FROM events))"""
                )
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _transaction(self):
        self._db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise

    @contextmanager
    def _locked(self):
        if not self._lock.acquire(timeout=1):
            raise ChatError("busy", "Store is busy.")
        try:
            if self._closed:
                raise ChatError("busy", "Store is closed.")
            yield
        except sqlite3.OperationalError:
            raise ChatError("busy", "Store is unavailable.") from None
        finally:
            self._lock.release()

    def close(self):
        with self._changed:
            if not self._closed:
                self._closed = True
                self._changed.notify_all()
                self._db.close()

    def health(self):
        with self._locked():
            return {
                "status": "ok", "service": "chat", "schema": 1,
                "version": VERSION, "installation_id": self.installation_id,
            }

    def _check_serving(self, generation=None):
        if self._closed:
            raise ChatError("busy", "Store is closed.")
        if self._draining or generation is not None and generation is not self._service_generation:
            raise ChatError("draining", "Store is draining; reconnect through the owning service.")

    @contextmanager
    def _waiter(self):
        with self._locked():
            self._check_serving()
            if not self._waiters.acquire(blocking=False):
                raise ChatError("busy", "Waiter capacity reached.")
            generation = self._service_generation
            self._active_waiters += 1
        try:
            yield generation
        finally:
            with self._changed:
                self._active_waiters -= 1
                self._waiters.release()
                self._changed.notify_all()

    def _lifecycle_state(self):
        return {
            "state": ("draining" if self._active_waiters else "drained") if self._draining else "active",
            "active_waiters": self._active_waiters,
            "storage_schema": 1, "installation_id": self.installation_id,
        }

    def drain(self):
        """Freeze RPC admission after committed transactions and invalidate held waits."""
        with self._locked():
            if not self._draining:
                self._draining = True
                self._service_generation = object()
                self._changed.notify_all()
            return self._lifecycle_state()

    def undrain(self):
        """Resume new admission without reviving waits from a drained generation."""
        with self._locked():
            self._draining = False
            return self._lifecycle_state()

    def dispatch(self, request):
        op = validate_request(request)
        if op == "wait":
            return self._wait(request)
        if op == "scan":
            return self._scan(request)
        with self._locked():
            self._check_serving()
            with self._transaction():
                result = getattr(self, "_" + op)(request)
            if op == "leave":
                self._membership_generations.pop((request["channel"], request["codename"]), None)
            if op in {"post", "leave", "cursor"}:
                self._changed.notify_all()
            return result

    def _count(self, table, where="", args=()):
        return self._db.execute(
            f"SELECT count(*) FROM {table} {where}", args
        ).fetchone()[0]

    def _member(self, channel, codename, *, active=True):
        row = self._db.execute(
            "SELECT * FROM memberships WHERE channel=? AND codename=?",
            (channel, codename),
        ).fetchone()
        if row is None or (active and not row["active"]):
            raise ChatError("notjoined", "An explicit active membership is required.")
        return row

    def _actor(self, codename):
        row = self._db.execute(
            "SELECT metadata FROM actors WHERE codename=?", (codename,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def _membership_generation(self, channel, codename):
        self._member(channel, codename)
        return self._membership_generations.setdefault((channel, codename), object())

    def _join(self, request):
        channel, actor = request["channel"], request["actor"]
        codename = actor["codename"]
        old = self._actor(codename)
        if old is not None:
            if _identity_collision(old, actor):
                raise ChatError("conflict", "Codename identity collision.")
            actor = {**old, **actor}
            if len(json_bytes(actor)) > 4096:
                raise ChatError("invalid", "Actor metadata is too large.")
        elif self._count("actors") >= self.max_actors:
            raise ChatError("capacity", "Actor capacity reached.")
        exists = self._db.execute(
            "SELECT 1 FROM channels WHERE name=?", (channel,)
        ).fetchone()
        if not exists and self._count("channels") >= self.max_channels:
            raise ChatError("capacity", "Channel capacity reached.")
        member = self._db.execute(
            "SELECT * FROM memberships WHERE channel=? AND codename=?",
            (channel, codename),
        ).fetchone()
        if not member and (
            self._count("memberships") >= self.max_memberships
            or self._count("memberships", "WHERE channel=?", (channel,))
            >= self.max_channel_members
        ):
            raise ChatError("capacity", "Membership capacity reached.")
        self._db.execute("INSERT OR IGNORE INTO channels VALUES (?)", (channel,))
        self._db.execute(
            """INSERT INTO actors VALUES (?, ?) ON CONFLICT(codename)
            DO UPDATE SET metadata=excluded.metadata""",
            (codename, json_bytes(actor).decode("utf-8")),
        )
        joined_at = member["joined_at"] if member and member["active"] else _utc()
        self._db.execute(
            """INSERT INTO memberships VALUES (?, ?, 1, ?, 0)
            ON CONFLICT(channel, codename) DO UPDATE SET
            active=1, joined_at=excluded.joined_at""",
            (channel, codename, joined_at),
        )
        return {
            "channel": channel, "actor": actor,
            "membership": {"active": True, "joined_at": joined_at},
            "cursor": member["cursor"] if member else 0,
        }

    def _leave(self, request):
        channel, codename = request["channel"], request["codename"]
        if "actor" in request:
            member = self._db.execute(
                "SELECT 1 FROM memberships WHERE channel=? AND codename=?",
                (channel, codename),
            ).fetchone()
            recorded = self._actor(codename) if member else None
            if recorded is not None and _identity_collision(recorded, request["actor"]):
                raise ChatError("chat_owner_mismatch", "Recorded membership owner differs.")
        self._db.execute(
            "UPDATE memberships SET active=0 WHERE channel=? AND codename=?",
            (channel, codename),
        )
        return {"channel": channel, "codename": codename, "active": False}

    @staticmethod
    def _event(row):
        return {
            "id": row["id"], "channel": row["channel"],
            "timestamp": row["timestamp"], "author": json.loads(row["author"]),
            "text": row["text"], "request_id": row["request_id"],
        }

    def _post(self, request):
        channel, codename = request["channel"], request["codename"]
        old = self._db.execute(
            "SELECT * FROM events WHERE codename=? AND request_id=?",
            (codename, request["request_id"]),
        ).fetchone()
        if old:
            if old["channel"] != channel or old["text"] != request["text"]:
                raise ChatError("conflict", "Request ID already has different content.")
            return {"event": self._event(old), "duplicate": True}
        self._member(channel, codename)
        size = len(request["text"].encode("utf-8"))
        counts = dict(self._db.execute(
            "SELECT key, value FROM settings WHERE key IN ('event_count', 'message_bytes')"
        ))
        if int(counts["event_count"]) >= self.max_events or (
            int(counts["message_bytes"]) + size > self.max_message_bytes
        ):
            raise ChatError("capacity", "Message capacity reached.")
        row_id = self._db.execute(
            """INSERT INTO events
            (channel, codename, timestamp, author, text, request_id, text_bytes)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (channel, codename, _utc(),
             json_bytes(self._actor(codename)).decode("utf-8"),
             request["text"], request["request_id"], size),
        ).lastrowid
        self._db.execute(
            "UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE key='event_count'"
        )
        self._db.execute(
            "UPDATE settings SET value=CAST(value AS INTEGER)+? WHERE key='message_bytes'",
            (size,),
        )
        row = self._db.execute("SELECT * FROM events WHERE id=?", (row_id,)).fetchone()
        return {"event": self._event(row), "duplicate": False}

    def _read(self, request):
        channel, codename = request["channel"], request["codename"]
        cursor = self._member(channel, codename)["cursor"]
        rows = self._db.execute(
            "SELECT * FROM events WHERE channel=? AND id>? ORDER BY id LIMIT ?",
            (channel, cursor, request.get("limit", 50) + 1),
        ).fetchall()
        result = {
            "channel": channel, "codename": codename, "cursor": cursor,
            "next_cursor": cursor, "messages": [], "more": False,
        }
        # Reserve the largest supported JSON encoding, including ASCII escaping.
        size = len(json.dumps(result, allow_nan=False).encode("utf-8"))
        for row in rows[:request.get("limit", 50)]:
            event = self._event(row)
            addition = len(json.dumps(event, allow_nan=False).encode("utf-8")) + 32
            if size + addition > MAX_PAGE_BYTES:
                break
            result["messages"].append(event)
            result["next_cursor"] = row["id"]
            size += addition
        result["more"] = len(rows) > len(result["messages"])
        return result

    def _cursor(self, request):
        channel, codename = request["channel"], request["codename"]
        stored = self._member(channel, codename, active=False)["cursor"]
        expected, next_cursor = request["expected_cursor"], request["next_cursor"]
        if expected != stored:
            raise ChatError("conflict", "Cursor compare-and-swap failed.")
        if next_cursor < expected:
            raise ChatError("invalid", "Cursor cannot move backwards.")
        if next_cursor != stored and not self._db.execute(
            "SELECT 1 FROM events WHERE channel=? AND id=?", (channel, next_cursor)
        ).fetchone():
            raise ChatError("invalid", "Cursor must name a message in this channel.")
        self._db.execute(
            "UPDATE memberships SET cursor=? WHERE channel=? AND codename=?",
            (next_cursor, channel, codename),
        )
        return {"channel": channel, "codename": codename, "cursor": next_cursor}

    def _unread(self, channel, codename):
        cursor = self._member(channel, codename)["cursor"]
        count, latest = self._db.execute(
            "SELECT count(*), max(id) FROM events WHERE channel=? AND id>?",
            (channel, cursor),
        ).fetchone()
        return {
            "channel": channel, "codename": codename, "ready": count > 0,
            "unread_count": count, "next_cursor": latest if count else cursor,
        }

    def _scan_page(self, request):
        if request["epoch"] not in (None, self.log_epoch):
            raise ChatError("scan_epoch_mismatch", "Log epoch changed; explicit recovery required.")
        result = {
            "schema": "agent-chat.scan", "version": 1,
            "installation_id": self.installation_id, "epoch": self.log_epoch,
            "codename": request["codename"], "channels": [], "events": [], "more": False,
        }
        rows = []
        states = {}
        limit = request.get("limit", 100)
        for position in request["after"]:
            channel, after = position["channel"], position["event_id"]
            member = self._member(channel, request["codename"])
            head = self._db.execute(
                "SELECT coalesce(max(id), 0) FROM events WHERE channel=?", (channel,)
            ).fetchone()[0]
            if after > head or after and not self._db.execute(
                "SELECT 1 FROM events WHERE channel=? AND id=?", (channel, after)
            ).fetchone():
                raise ChatError("scan_gap", "Scan position is unavailable; explicit recovery required.")
            state = {
                "channel": channel, "after": after, "scan_cursor": after,
                "head": head, "history_start": 0, "read_cursor": member["cursor"],
            }
            result["channels"].append(state)
            states[channel] = state
            rows.extend(self._db.execute(
                """SELECT id, channel, timestamp, author FROM events
                WHERE channel=? AND id>? ORDER BY id LIMIT ?""",
                (channel, after, limit + 1),
            ).fetchall())
        size = len(json.dumps(result, allow_nan=False).encode("utf-8")) + 1024
        for row in sorted(rows, key=lambda item: item["id"])[:limit]:
            event = {
                "id": row["id"], "channel": row["channel"],
                "timestamp": row["timestamp"], "author": json.loads(row["author"]),
            }
            addition = len(json.dumps(event, allow_nan=False).encode("utf-8")) + 32
            if size + addition > MAX_PAGE_BYTES:
                break
            result["events"].append(event)
            states[row["channel"]]["scan_cursor"] = row["id"]
            size += addition
        result["more"] = any(s["scan_cursor"] < s["head"] for s in result["channels"])
        return result

    def _scan(self, request):
        with self._waiter() as service_generation:
            deadline = time.monotonic() + request.get("timeout", 30)
            batch_end = None
            with self._locked():
                joined = {
                    p["channel"]: self._membership_generation(p["channel"], request["codename"])
                    for p in request["after"]
                }
                while True:
                    self._check_serving(service_generation)
                    with self._transaction():
                        for channel, generation in joined.items():
                            if self._membership_generation(channel, request["codename"]) is not generation:
                                raise ChatError("notjoined", "Membership ended during scan.")
                        result = self._scan_page(request)
                    now = time.monotonic()
                    if result["events"] and batch_end is None:
                        batch_end = min(deadline, now + request.get("batch_ms", 0) / 1000)
                    end = deadline if batch_end is None else batch_end
                    if now >= end:
                        return result
                    self._changed.wait(end - now)

    def _wait(self, request):
        with self._waiter() as service_generation:
            deadline = time.monotonic() + request.get("timeout", 30)
            batch_end = None
            with self._locked():
                generation = self._membership_generation(
                    request["channel"], request["codename"]
                )
                while True:
                    self._check_serving(service_generation)
                    if self._membership_generation(
                        request["channel"], request["codename"]
                    ) is not generation:
                        raise ChatError("notjoined", "Membership ended during wait.")
                    result = self._unread(request["channel"], request["codename"])
                    now = time.monotonic()
                    if result["ready"] and batch_end is None:
                        batch_end = min(
                            deadline, now + request.get("batch_ms", 0) / 1000
                        )
                    end = deadline if batch_end is None else batch_end
                    if now >= end:
                        return result
                    self._changed.wait(end - now)

    def _members(self, request):
        channel = request["channel"]
        if not self._db.execute(
            "SELECT 1 FROM channels WHERE name=?", (channel,)
        ).fetchone():
            raise ChatError("missing", "Channel does not exist.")
        paged = "limit" in request or "after" in request
        query = """SELECT actors.metadata, memberships.joined_at
            FROM memberships JOIN actors USING(codename)
            WHERE channel=? AND active=1"""
        parameters = (channel,)
        if paged:
            query += " AND codename>?"
            parameters += (request.get("after", ""),)
        query += " ORDER BY codename"
        if paged:
            limit = request.get("limit", 100)
            query += " LIMIT ?"
            parameters += (limit + 1,)
        members = [
            {**json.loads(row["metadata"]), "joined_at": row["joined_at"]}
            for row in self._db.execute(query, parameters)
        ]
        result = {
            "channel": channel,
            "members": members[:limit] if paged else members,
        }
        if paged:
            more = len(members) > limit
            result.update(
                more=more,
                next_after=result["members"][-1]["codename"] if more else None,
            )
        return result

    def _resolve(self, request):
        codename = request["codename"]
        channels = [
            row[0] for row in self._db.execute(
                "SELECT channel FROM memberships WHERE codename=? AND active=1 ORDER BY channel",
                (codename,),
            )
        ]
        if not channels:
            raise ChatError("missing", "No active recorded actor.")
        return {"actor": self._actor(codename), "channels": channels}
