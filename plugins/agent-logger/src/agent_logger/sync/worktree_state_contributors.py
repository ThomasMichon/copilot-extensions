"""Strict, lossless consumption of the selected owner's optional projection."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Protocol

from agent_logger.sync.worktree_state import (
    WorktreeStateError,
    WorktreeStateRevision,
    canonical_bytes,
)

MAX_CONTRIBUTOR_BYTES = 1 << 20
MAX_CONTRIBUTORS = 4096


class ContributorReader(Protocol):
    """Caller-negotiated owner reader; never a live-store or session resolver."""

    def __call__(
        self,
        directory: Path,
        *,
        worktree: str,
        store_id: str,
        expected_revision: int,
    ) -> dict[str, Any]: ...


def _fields(value: Any, keys: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise WorktreeStateError("unsupported or incomplete contributor projection")
    return value


def contributor_bytes(
    directory: Path,
    identity: WorktreeStateRevision,
    manifest: bytes,
    read: ContributorReader,
) -> bytes:
    """Preserve every owner tuple while binding it to the exact export cut."""
    value = _fields(
        read(
            directory,
            worktree=identity.worktree,
            store_id=identity.store_id,
            expected_revision=identity.revision,
        ),
        {"schema", "version", "completeness", "manifest_sha256", "source", "contributors"},
    )
    source = _fields(value["source"], {"worktree", "store_id", "revision"})
    if (
        value["schema"] != "efforts.agenda-export-contributors"
        or type(value["version"]) is not int
        or value["version"] != 1
        or value["completeness"] != "complete"
        or value["manifest_sha256"] != hashlib.sha256(manifest).hexdigest()
        or type(source["revision"]) is not int
        or source != {key: identity.as_dict()[key] for key in source}
    ):
        raise WorktreeStateError("contributor projection does not bind the selected export")
    entries = value["contributors"]
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_CONTRIBUTORS:
        raise WorktreeStateError("contributor projection exceeds its complete-result count budget")
    seen: set[tuple[str | None, str | None, str]] = set()
    previous, operations = 0, 0
    for item in entries:
        entry = _fields(
            item,
            {
                "actor",
                "session",
                "qualification",
                "first_revision",
                "last_revision",
                "operations",
            },
        )
        for field in ("actor", "session"):
            text = entry[field]
            if text is not None and (
                not isinstance(text, str)
                or not text.strip()
                or "\0" in text
                or len(text.encode("utf-8")) > 1024
            ):
                raise WorktreeStateError("contributor identity is not owner-valid UTF-8 metadata")
        qualification = "caller-asserted" if entry["actor"] or entry["session"] else "unknown"
        if entry["qualification"] != qualification:
            raise WorktreeStateError(
                "contributor qualification differs from its asserted metadata"
            )
        key = (entry["actor"], entry["session"], qualification)
        first, last, count = (
            entry[name]
            for name in (
                "first_revision",
                "last_revision",
                "operations",
            )
        )
        if (
            key in seen
            or any(type(number) is not int for number in (first, last, count))
            or not previous < first <= last <= identity.revision
            or not 1 <= count <= last - first + 1
        ):
            raise WorktreeStateError(
                "invalid contributor revisions, count or first-acceptance order"
            )
        seen.add(key)
        previous, operations = first, operations + count
    if operations != identity.revision:
        raise WorktreeStateError("contributor projection omits accepted operations")
    raw = canonical_bytes(value)
    if len(raw) > MAX_CONTRIBUTOR_BYTES:
        raise WorktreeStateError("complete contributor projection exceeds its byte budget")
    return raw
