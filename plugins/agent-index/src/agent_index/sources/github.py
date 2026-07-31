"""GitHub issues and pull requests source connector."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from agent_index.sources.base import FileEntry
from agent_index.sources.good_citizen_http import (
    ApiResult as _ApiResult,
)
from agent_index.sources.good_citizen_http import (
    GoodCitizenSession,
    has_next_link,
)

log = logging.getLogger(__name__)

_DEFAULT_API_BASE = "https://api.github.com"
_DEFAULT_TIMEOUT = 20.0
_DEFAULT_PER_PAGE = 100
_DEFAULT_MIN_INTERVAL_S = 0.2


class GitHubConnector:
    """Source connector for GitHub issues and pull requests."""

    def __init__(
        self,
        source: str,
        *,
        token: str | None = None,
        api_base: str | None = None,
        transport: httpx.BaseTransport | None = None,
        client: httpx.Client | None = None,
        min_interval_s: float = _DEFAULT_MIN_INTERVAL_S,
        since: str | None = None,
    ) -> None:
        self._source = source
        self.owner, self.repo = self._parse_source(source)
        self.api_base = (api_base or os.environ.get("AGENT_INDEX_GITHUB_API") or _DEFAULT_API_BASE)
        self.api_base = self.api_base.rstrip("/")
        self._token = token or _env_token()
        if not self._token:
            log.warning("GitHubConnector running without a token; API rate limits will be low")
        self._http = GoodCitizenSession(
            base_url=self.api_base,
            headers=self._headers(),
            transport=transport,
            client=client,
            timeout=_DEFAULT_TIMEOUT,
            min_interval_s=min_interval_s,
        )
        self._client = self._http.client
        self._owns_client = self._http.owns_client
        self._default_since = since

    @property
    def source_name(self) -> str:
        """Unique source family name for this repository."""
        return f"github:{self.owner}/{self.repo}"

    def discover(self, cancel_check: Callable[[], None] | None = None) -> list[FileEntry]:
        """Discover all issues and pull requests."""
        return self._discover_items(since=None, cancel_check=cancel_check)

    def discover_changed(
        self,
        last_commit: str | None,
        cancel_check: Callable[[], None] | None = None,
    ) -> list[FileEntry]:
        """Discover issues and pull requests updated after a timestamp marker."""
        since = self._since_marker(last_commit or self._default_since)
        return self._discover_items(since=since, cancel_check=cancel_check)

    def list_paths(
        self,
        cancel_check: Callable[[], None] | None = None,
    ) -> dict[str, set[str]]:
        """Return known issue and pull request paths without fetching comments."""
        issues: set[str] = set()
        pulls: set[str] = set()
        for item in self._iter_issue_timeline():
            if cancel_check:
                cancel_check()
            number = item.get("number")
            if number is None:
                continue
            if "pull_request" in item:
                pulls.add(f"pulls/{number}.md")
            else:
                issues.add(f"issues/{number}.md")
        return {
            self._issues_source: issues,
            self._pulls_source: pulls,
        }

    def current_commit(self) -> str | None:
        """GitHub issue and pull request state is timestamp-tracked, not HEAD-tracked."""
        return None

    @property
    def _issues_source(self) -> str:
        return f"{self.source_name}:issues"

    @property
    def _pulls_source(self) -> str:
        return f"{self.source_name}:pulls"

    def _discover_items(
        self,
        *,
        since: str | None,
        cancel_check: Callable[[], None] | None,
    ) -> list[FileEntry]:
        entries: list[FileEntry] = []
        for item in self._iter_issue_timeline(since=since):
            if cancel_check:
                cancel_check()
            number = item.get("number")
            if number is None:
                continue
            comments = self._comments_for(number)
            if "pull_request" in item:
                entries.append(self._pull_entry(item, comments))
            else:
                entries.append(self._issue_entry(item, comments))
        return entries

    def _iter_issue_timeline(self, *, since: str | None = None) -> Iterable[dict[str, Any]]:
        params: dict[str, Any] = {
            "state": "all",
            "sort": "updated",
            "direction": "asc" if since else "desc",
        }
        if since:
            params["since"] = since
        for page in self._paginate(f"/repos/{self.owner}/{self.repo}/issues", **params):
            if isinstance(page, list):
                for item in page:
                    if isinstance(item, dict):
                        yield item

    def _comments_for(self, number: int) -> list[dict[str, Any]]:
        comments: list[dict[str, Any]] = []
        for page in self._paginate(f"/repos/{self.owner}/{self.repo}/issues/{number}/comments"):
            if isinstance(page, list):
                comments.extend(item for item in page if isinstance(item, dict))
        return comments

    def _issue_entry(self, item: dict[str, Any], comments: list[dict[str, Any]]) -> FileEntry:
        number = item["number"]
        return FileEntry(
            path=f"issues/{number}.md",
            content=self._format_item(item, comments, item_type="issue"),
            language="issue",
            source=self._issues_source,
            metadata=self._metadata(item, item_type="issue"),
        )

    def _pull_entry(self, item: dict[str, Any], comments: list[dict[str, Any]]) -> FileEntry:
        number = item["number"]
        return FileEntry(
            path=f"pulls/{number}.md",
            content=self._format_item(item, comments, item_type="pull_request"),
            language="pull_request",
            source=self._pulls_source,
            metadata=self._metadata(item, item_type="pull_request"),
        )

    @staticmethod
    def _format_item(
        item: dict[str, Any],
        comments: list[dict[str, Any]],
        *,
        item_type: str,
    ) -> str:
        labels = ", ".join(label.get("name", "") for label in item.get("labels", []) if label)
        author = (item.get("user") or {}).get("login", "unknown")
        body = item.get("body") or ""
        lines = [
            f"# {item.get('title') or '(untitled)'}",
            "",
            f"Type: {item_type}",
            f"Number: {item.get('number')}",
            f"State: {item.get('state')}",
            f"Author: {author}",
            f"Labels: {labels}",
            f"Created: {item.get('created_at')}",
            f"Updated: {item.get('updated_at')}",
            "",
            "## Body",
            "",
            body,
        ]
        if comments:
            lines.extend(["", "## Comments", ""])
            for comment in comments:
                comment_author = (comment.get("user") or {}).get("login", "unknown")
                comment_updated = comment.get("updated_at") or comment.get("created_at")
                lines.extend([
                    f"### {comment_author} at {comment_updated}",
                    "",
                    comment.get("body") or "",
                    "",
                ])
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _metadata(item: dict[str, Any], *, item_type: str) -> dict[str, Any]:
        repo_url = item.get("repository_url", "").rstrip("/")
        repo_parts = repo_url.rsplit("/", 2)
        repo_name = "/".join(repo_parts[-2:]) if len(repo_parts) >= 2 else None
        return {
            "type": item_type,
            "repo": repo_name,
            "number": item.get("number"),
            "state": item.get("state"),
            "labels": [label.get("name") for label in item.get("labels", []) if label.get("name")],
            "author": (item.get("user") or {}).get("login"),
            "created_at": item.get("created_at"),
            "updated_at": item.get("updated_at"),
            "closed_at": item.get("closed_at"),
            "html_url": item.get("html_url"),
            "api_url": item.get("url"),
        }

    def _paginate(self, path: str, **params: Any) -> Iterable[Any]:
        page = int(params.pop("page", 1))
        per_page = int(params.pop("per_page", _DEFAULT_PER_PAGE))
        while True:
            result = self._api_get(path, **params, per_page=per_page, page=page)
            if result.not_modified:
                return
            yield result.data
            if not has_next_link(result.headers.get("Link", "")):
                return
            page += 1

    def _api_get(self, path: str, **params: Any) -> _ApiResult:
        """GET JSON through the shared good-citizen HTTP path."""
        return self._http.get_json(path, params=params)

    @staticmethod
    def _parse_source(source: str) -> tuple[str, str]:
        prefix = "github:"
        if not source.startswith(prefix) or "/" not in source[len(prefix):]:
            raise ValueError("GitHub source must be 'github:<owner>/<repo>'")
        owner_repo = source[len(prefix):]
        owner, repo = owner_repo.split("/", 1)
        if not owner or not repo:
            raise ValueError("GitHub source must include both owner and repo")
        return owner, repo

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "agent-index",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    @staticmethod
    def _since_marker(marker: str | None) -> str | None:
        if not marker:
            return None
        try:
            dt = datetime.fromtimestamp(float(marker), tz=UTC)
        except ValueError:
            dt = _parse_datetime(marker)
        if dt is None:
            return None
        return (dt - timedelta(seconds=60)).astimezone(UTC).isoformat().replace("+00:00", "Z")


def _env_token() -> str | None:
    for name in ("AGENT_INDEX_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        if token := os.environ.get(name):
            return token
    return None


def _parse_datetime(value: str) -> datetime | None:
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
