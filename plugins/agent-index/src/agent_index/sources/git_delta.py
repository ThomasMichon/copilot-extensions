"""Pinned Git revision deltas; reconciliation remains an explicit operation."""

from __future__ import annotations

import re
import subprocess
from typing import TYPE_CHECKING

from agent_index.sources.delta import DeletedFile, SourceDelta
from agent_index.sources.base import FileEntry
from agent_index.sources.git_repo import _MAX_FILE_BYTES
from agent_index.sources.git_records import decode_git_path

if TYPE_CHECKING:
    from collections.abc import Callable

    from agent_index.sources.git_repo import GitRepoConnector


class GitReconciliationRequired(ValueError):
    """A missing or incompatible revision cannot authorize an incremental crawl."""


def immutable_oid(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", value) is not None


def range_records(
    connector: GitRepoConnector, base: str, target: str,
) -> list[tuple[str, str, str, str]]:
    """Decode immutable raw diff records shared by polling and event acquisition."""
    if not immutable_oid(base) or not immutable_oid(target):
        raise ValueError("Invalid Git delta revision identity")
    raw = connector._git_bytes([
        "diff", "--raw", "--no-abbrev", "--no-renames", "--no-ext-diff", "--no-textconv",
        "-z", base, target, "--",
    ])
    records = raw.split(b"\0")
    if records[-1] != b"" or len(records) % 2 != 1:
        raise ValueError("Incomplete Git delta path records")
    result = []
    for offset in range(0, len(records) - 1, 2):
        header, raw_path = records[offset:offset + 2]
        fields = header.decode("ascii").split()
        if (
            len(fields) != 5 or not fields[0].startswith(":")
            or fields[4] not in {"A", "M", "D", "T"} or not raw_path
        ):
            raise ValueError("Invalid Git delta path record")
        _, mode, _, blob, status = fields
        path = decode_git_path(raw_path)
        if path is not None:
            result.append((path, mode, blob, status))
    return result


def read_delta_file(
    connector: GitRepoConnector, path: str, target: str, mode: str, blob: str, status: str,
) -> FileEntry | None:
    """Read a size-bounded immutable blob; failures never become tombstones."""
    if status == "D" or mode not in {"100644", "100755", "120000"}:
        return None
    if not immutable_oid(blob):
        raise ValueError("Invalid Git delta blob identity")
    size = int(connector._git(["cat-file", "-s", blob]).strip())
    if size < 0:
        raise ValueError("Invalid Git delta blob size")
    if size > _MAX_FILE_BYTES:
        return None
    content = connector._git_bytes(["cat-file", "blob", blob])
    if len(content) != size:
        raise ValueError("Incomplete Git delta blob")
    return connector._file_entry(path, target, content)


def read_current_file(connector: GitRepoConnector, path: str, target: str) -> FileEntry | None:
    raw = connector._git_bytes(["ls-tree", "-z", target, "--", f":(literal){path}"])
    if not raw:
        return None
    records = raw.split(b"\0")
    if len(records) != 2 or records[-1] != b"" or b"\t" not in records[0]:
        raise ValueError("Invalid Git hinted tree record")
    header, raw_path = records[0].split(b"\t", 1)
    fields = header.decode("ascii").split()
    if (
        len(fields) != 3 or decode_git_path(raw_path) != path
        or not immutable_oid(fields[2])
        or (fields[0], fields[1]) not in {
            ("100644", "blob"), ("100755", "blob"), ("120000", "blob"),
            ("040000", "tree"), ("160000", "commit"),
        }
    ):
        raise ValueError("Invalid Git hinted tree identity")
    return read_delta_file(connector, path, target, fields[0], fields[2], "M")


def discover_git_delta(
    connector: GitRepoConnector,
    last_commit: str | None,
    cancel_check: Callable[[], None] | None = None,
    *,
    refresh_paths: tuple[str, ...] = (),
    refresh_commits: tuple[str, ...] = (),
) -> SourceDelta:
    connector._resolve()
    target = connector._index_commit
    if connector._use_worktree or target is None:
        raise GitReconciliationRequired("Git working-tree discovery requires reconciliation")
    if not immutable_oid(last_commit):
        raise GitReconciliationRequired("Git cursor is missing or invalid; run an explicit full reindex")
    assert last_commit is not None
    if cancel_check:
        cancel_check()
    if last_commit == target and not refresh_paths and not refresh_commits:
        return SourceDelta(commit=target)
    history: dict[str, str] = {}
    for path in sorted(set(refresh_commits)):
        if cancel_check:
            cancel_check()
        oid = path.removeprefix("commits/").removesuffix(".txt")
        if not immutable_oid(oid) or path != f"commits/{oid}.txt":
            raise GitReconciliationRequired("Invalid hinted Git history; run an explicit full reindex")
        try:
            connector._git(["merge-base", "--is-ancestor", oid, target])
        except subprocess.CalledProcessError as exc:
            raise GitReconciliationRequired(
                "Hinted Git history is unavailable or was rewritten; run an explicit full reindex",
            ) from exc
        history[path] = oid
    try:
        connector._git(["merge-base", "--is-ancestor", last_commit, target])
    except subprocess.CalledProcessError as exc:
        raise GitReconciliationRequired(
            "Git cursor is unavailable or history was rewritten; run an explicit full reindex",
        ) from exc
    result = SourceDelta(commit=target)
    seen: set[str] = set()
    for path, mode, blob, status in range_records(connector, last_commit, target):
        if cancel_check:
            cancel_check()
        if not connector._is_indexable_path(path):
            continue
        seen.add(path)
        entry = read_delta_file(connector, path, target, mode, blob, status)
        if entry is None:
            result.deletions.append(DeletedFile(connector.source_name, path))
        else:
            result.upserts.append(entry)
    for path in sorted(set(refresh_paths) - seen):
        if cancel_check:
            cancel_check()
        entry = read_current_file(connector, path, target)
        if entry is None:
            result.deletions.append(DeletedFile(connector.source_name, path))
        else:
            result.upserts.append(entry)
    commits = {entry.path: entry for entry in connector._discover_commits(
        last_commit, cancel_check=cancel_check, strict=True,
    )}
    for path in sorted(history.keys() - commits.keys()):
        entries = connector._discover_commits(
            None, target_commit=history[path], max_count=1,
            cancel_check=cancel_check, strict=True,
        )
        if len(entries) != 1 or entries[0].path != path:
            raise GitReconciliationRequired(
                "Incomplete hinted Git history refresh; run an explicit full reindex",
            )
        commits[path] = entries[0]
    result.upserts.extend(commits.values())
    result.validate(connector.source_name)
    return result
