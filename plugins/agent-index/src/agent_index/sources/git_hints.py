"""Git event ranges nominate identities; the configured pinned ref owns content."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from typing import TYPE_CHECKING

from agent_index.indexing.change_hints import ChangeHints
from agent_index.sources.delta import DeletedFile, SourceDelta
from agent_index.sources.git_delta import (
    GitReconciliationRequired, immutable_oid, range_records, read_current_file,
)

if TYPE_CHECKING:
    from agent_index.sources.base import FileEntry
    from agent_index.sources.git_repo import GitRepoConnector


def acquire_hints(
    connector: GitRepoConnector, hints: ChangeHints, last_commit: str | None,
    *, cancel_check: Callable[[], None] | None = None,
) -> SourceDelta:
    if (
        type(hints) is not ChangeHints or hints.source != connector._requested_source
        or connector.hint_scope != (hints.canonical_family, hints.account_id)
    ):
        raise ValueError("Git hints require the exact configured source/family/account")
    if hints.item_ids or hints.metadata_revisions or not hints.ref_ranges:
        raise ValueError("Git hints require ref ranges without work-item identities")
    if any(not immutable_oid(oid) for pair in hints.ref_ranges for oid in (pair.base, pair.target)):
        raise ValueError("Git hints require immutable revision identities")
    if cancel_check:
        cancel_check()
    connector._resolve()
    target = connector._index_commit
    if connector._use_worktree or not immutable_oid(target):
        raise GitReconciliationRequired("Git hints require a configured immutable ref")
    assert target is not None
    paths: set[str] = set()
    commits: dict[str, FileEntry] = {}
    for pair in hints.ref_ranges:
        if cancel_check:
            cancel_check()
        try:
            connector._git(["merge-base", "--is-ancestor", pair.base, pair.target])
            connector._git(["merge-base", "--is-ancestor", pair.target, target])
        except subprocess.CalledProcessError as exc:
            raise GitReconciliationRequired(
                "Git hint history is unavailable or outside the configured ref; reconcile explicitly",
            ) from exc
        paths.update(
            path for path, _, _, _ in range_records(connector, pair.base, pair.target)
            if connector._is_indexable_path(path)
        )
        for history_entry in connector._discover_commits(
            pair.base, target_commit=pair.target, cancel_check=cancel_check, strict=True,
        ):
            commits[history_entry.path] = history_entry
    result = SourceDelta(commit=last_commit)
    for path in sorted(paths):
        if cancel_check:
            cancel_check()
        entry = read_current_file(connector, path, target)
        if entry is None:
            result.deletions.append(DeletedFile(connector.source_name, path))
        else:
            result.upserts.append(entry)
    result.upserts.extend(commits[key] for key in sorted(commits))
    if cancel_check:
        cancel_check()
    result.validate(connector.source_name)
    return result
