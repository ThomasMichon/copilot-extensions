"""Linux-only singleton supervision across descendant cutovers and manager exec."""

from __future__ import annotations

import logging
import math
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from . import routing
from .singleton_linux import LinuxBackend, WatchedProcess
from .singleton_state import LEASE_ENV, ManagerLease, ManagerState, ProcessIdentity, StateStore

log = logging.getLogger("zdd")


class SpawnedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...


class Backend(Protocol):
    owner: ProcessIdentity
    boot_id: str

    def claim_tree(self) -> None: ...
    def identify(self, pid: int) -> ProcessIdentity | None: ...
    def pid_is_alive(self, pid: int) -> bool: ...
    def open_process(self, identity: ProcessIdentity) -> WatchedProcess | None: ...
    def owns(self, reference: WatchedProcess, deadline: float | None = None) -> bool: ...
    def reap_zombies(self, watched_pid: int | None = None) -> None: ...
    def cleanup(self) -> None: ...


class Lease(Protocol):
    fd: int

    def close(self) -> None: ...
    def prepare_exec(self) -> None: ...


class UnmanagedDaemonError(RuntimeError):
    """A live incumbent is outside this manager's proven ownership."""


@dataclass(frozen=True)
class ManagerResult:
    """The manager's service outcome, not a transparent child exit status."""

    exit_code: int
    reason: str
    last_watched_pid: int


class SingletonManager:
    """Run in a dedicated process whose entire descendant tree is lifecycle-owned."""

    def __init__(
        self,
        config_dir: str | os.PathLike[str],
        spawn: Callable[[], SpawnedProcess],
        *,
        manager_state_dir: str | os.PathLike[str],
        resolve_update: Callable[[], Sequence[str] | None] | None = None,
        backend: Backend | None = None,
        lease_factory: Callable[[Path], Lease] = ManagerLease,
        execve: Callable[[str, list[str], dict[str, str]], object] = os.execve,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        successor_wait_s: float = 30.0,
        poll_interval: float = 0.1,
    ) -> None:
        if not math.isfinite(successor_wait_s) or successor_wait_s < 0:
            raise ValueError("successor_wait_s must be finite and non-negative")
        if not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError("poll_interval must be finite and positive")
        self.config_dir = Path(config_dir).resolve()
        state_dir = Path(manager_state_dir).resolve()
        if state_dir == self.config_dir:
            raise ValueError("routing and manager state directories must be distinct")
        self.store = StateStore(state_dir)
        self.spawn = spawn
        self._backend = backend
        self.lease_factory = lease_factory
        self.resolve_update = resolve_update
        self.execve = execve
        self.clock, self.sleep = clock, sleep
        self.successor_wait_s, self.poll_interval = successor_wait_s, poll_interval

    @property
    def backend(self) -> Backend:
        if self._backend is None:
            raise RuntimeError("manager backend is not initialized")
        return self._backend

    def _parse_endpoint(self, raw: object) -> routing.Endpoint | None:
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("ownership routing endpoint must be an object")
        pid, port, bind = raw.get("pid"), raw.get("port"), raw.get("bind")
        generation = raw.get("generation", 0)
        version, token = raw.get("version"), raw.get("process_start_time")
        if (
            type(pid) is not int or pid <= 0
            or type(port) is not int or not 0 < port <= 65535
            or not isinstance(bind, str)
            or type(generation) is not int or generation < 0
            or (version is not None and not isinstance(version, str))
            or (token is not None and (
                not isinstance(token, str) or not token.isascii() or not token.isdigit()
            ))
        ):
            raise ValueError("ownership routing endpoint has invalid field types or values")
        return routing.Endpoint(
            bind, port, pid, version, generation, process_start_time=token,
        )

    def _route_identity(self) -> tuple[int, ProcessIdentity | None] | None:
        table = routing.read_table(self.config_dir, strict=True)
        raw = table.get("active") if isinstance(table, dict) else None
        endpoint = self._parse_endpoint(raw)
        if endpoint is None or endpoint.pid is None:
            return None
        identity = (
            ProcessIdentity(endpoint.pid, endpoint.process_start_time, self.backend.boot_id)
            if endpoint.process_start_time is not None else None
        )
        return endpoint.pid, identity

    def _candidate(self) -> WatchedProcess | None:
        route = self._route_identity()
        if route is None or route[1] is None:
            return None
        reference = self.backend.open_process(route[1])
        if reference is None:
            return None
        return self._confirm_owned(reference)

    def _confirm_owned(self, reference: WatchedProcess) -> WatchedProcess | None:
        try:
            if self.backend.owns(reference):
                return reference
        except BaseException:
            reference.close()
            raise
        reference.close()
        return None

    def _discovering(self, state: ManagerState, exit_code: int = 1) -> ManagerState:
        log.info(
            "Watched daemon pid %d exited (child code %d); discovering a verified successor",
            state.watched.pid, exit_code,
        )
        discovery = replace(
            state, phase="discovering", deadline=self.clock() + self.successor_wait_s,
            exit_code=1 if exit_code == 0 else exit_code,
        )
        self.store.write(discovery)
        return discovery

    def _refuse_live_incumbent(
        self, pid: int, expected: ProcessIdentity | None, message: str,
    ) -> None:
        current = self.backend.identify(pid)
        if current is not None and expected is not None and current != expected:
            return
        if self.backend.pid_is_alive(pid):
            raise UnmanagedDaemonError(message)

    def _startup(self) -> tuple[ManagerState, WatchedProcess | None, SpawnedProcess | None]:
        saved = self.store.read()
        if saved is not None and saved.owner == self.backend.owner:
            if saved.phase == "discovering":
                return saved, None, None
            reference = self.backend.open_process(saved.watched)
            if reference is not None:
                reference = self._confirm_owned(reference)
                if reference is not None:
                    return saved, reference, None
                raise UnmanagedDaemonError("persisted daemon is not manager-owned")
            return self._discovering(saved), None, None
        if saved is not None:
            self._refuse_live_incumbent(
                saved.watched.pid, saved.watched,
                "old manager's daemon survived outside supervision",
            )

        table = routing.read_table(self.config_dir, strict=True) or {}
        for key in ("active", "previous"):
            raw = table.get(key)
            if raw is None:
                continue
            endpoint = self._parse_endpoint(raw)
            if endpoint is None or endpoint.pid is None:
                raise ValueError("ownership endpoint requires a process identity")
            expected = (
                ProcessIdentity(endpoint.pid, endpoint.process_start_time, self.backend.boot_id)
                if endpoint.process_start_time is not None else None
            )
            self._refuse_live_incumbent(
                endpoint.pid, expected, "live route is not adoptable by this manager",
            )

        child = self.spawn()
        identity = self.backend.identify(child.pid)
        if identity is None:
            raise RuntimeError("spawned daemon identity could not be established")
        reference = self.backend.open_process(identity)
        if reference is not None:
            reference = self._confirm_owned(reference)
            if reference is None:
                raise UnmanagedDaemonError("spawn callback detached daemon from manager ancestry")
        state = ManagerState(self.backend.owner, identity)
        try:
            self.store.write(state)
        except BaseException:
            if reference is not None:
                reference.close()
            raise
        if reference is None:
            code = child.poll()
            state = self._discovering(state, code if code is not None else 1)
        return state, reference, child

    def _maybe_exec(self, lease: Lease) -> None:
        command = self.resolve_update() if self.resolve_update is not None else None
        if command is None:
            return
        if isinstance(command, (str, bytes)):
            raise ValueError("update resolver must return argv, not a command string")
        argv = list(command)
        if not argv or not all(isinstance(arg, str) and arg for arg in argv):
            raise ValueError("update resolver must return a non-empty argv")
        if not os.path.isabs(argv[0]):
            raise ValueError("update resolver must return an absolute executable")
        environment = dict(os.environ)
        environment[LEASE_ENV] = str(lease.fd)
        lease.prepare_exec()
        log.info("Singleton manager replacing its process image without respawning daemon")
        self.execve(argv[0], argv, environment)
        raise RuntimeError("manager exec unexpectedly returned")

    def run(self) -> ManagerResult:
        lease = self.lease_factory(self.store.directory)
        reference: WatchedProcess | None = None
        claimed = False
        try:
            if self._backend is None:
                self._backend = LinuxBackend()
            self.backend.claim_tree()
            claimed = True
            state, reference, child = self._startup()
            while True:
                self.backend.reap_zombies(
                    state.watched.pid if state.phase == "watching" else None,
                )
                if state.phase == "watching":
                    code = child.poll() if child is not None else None
                    if reference is None or not reference.alive() or code is not None:
                        if reference is not None:
                            closing = reference
                            reference = None
                            closing.close()
                        child = None
                        state = self._discovering(state, code if code is not None else 1)
                if state.phase == "discovering":
                    if state.deadline is not None and self.clock() >= state.deadline:
                        log.warning("No verified successor; cleaning owned descendants before exit")
                        return ManagerResult(state.exit_code, "no verified successor", state.watched.pid)
                    candidate = self._candidate()
                    if candidate is not None:
                        reference = candidate
                        state = ManagerState(self.backend.owner, candidate.identity)
                        self.store.write(state)
                        log.info("Singleton manager adopted cutover successor pid %d", state.watched.pid)
                self._maybe_exec(lease)
                self.sleep(self.poll_interval)
        finally:
            try:
                if reference is not None:
                    reference.close()
            finally:
                try:
                    if claimed:
                        self.backend.cleanup()
                finally:
                    lease.close()


def run(
    config_dir: str | os.PathLike[str],
    spawn: Callable[[], SpawnedProcess],
    *,
    manager_state_dir: str | os.PathLike[str],
    resolve_update: Callable[[], Sequence[str] | None] | None = None,
) -> ManagerResult:
    return SingletonManager(
        config_dir, spawn, manager_state_dir=manager_state_dir,
        resolve_update=resolve_update,
    ).run()
