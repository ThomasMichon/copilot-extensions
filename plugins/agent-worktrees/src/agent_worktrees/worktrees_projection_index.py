"""Writer-built Worktrees selectors; point reads never enumerate a roster."""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

INDEX_SCHEMA = """
CREATE TABLE IF NOT EXISTS worktrees_projection_scope (
    project TEXT NOT NULL,
    scope TEXT NOT NULL,
    ready INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    PRIMARY KEY (project, scope)
);
CREATE TABLE IF NOT EXISTS worktrees_projection_member (
    project TEXT NOT NULL,
    scope TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY (project, scope, worktree_id)
);
CREATE TABLE IF NOT EXISTS worktrees_projection_selector (
    project TEXT NOT NULL,
    scope TEXT NOT NULL,
    selector TEXT NOT NULL,
    worktree_id TEXT,
    match_count INTEGER NOT NULL,
    preview TEXT NOT NULL,
    PRIMARY KEY (project, scope, selector)
);
"""
_CODENAME_SCOPE = "codename"
_PREVIEW_LIMIT = 8


class ProjectionError(ValueError):
    """A publication, selector or durable value violates the row contract."""


class ProjectionUnavailable(RuntimeError):
    """No complete, usable projection exists for the requested selection."""


class ProjectionOwnershipChanged(ProjectionUnavailable):
    """The expected owner changed before the activation transaction."""


@dataclass(frozen=True)
class Selection:
    status_filter: str | None = None
    platform_filter: str | None = None
    include_missing: bool = True
    worktree_id: str | None = None
    codename: str | None = None

    def __post_init__(self) -> None:
        for name, value, bound in (
            ("status filter", self.status_filter, 4096),
            ("platform filter", self.platform_filter, 4096),
            ("worktree id", self.worktree_id, 256),
            ("codename", self.codename, 4096),
        ):
            if value is not None and (
                not isinstance(value, str) or not value
                or len(value.encode("utf-8")) > bound or "\x00" in value
            ):
                raise ProjectionError(f"invalid {name}")
        if type(self.include_missing) is not bool:
            raise ProjectionError("invalid checkout selection")
        if self.worktree_id is not None and (
            self.worktree_id in {".", ".."} or "/" in self.worktree_id or "\\" in self.worktree_id
        ):
            raise ProjectionError("invalid worktree id")

    @property
    def scope(self) -> str:
        return json.dumps(
            (self.status_filter, self.platform_filter, self.include_missing),
            ensure_ascii=False, separators=(",", ":"),
        )


@dataclass(frozen=True)
class _Alias:
    worktree_id: str | None
    count: int
    preview: str

    def resolve(self, selector: str, *, codename: bool = False) -> str:
        if self.count > 1:
            label = "codename" if codename else "short ID"
            raise ProjectionError(
                f"Ambiguous {label} '{selector}' matches {self.count} worktrees: {self.preview}"
            )
        if self.count != 1 or not isinstance(self.worktree_id, str):
            raise ProjectionError("invalid durable worktree selector")
        return self.worktree_id


def _aliases(groups: dict[str, list[str]], *, short: bool) -> dict[str, _Alias]:
    result = {}
    for selector, ids in groups.items():
        shown = sorted(wid[-12:] if short else wid for wid in ids)[:_PREVIEW_LIMIT]
        preview = ", ".join(shown)
        if len(ids) > _PREVIEW_LIMIT:
            preview += f", ... ({len(ids) - _PREVIEW_LIMIT} more)"
        result[selector] = _Alias(ids[0] if len(ids) == 1 else None, len(ids), preview)
    return result


@dataclass(frozen=True)
class _Scope:
    ids: tuple[str, ...]
    aliases: dict[str, _Alias]
    ready: bool


class SelectionIndex:
    """Immutable after construction; costs of alias/filter assembly are writes."""

    def __init__(self, rows: Iterable[dict]) -> None:
        memberships: dict[str, list[str]] = defaultdict(list)
        unready: set[str] = set()
        codenames: dict[str, list[str]] = defaultdict(list)
        all_scope = Selection().scope
        memberships[all_scope] = []
        memberships[Selection(include_missing=False).scope] = []
        for row in rows:
            wid = row["id"]
            if row.get("codename"):
                codenames[row["codename"]].append(wid)
            statuses = {None, row.get("status")}
            platforms = {None, row.get("platform")}
            for status in statuses:
                for platform in platforms:
                    scope = Selection(status, platform).scope
                    memberships[scope].append(wid)
                    scope = Selection(status, platform, False).scope
                    memberships.setdefault(scope, [])
                    if type(row.get("checkout_present")) is not bool:
                        unready.add(scope)
                    elif row["checkout_present"]:
                        memberships[scope].append(wid)
        self.scopes: dict[str, _Scope] = {}
        for scope, ids in memberships.items():
            groups: dict[str, list[str]] = defaultdict(list)
            for wid in ids:
                for offset in range(len(wid)):
                    groups[wid[offset:]].append(wid)
            aliases = _aliases(groups, short=True)
            # Exact identity has precedence over even an ambiguous suffix.
            for wid in ids:
                aliases[wid] = _Alias(wid, 1, wid[-12:])
            self.scopes[scope] = _Scope(tuple(ids), aliases, scope not in unready)
        self.codenames = _aliases(codenames, short=False)

    def select(self, selection: Selection) -> tuple[str, ...]:
        wid = selection.worktree_id
        if wid is None and selection.codename is not None:
            alias = self.codenames.get(selection.codename)
            if alias is None:
                return ()
            wid = alias.resolve(selection.codename, codename=True)
        scope = self.scopes.get(selection.scope)
        if scope is None:
            return ()
        if not scope.ready:
            raise ProjectionUnavailable("checkout presence is not published for this selection")
        if wid is None:
            return scope.ids
        alias = scope.aliases.get(wid)
        return () if alias is None else (alias.resolve(wid),)

    def persist(self, conn: sqlite3.Connection, project: str) -> None:
        for table in ("scope", "member", "selector"):
            conn.execute(f"DELETE FROM worktrees_projection_{table} WHERE project=?", (project,))
        conn.executemany(
            "INSERT INTO worktrees_projection_scope VALUES (?, ?, ?, ?)",
            ((project, key, int(scope.ready), len(scope.ids))
             for key, scope in self.scopes.items()),
        )
        conn.executemany(
            "INSERT INTO worktrees_projection_member VALUES (?, ?, ?, ?)",
            ((project, key, wid, ordinal) for key, scope in self.scopes.items()
             for ordinal, wid in enumerate(scope.ids)),
        )
        conn.executemany(
            "INSERT INTO worktrees_projection_selector VALUES (?, ?, ?, ?, ?, ?)",
            ((project, key, selector, alias.worktree_id, alias.count, alias.preview)
             for key, aliases in (
                 *((key, scope.aliases) for key, scope in self.scopes.items()),
                 (_CODENAME_SCOPE, self.codenames),
             ) for selector, alias in aliases.items()),
        )


def _durable_alias(
    conn: sqlite3.Connection, project: str, scope: str, selector: str,
) -> _Alias | None:
    found = conn.execute(
        "SELECT CASE WHEN length(CAST(worktree_id AS BLOB))<=256 THEN worktree_id END, "
        "CASE WHEN typeof(match_count)='integer' THEN match_count END, "
        "CASE WHEN length(CAST(preview AS BLOB))<=4096 THEN preview END "
        "FROM worktrees_projection_selector WHERE project=? AND scope=? AND selector=?",
        (project, scope, selector),
    ).fetchone()
    if found is None:
        return None
    wid, count, preview = found
    if type(count) is not int or count < 1 or not isinstance(preview, str):
        raise ProjectionError("invalid durable worktree selector")
    return _Alias(wid, count, preview)


def durable_selection(
    conn: sqlite3.Connection, project: str, selection: Selection,
) -> tuple[str | None, int]:
    """Return one indexed identity or a full selection and its expected count."""
    wid = selection.worktree_id
    if wid is None and selection.codename is not None:
        alias = _durable_alias(conn, project, _CODENAME_SCOPE, selection.codename)
        if alias is None:
            return None, 0
        wid = alias.resolve(selection.codename, codename=True)
    found = conn.execute(
        "SELECT CASE WHEN typeof(ready)='integer' THEN ready END, "
        "CASE WHEN typeof(row_count)='integer' THEN row_count END "
        "FROM worktrees_projection_scope WHERE project=? AND scope=?",
        (project, selection.scope),
    ).fetchone()
    if found is None:
        return None, 0
    ready, count = found
    if ready not in (0, 1) or type(count) is not int or count < 0:
        raise ProjectionError("invalid durable selection metadata")
    if not ready:
        raise ProjectionUnavailable("checkout presence is not published for this selection")
    if wid is None:
        return None, count
    alias = _durable_alias(conn, project, selection.scope, wid)
    return (None, 0) if alias is None else (alias.resolve(wid), 1)
