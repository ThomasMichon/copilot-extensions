"""Private recovery evidence; head, task and launch authority stay with owners."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import sys
import time
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from .private_state import protect, reject_links


class RecoveryEvidenceError(RuntimeError):
    """Recovery evidence is unavailable or incompatible; never assume it empty."""


NATIVE_BINDING_PROOF = "inuse-hold-v1"


def _text(value: object, name: str, limit: int = 4096) -> None:
    if (
        not isinstance(value, str) or not value or len(value) > limit
        or any(ord(character) < 32 for character in value)
    ):
        raise ValueError(f"Invalid recovery evidence {name}")


@dataclass(frozen=True)
class PositiveEvidence:
    """One confirmed native hosting observation, with no executable payload."""

    project: str
    worktree_id: str
    worktree_path: str
    native_session_id: str
    native_incarnation: str
    binding_proof: str
    owner_revision: int
    session_incarnation: str
    mapping_revision: int
    platform: str
    environment_id: str
    boot_id: str
    observed_at: float
    observation_sequence: int
    activation_ordinal: int = 0
    source_identity: str | None = None
    lifecycle_qualified: bool = False
    worktree_incarnation: str | None = None
    head_revision: int | None = None

    def __post_init__(self) -> None:
        for name in (
            "project", "worktree_id", "worktree_path", "native_session_id",
            "native_incarnation", "session_incarnation", "environment_id", "boot_id",
        ):
            _text(getattr(self, name), name)
        if str(UUID(self.native_session_id)) != self.native_session_id:
            raise ValueError("Recovery evidence requires a canonical native UUID")
        if self.platform not in {"linux", "wsl", "windows"}:
            raise ValueError("Unknown recovery evidence platform")
        if self.binding_proof != NATIVE_BINDING_PROOF:
            raise ValueError("Unconfirmed native UUID/process binding proof")
        if type(self.mapping_revision) is not int or self.mapping_revision < 0:
            raise ValueError("Invalid recovery mapping revision")
        if type(self.owner_revision) is not int or self.owner_revision < 0:
            raise ValueError("Invalid recovery owner revision")
        if type(self.activation_ordinal) is not int or self.activation_ordinal < 0:
            raise ValueError("Invalid recovery activation ordinal")
        if type(self.lifecycle_qualified) is not bool:
            raise ValueError("Invalid recovery lifecycle qualification")
        if self.source_identity is not None:
            _text(self.source_identity, "activation source", 128)
        if (
            (self.source_identity is None) != (self.activation_ordinal == 0)
            or self.lifecycle_qualified and self.source_identity is None
        ):
            raise ValueError("Unconfirmed recovery activation identity")
        if (self.worktree_incarnation is None) != (self.head_revision is None):
            raise ValueError("Incomplete recovery worktree/head binding")
        if self.worktree_incarnation is not None and (
            not isinstance(self.worktree_incarnation, str)
            or len(self.worktree_incarnation) != 16
            or any(character not in "0123456789abcdef" for character in self.worktree_incarnation)
            or type(self.head_revision) is not int or self.head_revision < 0
        ):
            raise ValueError("Invalid recovery worktree/head binding")
        if type(self.observation_sequence) is not int or self.observation_sequence < 1:
            raise ValueError("Invalid recovery observation sequence")
        if (
            type(self.observed_at) not in {float, int}
            or not 0 < self.observed_at <= sys.float_info.max
        ):
            raise ValueError("Invalid recovery observation time")

    def identity(self) -> tuple:
        """The exact execution generation, excluding observation freshness."""
        return tuple(
            getattr(self, field.name) for field in dataclasses.fields(self)
            if field.name not in {
                "observed_at", "observation_sequence", "owner_revision", "mapping_revision",
                "lifecycle_qualified",
                "worktree_incarnation", "head_revision",
            }
        )

    @property
    def recovery_binding_qualified(self) -> bool:
        """Historical capture proof only; current owner admission is still owed."""
        return self.lifecycle_qualified and self.worktree_incarnation is not None

    def provider_identity(self) -> tuple:
        """The enclosing host generation, independent of its native occupant."""
        return tuple(
            getattr(self, field.name) for field in dataclasses.fields(self)
            if field.name not in {
                "observed_at", "observation_sequence", "owner_revision",
                "native_session_id", "native_incarnation", "mapping_revision",
                "activation_ordinal", "source_identity", "lifecycle_qualified",
                "worktree_incarnation", "head_revision",
            }
        )


_TABLE = (
    "CREATE TABLE journal (id INTEGER PRIMARY KEY, project TEXT NOT NULL, "
    "worktree_id TEXT NOT NULL, payload TEXT NOT NULL)"
)
_INDEX = "CREATE INDEX journal_subject ON journal(project, worktree_id, id)"
_APPLICATION_ID = 0x57525231
_DISPOSITIONS = {
    "live", "unknown", "intentional", "shutdown-pending", "shutdown-interrupted",
}
_INTENTIONAL_DETAILS = {"operator-stop", "native-session-end"}
_CANCELLED_SHUTDOWN_DETAILS = {"shutdown-cancelled", "native-end-after-cancel-unconfirmed"}
_HISTORY_PER_SUBJECT = 16
_MAX_SUBJECTS = 4096


def decode_recovery_json(payload: str) -> object:
    """Normalize the native JSON decoder's excessive-nesting refusal."""
    try:
        return json.loads(payload)
    except RecursionError as exc:
        raise ValueError("Recovery JSON nesting exceeds the decoder budget") from exc


def _decode(payload: str) -> dict:
    row = decode_recovery_json(payload)
    legacy_keys = {"last_positive", "disposition", "detail", "notification_id"}
    if (
        not isinstance(row, dict)
        or set(row) not in (legacy_keys, legacy_keys | {"end_consumed"})
        or row["disposition"] not in _DISPOSITIONS
        or not isinstance(row["detail"], str)
        or not isinstance(row["notification_id"], str)
        or type(row.get("end_consumed", False)) is not bool
    ):
        raise ValueError("Invalid recovery journal event")
    PositiveEvidence(**row["last_positive"])
    if row["disposition"] == "intentional" and row["detail"] not in _INTENTIONAL_DETAILS:
        raise ValueError("Invalid intentional recovery exit evidence")
    row.setdefault("end_consumed", False)
    return row


class RecoveryJournal:
    """Bounded transactional event journal, independent of display tombstones.

    A row is only evidence. Even ``live`` never authorizes automatic recovery;
    current agency and hosting owners must corroborate every candidate.
    """

    def __init__(self, root: Path) -> None:
        self.path = root / "restart-recovery" / "evidence.sqlite3"

    def _protect(self) -> None:
        reject_links(self.path)
        protect(self.path.parent, directory=True)
        if self.path.exists():
            protect(self.path)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            reject_links(sidecar)
            try:
                protect(sidecar)
            except FileNotFoundError:
                pass

    @staticmethod
    def _schema(conn: sqlite3.Connection, *, create: bool) -> bool:
        objects = dict(conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        ))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        application = conn.execute("PRAGMA application_id").fetchone()[0]
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        free = conn.execute("PRAGMA freelist_count").fetchone()[0]
        if not objects and version == application == free == 0 and pages in (0, 1):
            if not create:
                raise RecoveryEvidenceError("Recovery journal is uninitialized.")
            conn.execute(_TABLE)
            conn.execute(_INDEX)
            conn.execute("PRAGMA user_version=1")
            conn.execute(f"PRAGMA application_id={_APPLICATION_ID}")
            return True
        if (
            objects != {"journal": _TABLE, "journal_subject": _INDEX}
            or version != 1 or application != _APPLICATION_ID
        ):
            raise RecoveryEvidenceError("Recovery journal has an incompatible schema.")
        return True

    @contextmanager
    def _transaction(self, *, deadline: float | None = None):
        try:
            reject_links(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._protect()
            with closing(sqlite3.connect(self.path, timeout=0 if deadline is not None else 5)) as conn, conn:
                if deadline is not None:
                    conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
                protect(self.path)
                conn.execute("PRAGMA synchronous=FULL")
                conn.execute("BEGIN IMMEDIATE")
                self._schema(conn, create=True)
                yield conn
                if deadline is not None and time.monotonic() >= deadline:
                    raise RecoveryEvidenceError("Restart checkpoint deadline expired.")
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RecoveryEvidenceError(
                f"Could not persist restart recovery evidence ({type(exc).__name__}): {exc}"
            ) from exc

    @staticmethod
    def _latest(conn: sqlite3.Connection, evidence: PositiveEvidence) -> dict | None:
        row = conn.execute(
            "SELECT payload FROM journal WHERE project=? AND worktree_id=? "
            "ORDER BY id DESC LIMIT 1", (evidence.project, evidence.worktree_id),
        ).fetchone()
        return _decode(row[0]) if row else None

    @staticmethod
    def _append(conn: sqlite3.Connection, evidence: PositiveEvidence, row: dict) -> None:
        conn.execute(
            "INSERT INTO journal(project, worktree_id, payload) VALUES (?, ?, ?)",
            (evidence.project, evidence.worktree_id, json.dumps(row, allow_nan=False)),
        )
        conn.execute(
            "DELETE FROM journal WHERE project=? AND worktree_id=? AND id NOT IN "
            "(SELECT id FROM journal WHERE project=? AND worktree_id=? ORDER BY id DESC LIMIT ?)",
            (evidence.project, evidence.worktree_id, evidence.project, evidence.worktree_id,
             _HISTORY_PER_SUBJECT),
        )

    def observe(self, evidence: PositiveEvidence) -> dict:
        """Persist positive evidence without reopening a retired incarnation."""
        with self._transaction() as conn:
            current = self._latest(conn, evidence)
            if current:
                previous = PositiveEvidence(**current["last_positive"])
                if (
                    evidence.mapping_revision < previous.mapping_revision
                    or evidence.owner_revision < previous.owner_revision
                ):
                    return {"applied": False, "reason": "stale"}
                if evidence.identity() == previous.identity():
                    if current["disposition"] == "intentional":
                        return {"applied": False, "reason": "closed"}
                elif evidence.provider_identity() == previous.provider_identity():
                    if evidence.owner_revision <= previous.owner_revision:
                        return {"applied": False, "reason": "identity-conflict"}
                elif evidence.mapping_revision <= previous.mapping_revision:
                    return {"applied": False, "reason": "identity-conflict"}
                if evidence.identity() == previous.identity() and (
                    evidence.observation_sequence <= previous.observation_sequence
                    or evidence.observed_at < previous.observed_at
                ):
                    return {"applied": False, "reason": "stale"}
            else:
                count = conn.execute(
                    "SELECT count(*) FROM (SELECT 1 FROM journal GROUP BY project, worktree_id)"
                ).fetchone()[0]
                if count >= _MAX_SUBJECTS:
                    raise RecoveryEvidenceError("Recovery journal subject limit reached.")
            row = {
                "last_positive": dataclasses.asdict(evidence),
                "disposition": "live", "detail": "", "notification_id": "",
                "end_consumed": bool(
                    current and evidence.identity() == previous.identity()
                    and current["end_consumed"]
                ),
            }
            if (
                current and evidence.identity() == previous.identity()
                and (
                    current["end_consumed"]
                    or current["disposition"] in {"shutdown-pending", "shutdown-interrupted"}
                )
            ):
                row["disposition"] = current["disposition"]
                row["notification_id"] = current["notification_id"]
                row["detail"] = current["detail"]
            elif (
                current and evidence.identity() == previous.identity()
                and current["detail"] in _CANCELLED_SHUTDOWN_DETAILS
            ):
                row["detail"] = current["detail"]
            self._append(conn, evidence, row)
            return {"applied": True}

    def record_exit(
        self, evidence: PositiveEvidence, *, reason: str, detail: str = "",
        end_consumed: bool = False,
    ) -> dict:
        """Fence reasoned exit evidence to the exact last-positive generation."""
        if reason not in {"intentional", "unknown", "shutdown-interrupted"}:
            raise ValueError("Unsupported recovery exit reason")
        if type(end_consumed) is not bool:
            raise ValueError("Invalid recovery End consumption")
        if reason == "intentional" and detail not in _INTENTIONAL_DETAILS:
            raise ValueError("Intentional closure requires explicit operator or native exit evidence")
        if detail:
            _text(detail, "exit detail", 256)
        with self._transaction() as conn:
            current = self._latest(conn, evidence)
            if (
                current is None
                or PositiveEvidence(**current["last_positive"]).identity() != evidence.identity()
            ):
                return {"applied": False, "reason": "superseded"}
            if current["disposition"] == "intentional":
                return {"applied": False, "reason": "closed"}
            if reason == "intentional" and detail == "native-session-end":
                if current["disposition"] in {"shutdown-pending", "shutdown-interrupted"}:
                    reason = "shutdown-interrupted"
                    detail = "native-session-end-during-shutdown"
                elif current["detail"] in _CANCELLED_SHUTDOWN_DETAILS:
                    reason = "unknown"
                    detail = "native-end-after-cancel-unconfirmed"
            elif reason == "unknown" and current["detail"] in _CANCELLED_SHUTDOWN_DETAILS:
                detail = current["detail"]
            if reason == "shutdown-interrupted" and not current["notification_id"]:
                return {"applied": False, "reason": "unconfirmed-shutdown"}
            row = dict(current)
            row["end_consumed"] = current["end_consumed"] or end_consumed
            row["disposition"] = (
                "shutdown-interrupted"
                if reason == "unknown" and current["disposition"] in {
                    "shutdown-pending", "shutdown-interrupted",
                }
                else reason
            )
            row["detail"] = detail
            self._append(conn, evidence, row)
            return {"applied": True}

    def _shutdown_event(
        self, platform: str, environment_id: str, boot_id: str,
        notification_ids: frozenset[str], *,
        cancelled: bool, deadline: float | None = None,
    ) -> None:
        # One persisted ID per subject, plus this adapter's current native event.
        if (
            not isinstance(notification_ids, frozenset) or not notification_ids
            or len(notification_ids) > _MAX_SUBJECTS + 1
            or not cancelled and len(notification_ids) != 1
        ):
            raise ValueError("Invalid recovery notification identity budget")
        for identifier in notification_ids:
            _text(identifier, "notification", 256)
        for name, value in (
            ("platform", platform), ("environment", environment_id),
            ("boot", boot_id),
        ):
            _text(value, name, 256)
        with self._transaction(deadline=deadline) as conn:
            rows = conn.execute(
                "SELECT payload FROM journal WHERE id IN "
                "(SELECT max(id) FROM journal GROUP BY project, worktree_id)"
            ).fetchall()
            for (payload,) in rows:
                row = _decode(payload)
                evidence = PositiveEvidence(**row["last_positive"])
                if (evidence.platform, evidence.environment_id, evidence.boot_id) != (
                    platform, environment_id, boot_id,
                ):
                    continue
                if cancelled:
                    if (
                        row["disposition"] not in {"shutdown-pending", "shutdown-interrupted"}
                        or row["notification_id"] not in notification_ids
                    ):
                        continue
                    interrupted = row["disposition"] == "shutdown-interrupted"
                    row.update(
                        disposition="unknown" if interrupted else "live",
                        notification_id="",
                        detail=(
                            "native-end-after-cancel-unconfirmed" if interrupted
                            else "shutdown-cancelled"
                        ),
                    )
                else:
                    if row["disposition"] != "live":
                        continue
                    row.update(
                        disposition="shutdown-pending",
                        notification_id=next(iter(notification_ids)),
                    )
                self._append(conn, evidence, row)

    def shutdown_pending(
        self, platform: str, environment_id: str, boot_id: str, *, notification_id: str,
        deadline: float | None = None,
    ) -> None:
        """Freeze eligible positive generations before shutdown teardown."""
        self._shutdown_event(
            platform, environment_id, boot_id, frozenset({notification_id}),
            cancelled=False, deadline=deadline,
        )

    def shutdown_cancelled(
        self, platform: str, environment_id: str, boot_id: str, *,
        notification_id: str | frozenset[str],
    ) -> None:
        """Cancel matching notification identities atomically, never explicit exits."""
        identifiers = (
            frozenset({notification_id}) if isinstance(notification_id, str) else notification_id
        )
        self._shutdown_event(platform, environment_id, boot_id, identifiers, cancelled=True)

    def snapshot(self, *, recover_existing: bool = False) -> list[dict]:
        """Read evidence without creating absent storage or launch intent.

        Only the active daemon lease holder enables ``recover_existing``:
        SQLite may then roll back a hot journal before the read transaction.
        Report-only callers retain a genuinely read-only database connection.
        """
        try:
            reject_links(self.path)
            if not self.path.exists():
                return []
            self._protect()
            mode = "rw" if recover_existing else "ro"
            uri = self.path.absolute().as_uri() + f"?mode={mode}"
            with closing(sqlite3.connect(uri, uri=True, timeout=5)) as conn:
                conn.execute("BEGIN")
                self._schema(conn, create=False)
                rows = conn.execute(
                    "SELECT payload FROM journal WHERE id IN "
                    "(SELECT max(id) FROM journal GROUP BY project, worktree_id) "
                    "ORDER BY project, worktree_id"
                ).fetchall()
                return [{**_decode(row[0]), "automatic_eligible": False} for row in rows]
        except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
            raise RecoveryEvidenceError(
                f"Could not read restart recovery evidence ({type(exc).__name__}): {exc}"
            ) from exc
