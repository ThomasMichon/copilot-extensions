"""Durable index of live Session Hosts, keyed by session id.

The reattach story (goal 3) needs the frontend to know, after a restart, *which*
Session Hosts are still alive and how to reach them -- without respawning a child
for a session whose host survived. This module is that durable map.

It is deliberately **self-contained** (its own JSON file under the agent-bridge
state dir, atomic-replace writes) rather than a new column in the frontend's
SQLite schema, so it is additive and carries no migration. The cutover
(Phase 2) reads it on startup: for each record whose host process is still
alive, reconnect via ``SessionHostClient``; prune the rest.

Records are transport addressing only -- no ACP semantics, no conversation
state (that stays in the frontend event log). ``host_version`` (the agent-bridge
build) and ``protocol_version`` (the wire-envelope generation) are carried so the
Phase-4 version-mux can route a session to the host generation that owns it and
tell whether this frontend can still speak its wire (see ``version_mux``).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from single_instance_lease import AlreadyRunningError, SingleInstance
from zdd.claims import ClaimConflict, decide_acquire

__all__ = ["ClaimConflict", "HostIndex", "HostRecord"]


@dataclass
class HostRecord:
    """How to reach the Session Host that owns one session's child."""

    session_id: str
    port: int
    host_pid: int
    child_pid: int
    host_version: str = ""
    protocol_version: int = 1
    state_file: str = ""
    created_at: float = 0.0
    resume_on_reattach: bool = False
    # Connect-auth nonce to present on ATTACH (empty == unsecured legacy host).
    nonce: str = ""
    # Which boundary the host lives across -- decides how the endpoint is
    # re-pointed on reattach (local: direct loopback, no-op; ssh/codespace:
    # re-establish the forward). Local is the only P2a boundary.
    boundary: str = "local"
    # For a remote (ssh/codespace) boundary, how to rebuild the frontend -L
    # forward and any dedicated -R relay supervisors from ssh-manager ALONE after
    # a frontend restart -- no live Spawner, no agent-codespaces import. Empty
    # for a local host. See
    # ``session_host.endpoints``.
    endpoint: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    # Generation-scoped ownership claim (effort
    # agent-bridge-unified-zdd-cutover, Phase 2): which agent-bridge daemon
    # *generation* currently owns this session-host record, and the pid to
    # probe for that generation's liveness. This is the frontend daemon's own
    # pid, never the session-host child's -- ``host_pid``/``child_pid`` above
    # already track the child; this pair tracks which daemon instance may
    # hand it off. Empty/zero means "never claimed" (a record from before
    # this field existed, or one a generation registered but has not yet
    # explicitly claimed) -- ``zdd.claims.is_recoverable`` treats that the
    # same as a claim whose owner is dead: free for the taking, no live
    # handshake required.
    owner_generation: str = ""
    owner_pid: int = 0

    @classmethod
    def from_state_file(cls, session_id: str, state_file: str | os.PathLike[str],
                        host_version: str = "") -> HostRecord:
        """Build a record from the JSON the launcher's ``run_host`` wrote."""
        data = json.loads(Path(state_file).read_text())
        recorded_session = str(data.get("session_id") or session_id)
        if recorded_session != session_id:
            raise ValueError(
                f"state file session mismatch: {recorded_session} != {session_id}"
            )
        return cls(
            session_id=session_id,
            port=int(data["port"]),
            host_pid=int(data.get("host_pid", data["pid"])),
            child_pid=int(data["child_pid"]),
            host_version=host_version or str(data.get("host_version") or ""),
            protocol_version=int(data.get("protocol_version", 1)),
            state_file=str(state_file),
            created_at=float(data.get("created_at") or 0.0),
            nonce=str(data.get("nonce") or ""),
            extra={
                "child_executable": str(data.get("child_executable") or ""),
                "cwd": str(data.get("cwd") or ""),
            },
        )


class HostIndex:
    """Atomic, JSON-backed ``session_id -> HostRecord`` map.

    **Cross-process write safety (PR #4543 review).** The old and new
    daemon generations each hold their own in-process ``HostIndex`` instance
    over the *same* on-disk file during a cutover -- the new one registers
    or updates records for sessions it is adopting/spawning while the old
    one is still draining. Every mutating method therefore acquires an
    exclusive cross-process lock, **reloads the latest on-disk state**
    into ``self._records`` (discarding this process's now-possibly-stale
    in-memory snapshot), applies the mutation, and flushes -- all before
    releasing the lock. Without the reload, a mutation from a
    longer-lived instance (a snapshot taken at process startup) could
    silently overwrite a concurrent write from the other generation.
    Query-only methods (``get``, ``all``, ``live_records``, ...) still read
    the in-process cache directly -- that staleness is the existing,
    accepted tradeoff (a fresher read is always available by constructing
    a new ``HostIndex``); only writes needed this guarantee.
    """

    # How long a mutating call waits for a contending process's lock before
    # giving up. Generous relative to how long one reload+mutate+flush cycle
    # takes (milliseconds), tight enough that a genuinely wedged holder is
    # still noticed quickly rather than hanging a request indefinitely.
    _LOCK_TIMEOUT_S = 5.0
    _LOCK_POLL_S = 0.02

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._records: dict[str, HostRecord] = {}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        """(Re)populate ``self._records`` from disk.

        Safe to call again later, not just once at construction (see
        :meth:`refresh`/:meth:`_locked_reload`). Parses into a fresh
        temporary map first and only replaces ``self._records`` on a fully
        successful read (PR #4543 review): a transient read failure or
        corrupt/partial write on an *existing* index file must never be
        treated as "empty". A missing file (no index has ever been written
        yet) is the one legitimate "actually empty" case. This lenient
        variant is for construction/:meth:`refresh` (a read failure there
        simply keeps whatever was already in memory); :meth:`_locked_reload`
        uses the stricter :meth:`_load_or_raise` instead, since silently
        proceeding to mutate-and-flush a stale snapshot after a read failure
        risks clobbering a concurrent writer's just-written state.
        """
        try:
            self._load_or_raise()
        except (json.JSONDecodeError, OSError):
            pass

    def _load_or_raise(self) -> None:
        """Like :meth:`_load`, but raises on a read failure of an existing
        file instead of silently keeping stale in-memory state."""
        if not self._path.exists():
            self._records = {}
            return
        raw = json.loads(self._path.read_text())
        records: dict[str, HostRecord] = {}
        for sid, rec in raw.get("hosts", {}).items():
            try:
                records[sid] = HostRecord(**rec)
            except TypeError:
                continue
        self._records = records
        self._records = records

    def _flush(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1,
                   "hosts": {sid: asdict(r) for sid, r in self._records.items()}}
        # Atomic replace so a crashed write never corrupts the index.
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self._path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @contextmanager
    def _locked_reload(self):
        """Hold a cross-process lock for one reload -> mutate -> flush cycle.

        ``single_instance_lease.SingleInstance`` is normally a whole-process
        lease; here it is acquired and released around a single short
        operation, retried (blocking, bounded by ``_LOCK_TIMEOUT_S``) on
        contention rather than failing immediately -- a mutation should wait
        out a few milliseconds of contention from a concurrent writer, not
        refuse outright.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        lease = SingleInstance(
            self._path.parent, service="agent-bridge-host-index",
            lock_name=self._path.name + ".lock",
        )
        deadline = time.monotonic() + self._LOCK_TIMEOUT_S
        while True:
            try:
                lease.acquire()
                break
            except AlreadyRunningError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(self._LOCK_POLL_S)
        try:
            self._load_or_raise()
            yield
            self._flush()
        finally:
            lease.release()

    # -- mutation ----------------------------------------------------------
    def refresh(self) -> None:
        """Reload the in-memory cache from the latest on-disk state.

        Query methods (``get``/``all``/``live_records``/...) read this
        process's own in-memory snapshot, which can predate a concurrent
        writer's registration -- the other daemon generation during a
        cutover, most importantly. A per-record reload inside :meth:`claim`
        cannot help a scan that needs to *discover* a session id it doesn't
        already know about (PR #4543 review). Call this before any scan
        (like the startup/post-cutover reattach pass) that must see records
        registered after this ``HostIndex`` was constructed. No lock is
        needed for a plain reload: writes are atomic replace, so a reader
        never observes a torn file, only possibly a slightly earlier or
        later complete version.
        """
        self._load()

    def register(self, record: HostRecord) -> None:
        """Register (or update the location/metadata of) a Session Host.

        ``register()`` is never an ownership change -- ``claim()``/
        ``release()``/``release_all()`` are the only ownership mutation
        paths (PR #4543 review). A caller updating an *existing* record's
        location (e.g. refreshing a remote forward's port) may be holding a
        stale in-memory copy whose ``owner_generation``/``owner_pid`` predate
        a concurrent claim; overwriting the durable, just-reloaded ownership
        with that stale copy would silently clobber it. Preserve whatever
        this durable index currently has recorded for ownership when the
        record already exists; a genuinely new record keeps its own
        caller-stamped ownership (the initial-spawn path stamps its own
        generation at registration time).
        """
        with self._locked_reload():
            existing = self._records.get(record.session_id)
            if existing is not None:
                record.owner_generation = existing.owner_generation
                record.owner_pid = existing.owner_pid
            self._records[record.session_id] = record

    def remove(self, session_id: str) -> bool:
        with self._locked_reload():
            existed = self._records.pop(session_id, None) is not None
        return existed

    def set_resume_flag(self, session_id: str, value: bool) -> bool:
        """Mark (or clear) a session to receive a 'Resume' nudge on reattach.

        Set during a redeploy's graceful-cancel for a session whose in-flight
        turn we cancelled, so the restarted frontend knows to resume it. Returns
        True if the record existed and was updated.
        """
        with self._locked_reload():
            rec = self._records.get(session_id)
            if rec is None or rec.resume_on_reattach == value:
                return False
            rec.resume_on_reattach = value
        return True

    def prune_dead(self, is_alive: Callable[[int], bool]) -> list[HostRecord]:
        """Drop records whose host process is gone. Returns the pruned records."""
        with self._locked_reload():
            dead = [r for r in self._records.values() if not is_alive(r.host_pid)]
            for r in dead:
                self._records.pop(r.session_id, None)
        return dead

    # -- generation-scoped claims (effort agent-bridge-unified-zdd-cutover,
    # Phase 2) --------------------------------------------------------------
    def claim(
        self,
        session_id: str,
        *,
        generation: str,
        owner_pid: int,
        pid_alive: Callable[[int], bool],
        force: bool = False,
    ) -> HostRecord:
        """Claim ``session_id`` for ``generation``, or raise :class:`ClaimConflict`.

        A live generation's claim on a record it still owns is refused (the
        exact race this primitive exists to prevent: two daemon generations
        each believing they own the same session-host); a claim whose owning
        generation is provably dead (``pid_alive`` is false for its recorded
        ``owner_pid``) is recovered silently -- no live handshake with the
        dead generation is needed. ``force=True`` overrides a live conflict
        (an explicit operator escape hatch; not used by the normal handoff
        path). Raises ``KeyError`` if no record exists for ``session_id``
        (claiming is about taking over an *existing* record -- use
        :meth:`register` to create one).
        """
        with self._locked_reload():
            rec = self._records[session_id]
            decide_acquire(
                rec, key=session_id, generation=generation, owner_pid=owner_pid,
                pid_alive=pid_alive, force=force,
            )
            if rec.owner_generation != generation or rec.owner_pid != owner_pid:
                rec.owner_generation = generation
                rec.owner_pid = owner_pid
        return rec

    def release(self, session_id: str, generation: str) -> bool:
        """Release ``session_id``'s claim, but only if ``generation`` holds it.

        This is the outgoing generation's exit-contract half of claim/release/
        recover: it releases claims it actually held, one host at a time, as
        it hands off -- never another generation's claim (a stale caller
        racing an already-superseded release must not clobber the new
        owner). Returns True if a release happened.
        """
        with self._locked_reload():
            rec = self._records.get(session_id)
            if rec is None or rec.owner_generation != generation:
                return False
            rec.owner_generation = ""
            rec.owner_pid = 0
        return True

    def release_all(self, generation: str) -> list[str]:
        """Release every claim ``generation`` holds (the exit-contract sweep).

        Called by an outgoing generation as it hands off, so every record it
        held becomes immediately recoverable by the next generation without
        waiting for that generation to notice the owner died. Returns the
        released session ids.
        """
        with self._locked_reload():
            released = [
                sid for sid, r in self._records.items() if r.owner_generation == generation
            ]
            for sid in released:
                rec = self._records[sid]
                rec.owner_generation = ""
                rec.owner_pid = 0
        return released

    def claims_owned_by(self, generation: str) -> list[HostRecord]:
        """Records currently claimed by ``generation``."""
        return [r for r in self._records.values() if r.owner_generation == generation]

    def recoverable_claims(
        self, pid_alive: Callable[[int], bool]
    ) -> list[HostRecord]:
        """Records whose owning generation is provably dead (or never claimed).

        Candidates a new generation may claim outright via :meth:`claim`
        without contention -- no live handshake with a dead process required.
        """
        return [
            r for r in self._records.values()
            if not r.owner_generation or not r.owner_pid or not pid_alive(r.owner_pid)
        ]

    # -- query -------------------------------------------------------------
    def get(self, session_id: str) -> HostRecord | None:
        return self._records.get(session_id)

    def all(self) -> list[HostRecord]:
        return list(self._records.values())

    def live_records(self, is_alive: Callable[[int], bool]) -> list[HostRecord]:
        """Records whose host process is currently alive (for reattach)."""
        return [r for r in self._records.values() if is_alive(r.host_pid)]

    def __len__(self) -> int:
        return len(self._records)

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._records
