"""Durable repair fences and explicitly acknowledged restart obligations."""

from __future__ import annotations

import json
import os
import secrets
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .installation_repair_admission import RepairAdmissionError, digest, ordinary
from .installation_repair_process import defer_cancellation

_SCHEMA = "copilot-extensions.installation-repair-restarts"
_MAX_BYTES = 2 * 1024 * 1024
_MAX_PENDING = 4096


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate restart journal field")
        result[key] = value
    return result


class RestartJournal:
    def __init__(self, home: Path):
        self.root = home / ".agent-machines" / "installation-repair"
        self.path = self.root / "restarts.json"
        self.pending: dict[str, dict[str, str]] = {}
        self._inflight: dict[str, str] | None = None
        self._locked = False

    def load(self) -> None:
        for path in reversed((self.root, *self.root.parents)):
            try:
                path.lstat()
            except FileNotFoundError:
                continue
            ordinary(path, directory=True)
        try:
            self.path.lstat()
        except FileNotFoundError:
            return
        ordinary(self.path)
        try:
            with self.path.open("rb") as stream:
                content = stream.read(_MAX_BYTES + 1)
            if len(content) > _MAX_BYTES:
                raise ValueError("restart journal exceeds its bound")
            value = json.loads(content, object_pairs_hook=_unique_object)
            if (
                not isinstance(value, dict)
                or set(value) not in (
                    {"schema", "version", "pending"},
                    {"schema", "version", "pending", "inFlight"},
                )
                or value["schema"] != _SCHEMA or type(value["version"]) is not int
                or value["version"] != 1 or not isinstance(value["pending"], dict)
                or len(value["pending"]) > _MAX_PENDING
            ):
                raise ValueError("invalid restart journal")
            for module, row in value["pending"].items():
                if (
                    not isinstance(module, str) or not 1 <= len(module) <= 256
                    or not isinstance(row, dict)
                    or set(row) != {"restart", "generation", "planDigest"}
                    or row["restart"] not in {"shell", "session", "machine"}
                    or any(
                        not isinstance(row[key], str) or len(row[key]) != size
                        or any(char not in "0123456789abcdef" for char in row[key])
                        for key, size in (("generation", 32), ("planDigest", 64))
                    )
                ):
                    raise ValueError("invalid restart obligation")
            inflight = value.get("inFlight")
            if "inFlight" in value and (
                not isinstance(inflight, dict)
                or set(inflight) != {"module", "planDigest", "generation"}
                or not isinstance(inflight["module"], str)
                or not 1 <= len(inflight["module"]) <= 256
                or not isinstance(inflight["planDigest"], str)
                or len(inflight["planDigest"]) != 64
                or any(char not in "0123456789abcdef" for char in inflight["planDigest"])
                or not isinstance(inflight["generation"], str)
                or len(inflight["generation"]) != 32
                or any(char not in "0123456789abcdef" for char in inflight["generation"])
            ):
                raise ValueError("invalid installer fence")
            self.pending = value["pending"]
            self._inflight = inflight
        except (OSError, ValueError, TypeError) as error:
            raise RepairAdmissionError("restart journal cannot be read or validated") from error

    @property
    def pending_digest(self) -> str:
        return digest(self.pending)

    def evidence(self) -> dict[str, object]:
        return {
            "pendingDigest": self.pending_digest,
            "pending": [
                {"module": module, **row} for module, row in sorted(self.pending.items())
            ],
            "completion": "explicit-operator-confirmation",
            "automaticRestart": False,
        }

    def require_settled(self) -> None:
        if self._inflight is not None:
            raise RepairAdmissionError(
                "installation repair has an unconfirmed installer; preserve its journal "
                "and establish execution cleanup before owner recovery"
            )

    def action_fence(self) -> dict[str, object]:
        return {
            "pendingDigest": digest(self._inflight) if self._inflight is not None else None,
            "pending": dict(self._inflight) if self._inflight is not None else None,
            "completion": "explicit-operator-cleanup-attestation",
            "automaticCleanup": False,
        }

    @contextmanager
    def locked(self, *, confirm_cleanup: bool = False) -> Iterator[RestartJournal]:
        if type(confirm_cleanup) is not bool:
            raise RepairAdmissionError("invalid cleanup confirmation selection")
        self.load()
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        ordinary(self.root, directory=True)
        lock = self.root / "apply.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        if lock.exists() or lock.is_symlink():
            ordinary(lock)
        descriptor = os.open(lock, flags, 0o600)
        acquired = False
        try:
            ordinary(lock)
            if sys.platform == "win32":
                import msvcrt
                if os.fstat(descriptor).st_size == 0:
                    os.write(descriptor, b"\0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                try:
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                except OSError as error:
                    raise RepairAdmissionError("installation repair is already active") from error
            else:
                import fcntl
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as error:
                    raise RepairAdmissionError("installation repair is already active") from error
            acquired = self._locked = True
            self.load()
            if not confirm_cleanup:
                self.require_settled()
            yield self
        finally:
            self._locked = False
            if acquired:
                if sys.platform == "win32":
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _save(self) -> None:
        if not self._locked:
            raise RepairAdmissionError("restart mutation requires the repair lock")
        ordinary(self.root, directory=True)
        if self.path.exists() or self.path.is_symlink():
            ordinary(self.path)
        value = {
            "schema": _SCHEMA, "version": 1, "pending": self.pending,
        }
        if self._inflight is not None:
            # Older readers must refuse rather than replay an unconfirmed action.
            value["inFlight"] = self._inflight
        content = json.dumps(value, sort_keys=True).encode("utf-8")
        if len(content) > _MAX_BYTES or len(self.pending) > _MAX_PENDING:
            raise RepairAdmissionError("restart journal exceeds its bound")
        temporary = self.root / (".restarts-" + secrets.token_hex(16))
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            if sys.platform != "win32":
                directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def begin_action(self, module: str, plan_digest: str) -> None:
        """Fence another installer if this lock holder dies before confirmed cleanup."""
        with defer_cancellation():
            previous = self._inflight
            self._inflight = {
                "module": module, "planDigest": plan_digest,
                "generation": secrets.token_hex(16),
            }
            try:
                self._save()
            except BaseException:
                self._inflight = previous
                raise

    def confirm_action_cleanup(self, expected_digest: str) -> None:
        if not self._locked or self._inflight is None or expected_digest != digest(self._inflight):
            raise RepairAdmissionError("pending installer fence changed or is empty")
        self.finish_action()

    def finish_action(self) -> None:
        with defer_cancellation():
            if self._inflight is not None:
                previous = self._inflight
                self._inflight = None
                try:
                    self._save()
                except BaseException:
                    self._inflight = previous
                    raise

    def require(self, module: str, restart: str, plan_digest: str) -> None:
        if (
            not isinstance(module, str) or not 1 <= len(module) <= 256
            or restart not in {"shell", "session", "machine"}
            or not isinstance(plan_digest, str) or len(plan_digest) != 64
            or any(char not in "0123456789abcdef" for char in plan_digest)
        ):
            raise RepairAdmissionError("invalid restart obligation")
        if not self._locked:
            raise RepairAdmissionError("restart mutation requires the repair lock")
        if module in self.pending:
            raise RepairAdmissionError("module has an unresolved restart obligation")
        self.pending[module] = {
            "restart": restart, "generation": secrets.token_hex(16), "planDigest": plan_digest,
        }
        self._save()

    def confirm(self, expected_digest: str) -> None:
        if not self.pending or expected_digest != self.pending_digest:
            raise RepairAdmissionError("pending restart batch changed or is empty")
        self.pending = {}
        self._save()
