"""Gitea adapter for the reviewer-side PR state machine.

Mirrors :mod:`agent_dispatch.github_provider_adapter`'s and
:mod:`agent_dispatch.azure_devops_provider_adapter`'s deliberately narrow
split (observe only; no vote/merge/close actions -- those remain
worker-direct tool actions, exactly as for the other two forges):

- :func:`observe_pr_state` is pure: raw Gitea PR / reviews / review-comment
  payloads in, a provider-neutral
  :class:`~agent_dispatch.github_provider_adapter.PRObservation` out.
- :class:`GiteaPRAdapter` is the thin, curl-based REST wrapper that fetches
  that raw shape and calls the classifier, via the Gitea REST API over
  ``curl`` (no vendored CLI, no new Python HTTP dependency) -- the same
  integration approach :mod:`agent_dispatch.gitea_provider` uses for the
  backlog surface; see that module's own docstring for the full rationale.

Repo addressing: unlike GitHub (always github.com) and Azure DevOps (whose
``organization/project/repository`` already names the org), Gitea is
self-hosted with no single well-known host -- a reviewer ``payload_ref``
names it directly: ``gitea-pr:<key>/<owner>/<repo>#<number>``, where
``<key>`` is an opaque authority key (conventionally the instance hostname,
e.g. ``gitea.example.com``, but never resolved as one). The ref's ``<key>``
is **never trusted directly** as a credential authority or turned into a
URL -- it is looked up in a configured, non-empty ``api_bases`` mapping
(``<key>`` -> the instance's real, possibly path-hosted, base URL, e.g.
``https://h/gitea`` for an instance mounted under a path, the same
deployment shape ``agent_worktrees.providers.gitea`` already supports via
its own explicit ``api_base``). A ``<key>`` with no configured entry is
refused before any request is made, since an untrusted/attacker-
controlled ref would otherwise be able to redirect this adapter's token to
an arbitrary server.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from .github_provider_adapter import PRObservation, _has_wip_marker
from .gitea_connection import DEFAULT_GITEA_TOKEN_ENV
from .provider_state_machine import ApprovalStatus, HoldReason, Mergeability, Revision

_REVIEW_PAGE_SIZE = 50
_MAX_REVIEW_PAGES = 20

_REVIEW_STATE_TO_APPROVAL: dict[str, str] = {
    "APPROVED": "APPROVED",
    "REQUEST_CHANGES": "CHANGES_REQUESTED",
    "COMMENT": "COMMENT",
    "PENDING": "PENDING",
    "REQUEST_REVIEW": "PENDING",
}
_COMMIT_STATUS_TO_MERGEABILITY: dict[str, Mergeability] = {
    "success": Mergeability.CLEAN,
    "skipped": Mergeability.CLEAN,
    "pending": Mergeability.CHECKS_PENDING,
    "warning": Mergeability.CHECKS_PENDING,
    "failure": Mergeability.CHECKS_FAILED,
    "error": Mergeability.CHECKS_FAILED,
}


class GiteaPRObservationError(RuntimeError):
    """Raised when a raw Gitea PR payload cannot be classified -- an adapter
    gap, not a caller error."""


def _split(repo: str) -> tuple[str, str, str]:
    parts = repo.split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            "Gitea reviewer repo must be '<key>/<owner>/<repository>'"
        )
    return parts[0], parts[1], parts[2]


def _approval_status(reviews: list[Mapping[str, Any]]) -> ApprovalStatus:
    """Reduce Gitea's per-review list to one aggregate decision.

    Gitea has no server-computed aggregate like GitHub's ``reviewDecision``
    -- only the latest *verdict-bearing* review **per reviewer** counts (an
    earlier ``REQUEST_CHANGES`` superseded by that same reviewer's later
    ``APPROVED`` must not still block), mirroring how GitHub's own
    ``reviewDecision`` already discards a dismissed/superseded review.
    A ``COMMENT``/``PENDING`` review carries no verdict of its own and must
    never supersede that reviewer's last real verdict -- Gitea keeps an
    approval/rejection as the official decision until another *verdict-
    bearing* review changes it, so a later comment-only review is tracked
    only far enough to know a review exists at all (for the all-comments,
    no-verdicts-yet ``PENDING`` case below).
    """
    verdict_by_reviewer: dict[str, tuple[int, str]] = {}
    reviewers_with_any_review: set[str] = set()
    for review in reviews:
        if review.get("dismissed") or review.get("stale"):
            # A dismissed verdict must not still block/approve; a stale one
            # (Gitea 1.26+: a review attached to an older head, distinct
            # from dismissed) must not determine the current status either
            # -- especially on a first observation, where no history
            # evaluator exists yet to correct it.
            continue
        state = review.get("state")
        if state not in _REVIEW_STATE_TO_APPROVAL:
            raise GiteaPRObservationError(f"unrecognized Gitea review state {state!r}")
        if _REVIEW_STATE_TO_APPROVAL[state] == "PENDING" and state == "PENDING":
            continue  # a draft/unsubmitted review carries no verdict yet.
        reviewer = str((review.get("user") or {}).get("login") or "")
        if not reviewer:
            continue
        reviewers_with_any_review.add(reviewer)
        canonical = _REVIEW_STATE_TO_APPROVAL[state]
        if canonical not in ("APPROVED", "CHANGES_REQUESTED"):
            continue  # COMMENT: feedback, not a verdict -- never supersedes one.
        review_id = int(review.get("id") or 0)
        existing = verdict_by_reviewer.get(reviewer)
        if existing is None or review_id >= existing[0]:
            verdict_by_reviewer[reviewer] = (review_id, canonical)
    canonical = {state for _rid, state in verdict_by_reviewer.values()}
    if "CHANGES_REQUESTED" in canonical:
        return ApprovalStatus.CHANGES_REQUESTED
    if "APPROVED" in canonical:
        return ApprovalStatus.APPROVED
    if reviewers_with_any_review:
        return ApprovalStatus.PENDING
    return ApprovalStatus.NONE


def _mergeability(pull_request: Mapping[str, Any], status_rollup: str | None) -> Mergeability:
    mergeable = pull_request.get("mergeable")
    if mergeable is False:
        return Mergeability.CONFLICTED
    if mergeable is None:
        return Mergeability.UNKNOWN
    if mergeable is not True:
        raise GiteaPRObservationError(f"unrecognized Gitea mergeable value {mergeable!r}")
    if status_rollup is None:
        return Mergeability.CLEAN
    try:
        return _COMMIT_STATUS_TO_MERGEABILITY[status_rollup]
    except KeyError:
        raise GiteaPRObservationError(
            f"unrecognized Gitea combined commit status {status_rollup!r}"
        ) from None


def _holds(
    pull_request: Mapping[str, Any],
    review_comment_groups: list[list[Mapping[str, Any]]],
) -> frozenset[HoldReason]:
    holds: set[HoldReason] = set()
    if pull_request.get("draft") or pull_request.get("is_draft"):
        holds.add(HoldReason.DRAFT)
    title = str(pull_request.get("title") or "")
    labels = tuple(
        str(label.get("name")) for label in (pull_request.get("labels") or [])
        if isinstance(label, Mapping) and label.get("name")
    )
    if _has_wip_marker(title, labels):
        holds.add(HoldReason.WIP)
    # Each group is one review's own thread of comments. Gitea records the
    # resolver on a resolved thread's root comment while replies stay
    # unset, so a group is resolved when ANY of its comments carries one --
    # the same reading agent_worktrees.providers.gitea.get_comment_threads
    # already uses for the identical field.
    if any(
        not any(comment.get("resolver") for comment in group)
        for group in review_comment_groups
        if group
    ):
        holds.add(HoldReason.BLOCKING_THREADS)
    return frozenset(holds)


def observe_pr_state(
    pull_request: Mapping[str, Any],
    reviews: list[Mapping[str, Any]] | None = None,
    review_comment_groups: list[list[Mapping[str, Any]]] | None = None,
    status_rollup: str | None = None,
) -> PRObservation:
    """Classify one raw Gitea PR payload against the declared provider
    machine. Pure: no network, no transport call.

    ``reviews`` is the raw ``/pulls/{index}/reviews`` list;
    ``review_comment_groups`` is one inline-comment thread per review (each
    comment carrying a ``resolver`` field when resolved, Gitea's own
    read-side convention -- see ``agent_worktrees.providers.gitea.
    get_comment_threads``'s identical reading of the same field); a thread
    is resolved when any comment in its own group carries one, since Gitea
    records the resolver on a thread's root comment while replies stay
    unset. ``status_rollup`` is the combined commit-status state
    (``"success"``/``"pending"``/``"warning"``/``"failure"``/``"error"``/
    ``"skipped"``, or ``None`` when no statuses are configured).
    """
    number = pull_request.get("number")
    if not isinstance(number, int):
        raise GiteaPRObservationError("PR payload missing an integer number")
    head_sha = (pull_request.get("head") or {}).get("sha")
    base_sha = (pull_request.get("base") or {}).get("sha")
    if not isinstance(head_sha, str) or not head_sha:
        raise GiteaPRObservationError("PR payload missing head.sha")
    if not isinstance(base_sha, str) or not base_sha:
        raise GiteaPRObservationError("PR payload missing base.sha")
    return PRObservation(
        number=number,
        approval_status=_approval_status(list(reviews or ())),
        mergeability=_mergeability(pull_request, status_rollup),
        holds=_holds(pull_request, list(review_comment_groups or ())),
        revision=Revision(diff_hash=head_sha, base_sha=base_sha),
        last_commit_at=_last_commit_at(pull_request),
    )


def _last_commit_at(pull_request: Mapping[str, Any]) -> float | None:
    commit = (pull_request.get("head") or {}).get("_commit_detail")
    if commit is None:
        return None
    if not isinstance(commit, Mapping):
        raise GiteaPRObservationError("PR payload has an invalid head commit detail")
    date = (
        (commit.get("committer") or {}).get("date")
        or (commit.get("author") or {}).get("date")
    )
    if date is None:
        return None
    if not isinstance(date, str) or not date:
        raise GiteaPRObservationError("PR payload has an invalid commit date")
    try:
        return datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise GiteaPRObservationError(
            f"PR payload has an invalid commit date {date!r}"
        ) from exc


class GiteaPRAdapter:
    """Read-only Gitea PR-state adapter implemented through the Gitea REST
    API via ``curl`` (see this module's own docstring for why)."""

    def __init__(
        self,
        expected_login: str,
        runner: Callable[..., Any] = subprocess.run,
        *,
        api_bases: Mapping[str, str] | None = None,
        token_env: str | None = None,
    ):
        if not expected_login:
            raise ValueError("expected_login must be non-empty")
        if not api_bases:
            raise ValueError(
                "GiteaPRAdapter requires a non-empty api_bases: the "
                "target instance comes from a caller-supplied payload_ref "
                "(gitea-pr:<key>/<owner>/<repo>#<number>), so the "
                "credential authority and real (possibly path-hosted) API "
                "base must be bound to a configured mapping rather than "
                "reconstructed from that ref's key directly -- otherwise a "
                "ref naming an arbitrary key would send this adapter's "
                "token to an unconfigured server."
            )
        self.expected_login = expected_login
        self.runner = runner
        self.api_bases = {key.casefold(): base.rstrip("/") for key, base in api_bases.items()}
        self.token_env = token_env or DEFAULT_GITEA_TOKEN_ENV
        self._verified_repos: set[str] = set()

    def _token(self) -> str:
        token = os.environ.get(self.token_env)
        if not token:
            raise RuntimeError(
                f"Gitea operation failed: environment variable "
                f"{self.token_env!r} is unset or empty"
            )
        return token

    def _call(self, api_base: str, method: str, path: str) -> Any:
        args = [
            "curl", "-sS", "-X", method, f"{api_base}/api/v1{path}",
            "-H", f"Authorization: token {self._token()}",
            "-H", "Accept: application/json",
            "-w", "\n%{http_code}",
        ]
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
        if status != 200:
            raise RuntimeError(
                f"Gitea {method} {path} failed (HTTP {status}): {body.strip()[:300]}"
            )
        return json.loads(body) if body.strip() else None

    def _verify_identity(self, api_base: str, repo_key: str, owner: str, name: str) -> None:
        if repo_key in self._verified_repos:
            return
        login = str((self._call(api_base, "GET", "/user") or {}).get("login") or "")
        if login.casefold() != self.expected_login.casefold():
            raise RuntimeError(
                "Gitea adapter identity mismatch: expected "
                f"{self.expected_login!r}, got {login!r}"
            )
        full_name = str(
            (self._call(api_base, "GET", f"/repos/{owner}/{name}") or {}).get("full_name") or ""
        )
        if full_name.casefold() != f"{owner}/{name}".casefold():
            raise RuntimeError(
                f"Gitea repository identity mismatch: expected {owner}/{name!r}, "
                f"got {full_name!r}"
            )
        self._verified_repos.add(repo_key)

    def _all_reviews(self, api_base: str, owner: str, name: str, number: int) -> list[dict[str, Any]]:
        """List every review on a PR, across pages.

        A single unpaginated page silently drops approvals/change-requests
        once a PR accumulates more reviews than one page -- ``_approval_status``
        would then classify against a stale/incomplete verdict, and the
        dropped reviews' own inline comments would never be checked for
        :data:`HoldReason.BLOCKING_THREADS` either. Stops on a genuinely
        **empty** page, not merely a short one -- a Gitea server may clamp
        ``limit`` below the requested value, and a short-but-nonempty page
        would then be mistaken for the last one.
        """
        reviews: list[dict[str, Any]] = []
        for _page_index in range(_MAX_REVIEW_PAGES):
            page = _page_index + 1
            rows = self._call(
                api_base, "GET",
                f"/repos/{owner}/{name}/pulls/{number}/reviews"
                f"?page={page}&limit={_REVIEW_PAGE_SIZE}",
            ) or []
            if not rows:
                return reviews
            reviews.extend(rows)
        raise RuntimeError(
            f"Gitea review listing for {owner}/{name}#{number} exceeded the "
            f"bounded {_MAX_REVIEW_PAGES} pages"
        )

    def _all_review_comments(
        self, api_base: str, owner: str, name: str, number: int, review_id: int,
    ) -> list[dict[str, Any]]:
        """List every inline comment on one review, across pages.

        A single unpaginated page can hide an unresolved comment sitting on
        a later page, silently clearing :data:`HoldReason.BLOCKING_THREADS`
        for a review that still has open feedback. Stops on a genuinely
        empty page, for the same server-clamped-``limit`` reason
        :meth:`_all_reviews` does.
        """
        comments: list[dict[str, Any]] = []
        for _page_index in range(_MAX_REVIEW_PAGES):
            page = _page_index + 1
            rows = self._call(
                api_base, "GET",
                f"/repos/{owner}/{name}/pulls/{number}/reviews/{review_id}/comments"
                f"?page={page}&limit={_REVIEW_PAGE_SIZE}",
            ) or []
            if not rows:
                return comments
            comments.extend(rows)
        raise RuntimeError(
            f"Gitea review-comment listing for {owner}/{name}#{number} review "
            f"{review_id} exceeded the bounded "
            f"{_MAX_REVIEW_PAGES * _REVIEW_PAGE_SIZE}-comment scan"
        )

    def fetch_pr(self, repo: str, number: int) -> dict[str, Any]:
        """Fetch the raw PR payload (plus reviews/comments/status) for
        ``repo`` (``<key>/<owner>/<name>``) and ``number``."""
        key, owner, name = _split(repo)
        api_base = self.api_bases.get(key.casefold())
        if api_base is None:
            raise RuntimeError(
                f"Gitea reviewer ref names key {key!r}, which is not in "
                "this adapter's configured api_bases -- refusing to send "
                "credentials to an unconfigured authority."
            )
        self._verify_identity(api_base, repo, owner, name)
        pull_request = self._call(api_base, "GET", f"/repos/{owner}/{name}/pulls/{number}")
        if not isinstance(pull_request, Mapping):
            raise RuntimeError(f"Gitea PR fetch returned nothing for {repo}#{number}")
        reviews = self._all_reviews(api_base, owner, name, number)
        review_comment_groups: list[list[dict[str, Any]]] = []
        for review in reviews:
            if review.get("dismissed") or review.get("stale"):
                continue  # a dismissed/stale review's inline comments don't block either.
            review_id = review.get("id")
            if not isinstance(review_id, int):
                continue
            group = self._all_review_comments(api_base, owner, name, number, review_id)
            if group:
                review_comment_groups.append(group)
        head_sha = (pull_request.get("head") or {}).get("sha")
        status_rollup = None
        if isinstance(head_sha, str) and head_sha:
            status = self._call(
                api_base, "GET", f"/repos/{owner}/{name}/commits/{head_sha}/status"
            )
            if isinstance(status, Mapping):
                # Gitea's combined-status endpoint reports "pending" by
                # default even when NO statuses are configured at all
                # (total_count == 0, statuses == null) -- unlike GitHub,
                # which omits the rollup entirely in that case. Treat a
                # genuinely empty status list the same way the GitHub/
                # Azure DevOps adapters treat "no checks configured": clean,
                # not pending (confirmed live against a real Gitea instance
                # with no CI configured).
                status_rollup = status.get("state") if status.get("total_count") else None
            commit_detail = self._call(
                api_base, "GET", f"/repos/{owner}/{name}/git/commits/{head_sha}"
            )
            if isinstance(commit_detail, Mapping):
                inner_commit = commit_detail.get("commit")
                pull_request = {
                    **dict(pull_request),
                    "head": {
                        **dict(pull_request.get("head") or {}),
                        "_commit_detail": inner_commit if isinstance(inner_commit, Mapping) else None,
                    },
                }
        return {
            "pull_request": dict(pull_request),
            "reviews": reviews,
            "review_comment_groups": review_comment_groups,
            "status_rollup": status_rollup,
        }

    def observe(self, repo: str, number: int) -> PRObservation:
        """Fetch and classify ``repo``/``number`` in one call."""
        raw = self.fetch_pr(repo, number)
        return observe_pr_state(
            raw["pull_request"], raw["reviews"], raw["review_comment_groups"],
            raw["status_rollup"],
        )
