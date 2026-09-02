"""The **session-source** seam of the background chronicler.

This seam answers three questions the daemon must ask before it logs anything,
each of which maps to a work-locking invariant in the chronicler compatibility
contract:

* **What is loggable right now?** :meth:`SessionSource.scan` discovers sessions
  from the synced corpus, applying the **settle gate** (I4): a session synced
  more recently than ``settle_seconds`` is mid-sync and must not be claimed.
* **What have we already logged?** :meth:`SessionSource.is_journaled` is the
  **already-journaled skip predicate** (I4): idempotent under catch-up replays,
  so a multi-day gap never re-files a day that was already chronicled.
* **Can this unit fence the segments it will log?** :class:`ReservationStore`
  is the **continuation-segment reservation** (I2 -- the highest-risk lock).
  The work-locked mesh fences a task *record* (atomic claim + unique dedup_key)
  but does **not** fence a task's *inputs*; the session segments live outside
  the mesh, so the reservation table is carried here, in the source seam. A
  chronicle unit reserves the exact ``(parent_session_id, segment_index)``
  segments it will log via a compare-and-set; a racing digest pass that finds a
  segment already reserved reserves nothing for it, and a downgrade guard never
  moves a journaled segment back to available.

The mesh task's dedup key and the reservation key are derived from the **same**
identity -- :func:`chronicle_dedup_key` and :attr:`SegmentRef.key` both key on
``(parent_session_id, segment_index)`` -- so the create-time dedup fence and the
reservation-CAS fence can never disagree about "same segment".

:class:`SyncedSessionSource` is the reference implementation over the local
synced corpus (``<sync-root>/<machine>/session-state/<id>/``). Consumers with a
different corpus (e.g. a NAS-mounted fleet share) subclass :class:`SessionSource`
or point the reference impl at a different root.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from agent_logger import sessions
from agent_logger.segmenter.collate import read_workspace
from agent_logger.sync.origin import read_origin_sidecar
from agent_logger.sync.provenance import (
    RESCUE_SNAPSHOT_PROVENANCE,
    _windows_extended_path,
    existing_real_directory,
    existing_rescue_snapshot_path,
    is_link_or_reparse,
    open_regular_no_follow,
    read_provenance,
    read_provenance_file,
    rescue_snapshot_path,
)

# The settle window: never claim a session whose synced state changed within
# this many seconds (it may be mid-sync). ~10 minutes matches permanent-record.
DEFAULT_SETTLE_SECONDS = 600


@dataclass(frozen=True)
class SegmentRef:
    """Identity of one loggable unit.

    A whole session is ``segment_index == 0``; a continuation session's Nth
    logged segment is ``segment_index == N``. This is the single identity the
    reservation table keys on *and* the mesh task's dedup key derives from, so
    the two fences agree about "same segment".
    """

    parent_session_id: str
    segment_index: int = 0

    @property
    def key(self) -> str:
        return f"{self.parent_session_id}:{self.segment_index}"

    @classmethod
    def parse(cls, key: str) -> SegmentRef:
        parent, _, idx = key.rpartition(":")
        if not parent:
            raise ValueError(f"invalid segment ref key: {key!r}")
        return cls(parent, int(idx))


def chronicle_dedup_key(ref: SegmentRef) -> str:
    """The agent-dispatch task ``dedup_key`` for a chronicle unit.

    Derived from the **same** identity the reservation keys on so a create-time
    collision (mesh ``UNIQUE(dedup_key)``) and a reservation CAS can never
    disagree about whether two producers mean the same segment.
    """
    return f"chronicle:{ref.key}"


@dataclass
class DiscoveredSession:
    """A settled, loggable session discovered from the synced corpus."""

    session_id: str
    machine: str
    session_path: Path
    repository: str | None = None
    branch: str | None = None
    summary: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    #: The **recorded** origin repo (``origin.json`` ``source_repo``): the
    #: harness repo this session was worked in, derived at sync time
    #: (worktree-safe, config-driven). ``None`` means either no origin sidecar
    #: was recorded or the sidecar is machine-only (no harness matched) -- both
    #: cases route by the machine default (``derive-the-origin-never-guess``).
    source_repo: str | None = None
    #: Whether an origin sidecar was recorded for this session at all. Lets the
    #: router distinguish "no recorded origin" (fall back to the raw
    #: ``repository`` during the pre-backfill transition) from "recorded
    #: machine-only origin" (authoritatively route by the machine default).
    origin_recorded: bool = False
    #: ``True`` when this session was discovered as a compressed archive
    #: (``<machine>/archived/<id>.tar.gz``) rather than a live
    #: ``session-state/<id>/`` directory. ``session_path`` then points at the
    #: archive; a content consumer (the writer) must materialize it. Metadata
    #: here was read from the uncompressed selector sidecars, no decompress.
    archived: bool = False
    #: The continuation-segment identity this unit will log. Single-segment
    #: sessions use index 0; a source that splits continuation sessions sets
    #: one DiscoveredSession per reserved segment.
    ref: SegmentRef | None = None

    def __post_init__(self) -> None:
        if self.ref is None:
            self.ref = SegmentRef(self.session_id, 0)

    @property
    def day(self) -> str:
        """The calendar day (YYYY-MM-DD) this session is chronicled under."""
        stamp = self.created_at or self.updated_at
        return _day_of(stamp)


class ReservationState(str, Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    JOURNALED = "journaled"


class ReservationStore:
    """The I2 continuation-segment reservation table (source-seam-carried).

    A tiny SQLite table of segment identities and their state. The three
    mutations are compare-and-set so concurrent chronicle passes never
    double-claim or double-log a segment:

    * :meth:`reserve` -- ``available -> reserved`` for exactly one holder. A
      racing caller (or a second pass) finds it already reserved and reserves
      nothing. Idempotent for the *same* holder.
    * :meth:`mark_journaled` -- ``reserved -> journaled`` (terminal). The
      already-journaled skip predicate reads this state.
    * :meth:`release` -- ``reserved -> available`` for stale-reclaim when a
      holder crashed before journaling. A **downgrade guard** refuses to move a
      ``journaled`` segment back to ``available``.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, isolation_level=None, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.row_factory = sqlite3.Row
            yield conn
        finally:
            conn.close()

    def _migrate(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS segment_reservations (
                    ref_key           TEXT PRIMARY KEY,
                    parent_session_id TEXT NOT NULL,
                    segment_index     INTEGER NOT NULL,
                    state             TEXT NOT NULL,
                    holder            TEXT,
                    reserved_at       TEXT,
                    journaled_at      TEXT,
                    log_path          TEXT
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_seg_state "
                "ON segment_reservations(state)"
            )

    def reserve(self, ref: SegmentRef, holder: str) -> bool:
        """CAS ``available -> reserved`` for *holder*. Returns True iff held.

        Semantics (matching permanent-record's ``continuation_segments`` claim):
        - first sight of a segment is inserted directly as ``reserved`` by this
          holder;
        - an ``available`` row transitions to ``reserved`` for this holder;
        - a row already ``reserved`` by the **same** holder returns True
          (idempotent re-reservation);
        - a row ``reserved`` by a **different** holder, or already
          ``journaled``, returns False -- this holder reserved nothing.
        """
        now = _utcnow()
        with self._conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT state, holder FROM segment_reservations WHERE ref_key = ?",
                (ref.key,),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO segment_reservations "
                    "(ref_key, parent_session_id, segment_index, state, holder, "
                    " reserved_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        ref.key,
                        ref.parent_session_id,
                        ref.segment_index,
                        ReservationState.RESERVED.value,
                        holder,
                        now,
                    ),
                )
                conn.execute("COMMIT")
                return True
            state, current_holder = row["state"], row["holder"]
            if state == ReservationState.AVAILABLE.value:
                conn.execute(
                    "UPDATE segment_reservations SET state = ?, holder = ?, "
                    "reserved_at = ? WHERE ref_key = ? AND state = ?",
                    (
                        ReservationState.RESERVED.value,
                        holder,
                        now,
                        ref.key,
                        ReservationState.AVAILABLE.value,
                    ),
                )
                conn.execute("COMMIT")
                return True
            conn.execute("COMMIT")
            # Reserved by same holder is idempotent; anything else reserves
            # nothing for this caller.
            return (
                state == ReservationState.RESERVED.value
                and current_holder == holder
            )

    def mark_journaled(
        self, ref: SegmentRef, *, log_path: str | None = None
    ) -> None:
        """Terminal ``reserved -> journaled``. Idempotent."""
        now = _utcnow()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO segment_reservations "
                "(ref_key, parent_session_id, segment_index, state, "
                " journaled_at, log_path) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(ref_key) DO UPDATE SET state = excluded.state, "
                "journaled_at = excluded.journaled_at, log_path = excluded.log_path",
                (
                    ref.key,
                    ref.parent_session_id,
                    ref.segment_index,
                    ReservationState.JOURNALED.value,
                    now,
                    log_path,
                ),
            )

    def release(self, ref: SegmentRef, holder: str) -> bool:
        """Stale-reclaim ``reserved -> available`` (downgrade-guarded).

        Only a segment currently ``reserved`` by *holder* is released. A
        ``journaled`` segment is never downgraded (the guard that stops a
        crashed-worker reclaim from resurrecting an already-logged unit).
        Returns True iff a row was released.
        """
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE segment_reservations SET state = ?, holder = NULL, "
                "reserved_at = NULL WHERE ref_key = ? AND state = ? AND holder = ?",
                (
                    ReservationState.AVAILABLE.value,
                    ref.key,
                    ReservationState.RESERVED.value,
                    holder,
                ),
            )
            return cur.rowcount > 0

    def state_of(self, ref: SegmentRef) -> ReservationState:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT state FROM segment_reservations WHERE ref_key = ?",
                (ref.key,),
            ).fetchone()
        if row is None:
            return ReservationState.AVAILABLE
        return ReservationState(row["state"])

    def is_journaled(self, ref: SegmentRef) -> bool:
        return self.state_of(ref) is ReservationState.JOURNALED


class SessionSource:
    """Abstract session-source seam.

    A source discovers loggable units, gates them behind the settle window, and
    exposes the reservation + already-journaled predicates the daemon needs to
    stay idempotent. Subclass to read a different corpus.
    """

    def __init__(
        self,
        reservations: ReservationStore,
        *,
        settle_seconds: int = DEFAULT_SETTLE_SECONDS,
    ) -> None:
        self.reservations = reservations
        self.settle_seconds = settle_seconds

    def scan(self, *, now: datetime | None = None) -> list[DiscoveredSession]:
        raise NotImplementedError

    # -- I4: settle gate + already-journaled skip -----------------------

    def is_settled(self, mtime_epoch: float, *, now: datetime | None = None) -> bool:
        ref_now = (now or _utcnow_dt()).timestamp()
        return (ref_now - mtime_epoch) >= self.settle_seconds

    def is_journaled(self, ref: SegmentRef) -> bool:
        return self.reservations.is_journaled(ref)

    # -- I2: reservation ------------------------------------------------

    def reserve(self, ref: SegmentRef, holder: str) -> bool:
        return self.reservations.reserve(ref, holder)

    def mark_journaled(self, ref: SegmentRef, *, log_path: str | None = None) -> None:
        self.reservations.mark_journaled(ref, log_path=log_path)

    def release(self, ref: SegmentRef, holder: str) -> bool:
        return self.reservations.release(ref, holder)


class SyncedSessionSource(SessionSource):
    """Reference source over the local synced corpus.

    Reads ``<corpus_root>/<machine>/session-state/<id>/`` trees produced by
    ``session-sync``, applying the settle gate on the session directory mtime
    and skipping already-journaled segments. Single-segment sessions only
    (``segment_index == 0``); a consumer that splits continuation sessions into
    multiple reserved segments overrides :meth:`scan`.
    """

    def __init__(
        self,
        corpus_root: Path,
        reservations: ReservationStore,
        *,
        settle_seconds: int = DEFAULT_SETTLE_SECONDS,
    ) -> None:
        super().__init__(reservations, settle_seconds=settle_seconds)
        self.corpus_root = corpus_root

    def scan(self, *, now: datetime | None = None) -> list[DiscoveredSession]:
        corpus_root = existing_real_directory(self.corpus_root)
        if corpus_root is None:
            return []
        out: list[DiscoveredSession] = []
        for raw_machine_dir in sorted(corpus_root.iterdir()):
            machine_dir = existing_real_directory(raw_machine_dir)
            if machine_dir is None:
                continue
            generation = _machine_generation(machine_dir)
            if generation is None or _has_active_replacement(machine_dir):
                continue
            machine_out: list[DiscoveredSession] = []
            ss = existing_real_directory(machine_dir / "session-state")
            if ss is not None:
                for raw_session_dir in sorted(ss.iterdir()):
                    session_dir = existing_real_directory(raw_session_dir)
                    if session_dir is None:
                        continue
                    discovered = self._discover(machine_dir, session_dir, now=now)
                    if discovered is not None:
                        machine_out.append(discovered)
            # Cold sessions compacted into the sibling ``archived/`` tree. A live
            # dir of the same id shadows an archive (a compaction/reconcile
            # race), so ``iter_session_refs`` yields only the un-shadowed
            # archives here.
            archived_store = existing_real_directory(machine_dir / "archived")
            if archived_store is not None:
                live_store = ss or machine_dir / ".absent-session-state"
                for ref in sessions.iter_session_refs(live_store, archived_store):
                    if ref.kind != "archive":
                        continue
                    try:
                        mode = ref.path.lstat().st_mode
                    except OSError:
                        continue
                    if is_link_or_reparse(ref.path, mode):
                        continue
                    discovered = self._discover_archived(
                        machine_dir, ref, now=now
                    )
                    if discovered is not None:
                        machine_out.append(discovered)
            snapshots_root = existing_real_directory(
                machine_dir / ".session-sync-rescue-captures"
            )
            if snapshots_root is not None:
                with os.scandir(_windows_extended_path(snapshots_root)) as entries:
                    session_root_entries = sorted(
                        snapshots_root / entry.name for entry in entries
                    )
                for session_root_entry in session_root_entries:
                    session_root = existing_real_directory(session_root_entry)
                    if session_root is None:
                        continue
                    with os.scandir(_windows_extended_path(session_root)) as entries:
                        snapshot_entries = sorted(
                            session_root / entry.name for entry in entries
                        )
                    for snapshot_entry in snapshot_entries:
                        snapshot = existing_real_directory(snapshot_entry)
                        if snapshot is None:
                            continue
                        discovered = self._discover_rescue_snapshot(
                            machine_dir,
                            snapshot,
                        )
                        if discovered is not None:
                            machine_out.append(discovered)
            machine_out = list(
                {
                    session.ref.key: session
                    for session in machine_out
                }.values()
            )
            if (
                not _has_active_replacement(machine_dir)
                and _machine_generation(machine_dir) == generation
            ):
                out.extend(machine_out)
        return out

    def _discover_archived(
        self, machine_dir: Path, ref: sessions.SessionRef, *, now: datetime | None
    ) -> DiscoveredSession | None:
        if not sessions.verify_archive(ref):
            return None
        provenance = read_provenance(machine_dir, ref.id)
        seg = _segment_ref(machine_dir.name, ref.id, provenance)
        # I4: never re-file a journaled unit -- keyed on the same SegmentRef the
        # session had while live, so archiving never re-chronicles it.
        if self.is_journaled(seg):
            return None
        content_path = _rescued_snapshot_or(
            machine_dir,
            ref.id,
            provenance,
            ref.path,
        )
        if content_path is None or (
            _is_rescue_provenance(provenance)
            and not _tree_is_real(content_path)
        ):
            return None
        # An archive is immutable and cold (>= the compaction age threshold), so
        # the file-mtime settle gate -- which exists to skip a mid-sync *live*
        # dir -- does not apply. Archives are inherently settled.
        if _is_rescue_provenance(provenance):
            ws = read_workspace(content_path)
            origin = read_origin_sidecar(content_path)
            has_origin = origin is not None
        else:
            ws = sessions.read_workspace(ref)
            has_origin = sessions.member_exists(ref, "origin.json")
            origin = sessions.read_origin(ref) if has_origin else {}
        source_repo = (provenance or {}).get("source_repo") or (
            origin.get("source_repo") if origin else None
        )
        return DiscoveredSession(
            session_id=ref.id,
            machine=machine_dir.name,
            session_path=content_path,
            repository=(ws.get("repository") or None),
            branch=(ws.get("branch") or None),
            summary=(ws.get("summary") or None),
            created_at=(ws.get("created_at") or None),
            updated_at=(ws.get("updated_at") or None),
            source_repo=(source_repo or None),
            origin_recorded=has_origin or provenance is not None,
            archived=not _is_rescue_provenance(provenance),
            ref=seg,
        )

    def _discover(
        self, machine_dir: Path, session_dir: Path, *, now: datetime | None
    ) -> DiscoveredSession | None:
        # I4: never claim a mid-sync session.
        try:
            mtime = session_dir.stat().st_mtime
        except OSError:
            return None
        if not self.is_settled(mtime, now=now):
            return None
        provenance = read_provenance(machine_dir, session_dir.name)
        if _is_rescue_provenance(provenance):
            return None
        content_path = _rescued_snapshot_or(
            machine_dir,
            session_dir.name,
            provenance,
            session_dir,
        )
        if content_path is None or not _tree_is_real(content_path):
            return None
        ws = read_workspace(content_path)
        # Prefer the durable, worktree-safe recorded origin (origin.json) for
        # routing (derive-the-origin-never-guess); the raw workspace repository
        # remains as display metadata and a pre-backfill routing fallback.
        origin = read_origin_sidecar(content_path)
        ref = _segment_ref(machine_dir.name, session_dir.name, provenance)
        # I4: never re-file the same local session or rescued capture.
        if self.is_journaled(ref):
            return None
        source_repo = (provenance or {}).get("source_repo") or (
            origin.get("source_repo") if origin else None
        )
        return DiscoveredSession(
            session_id=session_dir.name,
            machine=machine_dir.name,
            session_path=content_path,
            repository=(ws.get("repository") or None),
            branch=(ws.get("branch") or None),
            summary=(ws.get("summary") or None),
            created_at=(ws.get("created_at") or None),
            updated_at=(ws.get("updated_at") or None),
            source_repo=(source_repo or None),
            origin_recorded=origin is not None or provenance is not None,
            ref=ref,
        )

    def _discover_rescue_snapshot(
        self,
        machine_dir: Path,
        snapshot: Path,
    ) -> DiscoveredSession | None:
        if not _tree_is_real(snapshot):
            return None
        metadata_path = snapshot / RESCUE_SNAPSHOT_PROVENANCE
        try:
            with open_regular_no_follow(metadata_path) as stream:
                raw = json.load(stream)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return None
        session_id = raw.get("session_id") if isinstance(raw, dict) else None
        if not isinstance(session_id, str):
            return None
        provenance = read_provenance_file(metadata_path, session_id)
        if not _is_rescue_provenance(provenance):
            return None
        capture_id = provenance.get("capture_id")
        if (
            not isinstance(capture_id, str)
            or rescue_snapshot_path(machine_dir, session_id, capture_id) != snapshot
        ):
            return None
        ref = _segment_ref(machine_dir.name, session_id, provenance)
        if self.is_journaled(ref):
            return None
        ws = read_workspace(snapshot)
        origin = read_origin_sidecar(snapshot)
        source_repo = provenance.get("source_repo") or (
            origin.get("source_repo") if origin else None
        )
        return DiscoveredSession(
            session_id=session_id,
            machine=machine_dir.name,
            session_path=snapshot,
            repository=(ws.get("repository") or None),
            branch=(ws.get("branch") or None),
            summary=(ws.get("summary") or None),
            created_at=(ws.get("created_at") or None),
            updated_at=(ws.get("updated_at") or None),
            source_repo=(source_repo or None),
            origin_recorded=True,
            archived=False,
            ref=ref,
        )


def _utcnow() -> str:
    return _utcnow_dt().isoformat()


def _has_active_replacement(machine_dir: Path) -> bool:
    """Fail closed while a filesystem target transaction is publishing."""
    replacement_root = machine_dir / ".session-sync-replacement"
    try:
        mode = replacement_root.lstat().st_mode
    except FileNotFoundError:
        return False
    except OSError:
        return True
    if is_link_or_reparse(replacement_root, mode) or not stat.S_ISDIR(mode):
        return True
    try:
        active = [
            child.name
            for child in replacement_root.iterdir()
            if child.name.endswith(".active")
        ]
    except OSError:
        return True
    if not active:
        return False
    generation = _machine_generation(machine_dir)
    return not (len(active) == 1 and generation == active[0])


def _machine_generation(machine_dir: Path) -> str | None:
    """Read the atomic filesystem-publish generation; ``None`` fails closed."""
    path = machine_dir / ".session-sync-generation"
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return ""
    except OSError:
        return None
    if is_link_or_reparse(path, mode) or not stat.S_ISREG(mode):
        return None
    try:
        value = path.read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError):
        return None
    if (
        not value
        or len(value) > 128
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for char in value)
    ):
        return None
    return value


def _segment_ref(
    machine: str,
    session_id: str,
    provenance: dict | None,
) -> SegmentRef:
    """Use rescue capture lineage in the chronicler reservation identity."""
    if provenance and provenance.get("provider") == "agent-containers":
        capture_id = provenance.get("capture_id")
        venue_id = provenance.get("venue_id")
        if isinstance(capture_id, str) and isinstance(venue_id, str):
            return SegmentRef(f"{venue_id}/{session_id}@{capture_id}", 0)
    return SegmentRef(session_id, 0)


def _tree_is_real(root: Path) -> bool:
    """Reject links, reparse points, and special files anywhere in a session."""
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(_windows_extended_path(directory)) as entries:
                for entry in entries:
                    path = directory / entry.name
                    try:
                        mode = entry.stat(follow_symlinks=False).st_mode
                    except OSError:
                        return False
                    if is_link_or_reparse(path, mode):
                        return False
                    if stat.S_ISDIR(mode):
                        pending.append(path)
                    elif not stat.S_ISREG(mode):
                        return False
        except OSError:
            return False
    return True


def _is_rescue_provenance(provenance: dict | None) -> bool:
    return bool(provenance and provenance.get("provider") == "agent-containers")


def _rescued_snapshot_or(
    machine_dir: Path,
    session_id: str,
    provenance: dict | None,
    fallback: Path,
) -> Path | None:
    """Resolve immutable rescued bytes, failing closed if they are unavailable."""
    if not _is_rescue_provenance(provenance):
        return fallback
    capture_id = provenance.get("capture_id")
    if not isinstance(capture_id, str):
        return None
    return existing_rescue_snapshot_path(machine_dir, session_id, capture_id)


def _utcnow_dt() -> datetime:
    return datetime.now(timezone.utc)


def _day_of(stamp: str | None) -> str:
    if not stamp:
        return "unknown"
    text = stamp.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        # Best-effort: take a leading YYYY-MM-DD if present.
        return text[:10] if len(text) >= 10 else "unknown"
