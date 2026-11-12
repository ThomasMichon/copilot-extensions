"""Bounded read-only bytes for the existing claim-handoff registry owner."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import yaml


def load(path: Path, *, maximum_bytes, version, parse_bundle, error_type):
    if not (os.path.lexists(path) if maximum_bytes is not None else path.exists()):
        return []
    try:
        if maximum_bytes is None:
            from . import tracking

            data = yaml.safe_load(tracking._read_text_with_retry(path))
        else:
            data = yaml.safe_load(read(path, maximum_bytes))
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise error_type(f"cannot read claim-handoff registry {path}: {exc}") from exc
    if not isinstance(data, dict) or data.get("version") != version:
        raise error_type(f"invalid claim-handoff registry: {path}")
    raw_bundles = data.get("bundles")
    if not isinstance(raw_bundles, list):
        raise error_type(f"invalid claim-handoff bundle list: {path}")
    if maximum_bytes is not None and len(raw_bundles) > 10_000:
        raise error_type("claim-handoff bundle inventory exceeds its read budget")
    bundles = [parse_bundle(raw) for raw in raw_bundles]
    ids = [bundle.bundle_id for bundle in bundles]
    if len(set(ids)) != len(ids):
        raise error_type("claim-handoff registry contains duplicate bundle ids")
    return bundles


def read(path: Path, maximum_bytes: int) -> bytes:
    before = path.lstat()
    if (
        not stat.S_ISREG(before.st_mode)
        or getattr(before, "st_file_attributes", 0) & 0x400
        or before.st_size > maximum_bytes
    ):
        raise ValueError("claim-handoff registry is redirected or exceeds its read budget")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(maximum_bytes + 1)
        after = os.fstat(stream.fileno())
    named = path.lstat()
    if (
        len(raw) > maximum_bytes
        or not all(os.path.samestat(before, item) for item in (opened, after, named))
        or any((item.st_size, item.st_mtime_ns) != (before.st_size, before.st_mtime_ns)
               for item in (opened, after, named))
    ):
        raise ValueError("claim-handoff registry changed during bounded inspection")
    return raw
