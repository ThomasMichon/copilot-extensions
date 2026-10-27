"""Finite timestamp polling; mutable provider offsets are never recovery cursors."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from agent_index.sources.delta import AcquisitionPage, SourceDelta
from agent_index.sources.github_hints import _revision, acquire_item, validate_item
from agent_index.sources.good_citizen_http import has_next_link

if TYPE_CHECKING:
    from agent_index.sources.github import GitHubConnector


def _checkpoint(value: str | None) -> datetime:
    if not value:
        raise ValueError("GitHub polling requires a committed timestamp; request explicit reconciliation")
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (ValueError, OverflowError, OSError):
        return _revision(value)


def _plan(connector: GitHubConnector, last_commit: str | None) -> dict:
    base = _checkpoint(last_commit)
    result = connector._http.get_json(
        f"/repos/{connector.owner}/{connector.repo}/issues", etag=False,
        params={
            "state": "all", "sort": "updated", "direction": "asc",
            "since": connector._since_marker(last_commit), "per_page": 100, "page": 1,
        },
    )
    data = result.data
    if result.not_modified or not isinstance(data, list) or len(data) > 100:
        raise ValueError("GitHub polling requires a complete fresh identity page")
    if has_next_link(result.headers.get("Link", "")):
        raise ValueError(
            "GitHub polling window exceeds 100 identities; request explicit reconciliation "
            "instead of advancing across mutable offset pages",
        )
    items = []
    seen: set[int] = set()
    target = base
    for item in data:
        number = item.get("number") if isinstance(item, dict) else None
        if type(number) is not int or number in seen:
            raise ValueError("GitHub polling page has invalid or repeated identities")
        updated = validate_item(connector, item, number)
        seen.add(number)
        target = max(target, updated)
        items.append([number, item["updated_at"]])
    return {
        "version": 1, "source": connector.source_name, "account": connector.hint_scope[1],
        "base": last_commit, "target": target.isoformat(), "items": items, "index": 0,
    }


def _resume(connector: GitHubConnector, last_commit: str | None, cursor: str) -> dict:
    data = json.loads(cursor)
    if not isinstance(data, dict) or set(data) != {
        "version", "source", "account", "base", "target", "items", "index",
    } or type(data["version"]) is not int or data["version"] != 1:
        raise ValueError("Invalid GitHub polling acquisition plan")
    if (data["source"], data["account"]) != connector.hint_scope or data["base"] != last_commit:
        raise ValueError("GitHub polling plan belongs to a different source/account/checkpoint")
    target = _checkpoint(data["target"])
    if target < _checkpoint(last_commit):
        raise ValueError("GitHub polling plan regresses its checkpoint")
    items = data["items"]
    index = data["index"]
    if (
        not isinstance(items, list) or not 1 <= len(items) <= 100
        or type(index) is not int or not 0 <= index < len(items)
    ):
        raise ValueError("Invalid GitHub polling plan position")
    seen: set[int] = set()
    for item in items:
        if (
            not isinstance(item, list) or len(item) != 2 or type(item[0]) is not int
            or item[0] <= 0 or item[0] in seen or _revision(item[1]) > target
        ):
            raise ValueError("Invalid GitHub polling plan identity/revision")
        seen.add(item[0])
    return data


def acquire_page(
    connector: GitHubConnector, last_commit: str | None, cursor: str | None,
    *, cancel_check: Callable[[], None] | None = None,
) -> AcquisitionPage:
    if cancel_check:
        cancel_check()
    plan = _plan(connector, last_commit) if cursor is None else _resume(
        connector, last_commit, cursor,
    )
    if cursor is None:
        return AcquisitionPage(
            SourceDelta(commit=plan["target"]),
            json.dumps(plan) if plan["items"] else None,
        )
    number, revision = plan["items"][plan["index"]]
    entry = acquire_item(
        connector, number, minimum_revision=_revision(revision), cancel_check=cancel_check,
    )
    plan["index"] += 1
    return AcquisitionPage(
        SourceDelta(upserts=[entry], commit=plan["target"]),
        json.dumps(plan) if plan["index"] < len(plan["items"]) else None,
    )
