"""Pinned Git revision deltas; reconciliation remains an explicit operation."""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

from agent_index.sources.delta import DeletedFile, SourceDelta
from agent_index.sources.git_repo import _MAX_FILE_BYTES
from agent_index.sources.git_records import decode_git_path

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_index.sources.git_repo import GitRepoConnector


class GitReconciliationRequired(ValueError):
    """A missing or incompatible revision cannot authorize an incremental crawl."""


def discover_git_delta(
    connector: GitRepoConnector,
    last_commit: str | None,
    cancel_check: Callable[[], None] | None = None,
) -> SourceDelta:
    connector._resolve()
    target = connector._index_commit
    if connector._use_worktree or target is None:
        raise GitReconciliationRequired("Git working-tree discovery requires reconciliation")
    if last_commit is None or re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", last_commit) is None:
        raise GitReconciliationRequired("Git cursor is missing or invalid; run an explicit full reindex")
    if cancel_check:
        cancel_check()
    if last_commit == target:
        return SourceDelta(commit=target)
    try:
        connector._git(["merge-base", "--is-ancestor", last_commit, target])
    except subprocess.CalledProcessError as exc:
        raise GitReconciliationRequired(
            "Git cursor is unavailable or history was rewritten; run an explicit full reindex",
        ) from exc
    raw = connector._git_bytes([
        "diff", "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff", "--no-textconv",
        "-z", last_commit, target, "--",
    ])
    records = raw.split(b"\0")
    if records[-1] != b"" or len(records) % 2 != 1:
        raise ValueError("Incomplete Git delta path records")
    result = SourceDelta(commit=target)
    for offset in range(0, len(records) - 1, 2):
        if cancel_check:
            cancel_check()
        header, raw_path = records[offset:offset + 2]
        fields = header.decode("ascii").split()
        if (
            len(fields) != 5 or not fields[0].startswith(":")
            or fields[4] not in {"A", "M", "D", "T"} or not raw_path
        ):
            raise ValueError("Invalid Git delta path record")
        _, mode, _, blob, status = fields
        path = decode_git_path(raw_path)
        if path is None:
            continue
        if not connector._is_indexable_path(path):
            continue
        entry = None
        if status != "D" and mode in {"100644", "100755", "120000"}:
            if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", blob) is None:
                raise ValueError("Invalid Git delta blob identity")
            size = int(connector._git(["cat-file", "-s", blob]).strip())
            if size < 0:
                raise ValueError("Invalid Git delta blob size")
            if size <= _MAX_FILE_BYTES:
                content = connector._git_bytes(["cat-file", "blob", blob])
                if len(content) != size:
                    raise ValueError("Incomplete Git delta blob")
                entry = connector._file_entry(path, target, content)
        if entry is None:
            result.deletions.append(DeletedFile(connector.source_name, path))
        else:
            result.upserts.append(entry)
    result.upserts.extend(connector._discover_commits(
        last_commit, cancel_check=cancel_check, strict=True,
    ))
    result.validate(connector.source_name)
    return result
