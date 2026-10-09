"""Canonical source ownership admission for a locked filesystem publisher."""

from __future__ import annotations

import json
import os
from pathlib import Path

from agent_logger.source_roots import (
    SOURCE_METADATA_MEMBER,
    SourceIdentity,
    SourceLayoutError,
    read_source_metadata,
    validate_source_key,
)
from agent_logger.sync.provenance import (
    ensure_real_directory,
    existing_real_directory,
)

_CASE_INSENSITIVE = os.name == "nt"


def load_source_identity_file(path: Path) -> SourceIdentity:
    """Load required producer identity without inferring it from a physical alias."""
    identity, _, _ = read_source_metadata(path)
    return identity


def validate_publication_key(key: str, identity: SourceIdentity) -> str:
    """New publication may use only the identity's canonical namespace."""
    validate_source_key(key)
    if key != identity.namespace:
        raise SourceLayoutError(
            f"publication key does not match canonical source identity: {key!r}"
        )
    return key


def _directory(path: Path) -> Path:
    result = existing_real_directory(path)
    if result is None:
        raise SourceLayoutError(f"missing or unsafe publication directory: {path}")
    return result


def _child(parent: Path, name: str) -> Path:
    if _CASE_INSENSITIVE:
        for entry in parent.iterdir():
            if entry.name.casefold() == name.casefold() and entry.name != name:
                raise SourceLayoutError(f"case-conflicting publication path: {entry}")
    path = parent / name
    try:
        path.lstat()
    except FileNotFoundError:
        ensure_real_directory(path)
    return _directory(path)


def admit_publication_source(
    corpus_root: Path,
    key: str,
    identity: SourceIdentity,
) -> Path:
    """Claim an empty canonical leaf or verify its existing source ownership.

    The caller must hold its existing destination sync lock across this call
    and all subsequent session/provenance writes. This helper does not provide
    a second lock or a descriptor-pinned transaction over mutable ancestors.
    """
    validate_publication_key(key, identity)
    path = _directory(corpus_root)
    for component in key.split("/"):
        path = _child(path, component)
    marker = path / SOURCE_METADATA_MEMBER
    try:
        marker.lstat()
    except FileNotFoundError:
        if any(path.iterdir()):
            raise SourceLayoutError(
                f"cannot claim a nonempty source without identity metadata: {path}"
            ) from None
        payload = {
            "schema_version": 1,
            **identity.to_dict(),
            "legacy_aliases": [],
        }
        encoded = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        with os.fdopen(os.open(marker, flags, 0o600), "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    recorded, _, _ = read_source_metadata(marker)
    if recorded != identity:
        raise SourceLayoutError(f"publication source identity collision: {path}")
    return path
