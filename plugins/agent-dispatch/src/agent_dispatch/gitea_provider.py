"""Gitea backlog-provider adapter for repository-issue-loops.

Implements the same ``ForgeProvider`` contract (``list_open_issues``/
``reserve``/``claim``/``release``) as ``GitHubProvider``/
``AzureDevOpsProvider`` in ``repository_issue_loops.py``, kept in its own
module purely to stay under that module's size cap (mirroring the
``gitea_provider_stub.py`` precedent this file replaces).

Integration approach (the decision ``ThomasMichon/copilot-extensions#4825``
asked for): Gitea's REST API (``/api/v1``) via ``curl``, the same choice
``agent-worktrees``' own ``GiteaProvider`` (``plugins/agent-worktrees/src/
agent_worktrees/providers/gitea.py``) already made and validated in
production for PR operations against a real Gitea instance -- no vendored
``tea`` CLI, no new Python HTTP dependency, and a precedent already proven
rather than a fresh decision. Every method below has been validated against
a real Gitea instance (not unit tests alone), per this repo's own
"validate beyond unit tests before landing a fix" policy -- see this
effort's Journal for the validation record.

Comment-marker reservation convention (``_marker``/``_parse_marker``/
``_latest_reservations``) is shared unmodified with the GitHub/Azure DevOps
adapters -- Gitea issue comments are stored as raw Markdown (no HTML-comment
stripping, confirmed live), so the HTML-comment marker form (``_marker``,
GitHub's choice) round-trips unchanged.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from .gitea_connection import DEFAULT_GITEA_TOKEN_ENV
from .issue_loop_markers import _marker, _parse_marker

if TYPE_CHECKING:
    from .repository_issue_loops import Issue

#: Tracked follow-up this module closes out.
GITEA_PROVIDER_TRACKING_ISSUE = "ThomasMichon/copilot-extensions#4825"

_ISSUE_PAGE_SIZE = 50
_MAX_ISSUE_PAGES = 20
_MAX_LABEL_PAGES = 20


class GiteaProvider:
    """Gitea backlog-issue adapter implemented through the Gitea REST API.

    ``repo`` is ``owner/name``, the same shape ``validate_repo_field``
    already requires for every forge provider. ``api_base`` is the
    instance's base URL (e.g. ``https://gitea.example.com``, no trailing
    slash or ``/api/v1`` suffix); ``token_env`` names the environment
    variable holding an API token for ``expected_login`` (defaults to
    :data:`agent_dispatch.gitea_connection.DEFAULT_GITEA_TOKEN_ENV`).
    """

    def __init__(
        self,
        expected_login: str,
        runner: Callable[..., Any] = subprocess.run,
        *,
        api_base: str | None = None,
        token_env: str | None = None,
    ):
        if not expected_login:
            raise ValueError("expected_login must be non-empty")
        if not api_base:
            raise ValueError(
                "GiteaProvider requires api_base (the Gitea instance base URL)"
            )
        self.expected_login = expected_login
        self.runner = runner
        self.api_base = api_base.rstrip("/")
        self.token_env = token_env or DEFAULT_GITEA_TOKEN_ENV
        self._verified_repos: set[str] = set()
        self._label_ids: dict[str, dict[str, int]] = {}

    # -- transport -----------------------------------------------------

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"Gitea operation failed: environment variable "
                f"{self.token_env!r} is unset or empty"
            )
        return token

    def _curl(
        self, method: str, path: str, *, payload: Mapping[str, Any] | None = None
    ) -> tuple[int, str]:
        args = [
            "curl", "-sS", "-X", method, f"{self.api_base}/api/v1{path}",
            "-H", f"Authorization: token {self._token()}",
            "-H", "Accept: application/json",
            "-w", "\n%{http_code}",
        ]
        if payload is not None:
            args += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
        completed = self.runner(args, check=False, capture_output=True, text=True)
        if int(completed.returncode) != 0:
            raise RuntimeError(
                f"Gitea operation failed: {str(completed.stderr or '').strip()}"
            )
        out = completed.stdout or ""
        nl = out.rfind("\n")
        body, status_str = (out[:nl], out[nl + 1:]) if nl >= 0 else ("", out)
        try:
            status = int(status_str.strip())
        except ValueError:
            raise RuntimeError(
                f"Gitea operation returned an unparseable status {status_str!r}"
            ) from None
        return status, body

    def _call(
        self, method: str, path: str, *, payload: Mapping[str, Any] | None = None,
        ok: tuple[int, ...] = (200,),
    ) -> Any:
        status, body = self._curl(method, path, payload=payload)
        if status not in ok:
            raise RuntimeError(
                f"Gitea {method} {path} failed (HTTP {status}): {body.strip()[:300]}"
            )
        return json.loads(body) if body.strip() else None

    def _verify_identity(self, repo: str, *, allow_cache: bool = True) -> None:
        if allow_cache and repo in self._verified_repos:
            return
        login = str((self._call("GET", "/user") or {}).get("login") or "")
        if login.casefold() != self.expected_login.casefold():
            raise RuntimeError(
                "Gitea producer identity mismatch: expected "
                f"{self.expected_login!r}, got {login!r}"
            )
        full_name = str(
            (self._call("GET", f"/repos/{repo}") or {}).get("full_name") or ""
        )
        if full_name.casefold() != repo.casefold():
            raise RuntimeError(
                f"Gitea repository identity mismatch: expected {repo!r}, got {full_name!r}"
            )
        if allow_cache:
            self._verified_repos.add(repo)

    # -- label id resolution ---------------------------------------------

    def _labels_by_name(self, repo: str) -> dict[str, int]:
        cached = self._label_ids.get(repo)
        if cached is not None:
            return cached
        labels: dict[str, int] = {}
        for _page_index in range(_MAX_LABEL_PAGES):
            page = _page_index + 1
            rows = self._call(
                "GET", f"/repos/{repo}/labels?page={page}&limit={_ISSUE_PAGE_SIZE}"
            ) or []
            for row in rows:
                labels[str(row["name"])] = int(row["id"])
            if len(rows) < _ISSUE_PAGE_SIZE:
                self._label_ids[repo] = labels
                return labels
        raise RuntimeError(
            f"Gitea label listing exceeded the bounded "
            f"{_MAX_LABEL_PAGES * _ISSUE_PAGE_SIZE}-label scan"
        )

    def _label_id(self, repo: str, label: str) -> int:
        labels = self._labels_by_name(repo)
        label_id = labels.get(label)
        if label_id is None:
            raise RuntimeError(
                f"Gitea repository {repo!r} has no label named {label!r} "
                "(create it first; this adapter does not create labels)"
            )
        return label_id

    # -- ForgeProvider contract ------------------------------------------

    def list_open_issues(self, repo: str) -> list["Issue"]:
        from .repository_issue_loops import Issue, _parse_time

        self._verify_identity(repo)
        issues: list[Issue] = []
        for _page_index in range(_MAX_ISSUE_PAGES):
            page = _page_index + 1
            rows = self._call(
                "GET",
                f"/repos/{repo}/issues?state=open&type=issues"
                f"&page={page}&limit={_ISSUE_PAGE_SIZE}",
            ) or []
            for row in rows:
                if row.get("pull_request") is not None:
                    continue  # belt-and-suspenders: type=issues already excludes PRs.
                number = int(row["number"])
                comments = self._call(
                    "GET", f"/repos/{repo}/issues/{number}/comments"
                ) or []
                reservations = tuple(
                    marker
                    for comment in comments
                    if (
                        marker := _parse_marker(
                            str(comment.get("body") or ""),
                            author=str((comment.get("user") or {}).get("login") or ""),
                            expected_author=self.expected_login,
                            issue_number=number,
                        )
                    )
                )
                issues.append(
                    Issue(
                        number=number,
                        title=str(row["title"]),
                        url=str(row.get("html_url") or row.get("url") or ""),
                        labels=tuple(
                            str(label["name"]) for label in (row.get("labels") or [])
                            if label.get("name")
                        ),
                        created_at=_parse_time(str(row["created_at"])),
                        updated_at=_parse_time(str(row["updated_at"])),
                        reservations=reservations,
                    )
                )
            if len(rows) < _ISSUE_PAGE_SIZE:
                return issues
        raise RuntimeError(
            "Gitea issue discovery exceeded the bounded "
            f"{_MAX_ISSUE_PAGES * _ISSUE_PAGE_SIZE}-issue scan"
        )

    def _find_own_loop_comment(
        self, repo: str, number: int, loop: str
    ) -> int | None:
        comments = self._call("GET", f"/repos/{repo}/issues/{number}/comments") or []
        found_id: int | None = None
        for comment in comments:
            marker = _parse_marker(
                str(comment.get("body") or ""),
                author=str((comment.get("user") or {}).get("login") or ""),
                expected_author=self.expected_login,
                issue_number=number,
            )
            if marker is None or marker.get("loop") != loop:
                continue
            comment_id = comment.get("id")
            if isinstance(comment_id, int):
                found_id = comment_id
        return found_id

    def _comment(self, repo: str, issue: "Issue", payload: dict[str, Any]) -> None:
        payload = {k: v for k, v in payload.items() if k != "comment_author"}
        state = str(payload.get("state") or "reserved")
        summary = (
            f"Repository issue loop `{payload.get('loop')}` marked this issue "
            f"`{state}` for occurrence `{payload.get('occurrence')}`."
        )
        body = f"{summary}\n\n{_marker(payload)}"
        self._verify_identity(repo, allow_cache=False)
        existing_id = self._find_own_loop_comment(repo, issue.number, str(payload.get("loop")))
        if existing_id is not None:
            self._call(
                "PATCH", f"/repos/{repo}/issues/comments/{existing_id}",
                payload={"body": body}, ok=(200,),
            )
            return
        self._call(
            "POST", f"/repos/{repo}/issues/{issue.number}/comments",
            payload={"body": body}, ok=(201,),
        )

    def reserve(self, repo: str, issue: "Issue", reservation: dict[str, Any]) -> None:
        self._comment(repo, issue, {**reservation, "issue": issue.number})
        self._verify_identity(repo, allow_cache=False)
        label_id = self._label_id(repo, reservation["label"])
        self._call(
            "POST", f"/repos/{repo}/issues/{issue.number}/labels",
            payload={"labels": [label_id]}, ok=(200, 201),
        )

    def claim(
        self, repo: str, issue: "Issue", reservation: dict[str, Any], task_id: str
    ) -> None:
        self._comment(
            repo, issue,
            {**reservation, "issue": issue.number, "state": "claimed", "task_id": task_id},
        )

    def release(
        self, repo: str, issue: "Issue", reservation: dict[str, Any], reason: str,
    ) -> None:
        from .repository_issue_loops import Issue as _Issue
        from .repository_issue_loops import _latest_reservations

        self._comment(
            repo, issue,
            {**reservation, "issue": issue.number, "state": "released", "reason": reason},
        )
        comments = self._call("GET", f"/repos/{repo}/issues/{issue.number}/comments") or []
        current = _Issue(
            number=issue.number, title=issue.title, url=issue.url, labels=issue.labels,
            created_at=issue.created_at, updated_at=issue.updated_at,
            reservations=tuple(
                marker
                for comment in comments
                if (
                    marker := _parse_marker(
                        str(comment.get("body") or ""),
                        author=str((comment.get("user") or {}).get("login") or ""),
                        expected_author=self.expected_login,
                        issue_number=issue.number,
                    )
                )
            ),
        )
        other_active = any(
            value.get("state") in {"reserved", "claimed"}
            and value.get("loop") != reservation.get("loop")
            and value.get("label") == reservation.get("label")
            for value in _latest_reservations(current).values()
        )
        if not other_active:
            self._verify_identity(repo, allow_cache=False)
            label_id = self._label_id(repo, reservation["label"])
            self._call(
                "DELETE", f"/repos/{repo}/issues/{issue.number}/labels/{label_id}",
                ok=(200, 204),
            )
