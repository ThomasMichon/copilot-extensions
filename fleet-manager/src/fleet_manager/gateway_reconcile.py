"""Process-local desired-state publication for an explicitly trusted source.

The source owner is selected by the embedding deployment. Neither bytes, hashes
nor this store authenticate that owner. This prerequisite publishes desired state
only, not identity-verifier state, active connections or backend permits.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import NoReturn

from .gateway_manifest import GatewayManifest, ManifestError
from .planning import PlanError, identifier


class ReconcileError(ValueError):
    """A value-free refusal that never includes source contents or exceptions."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ManifestSnapshot:
    """An immutable desired snapshot, not a credential or execution permit."""

    manifest: GatewayManifest
    digest: str
    generation: int


@dataclass(frozen=True)
class Publication:
    snapshot: ManifestSnapshot
    changed: bool


@dataclass(frozen=True)
class ReconcileStatus:
    revision: int | None
    digest: str | None
    generation: int
    slots: int
    last_error: str | None


class ManifestStore:
    """Serialize validated publications while keeping source I/O off the lock.

    Revision rollback is fenced for this store's lifetime only. A durable source
    owner must retain its own accepted revision across process replacement.
    Readers must re-check current ownership at the actual admission/effect
    boundary; retaining an old snapshot does not authorize its continued use.
    """

    def __init__(
        self, *, fleet: str, gateway: str, source: Callable[[], bytes]
    ) -> None:
        try:
            identifier(fleet, "fleet")
            identifier(gateway, "gateway")
        except PlanError:
            raise ReconcileError("invalid-owner") from None
        if not callable(source):
            raise ReconcileError("invalid-source")
        self._fleet = fleet
        self._gateway = gateway
        self._source = source
        self._lock = RLock()
        self._snapshot: ManifestSnapshot | None = None
        self._last_error: str | None = None

    def snapshot(self) -> ManifestSnapshot:
        """Capture one coherent desired revision without reading its source."""
        with self._lock:
            if self._snapshot is None:
                raise ReconcileError("not-configured")
            return self._snapshot

    def status(self) -> ReconcileStatus:
        """Report publication evidence without trust references or source values."""
        with self._lock:
            current = self._snapshot
            return ReconcileStatus(
                revision=current.manifest.revision if current else None,
                digest=current.digest if current else None,
                generation=current.generation if current else 0,
                slots=len(current.manifest.slots) if current else 0,
                last_error=self._last_error,
            )

    def reload(self) -> Publication:
        """Validate a complete owner candidate before an atomic pointer swap."""
        try:
            raw = self._source()
        except OSError:
            self._fail("source-unavailable")
        try:
            candidate = GatewayManifest.parse(raw)
        except ManifestError as exc:
            self._fail(exc.code)
        if candidate.fleet != self._fleet or candidate.gateway != self._gateway:
            self._fail("owner-mismatch")
        content_digest = candidate.digest
        with self._lock:
            current = self._snapshot
            if current is not None:
                if candidate.revision < current.manifest.revision:
                    self._fail("stale-revision")
                if candidate.revision == current.manifest.revision:
                    if content_digest != current.digest:
                        self._fail("revision-conflict")
                    self._last_error = None
                    return Publication(current, False)
            next_generation = current.generation + 1 if current else 1
            published = ManifestSnapshot(candidate, content_digest, next_generation)
            self._snapshot = published
            self._last_error = None
            return Publication(published, True)

    def _fail(self, code: str) -> NoReturn:
        with self._lock:
            self._last_error = code
        raise ReconcileError(code) from None
