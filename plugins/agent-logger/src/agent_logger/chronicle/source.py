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

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from agent_logger.segmenter.collate import read_workspace
from agent_logger.sync.origin import read_origin_sidecar

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
        if not self.corpus_root.is_dir():
            return []
        out: list[DiscoveredSession] = []
        for machine_dir in sorted(self.corpus_root.iterdir()):
            ss = machine_dir / "session-state"
            if not ss.is_dir():
                continue
            for session_dir in sorted(ss.iterdir()):
                if not session_dir.is_dir():
                    continue
                discovered = self._discover(machine_dir.name, session_dir, now=now)
                if discovered is not None:
                    out.append(discovered)
        return out

    def _discover(
        self, machine: str, session_dir: Path, *, now: datetime | None
    ) -> DiscoveredSession | None:
        ref = SegmentRef(session_dir.name, 0)
        # I4: never re-file a journaled unit.
        if self.is_journaled(ref):
            return None
        # I4: never claim a mid-sync session.
        try:
            mtime = session_dir.stat().st_mtime
        except OSError:
            return None
        if not self.is_settled(mtime, now=now):
            return None
        ws = read_workspace(session_dir)
        # Prefer the durable, worktree-safe recorded origin (origin.json) for
        # routing (derive-the-origin-never-guess); the raw workspace repository
        # remains as display metadata and a pre-backfill routing fallback.
        origin = read_origin_sidecar(session_dir)
        source_repo = origin.get("source_repo") if origin else None
        return DiscoveredSession(
            session_id=session_dir.name,
            machine=machine,
            session_path=session_dir,
            repository=(ws.get("repository") or None),
            branch=(ws.get("branch") or None),
            summary=(ws.get("summary") or None),
            created_at=(ws.get("created_at") or None),
            updated_at=(ws.get("updated_at") or None),
            source_repo=(source_repo or None),
            origin_recorded=origin is not None,
            ref=ref,
        )


def _utcnow() -> str:
    return _utcnow_dt().isoformat()


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
