"""Gitea backlog-provider adapter for repository-issue-loops.

Implements the same ``ForgeProvider`` contract (``list_open_issues``/
``reserve``/``claim``/``release``) as ``GitHubProvider``/
``AzureDevOpsProvider`` in ``repository_issue_loops.py``, kept in its own
module purely to stay under that module's size cap.

Integration approach: Gitea's REST API (``/api/v1``) via ``curl``, the
same choice ``agent-worktrees``' own ``GiteaProvider``
(``plugins/agent-worktrees/src/agent_worktrees/providers/gitea.py``) uses
in production for PR operations -- no vendored ``tea`` CLI, no new Python
HTTP dependency.

Comment-marker reservation convention (``_marker``/``_parse_marker``/
``_latest_reservations``) is shared unmodified with the GitHub/Azure DevOps
adapters -- Gitea issue comments are stored as raw Markdown (no HTML-comment
stripping), so the HTML-comment marker form (``_marker``, GitHub's choice)
round-trips unchanged.
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
        if not api_base or not api_base.strip() or not api_base.strip().rstrip("/"):
            raise ValueError(
                "GiteaProvider requires api_base (the Gitea instance base URL)"
            )
        self.expected_login = expected_login
        self.runner = runner
        self.api_base = api_base.strip().rstrip("/")
        self.token_env = (token_env.strip() if token_env else None) or DEFAULT_GITEA_TOKEN_ENV
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

    # -- paginated comment listing ----------------------------------------

    def _all_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        """List every comment on an issue/PR, across pages.

        A single unpaginated page silently misses a reservation marker (or
        this loop's own prior comment to edit) once a busy issue accumulates
        enough comments to push it past the first page -- discovery would
        then treat an already-reserved issue as unreserved and dispatch
        duplicate work.

        Gitea's issue-comments endpoint (unlike its issues/labels listings)
        has been observed to silently ignore ``page``/``limit`` and return
        the full, unpaginated comment list on every call. Stopping only on
        an *empty* page would then never trigger -- every page repeats the
        same non-empty result until the bounded-pages ceiling raises, even
        though all comments were already seen on page 1. Stop as soon as a
        page contributes no new comment id, which is correct whether the
        server paginates normally or ignores pagination entirely.
        """
        comments: list[dict[str, Any]] = []
        seen_ids: set[Any] = set()
        for _page_index in range(_MAX_ISSUE_PAGES):
            page = _page_index + 1
            rows = self._call(
                "GET",
                f"/repos/{repo}/issues/{number}/comments"
                f"?page={page}&limit={_ISSUE_PAGE_SIZE}",
            ) or []
            if not rows:
                return comments
            new_rows = [row for row in rows if row.get("id") not in seen_ids]
            if not new_rows:
                return comments
            for row in new_rows:
                seen_ids.add(row.get("id"))
            comments.extend(new_rows)
        raise RuntimeError(
            f"Gitea comment listing for {repo}#{number} exceeded the bounded "
            f"{_MAX_ISSUE_PAGES} pages"
        )

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
            if not rows:
                self._label_ids[repo] = labels
                return labels
            for row in rows:
                labels[str(row["name"])] = int(row["id"])
        raise RuntimeError(
            f"Gitea label listing exceeded the bounded {_MAX_LABEL_PAGES} pages"
        )

    def _label_id(self, repo: str, label: str) -> int:
        labels = self._labels_by_name(repo)
        label_id = labels.get(label)
        if label_id is None:
            # The missing-label error below tells an operator to create it
            # -- but caching the (incomplete) lookup would make every later
            # retry in this same resident process reuse the stale mapping
            # and keep failing even after the label is created, until
            # restart. Evict so the next retry re-fetches from Gitea.
            self._label_ids.pop(repo, None)
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
            if not rows:
                return issues
            for row in rows:
                if row.get("pull_request") is not None:
                    continue  # belt-and-suspenders: type=issues already excludes PRs.
                number = int(row["number"])
                comments = self._all_comments(repo, number)
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
        raise RuntimeError(
            "Gitea issue discovery exceeded the bounded "
            f"{_MAX_ISSUE_PAGES * _ISSUE_PAGE_SIZE}-issue scan"
        )

    def _find_own_loop_comment(
        self, repo: str, number: int, loop: str
    ) -> int | None:
        comments = self._all_comments(repo, number)
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

    def _active_reservation_depends_on_label(
        self,
        repo: str,
        issue: "Issue",
        reservation: dict[str, Any],
        *,
        exclude_own_loop: bool,
    ) -> bool:
        """True when some active (reserved or claimed) reservation on this
        same label currently exists, per a fresh comment scan.

        With ``exclude_own_loop=True`` (``release()``'s use), only another
        loop's reservation counts -- release() is always called by the
        label's own current holder finishing its own cycle, so comparing
        against itself would be meaningless.

        With ``exclude_own_loop=False`` (``reserve()``'s rollback use), THIS
        loop's own marker counts too: a comment-write transport failure is
        indeterminate -- Gitea may have committed the POST before the
        client observed a timeout/error -- so a fresh scan can show this
        same loop's own marker as genuinely active despite the exception.
        Rolling back in that case would strip the label out from under a
        trusted reservation marker that is now live and depends on it.
        """
        from .repository_issue_loops import Issue as _Issue
        from .repository_issue_loops import _latest_reservations

        comments = self._all_comments(repo, issue.number)
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
        return any(
            value.get("state") in {"reserved", "claimed"}
            and value.get("label") == reservation.get("label")
            and (not exclude_own_loop or value.get("loop") != reservation.get("loop"))
            for value in _latest_reservations(current).values()
        )

    def reserve(self, repo: str, issue: "Issue", reservation: dict[str, Any]) -> None:
        # Resolve the label prerequisite (lookup + add) *before* writing the
        # `reserved` marker comment. If the label is missing or the add fails,
        # this raises before any marker exists, so discovery never sees a
        # phantom reservation on an issue the caller never actually reserved.
        self._verify_identity(repo, allow_cache=False)
        label_id = self._label_id(repo, reservation["label"])
        current_issue = self._call("GET", f"/repos/{repo}/issues/{issue.number}", ok=(200,))
        label_already_present = any(
            isinstance(label, dict) and label.get("id") == label_id
            for label in (current_issue.get("labels") or [])
        )
        self._call(
            "POST", f"/repos/{repo}/issues/{issue.number}/labels",
            payload={"labels": [label_id]}, ok=(200, 201),
        )
        try:
            self._comment(repo, issue, {**reservation, "issue": issue.number})
        except Exception:
            # The label is now live but the marker comment that is supposed
            # to accompany it never landed -- or so it appears: the write
            # is a transport call, and the exception is indeterminate
            # (Gitea may have committed it before the client observed a
            # timeout/error). Best-effort roll the label back so a
            # genuinely failed write never leaks a labeled-but-unmarked
            # reservation -- but ONLY when this call actually introduced
            # the label AND no active reservation (any loop, including this
            # one -- a possibly-succeeded write of our own) now depends on
            # it. Reservations deliberately race before coordinator
            # election, so a rollback must never silently clear a winning
            # reservation, whoever holds it.
            if not label_already_present and not self._active_reservation_depends_on_label(
                repo, issue, reservation, exclude_own_loop=False
            ):
                try:
                    self._call(
                        "DELETE", f"/repos/{repo}/issues/{issue.number}/labels/{label_id}",
                        ok=(200, 204),
                    )
                except Exception:
                    pass
            raise

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
        self._comment(
            repo, issue,
            {**reservation, "issue": issue.number, "state": "released", "reason": reason},
        )
        if not self._active_reservation_depends_on_label(
            repo, issue, reservation, exclude_own_loop=True
        ):
            self._verify_identity(repo, allow_cache=False)
            label_id = self._label_id(repo, reservation["label"])
            self._call(
                "DELETE", f"/repos/{repo}/issues/{issue.number}/labels/{label_id}",
                ok=(200, 204),
            )
            # The scan above and this delete are not atomic with a
            # concurrent reserve(): a racing loop can add the label (a
            # no-op, since it was already present) and not yet have
            # written its own marker at the moment of that scan --
            # reserve-before-election overlap is expected, so this is a
            # real, not hypothetical, window. Re-scan once more right
            # after deleting; if a reservation now appears active on this
            # same label, the race was hit -- re-add the label so that
            # reservation's marker is never left without it.
            if self._active_reservation_depends_on_label(
                repo, issue, reservation, exclude_own_loop=True
            ):
                self._call(
                    "POST", f"/repos/{repo}/issues/{issue.number}/labels",
                    payload={"labels": [label_id]}, ok=(200, 201),
                )
