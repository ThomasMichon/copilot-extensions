"""Fresh targeted GitHub work-item acquisition without polling-cursor advancement."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from agent_index.indexing.change_hints import ChangeHints
from agent_index.sources.base import FileEntry
from agent_index.sources.delta import SourceDelta
from agent_index.sources.good_citizen_http import has_next_link

if TYPE_CHECKING:
    from agent_index.sources.github import GitHubConnector


def _number(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or value.startswith("0"):
        raise ValueError("GitHub hint identities must be canonical positive issue numbers")
    return int(value)


def _revision(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("GitHub hint revisions require updated_at timestamps")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("GitHub hint revisions require updated_at timestamps") from exc
    if parsed.tzinfo is None:
        raise ValueError("GitHub hint revisions require timezone-qualified timestamps")
    return parsed


def acquire_hints(
    connector: GitHubConnector, hints: ChangeHints, last_commit: str | None,
    *, cancel_check: Callable[[], None] | None = None,
) -> SourceDelta:
    """Fetch each unique item and all its comments, or refuse the whole delta."""
    if type(hints) is not ChangeHints or hints.source != connector.source_name:
        raise ValueError("GitHub hints require the exact configured repository source")
    if hints.ref_ranges:
        raise ValueError("GitHub issue hints do not support Git ref ranges")
    numbers = {_number(value) for value in hints.item_ids}
    revisions: dict[int, datetime] = {}
    for revision in hints.metadata_revisions:
        number = _number(revision.item_id)
        parsed = _revision(revision.revision)
        revisions[number] = max(parsed, revisions.get(number, parsed))
        numbers.add(number)
    if hints.event_ids and not numbers:
        raise ValueError("GitHub delivery annotations require resolvable item identities")
    if cancel_check:
        cancel_check()
    if connector.hint_scope != (hints.canonical_family, hints.account_id):
        raise ValueError("GitHub hints do not match the effective family/account scope")
    entries = []
    for number in sorted(numbers):
        entries.append(acquire_item(
            connector, number, minimum_revision=revisions.get(number), cancel_check=cancel_check,
        ))
    if cancel_check:
        cancel_check()
    return SourceDelta(upserts=entries, commit=last_commit)


def validate_item(connector: GitHubConnector, item: object, number: int) -> datetime:
    """Validate the same repository identity and revision for hints and polling."""
    repository_url = str(connector._client.base_url).rstrip("/") + (
        f"/repos/{connector.owner}/{connector.repo}"
    )
    actual_repository = item.get("repository_url") if isinstance(item, dict) else None
    actual_url = item.get("url") if isinstance(item, dict) else None
    if (
        not isinstance(item, dict) or type(item.get("number")) is not int
        or item["number"] != number or number <= 0
        or not isinstance(actual_repository, str)
        or actual_repository.casefold() != repository_url.casefold()
        or not isinstance(actual_url, str)
        or actual_url.casefold() != (repository_url + f"/issues/{number}").casefold()
    ):
        raise ValueError("GitHub hinted response did not match the requested repository/item")
    count = item.get("comments")
    if type(count) is not int or count < 0:
        raise ValueError("GitHub hinted response did not include a valid comment count")
    return _revision(item.get("updated_at"))


def acquire_item(
    connector: GitHubConnector, number: int, *, minimum_revision: datetime | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> FileEntry:
    if cancel_check:
        cancel_check()
    path = f"/repos/{connector.owner}/{connector.repo}/issues/{number}"
    result = connector._http.get_json(path, etag=False, purpose="item_body")
    if result.not_modified:
        raise ValueError("GitHub hinted acquisition requires a fresh item")
    item = result.data
    updated = validate_item(connector, item, number)
    if minimum_revision is not None and updated < minimum_revision:
        raise ValueError("GitHub hinted response predates a required metadata revision")
    count = item["comments"]
    comments = []
    seen: set[int] = set()
    page = 1
    while True:
        if cancel_check:
            cancel_check()
        result = connector._http.get_json(
            path + "/comments", params={"per_page": 100, "page": page}, etag=False,
            purpose="comment_page",
        )
        data = result.data
        if result.not_modified or not isinstance(data, list) or len(data) > 100:
            raise ValueError("GitHub hinted comments require complete fresh pages")
        for comment in data:
            if (
                not isinstance(comment, dict) or type(comment.get("id")) is not int
                or comment["id"] <= 0 or comment["id"] in seen
            ):
                raise ValueError("GitHub hinted comments include invalid or repeated identities")
            seen.add(comment["id"])
            comments.append(comment)
        more = has_next_link(result.headers.get("Link", ""))
        if len(comments) > count or (more and (not data or len(comments) >= count)):
            raise ValueError("GitHub hinted comment pagination is inconsistent")
        if not more:
            break
        page += 1
    if len(comments) != count:
        raise ValueError("GitHub hinted comment acquisition is incomplete")
    return (
        connector._pull_entry(item, comments) if "pull_request" in item
        else connector._issue_entry(item, comments)
    )
