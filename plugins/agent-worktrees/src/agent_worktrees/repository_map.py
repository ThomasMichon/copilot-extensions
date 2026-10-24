"""Producer-owned registered repository snapshots on the existing resident."""

from __future__ import annotations

import json
import sys
import threading
import time
import uuid
from pathlib import Path

from . import repos
from .repos_registry_reader import read_registry
from .repos_registry_transaction import read_bytes, revision

CONTRACT_VERSION = 1
READ_ACTION = "repos-map-read"
CAPABILITY = "repos-map-v1"
MAX_AGE = 60.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def valid_repository_name(value: object) -> bool:
    """Registry labels are data; consumers own executable resource-name syntax."""
    return isinstance(value, str) and bool(value) and "\0" not in value


def build_snapshot(
    scope: str, generation: str, sequence: int, *, now: float, registry_path: Path,
) -> dict:
    registry = read_registry(registry_path, strict=True)
    if registry._baseline is None:
        raise repos.RepositoryResolutionError("repository registry has no source revision")
    source, source_revision = registry._baseline
    if any(not valid_repository_name(name) for name in registry.repos):
        raise repos.RepositoryResolutionError("repository registry has an invalid identity")
    entries = []
    for name, entry in sorted(registry.repos.items()):
        if entry.name != name:
            raise repos.RepositoryResolutionError("repository registry has an invalid identity")
        row = {
            "name": name,
            "class": entry.repo_class,
            "remote": entry.remote,
            "default_branch": entry.default_branch,
            "tags": entry.tags,
            "contributing": entry.contributing,
            "account": entry.account,
            "copilot_account": entry.copilot_account,
            "resolved_account": repos.resolve_account(entry, registry=registry),
            "agent": entry.agent,
            "visibility": entry.visibility,
            "paths": entry.paths,
        }
        for field in ("remote", "default_branch", "contributing", "account", "copilot_account", "visibility"):
            if not isinstance(row[field], str):
                raise repos.RepositoryResolutionError(
                    f"registered repository {name!r} has invalid {field} metadata"
                )
        try:
            row.update(registration="ready", path=str(repos.registered_entry_root(entry)))
        except repos.RepositoryResolutionError as exc:
            row.update(registration="unavailable", path=None, reason=str(exc))
        entries.append(row)
    if (
        str(registry_path.resolve()) != source
        or revision(read_bytes(registry_path)) != source_revision
    ):
        raise repos.RepositoryResolutionError("repository registry changed during map construction")
    return {
        "ok": True,
        "repository_map_version": CONTRACT_VERSION,
        "inventory_authority": "complete",
        "platform": repos._current_platform(),
        "repos": entries,
        "projection": {
            "scope": scope, "generation": generation, "revision": sequence,
            "source_revision": source_revision, "observed_at": now, "quality": "current",
        },
    }


class RepositoryMapRuntime:
    """Reads copy a published snapshot; only the resident producer resolves roots."""

    def __init__(
        self, scope: Path, *, now=time.time, monotonic=time.monotonic, wait=None,
    ):
        self.scope = str(scope.resolve())
        registry = repos._repos_yaml_path()
        self._registry_path = registry.parent.resolve() / registry.name
        self._now = now
        self._monotonic = monotonic
        self._last_demand: float | None = None
        self._lock = threading.Lock()
        self._generation = uuid.uuid4().hex
        self._epoch = 0
        self._sequence = 0
        self._admitting = False
        self._reason = "unready"
        self._snapshot: str | None = None
        self._error: str | None = None
        self._refreshing = False
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._wait = wait or (lambda event, seconds: event.wait(seconds))
        self._thread: threading.Thread | None = None

    def start(self, interval: float = 15.0) -> None:
        interval = min(max(interval, 1.0), MAX_AGE / 2)
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                return
            worker = threading.Thread(
                target=self._produce, args=(interval,),
                name="agent-worktrees-repository-map", daemon=True,
            )
            worker.start()
            self._thread = worker

    def _produce(self, interval: float) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            self.refresh()
            self._wait(self._wake, interval)

    def shutdown(self) -> bool:
        self._stop.set()
        self.close_admission("shutdown")
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            settled = not self._thread.is_alive()
            if not settled:
                try:
                    print("repository-map producer still draining", file=sys.stderr)
                except (OSError, ValueError):
                    pass
            return settled
        return True

    def activate(self) -> None:
        with self._lock:
            if self._stop.is_set():
                return
            if not self._admitting:
                self._epoch += 1
                self._generation = uuid.uuid4().hex
                self._snapshot = None
                self._error = None
            self._admitting = True
            self._reason = "unready" if self._snapshot is None else "ready"
        self._wake.set()

    def close_admission(self, reason: str) -> None:
        with self._lock:
            self._epoch += 1
            self._admitting = False
            self._reason = reason

    def refresh(self) -> None:
        with self._lock:
            if not self._admitting or self._stop.is_set() or self._refreshing:
                return
            self._refreshing = True
            epoch, generation, sequence = self._epoch, self._generation, self._sequence + 1
        try:
            result = build_snapshot(
                self.scope, generation, sequence, now=self._now(), registry_path=self._registry_path,
            )
            encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
            if len(encoded.encode("utf-8")) > MAX_RESPONSE_BYTES - 1024:
                raise repos.RepositoryResolutionError("repository map exceeds its response bound")
            error = None
        except (OSError, TypeError, ValueError, RuntimeError) as exc:
            encoded, error = None, str(exc)[:4096]
        except BaseException:
            with self._lock:
                self._refreshing = False
            raise
        with self._lock:
            self._refreshing = False
            if not self._admitting or self._stop.is_set() or self._epoch != epoch:
                return
            self._error = error
            if encoded is not None:
                self._snapshot = encoded
                self._sequence = sequence
            self._reason = "ready" if error is None else "unavailable"

    def read(self, payload: dict) -> dict:
        if (
            not isinstance(payload, dict)
            or set(payload) != {"repository_map_version", "scope"}
            or type(payload.get("repository_map_version")) is not int
            or payload["repository_map_version"] != CONTRACT_VERSION
            or payload.get("scope") != self.scope
        ):
            return self._failure("invalid-request", "invalid repository-map version or scope")
        with self._lock:
            if not self._admitting:
                return self._failure(self._reason, "repository-map admission is closed")
            self._last_demand = self._monotonic()
            encoded, error = self._snapshot, self._error
        if encoded is None:
            return self._failure("unready" if error is None else "unavailable",
                                 error or "repository map has not been published")
        result = json.loads(encoded)
        age = self._now() - result["projection"]["observed_at"]
        if error is not None or not 0 <= age <= MAX_AGE:
            result["inventory_authority"] = "advisory"
            result["projection"]["quality"] = "stale"
            result["availability"] = {
                "status": "stale", "reason": error or "repository map observation expired",
            }
        return result

    def has_active_demand(self) -> bool:
        with self._lock:
            return (
                self._last_demand is not None
                and self._monotonic() - self._last_demand < 30.0
            )

    @staticmethod
    def _failure(code: str, message: str) -> dict:
        return {
            "ok": False, "repository_map_version": CONTRACT_VERSION,
            "error": {"code": code, "message": message},
        }

    def health(self) -> dict:
        with self._lock:
            return {
                "capabilities": [CAPABILITY],
                "repositories": {
                    "status": self._reason, "generation": self._generation,
                    "admitting": self._admitting,
                    "refreshing": self._refreshing,
                },
            }
