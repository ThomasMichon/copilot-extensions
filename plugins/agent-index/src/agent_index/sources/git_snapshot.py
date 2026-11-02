"""Pinned Git full-snapshot pages under the shared acquisition journal."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agent_index.sources.delta import SnapshotPage
from agent_index.sources.git_delta import immutable_oid, read_delta_file
from agent_index.sources.git_records import decode_git_path

if TYPE_CHECKING:
    from agent_index.sources.git_repo import GitRepoConnector

_PAGE_SIZE = 128
_MAX_TREE_BYTES = 16_000_000
_MAX_TOKEN_BYTES = 65536
_PREFIX = "git-snapshot-v1:"


@dataclass
class _Cursor:
    files: list[tuple[str, int]] | None = None
    history: int = 0
    previous: str | None = None

    def encode(self) -> str:
        return json.dumps(
            {"files": self.files} if self.files
            else {"history": self.history, "previous": self.previous}
        )


def _cursor(value: str | None) -> _Cursor:
    if value is None:
        return _Cursor(files=[("", 0)])
    if not isinstance(value, str) or not value or len(value.encode()) > _MAX_TOKEN_BYTES:
        raise ValueError("Invalid Git snapshot cursor")
    data = json.loads(value)
    if not isinstance(data, dict):
        raise ValueError("Invalid Git snapshot cursor")
    if set(data) == {"history", "previous"}:
        offset, previous = data["history"], data["previous"]
        if type(offset) is not int or offset < 0 or (
            previous is not None if offset == 0 else not immutable_oid(previous)
        ):
            raise ValueError("Invalid Git snapshot history offset")
        return _Cursor(history=offset, previous=previous)
    if set(data) != {"files"} or not isinstance(data["files"], list) or not data["files"]:
        raise ValueError("Invalid Git snapshot tree cursor")
    frames: list[tuple[str, int]] = []
    for index, frame in enumerate(data["files"]):
        if (
            not isinstance(frame, list) or len(frame) != 2
            or not isinstance(frame[0], str) or "\0" in frame[0]
            or type(frame[1]) is not int or not 0 <= frame[1] <= _MAX_TREE_BYTES
        ):
            raise ValueError("Invalid Git snapshot tree frame")
        component = frame[0]
        valid = component == "" if index == 0 else (
            component not in {"", ".", ".."} and "/" not in component
        )
        if not valid:
            raise ValueError("Invalid Git snapshot directory ancestry")
        frames.append((component, frame[1]))
    return _Cursor(files=frames)


def _stable_graph(connector: GitRepoConnector, target: str) -> None:
    if connector._git(["cat-file", "-t", target]).strip() != "commit":
        raise ValueError("Git full snapshot requires an available commit object")
    if connector._git(["rev-parse", "--is-shallow-repository"]).strip() != "false":
        raise ValueError("Git full snapshots require complete history, not a shallow repository")
    grafts = Path(connector._git(["rev-parse", "--git-path", "info/grafts"]).strip())
    if not grafts.is_absolute():
        grafts = connector.repo_path / grafts
    if grafts.exists():
        raise ValueError("Git full snapshots do not support mutable history grafts")


def _tree(connector: GitRepoConnector, target: str, directory: str) -> bytes:
    identity = f"{target}:{directory}"
    size = int(connector._git(["cat-file", "-s", identity]).strip())
    if not 0 <= size <= _MAX_TREE_BYTES:
        raise ValueError("Git snapshot directory exceeds the 16000000-byte tree limit")
    raw = connector._git_bytes(["cat-file", "tree", identity])
    if len(raw) != size:
        raise ValueError("Incomplete Git snapshot tree object")
    return raw


def _record(raw: bytes, offset: int, oid_bytes: int) -> tuple[str, bytes, str, int]:
    separator = raw.find(b" ", offset)
    end = raw.find(b"\0", separator + 1)
    following = end + 1 + oid_bytes
    if separator < offset or end < separator or following > len(raw):
        raise ValueError("Invalid Git snapshot tree record")
    mode = raw[offset:separator].decode("ascii")
    name = raw[separator + 1:end]
    if mode not in {"40000", "100644", "100755", "120000", "160000"} or (
        not name or b"/" in name or name in {b".", b".."}
    ):
        raise ValueError("Invalid Git snapshot tree entry")
    return mode, name, raw[end + 1:following].hex(), following


def _restore_offset(
    raw: bytes, offset: int, oid_bytes: int,
) -> tuple[str, bytes, str, int] | None:
    boundary = 0
    previous = None
    while boundary < offset:
        previous = _record(raw, boundary, oid_bytes)
        boundary = previous[3]
    if boundary != offset:
        raise ValueError("Git snapshot offset is not a tree-record boundary")
    return previous


def _pop_tree(
    connector: GitRepoConnector, target: str, stack: list[tuple[str, int]],
) -> None:
    if len(stack) > 1:
        parent = "/".join(name for name, _ in stack[1:-1])
        raw = _tree(connector, target, parent)
        previous = _restore_offset(raw, stack[-2][1], len(target) // 2)
        if previous is None or previous[:2] != ("40000", stack[-1][0].encode()):
            raise ValueError("Git snapshot parent continuation does not match its child")
    stack.pop()


def _files(
    connector: GitRepoConnector, target: str, stack: list[tuple[str, int]],
    cancel_check: Callable[[], None] | None,
) -> SnapshotPage:
    component, offset = stack[-1]
    directory = "/".join(name for name, _ in stack[1:])
    raw = _tree(connector, target, directory)
    if offset > len(raw):
        raise ValueError("Git snapshot tree offset exceeds its pinned object")
    entries = []
    oid_bytes = len(target) // 2
    _restore_offset(raw, offset, oid_bytes)
    for _ in range(_PAGE_SIZE):
        if cancel_check:
            cancel_check()
        if offset == len(raw):
            _pop_tree(connector, target, stack)
            break
        mode, name, blob, offset = _record(raw, offset, oid_bytes)
        stack[-1] = (component, offset)
        decoded = decode_git_path(name)
        if decoded is None:
            continue
        path = f"{directory}/{decoded}" if directory else decoded
        if mode == "40000":
            stack.append((decoded, 0))
            break
        if connector._is_indexable_path(path):
            entry = read_delta_file(connector, path, target, mode, blob, "M")
            if entry is not None:
                entries.append(entry)
    return SnapshotPage(_PREFIX + target, entries, target, _Cursor(files=stack).encode())


def discover_git_snapshot(
    connector: GitRepoConnector, snapshot: str | None, cursor: str | None,
    *, cancel_check: Callable[[], None] | None = None,
) -> SnapshotPage:
    """Return bounded result pages, not a Git history traversal/time bound.

    Directory objects have a fixed byte ceiling. Native ``log --skip`` bounds
    returned records but may revisit ancestors internally on later pages.
    """
    if cancel_check:
        cancel_check()
    if snapshot is None:
        if cursor is not None:
            raise ValueError("Git snapshot cursor requires its pinned snapshot")
        connector._resolve()
        if connector._use_worktree:
            raise ValueError("Git working-tree discovery requires legacy reconciliation")
        target = connector._index_commit
    elif isinstance(snapshot, str) and snapshot.startswith(_PREFIX):
        target = snapshot.removeprefix(_PREFIX)
    else:
        raise ValueError("Invalid Git snapshot identity")
    if not immutable_oid(target):
        raise ValueError("Invalid Git snapshot commit identity")
    assert target is not None
    data = _cursor(cursor)
    _stable_graph(connector, target)
    if data.files is not None:
        page = _files(connector, target, data.files, cancel_check)
    else:
        offset = data.history
        entries = connector._discover_commits(
            None, target_commit=target, max_count=_PAGE_SIZE + int(offset > 0),
            skip=max(0, offset - 1),
            cancel_check=cancel_check, strict=True,
        )
        if offset:
            if not entries or entries[0].path != f"commits/{data.previous}.txt":
                raise ValueError("Git snapshot history cursor does not match its pinned predecessor")
            entries = entries[1:]
        if len(entries) > _PAGE_SIZE:
            raise ValueError("Git snapshot history exceeded its requested page")
        next_cursor = _Cursor(
            history=offset + len(entries), previous=entries[-1].metadata["sha"],
        ).encode() if (
            len(entries) == _PAGE_SIZE
        ) else None
        page = SnapshotPage(_PREFIX + target, entries, target, next_cursor)
    page.validate(connector.source_name)
    return page
