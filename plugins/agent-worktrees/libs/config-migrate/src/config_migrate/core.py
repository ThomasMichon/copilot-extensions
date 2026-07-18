"""Core migration primitives: ``migrate_doc`` (in-memory) and ``migrate_file``.

Two call sites, one migrator registry -- the *migrate-by-rewrite* model:

* ``migrate_doc`` applies the ordered ``vN->vN+1`` migrators to a parsed config
  document **in memory** and returns ``(new_doc, changed)``. This is the
  **loader's lazy path**: a strict current-only loader would break on a still-old
  config read *before* ``install``/``update`` runs on that machine, so the loader
  reuses the same migrators to reach the current shape in memory on every read.
* ``migrate_file`` reads a YAML file, runs ``migrate_doc``, and -- only when the
  document changed -- **persists** the result atomically (temp + rename) with a
  ``.bak`` backup. This is the **install/update eager path**: on-disk config
  converges to the current shape so the loader ultimately targets one shape.

Safety properties both paths guarantee:

* **Idempotent** -- a second run over an already-current file is a no-op.
* **Atomic** -- an interrupted ``migrate_file`` leaves the original intact (the
  new content is written to a temp file and ``os.replace``'d in one step).
* **Backed up** -- the pre-migration file is copied to ``<name>.bak`` before the
  swap, enabling rollback.
* **Fail-closed on newer** -- a document whose recorded version is *greater* than
  ``current_version`` raises ``NewerThanCurrentError`` rather than lossily
  downgrading. A document older than the supported migration window fails the
  same way (a missing migrator surfaces as ``SchemaError`` at registration).

Formatting: when a migration adds *only* the version marker (no shape change --
the baseline stamp), the marker is inserted **textually** so existing comments
and hand-formatting survive. When a real ``vN->vN+1`` transform runs (the shape
genuinely changes), the document is reserialized via ``yaml.safe_dump`` -- the
rewrite the shape change already implies.
"""

from __future__ import annotations

import copy
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .registry import SchemaError, SchemaRegistry

# Top-level marker key stamped onto every managed config document.
SCHEMA_VERSION_KEY = "schema_version"


class MigrationError(SchemaError):
    """A config document could not be migrated."""


class NewerThanCurrentError(MigrationError):
    """The document records a version newer than the registry's current version.

    Fail-closed: a newer on-disk config was written by a newer build; silently
    downgrading it would lose information. The caller should surface a clear
    "update this plugin" message rather than rewrite the file.
    """


def read_version(doc: dict[str, Any], *, baseline: int = 1) -> int:
    """Return the recorded ``schema_version`` of a parsed document.

    An absent marker means the document predates versioning and is treated as
    ``baseline`` (never "unknown"). A present-but-invalid marker is an error.
    """
    if SCHEMA_VERSION_KEY not in doc:
        return baseline
    raw = doc[SCHEMA_VERSION_KEY]
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise MigrationError(
            f"invalid {SCHEMA_VERSION_KEY}: {raw!r} (expected a positive integer)"
        )
    return raw


def migrate_doc(
    doc: dict[str, Any],
    schema_id: str,
    registry: SchemaRegistry,
) -> tuple[dict[str, Any], bool]:
    """Migrate a parsed config document to its schema's current version.

    Returns ``(new_doc, changed)``. ``changed`` is True when the marker was
    added or the version advanced -- i.e. when a persist is warranted. The input
    document is never mutated.

    Raises:
        NewerThanCurrentError: the document is newer than the current version.
        MigrationError / SchemaError: an invalid marker or an unregistered
            schema.
    """
    spec = registry.get(schema_id)
    if not isinstance(doc, dict):
        raise MigrationError(
            f"{schema_id}: config root must be a mapping, got {type(doc).__name__}"
        )

    marker_present = SCHEMA_VERSION_KEY in doc
    version = read_version(doc, baseline=spec.baseline_version)
    current = spec.current_version

    if version > current:
        raise NewerThanCurrentError(
            f"{schema_id}: on-disk schema_version {version} is newer than the supported "
            f"version {current} -- update the plugin (refusing to downgrade)"
        )

    new_doc = copy.deepcopy(doc)
    for n in range(version, current):
        migrator = spec.migrators.get(n)
        if migrator is None:  # pragma: no cover - guarded at registration
            raise MigrationError(f"{schema_id}: missing vN->vN+1 migrator for v{n}")
        new_doc = migrator(new_doc)
        if not isinstance(new_doc, dict):
            raise MigrationError(
                f"{schema_id}: migrator v{n}->v{n + 1} returned {type(new_doc).__name__}, "
                "expected a mapping"
            )
        new_doc[SCHEMA_VERSION_KEY] = n + 1

    # Ensure the marker reflects the current version (covers the baseline-stamp
    # case where no migrator ran but the marker was absent).
    new_doc[SCHEMA_VERSION_KEY] = current
    changed = (version != current) or (not marker_present)
    return new_doc, changed


@dataclass(frozen=True)
class MigrationResult:
    """Outcome of a single ``migrate_file`` call."""

    path: Path
    schema_id: str
    changed: bool
    from_version: int
    to_version: int
    skipped: bool = False
    reason: str = ""

    def summary(self) -> str:
        name = self.path.name
        if self.skipped:
            return f"{name}: skipped ({self.reason})"
        if not self.changed:
            return f"{name}: up to date (v{self.to_version})"
        if self.from_version == self.to_version:
            # Marker added on an already-current-shape file (baseline stamp).
            return f"{name}: stamped v{self.to_version}"
        return f"{name}: migrated v{self.from_version} -> v{self.to_version}"


def _insert_marker_textually(raw_text: str, version: int) -> str:
    """Insert ``schema_version: <version>`` after leading comments/blank lines.

    Preserves all existing content (comments, formatting, key order) -- used for
    the no-shape-change baseline stamp so a hand-formatted file keeps its shape.
    """
    lines = raw_text.splitlines(keepends=True)
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].lstrip()
        if stripped == "" or stripped.startswith("#"):
            idx += 1
            continue
        break
    stamp = f"{SCHEMA_VERSION_KEY}: {version}\n"
    # Guarantee the inserted line ends with a newline even if the preceding
    # comment block did not.
    if idx > 0 and lines[idx - 1] and not lines[idx - 1].endswith("\n"):
        lines[idx - 1] = lines[idx - 1] + "\n"
    lines.insert(idx, stamp)
    return "".join(lines)


def _atomic_write(path: Path, text: str, *, backup: bool) -> None:
    """Write ``text`` to ``path`` atomically, backing up any prior file first."""
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(directory), prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if backup and path.exists():
            shutil.copy2(path, path.with_name(path.name + ".bak"))
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def migrate_file(
    path: Path | str,
    schema_id: str,
    registry: SchemaRegistry,
    *,
    backup: bool = True,
) -> MigrationResult:
    """Migrate a YAML config file in place, atomically, if it is out of date.

    A missing file is skipped (nothing to migrate). A file that is already
    current is a no-op (idempotent). Otherwise the migrated document is written
    atomically with a ``.bak`` backup; when the only change is the version marker
    the marker is inserted textually to preserve comments/formatting.

    Raises:
        NewerThanCurrentError: the file is newer than the current version.
        MigrationError: malformed YAML or an invalid marker.
    """
    path = Path(path)
    spec = registry.get(schema_id)
    if not path.exists():
        return MigrationResult(
            path=path,
            schema_id=schema_id,
            changed=False,
            from_version=spec.current_version,
            to_version=spec.current_version,
            skipped=True,
            reason="file not found",
        )

    raw_text = path.read_text(encoding="utf-8")
    try:
        parsed = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise MigrationError(f"{path}: malformed YAML: {exc}") from exc

    doc: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
    from_version = read_version(doc, baseline=spec.baseline_version)
    new_doc, changed = migrate_doc(doc, schema_id, registry)

    if not changed:
        return MigrationResult(
            path=path,
            schema_id=schema_id,
            changed=False,
            from_version=from_version,
            to_version=spec.current_version,
        )

    shape_changed = from_version != spec.current_version
    if shape_changed:
        # A real vN->vN+1 transform ran: the shape changed, so reserialize.
        header = f"{SCHEMA_VERSION_KEY}: {spec.current_version}\n"
        body = {k: v for k, v in new_doc.items() if k != SCHEMA_VERSION_KEY}
        dumped = yaml.safe_dump(
            body, default_flow_style=False, sort_keys=False, allow_unicode=True
        )
        new_text = header + (dumped if body else "")
    else:
        # Baseline stamp only (no shape change): preserve the file textually.
        new_text = _insert_marker_textually(raw_text, spec.current_version)

    _atomic_write(path, new_text, backup=backup)
    return MigrationResult(
        path=path,
        schema_id=schema_id,
        changed=True,
        from_version=from_version,
        to_version=spec.current_version,
    )
