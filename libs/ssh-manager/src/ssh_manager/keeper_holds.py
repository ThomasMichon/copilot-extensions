"""Durable per-session holds for shared forward keepers.

The keeper process is one per venue target (for example, one container), while
multiple logical sessions may depend on it. This module owns the venue-neutral
state-file protocol for those session holds: lock-safe reads/writes, stale hold
pruning using an injected liveness probe, and safe keeper retirement.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .forward_keeper import KeeperStore
from .locks import process_identity

ProbeVerdict = bool | None
HoldProbe = Callable[[str], ProbeVerdict]

KEEPER_HOLDS_PROTOCOL = 2
DEFAULT_HOLD_STARTUP_GRACE = 300.0
DEFAULT_UNKNOWN_HOLD_GRACE = 1800.0
DEFAULT_LOCK_TIMEOUT = 10.0
DEFAULT_LOCK_POLL = 0.05
_WIN_LOCK_OFFSET = 1 << 30
log = logging.getLogger("ssh-manager.keeper_holds")


class KeeperHoldStore:
    """Hold-aware state manager for a :class:`~ssh_manager.forward_keeper.KeeperStore`.

    The probe callback contract is tri-state:

    * ``True``: the mux/session is alive; keep the hold and refresh
      ``confirmed_at``.
    * ``False``: the mux/session is definitively gone; keep only while the hold
      is still inside ``startup_grace``.
    * ``None``: liveness is unknown (transport failure, timeout, container
      temporarily unreachable); keep while inside ``startup_grace`` or within
      ``unknown_grace`` of the last ``confirmed_at``.
    """

    def __init__(
        self,
        store: KeeperStore,
        *,
        protocol: int = KEEPER_HOLDS_PROTOCOL,
        startup_grace: float = DEFAULT_HOLD_STARTUP_GRACE,
        unknown_grace: float = DEFAULT_UNKNOWN_HOLD_GRACE,
        lock_timeout: float = DEFAULT_LOCK_TIMEOUT,
        lock_poll: float = DEFAULT_LOCK_POLL,
    ) -> None:
        self.store = store
        self.protocol = int(protocol)
        self.startup_grace = float(startup_grace)
        self.unknown_grace = float(unknown_grace)
        self.lock_timeout = float(lock_timeout)
        self.lock_poll = float(lock_poll)

    def state_path(self, key: str) -> Path:
        return self.store.state_path(key)

    def read_state(self, key: str) -> dict[str, Any] | None:
        return self.store.read(key)

    @contextmanager
    def lock(self, key: str) -> Iterator[None]:
        self.store.state_dir.mkdir(parents=True, exist_ok=True)
        lock = self.store.state_path(key).with_suffix(".lock")
        handle = None
        deadline = time.monotonic() + self.lock_timeout
        while True:
            try:
                handle = self.open_lock(lock)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Could not acquire forward-keeper state lock") from None
                time.sleep(self.lock_poll)
        try:
            yield
        finally:
            self.close_lock(handle)

    def open_lock(self, lock: Path):
        fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
        handle = os.fdopen(fd, "r+", encoding="utf-8")
        try:
            self.acquire_os_lock(handle)
        except OSError:
            try:
                handle.close()
            except OSError:
                pass
            raise
        self.write_lock_owner(handle)
        return handle

    def acquire_os_lock(self, handle) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(_WIN_LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            handle.seek(0)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def release_os_lock(self, handle) -> None:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(_WIN_LOCK_OFFSET)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass

    def write_lock_owner(self, handle) -> None:
        owner = {
            "pid": os.getpid(),
            "identity": process_identity(os.getpid()),
            "created_at": time.time(),
        }
        try:
            handle.seek(0)
            handle.truncate()
            json.dump(owner, handle)
            handle.flush()
            os.fsync(handle.fileno())
        except OSError:
            pass

    def close_lock(self, handle) -> None:
        if handle is None:
            return
        self.release_os_lock(handle)
        try:
            handle.close()
        except OSError:
            pass

    def read_holds(self, state: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
        if not state:
            return {}
        raw = state.get("holds")
        holds: dict[str, dict[str, Any]] = {}
        if isinstance(raw, dict):
            for hold_id, hold in raw.items():
                if not isinstance(hold_id, str) or not isinstance(hold, dict):
                    continue
                mux = hold.get("mux")
                if not isinstance(mux, str) or not mux:
                    continue
                normalized: dict[str, Any] = {
                    "mux": mux,
                    "updated_at": _float_value(hold.get("updated_at")),
                }
                confirmed_at = _float_value(hold.get("confirmed_at"))
                if confirmed_at:
                    normalized["confirmed_at"] = confirmed_at
                holds[hold_id] = normalized
        if raw is None and not holds and isinstance(state.get("mux"), str) and state["mux"]:
            holds[str(state["mux"])] = {
                "mux": str(state["mux"]),
                "updated_at": _float_value(state.get("started_at")),
            }
        return holds

    def state_with_holds(
        self,
        state: dict[str, Any],
        holds: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        out = {**state, "holds": holds}
        if holds:
            out["mux"] = next(iter(holds.values()))["mux"]
        else:
            out.pop("mux", None)
        return out

    def confirm_missing_holds(
        self,
        holds: dict[str, dict[str, Any]],
        *,
        now: float | None = None,
    ) -> tuple[dict[str, dict[str, Any]], bool]:
        confirmed_now = time.time() if now is None else now
        changed = False
        confirmed: dict[str, dict[str, Any]] = {}
        for hold_id, hold in holds.items():
            if hold.get("confirmed_at"):
                confirmed[hold_id] = hold
            else:
                confirmed[hold_id] = {**hold, "confirmed_at": confirmed_now}
                changed = True
        return confirmed, changed

    def refresh_hold(
        self,
        holds: dict[str, dict[str, Any]],
        hold_id: str,
        mux: str,
        *,
        now: float | None = None,
        confirmed: bool = False,
    ) -> dict[str, dict[str, Any]]:
        refreshed = dict(holds)
        stamp = time.time() if now is None else now
        hold: dict[str, Any] = {"mux": mux, "updated_at": stamp}
        if confirmed:
            hold["confirmed_at"] = stamp
        refreshed[hold_id] = hold
        return refreshed

    def refresh_hold_with_status(
        self,
        holds: dict[str, dict[str, Any]],
        hold_id: str,
        mux: str,
        *,
        now: float | None = None,
        confirmed: bool = False,
    ) -> tuple[dict[str, dict[str, Any]], bool, float]:
        hold_added = hold_id not in holds
        refreshed = self.refresh_hold(
            holds,
            hold_id,
            mux,
            now=now,
            confirmed=confirmed,
        )
        updated_at = _float_value(refreshed.get(hold_id, {}).get("updated_at"))
        return refreshed, hold_added, updated_at

    def hold_mux(self, key: str, hold_id: str) -> str | None:
        with self.lock(key):
            state = self.read_state(key)
            hold = self.read_holds(state).get(hold_id)
        mux = hold.get("mux") if hold else None
        return str(mux) if isinstance(mux, str) and mux else None

    def hold_mux_or_none(self, key: str, hold_id: str) -> str | None:
        try:
            return self.hold_mux(key, hold_id)
        except (RuntimeError, OSError) as exc:
            log.warning("Could not read forward-keeper hold for %s/%s: %s", key, hold_id, exc)
            return None

    def list_holds(
        self,
        key: str,
        *,
        probe: HoldProbe | None = None,
        prune: bool = True,
    ) -> dict[str, dict[str, Any]]:
        if prune:
            _state, holds, _live_muxes = self.prune_snapshot(key, probe=probe)
            return holds
        with self.lock(key):
            return self.read_holds(self.read_state(key) or {})

    def prune_snapshot(
        self,
        key: str,
        *,
        probe: HoldProbe | None,
        startup_grace: float | None = None,
        retire_pid: int | None = None,
    ) -> tuple[dict[str, Any], dict[str, dict[str, Any]], set[str]]:
        owner_pid = os.getpid() if retire_pid is None else int(retire_pid)
        grace = self.startup_grace if startup_grace is None else float(startup_grace)
        with self.lock(key):
            state = self.read_state(key) or {}
            holds = self.read_holds(state)
            if state and not holds and int(state.get("pid") or 0) == owner_pid:
                self.store.remove(key)
                return state, {}, set()
            holds, confirmed_changed = self.confirm_missing_holds(holds)
            if state and confirmed_changed:
                self.store.write(key, self.state_with_holds(state, holds))
        kept, live_muxes = self._probe_holds(holds, probe=probe, startup_grace=grace)
        stale = {hold_id: hold for hold_id, hold in holds.items() if hold_id not in kept}
        refreshed = {
            hold_id: hold
            for hold_id, hold in kept.items()
            if hold != holds.get(hold_id)
        }
        if not stale and not refreshed:
            return state, kept, live_muxes
        with self.lock(key):
            current = self.read_state(key) or {}
            current_holds = self.read_holds(current)
            for hold_id, stale_hold in stale.items():
                current_hold = current_holds.get(hold_id)
                if (
                    current_hold
                    and current_hold.get("updated_at") == stale_hold.get("updated_at")
                ):
                    current_holds.pop(hold_id, None)
            for hold_id, refreshed_hold in refreshed.items():
                original = holds.get(hold_id) or {}
                current_hold = current_holds.get(hold_id)
                if (
                    current_hold
                    and current_hold.get("updated_at") == original.get("updated_at")
                ):
                    current_holds[hold_id] = {**current_hold, **refreshed_hold}
            if current:
                if not current_holds and int(current.get("pid") or 0) == owner_pid:
                    self.store.remove(key)
                else:
                    self.store.write(key, self.state_with_holds(current, current_holds))
            return current, current_holds, {
                mux
                for hold in current_holds.values()
                for mux in [str(hold.get("mux") or "")]
                if mux in live_muxes
            }

    def _probe_holds(
        self,
        holds: dict[str, dict[str, Any]],
        *,
        probe: HoldProbe | None,
        startup_grace: float,
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        now = time.time()
        kept: dict[str, dict[str, Any]] = {}
        live_muxes: set[str] = set()
        for hold_id, hold in holds.items():
            mux = str(hold.get("mux") or "")
            if not mux:
                continue
            updated_at = _float_value(hold.get("updated_at"))
            confirmed_at = _float_value(hold.get("confirmed_at"))
            if probe is None:
                kept[hold_id] = hold
                continue
            try:
                verdict = probe(mux)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                verdict = None
            if verdict is True:
                kept[hold_id] = {**hold, "confirmed_at": now}
                live_muxes.add(mux)
            elif now - updated_at <= startup_grace:
                kept[hold_id] = hold
            elif (
                verdict is None
                and confirmed_at > 0
                and now - confirmed_at <= self.unknown_grace
            ):
                kept[hold_id] = hold
        return kept, live_muxes

    def release_hold(
        self,
        key: str,
        *,
        hold_id: str | None = None,
        probe: HoldProbe | None = None,
        expected_updated_at: float | None = None,
    ) -> bool:
        if hold_id is None:
            with self.lock(key):
                return self.store.stop(key)
        with self.lock(key):
            state = self.read_state(key)
            if not state:
                return False
            holds = self.read_holds(state)
            current_hold = holds.get(hold_id)
            if (
                expected_updated_at is not None
                and (
                    not current_hold
                    or current_hold.get("updated_at") != float(expected_updated_at)
                )
            ):
                return False
            holds.pop(hold_id, None)
            self.store.write(key, self.state_with_holds(state, holds))
            if not holds:
                return self.store.stop(key)
        self.prune_snapshot(key, probe=probe)
        with self.lock(key):
            state = self.read_state(key)
            if not state:
                return False
            holds = self.read_holds(state)
            if holds:
                self.store.write(key, self.state_with_holds(state, holds))
                return False
            return self.store.stop(key)

    def write_self_state(
        self,
        key: str,
        state: dict[str, Any],
        *,
        fallback_hold_id: str | None = None,
        fallback_mux: str | None = None,
        pid: int | None = None,
        only_if: Callable[[dict[str, Any]], bool] | None = None,
    ) -> bool:
        """Merge ``state`` over the stored one under the lock.

        ``only_if`` sees the stored state inside the same lock; when it returns
        False nothing is written (e.g. a superseded keeper instance).
        """
        writer_pid = os.getpid() if pid is None else int(pid)
        with self.lock(key):
            existing = self.read_state(key) or {}
            if only_if is not None and not only_if(existing):
                return False
            holds = self.read_holds(existing)
            now = time.time()
            if not holds and fallback_hold_id and fallback_mux:
                holds[fallback_hold_id] = {
                    "mux": fallback_mux,
                    "updated_at": now,
                    "confirmed_at": now,
                }
            holds = self.confirm_missing_holds(holds, now=now)[0]
            self.store.write(
                key,
                self.state_with_holds(
                    {
                        **existing,
                        **state,
                        "keeper_protocol": self.protocol,
                        "pid": writer_pid,
                        "pid_identity": process_identity(writer_pid),
                    },
                    holds,
                ),
            )
        return True

    def remove_self_state(self, key: str, *, pid: int | None = None) -> None:
        owner_pid = os.getpid() if pid is None else int(pid)
        with self.lock(key):
            state = self.read_state(key)
            if (
                state
                and int(state.get("pid") or 0) == owner_pid
                and not self.read_holds(state)
            ):
                self.store.remove(key)

    def alive_or_fail_open(
        self,
        key: str,
        *,
        probe: HoldProbe,
        startup_grace: float | None = None,
    ) -> bool:
        try:
            _state, holds, _live_muxes = self.prune_snapshot(
                key,
                probe=probe,
                startup_grace=startup_grace,
            )
        except (RuntimeError, OSError):
            return True
        return bool(holds)


def _float_value(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
