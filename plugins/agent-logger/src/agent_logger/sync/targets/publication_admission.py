"""Destination identity-admission gate for :meth:`Target.push`.

Split out of ``targets/filesystem.py`` to stay under its module-size cap
(see ``tools/check-module-size.py``): this is cohesive, self-contained
logic with a single call site, not a widening of the filesystem target
itself.

See the ``session-intelligence-and-accounting`` effort's identity-admission
interface: a ``source_identity`` argument on ``Target.push`` requests
destination identity-admission -- the filesystem target enforces it here
before any write; ``SshTarget``/``IngestTarget`` fail closed instead (no
receiver-side atomic admission exists yet for those transports).
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_logger.sync.lock import sync_lock
from agent_logger.sync.targets.base import PushResult, SourceIdentityLike

#: Persisted at a claimed destination root once a ``source_identity`` push
#: admits it -- the same bounded JSON shape the (forthcoming, peer-owned)
#: ``source_publication`` module's ``.archive-source.json`` CLI input uses.
#: If that upstream schema lands with different field names, treat this as
#: the contract correction to record in the effort README, not a silent
#: local divergence.
PUBLICATION_IDENTITY_MARKER = ".archive-source.json"


def check_publication_identity(
    dest: Path, identity: SourceIdentityLike | None
) -> PushResult | None:
    """Enforce destination identity-admission before any write under *dest*.

    Returns ``None`` when the push may proceed: legacy ``identity=None``
    behavior, a first claim of an empty destination leaf, or an idempotent
    re-push matching the already-persisted marker. Returns a failing
    :class:`PushResult` when admission must be refused outright.

    Comparison, any marker write, and the emptiness check all happen under
    one dedicated destination lock so two concurrent publishers can never
    race past this gate -- the same check/use race already tracked as a
    known gap for every *other* destination write in ``targets/filesystem.py``
    (see ``push_process_logs``'s docstring) is exactly what owning the whole
    sequence under a single lock closes here. Never overwrites or guesses:
    a mismatched marker, or an existing nonempty leaf with no marker at
    all, is refused rather than silently adopted.
    """
    if identity is None:
        return None
    lock_file = dest.parent / f".{dest.name}.publication-admission.lock"
    with sync_lock(lock_file, timeout=30) as acquired:
        if not acquired:
            return PushResult(
                ok=False,
                detail=f"publication admission lock is busy: {lock_file}",
            )
        incoming = {
            "provider": identity.provider,
            "host": identity.host,
            "repository": identity.repository,
            "venue": identity.venue,
        }
        marker_path = dest / PUBLICATION_IDENTITY_MARKER
        existing: dict | None = None
        try:
            if marker_path.exists():
                existing = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return PushResult(
                ok=False, detail=f"unreadable publication marker: {exc}"
            )
        if existing is not None:
            if existing == incoming:
                return None  # idempotent re-push of the same identity
            return PushResult(
                ok=False,
                detail=(
                    f"publication identity mismatch at {marker_path}: "
                    f"destination already claimed by {existing}"
                ),
            )
        try:
            has_content = dest.exists() and any(dest.iterdir())
        except OSError as exc:
            return PushResult(ok=False, detail=f"cannot inspect destination: {exc}")
        if has_content:
            return PushResult(
                ok=False,
                detail=(
                    f"destination {dest} has existing content with no "
                    "publication marker; refusing to claim an unowned leaf"
                ),
            )
        try:
            dest.mkdir(parents=True, exist_ok=True)
            marker_path.write_text(
                json.dumps(incoming, sort_keys=True), encoding="utf-8"
            )
        except OSError as exc:
            return PushResult(ok=False, detail=f"cannot claim destination: {exc}")
        return None
