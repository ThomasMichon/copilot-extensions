"""Private persistent pipe adapter; the original shell remains the instance."""

from __future__ import annotations

import base64
import errno
import json
import os
import sys
import threading
import traceback
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, ContextManager, Protocol

from . import _daemon_instance_store as store
from ._daemon_instance_clock import WallLeaseObservation
from ._daemon_instance_records import (
    ProcessIdentity, admit_for_process, bind_for_process, finish_for_process, reserve_for_process,
)
from .daemon_instances import (
    INSTANCE_ENV, REGISTRY_ENV, TEST_CONTEXT_ENV, DaemonInstanceReceipt,
    DaemonInstanceSpec, DaemonLeasePolicy,
)

_MAX_FRAME = 524288
_ENVIRONMENT = {INSTANCE_ENV, REGISTRY_ENV, TEST_CONTEXT_ENV, "COPILOT_EXTENSIONS_OWNED_JOB_SCOPES"}

if TYPE_CHECKING:
    from .daemon_broker import WindowsDaemonProcess


class ShellOwner(Protocol):
    identity: ProcessIdentity
    scopes: str | None

    def require_live(self) -> None: ...
    def contain(self, environment: dict[str, str]) -> str: ...
    def child(
        self, identity: ProcessIdentity, source_handle: int,
    ) -> ContextManager[WindowsDaemonProcess]: ...
    def retire(self) -> None: ...


def _receipt(value: dict) -> DaemonInstanceReceipt:
    return DaemonInstanceReceipt(value["instance_id"], Path(value["registry_root"]))


def _metadata(receipt: DaemonInstanceReceipt, **extra: object) -> dict:
    return {
        "instance_id": receipt.instance_id, "registry_root": str(receipt.registry_root),
        "record_path": str(receipt.record_path), **extra,
    }


def _spec(parameters: dict, environment: dict, *, admission: bool) -> DaemonInstanceSpec:
    purpose = parameters.get("purpose")
    if not purpose:
        if admission and not environment.get(TEST_CONTEXT_ENV):
            raise ValueError("direct daemon startup requires an explicit purpose or lifecycle reservation")
        purpose = "unattended-test" if admission else "production"
    ttl, maximum = parameters.get("ttl", 0), parameters.get("maximum", 0)
    if type(ttl) not in (int, float) or type(maximum) not in (int, float):
        raise ValueError("daemon lease limits must be numeric")
    lease = DaemonLeasePolicy(ttl, maximum) if ttl or maximum else None
    root = parameters.get("registry_root")
    return DaemonInstanceSpec(
        parameters["service"], Path(parameters["runtime_root"]), Path(parameters["source"]),
        purpose=purpose, registry_root=Path(root) if root else None, lease=lease,
    )


class ShellInstanceSession:
    """Only the native pipe-owner adapter supplies ``owner`` in production."""

    def __init__(self, owner: ShellOwner) -> None:
        self.owner = owner
        self._active: tuple[DaemonInstanceReceipt, WallLeaseObservation | None] | None = None
        self._tokens: set[str] = set()
        self._guard_lock = threading.RLock()
        self._failed = False

    def dispatch(self, request: dict) -> dict | list | str | None:
        if self._failed:
            raise RuntimeError("original shell lifetime admission failed; replay is refused")
        self.owner.require_live()
        command = request["command"]
        environment = request.get("environment", {})
        if not isinstance(environment, dict) or any(
            name not in _ENVIRONMENT or not isinstance(value, str) or len(value) > 8192
            for name, value in environment.items()
        ):
            raise ValueError("invalid instance shell environment")
        parameters = request.get("parameters", {})
        if command == "reserve":
            receipt = reserve_for_process(
                _spec(parameters, environment, admission=False), self.owner.identity,
                environment=environment,
            )
            with store._locked(receipt, wait_seconds=1) as path:
                source = store._read(path)["source"]
            return _metadata(receipt, source=source)
        if command == "enter":
            with self._guard_lock:
                return self._enter(parameters, environment)
        if command == "exit":
            with self._guard_lock:
                return self._exit(request)
        if command == "bind":
            return self._bind(request)
        if command == "finish-child":
            receipt = _receipt(request["receipt"])
            identity, handle = self._child_request(request)
            with self.owner.child(identity, handle) as process:
                if process.poll() is None:
                    raise ValueError("child daemon completion requires observed exit")
                finish_for_process(receipt, identity, launcher=self.owner.identity)
            return None
        if command == "read":
            path = Path(parameters["path"])
            receipt = DaemonInstanceReceipt(path.stem, path.parent)
            if path != receipt.record_path:
                raise ValueError("noncanonical daemon instance record path")
            with store._locked(receipt, wait_seconds=1) as record_path:
                return store._read(record_path)
        if command == "root":
            return str(store.registry_root())
        if command == "reconcile":
            root = parameters.get("registry_root")
            return [asdict(result) for result in store.reconcile(
                Path(root) if root else None, parameters.get("limit", 128),
                parameters.get("max_seconds", 5), _caller_pid=self.owner.identity.pid,
            )]
        raise ValueError("unknown instance shell operation")

    @staticmethod
    def _child_request(request: dict) -> tuple[ProcessIdentity, int]:
        value = request["process"]
        return ProcessIdentity(value["pid"], value["birth"]), value["handle"]

    def _bind(self, request: dict) -> dict[str, str]:
        receipt = _receipt(request["receipt"])
        identity, handle = self._child_request(request)
        with self.owner.child(identity, handle):
            if request.get("admitted"):
                with store._locked(receipt, wait_seconds=1) as path:
                    record = store._read(path)
                    if record["purpose"] != "production":
                        raise ValueError("disposable children must admit their own lifetime guard")
            bind_for_process(receipt, identity, self.owner.identity)
            if request.get("admitted"):
                return admit_for_process(receipt, identity)
            with store._locked(receipt, wait_seconds=1) as path:
                return store._read(path)

    def _enter(self, parameters: dict, environment: dict[str, str]) -> dict:
        if len(self._tokens) >= 4096:
            raise ValueError("daemon guard reference bound exceeded")
        scopes = self.owner.scopes
        if self._active is None:
            inherited = environment.get(INSTANCE_ENV)
            if inherited:
                receipt = DaemonInstanceReceipt(inherited, Path(environment[REGISTRY_ENV]))
            else:
                receipt = reserve_for_process(
                    _spec(parameters, environment, admission=True), self.owner.identity,
                    environment=environment,
                )
            observation = None
            contained = False

            def contain(record: dict[str, str]) -> None:
                nonlocal observation, scopes, contained
                if record["purpose"] != "production":
                    observation = WallLeaseObservation(
                        record, wall=store._wall_clock(), monotonic=store._monotonic_clock(),
                    )
                    scopes = self.owner.contain(environment)
                    contained = True

            try:
                admit_for_process(receipt, self.owner.identity, before_admit=contain)
            except BaseException:
                if contained:
                    self._failed = True
                    self._active = (receipt, observation)
                    self.owner.retire()
                raise
            self._active = (receipt, observation)
        receipt, _ = self._active
        token = uuid.uuid4().hex
        self._tokens.add(token)
        return _metadata(receipt, guard_token=token, job_scopes=scopes)

    def _exit(self, request: dict) -> None:
        token = request["guard_token"]
        if self._active is None or token not in self._tokens:
            raise ValueError("daemon admission guard custody changed")
        receipt, _ = self._active
        if _receipt(request["receipt"]) != receipt:
            raise ValueError("daemon admission receipt changed")
        if len(self._tokens) == 1:
            finish_for_process(receipt, self.owner.identity)
            self._active = None
        self._tokens.remove(token)
        return None

    def observe(self) -> None:
        active = self._active
        if active is None or active[1] is None:
            return
        receipt, observation = active
        if not self._guard_lock.acquire(blocking=False):
            if self._active == active and observation.past_hard(store._monotonic_clock()):
                self.owner.retire()
            return
        try:
            if self._active != active:
                return
            if observation.past_hard(store._monotonic_clock()):
                self.owner.retire()
                return
            try:
                with store._locked(receipt) as path:
                    record = store._read(path)
                    if not self.owner.identity.matches(record) or record["state"] != "running":
                        raise ValueError("daemon monitor instance identity changed")
                    expired = observation.expired(
                        record, wall=store._wall_clock(), monotonic=store._monotonic_clock(),
                    )
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    return
                raise
            if expired:
                self.owner.retire()
        finally:
            self._guard_lock.release()


def _serve_requests(session: ShellInstanceSession, nonce: str) -> int:
    sequence = 0
    while True:
        raw = sys.stdin.buffer.raw.readline(_MAX_FRAME + 1)
        if not raw:
            return 0
        if len(raw) > _MAX_FRAME or not raw.endswith(b"\n"):
            raise ValueError("instance shell request exceeds its frame bound")
        request = json.loads(base64.b64decode(raw[:-1], validate=True))
        sequence += 1
        if (
            not isinstance(request, dict)
            or type(request.get("sequence")) is not int or request["sequence"] != sequence
            or request.get("nonce") != nonce
        ):
            raise ValueError("uncorrelated instance shell request")
        try:
            result = session.dispatch(request)
        except (OSError, ValueError, KeyError, TypeError) as error:
            response = {"ok": False, "error": str(error)}
        else:
            response = {"ok": True, "result": result}
        response.update(sequence=sequence, nonce=nonce)
        encoded = json.dumps(response, ensure_ascii=True)
        if len(encoded) > _MAX_FRAME:
            raise ValueError("instance shell response exceeds its frame bound")
        sys.stdout.write(encoded + "\n")
        sys.stdout.flush()


def main(request: dict) -> int:
    import msvcrt

    from ._daemon_instance_shell_windows import ShellProcessOwner
    from ._shell_caller_owned import serve

    lifetime = request["lifetime"]
    owner = ShellProcessOwner(
        ProcessIdentity(lifetime["owner_pid"], lifetime["owner_birth"]),
        msvcrt.get_osfhandle(sys.stdin.fileno()),
    )
    for name in (INSTANCE_ENV, REGISTRY_ENV, TEST_CONTEXT_ENV):
        os.environ.pop(name, None)
    stopped = threading.Event()

    def run() -> int:
        session = ShellInstanceSession(owner)

        def watch() -> None:
            while not stopped.wait(.1):
                try:
                    session.observe()
                except BaseException:
                    traceback.print_exc()
                    sys.stderr.flush()
                    # Process exit closes the exact retained kill-on-close Job.
                    os._exit(1)

        monitor = threading.Thread(target=watch, name="shell-instance-lifetime", daemon=True)
        monitor.start()
        try:
            return _serve_requests(session, lifetime["nonce"])
        finally:
            stopped.set()
            monitor.join(timeout=1)
            if monitor.is_alive():
                raise RuntimeError("shell instance monitor retirement is unconfirmed")

    try:
        return serve(run, **lifetime)
    finally:
        owner.close()
