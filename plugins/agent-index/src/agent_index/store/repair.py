"""Startup self-heal for corrupt LanceDB manifests.

An unclean shutdown (power loss, WSL/host reboot) *during* an in-flight
LanceDB commit can leave a zero-byte manifest/transaction/deletion file on
disk. LanceDB then fails to open the dataset with::

    LanceError(IO): Invalid range 0..0 for object of size 0 bytes

Because the poison file is the *latest* version, the server crash-loops on
startup forever (the FTS build opens the ``chunks`` table, which raises, which
aborts application startup) and the gateway serves 502 to every remote caller.

The failed commit is fully recoverable: LanceDB keeps every prior manifest, so
rolling the version hint back to the last non-empty manifest restores the last
consistent state with zero data loss. This module performs that rollback
automatically at startup -- quarantining the zero-byte artifacts (never
deleting them) and repointing ``_versions/latest_version_hint.json`` at the
newest intact version.

See the 2026-07-06 agent-index 502 incident: eight zero-byte files across the
``chunks``/``vectors_code``/``vectors_prose`` datasets, hand-repaired by exactly
this procedure.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# An optional readiness probe: (lance_dir, table_name) -> None, raising if the
# table's current version hint does not open. See repair_lance_manifests.
OpenChecker = Callable[[Path, str], None]

# LanceDB's V2 manifest-naming scheme stores each version as a file whose stem
# is ``u64::MAX - version`` zero-padded to 20 digits, so a directory listing
# sorts newest-first. Inverting the stem recovers the version number.
_U64_MAX = (1 << 64) - 1


def _version_from_manifest(path: Path) -> int | None:
    """Recover the dataset version encoded in a ``*.manifest`` filename."""
    try:
        return _U64_MAX - int(path.stem)
    except (ValueError, TypeError):
        return None


def _quarantine(src: Path, lance_dir: Path, quarantine_root: Path) -> None:
    """Move a corrupt file aside, preserving its path relative to ``lance_dir``."""
    rel = src.relative_to(lance_dir)
    dest = quarantine_root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dest)
    logger.warning("Quarantined corrupt LanceDB file: %s -> %s", src, dest)


def _lancedb_open_check(lance_dir: Path, table_name: str) -> None:
    """Open ``table_name`` at the current version hint and touch its fragments.

    Raises if the version the hint points at is not genuinely readable (e.g. a
    manifest that is non-empty but references a missing/zero-byte data
    fragment). Used by ``repair_lance_manifests`` to validate a rollback target
    before committing to it. Kept import-local so the module stays cheap to
    import when validation isn't used.
    """
    import lancedb

    lancedb.connect(str(lance_dir)).open_table(table_name).count_rows()


def _select_rollback_version(
    lance_dir: Path,
    table_dir: Path,
    hint_path: Path,
    good: list[Path],
    opener: OpenChecker | None,
) -> int | None:
    """Pick the newest intact version to roll back to.

    Without an ``opener`` this is the newest non-empty manifest (fast, byte-size
    only). With one, walk newest-first and return the first version that
    actually *opens* -- so a manifest that survived as non-empty but references
    broken fragments is skipped in favour of an older, genuinely readable
    version. Returns ``None`` if an opener is given and no version opens.
    """
    versions = sorted(
        (v for m in good if (v := _version_from_manifest(m)) is not None),
        reverse=True,
    )
    if not versions:
        return None
    if opener is None:
        return versions[0]
    for version in versions:
        hint_path.write_text(json.dumps({"version": version}))
        try:
            opener(lance_dir, table_dir.stem)
        except Exception:
            logger.warning(
                "LanceDB table %s version %d is non-empty but does not open; "
                "trying an older version",
                table_dir.name,
                version,
            )
            continue
        return version
    return None


def _repair_table(
    table_dir: Path,
    lance_dir: Path,
    quarantine_root: Path,
    opener: OpenChecker | None,
) -> bool:
    """Repair a single ``<name>.lance`` dataset. Returns True if it was repaired."""
    versions_dir = table_dir / "_versions"
    if not versions_dir.is_dir():
        return False

    # Any zero-byte file under the table is a failed-commit artifact. The fatal
    # one is a zero-byte *manifest*; zero-byte transaction/deletion files are
    # harmless orphans, but we quarantine them all so the tree is clean.
    zero_byte = [p for p in table_dir.rglob("*") if p.is_file() and p.stat().st_size == 0]
    if not zero_byte:
        return False

    manifests = list(versions_dir.glob("*.manifest"))
    good = [m for m in manifests if m.stat().st_size > 0]
    if not good:
        # Every manifest is empty -- nothing to roll back to. Leave the dataset
        # untouched so the operator can restore from a backup snapshot or rebuild;
        # a partial repair here could make recovery harder.
        logger.error(
            "LanceDB table %s has no intact manifest; cannot self-heal "
            "(restore from snapshot or run 'agent-index reindex --full')",
            table_dir.name,
        )
        return False

    for corrupt in zero_byte:
        _quarantine(corrupt, lance_dir, quarantine_root)

    hint_path = versions_dir / "latest_version_hint.json"
    best_version = _select_rollback_version(
        lance_dir, table_dir, hint_path, good, opener
    )
    if best_version is None:
        # An opener was supplied but no surviving version opened. Fall back to
        # the newest non-empty manifest (least-bad) so the hint is coherent, and
        # flag loudly -- the operator likely needs a snapshot restore / rebuild.
        best_version = max(
            v for m in good if (v := _version_from_manifest(m)) is not None
        )
        logger.error(
            "LanceDB table %s: no surviving version opened cleanly; rolled hint "
            "to newest intact manifest %d but the table may still be broken "
            "(restore from snapshot or run 'agent-index reindex --full')",
            table_dir.name,
            best_version,
        )
    hint_path.write_text(json.dumps({"version": best_version}))
    logger.warning(
        "Repaired LanceDB table %s: rolled version hint back to %d",
        table_dir.name,
        best_version,
    )
    return True


def repair_lance_manifests(
    lance_dir: str | Path, *, opener: OpenChecker | None = None
) -> list[str]:
    """Detect and repair zero-byte LanceDB manifests under ``lance_dir``.

    Idempotent and safe to call unconditionally on every startup: it does
    nothing when no zero-byte files are present. Corrupt files are *moved* into
    a timestamped ``lance-quarantine-*`` sibling directory (never deleted) so
    they remain available for forensics. Returns the names of the datasets that
    were repaired (empty when the index is healthy).

    ``opener`` is an optional ``(lance_dir, table_name) -> None`` callable that
    raises if the table's current version hint does not open; when supplied,
    repair validates each candidate rollback version and skips any that survives
    as a non-empty manifest but references broken fragments. Production callers
    pass :func:`_lancedb_open_check`; omit it for a fast, filesystem-only repair.
    """
    lance_dir = Path(lance_dir)
    if not lance_dir.is_dir():
        return []

    quarantine_root = lance_dir.parent / (
        f"lance-quarantine-{datetime.now(tz=UTC).strftime('%Y%m%d-%H%M%S')}"
    )
    repaired: list[str] = []
    for table_dir in sorted(lance_dir.glob("*.lance")):
        try:
            if _repair_table(table_dir, lance_dir, quarantine_root, opener):
                repaired.append(table_dir.name)
        except Exception:
            # A repair failure must never block startup harder than the original
            # corruption would -- log and let the normal open path surface it.
            logger.exception("Self-heal failed for LanceDB table %s", table_dir.name)

    if repaired:
        logger.warning(
            "LanceDB self-heal repaired %d table(s): %s (quarantine: %s)",
            len(repaired),
            ", ".join(repaired),
            quarantine_root,
        )
    return repaired
