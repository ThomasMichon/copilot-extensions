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
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path


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
    # For a remote (ssh/codespace) boundary, how to rebuild the -L (+ -R relay)
    # forward from ssh-manager ALONE after a frontend restart -- no live Spawner,
    # no agent-codespaces import. Empty for a local host. See
    # ``session_host.endpoints``.
    endpoint: dict = field(default_factory=dict)
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_state_file(cls, session_id: str, state_file: str | os.PathLike[str],
                        host_version: str = "") -> HostRecord:
        """Build a record from the JSON the launcher's ``run_host`` wrote."""
        data = json.loads(Path(state_file).read_text())
        return cls(
            session_id=session_id,
            port=int(data["port"]),
            host_pid=int(data["pid"]),
            child_pid=int(data["child_pid"]),
            host_version=host_version,
            protocol_version=int(data.get("protocol_version", 1)),
            state_file=str(state_file),
        )


class HostIndex:
    """Atomic, JSON-backed ``session_id -> HostRecord`` map."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = Path(path)
        self._records: dict[str, HostRecord] = {}
        self._load()

    # -- persistence -------------------------------------------------------
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
        except (json.JSONDecodeError, OSError):
            return
        for sid, rec in raw.get("hosts", {}).items():
            try:
                self._records[sid] = HostRecord(**rec)
            except TypeError:
                continue

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

    # -- mutation ----------------------------------------------------------
    def register(self, record: HostRecord) -> None:
        self._records[record.session_id] = record
        self._flush()

    def remove(self, session_id: str) -> bool:
        existed = self._records.pop(session_id, None) is not None
        if existed:
            self._flush()
        return existed

    def set_resume_flag(self, session_id: str, value: bool) -> bool:
        """Mark (or clear) a session to receive a 'Resume' nudge on reattach.

        Set during a redeploy's graceful-cancel for a session whose in-flight
        turn we cancelled, so the restarted frontend knows to resume it. Returns
        True if the record existed and was updated.
        """
        rec = self._records.get(session_id)
        if rec is None or rec.resume_on_reattach == value:
            return False
        rec.resume_on_reattach = value
        self._flush()
        return True

    def prune_dead(self, is_alive: Callable[[int], bool]) -> list[HostRecord]:
        """Drop records whose host process is gone. Returns the pruned records."""
        dead = [r for r in self._records.values() if not is_alive(r.host_pid)]
        if dead:
            for r in dead:
                self._records.pop(r.session_id, None)
            self._flush()
        return dead

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
