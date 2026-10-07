"""In-process fake PR provider -- test/fixture double for :class:`PRProvider`.

``MockPRProvider`` fabricates PRs entirely in memory: no CLI, no network, no
credentials. It exists so the conformance contract (``tests/
test_pr_provider_conformance.py``) -- and any other test that needs a working
``PRProvider`` -- can exercise the full create -> observe -> review -> merge
lifecycle without depending on ``gh`` / ``az`` / a live Gitea instance.

Registered in :data:`agent_worktrees.providers.base._PROVIDERS` as ``"mock"``.
Per Vision ``plugins/agent-worktrees/pull-requests`` §Features/
``conformance-verified-mock-provider``: start with the simplest fabrication
strategy (an in-process fake store) and escalate only if the conformance
contract proves it insufficient.

Each ``MockPRProvider()`` instance owns its own store -- state is **not**
shared across instances or processes. Tests that need to observe/merge a PR
they created must reuse the same instance (this mirrors how a real provider's
state lives entirely server-side, but keeps the fake dependency-free).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .base import ProviderError, PRScope, PullResult

if TYPE_CHECKING:
    from ..pr_bar import Snapshot
    from ..pr_contract import PRDiff, PRSnapshot, ReviewNudgeResult, ThreadsResult


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


@dataclass
class _FakePR:
    """One fabricated PR living in a :class:`MockPRProvider`'s in-memory store."""

    number: int
    repo: str
    url: str
    head: str
    base: str
    title: str
    body: str = ""
    state: str = "open"
    merged: bool = False
    draft: bool = False
    labels: list[str] = field(default_factory=list)
    author: str = "mock-author"
    head_sha: str = ""
    updated_at: str = ""
    mergeable: bool | None = True
    checks_state: str = ""
    reviews: list = field(default_factory=list)
    review_bodies: dict = field(default_factory=dict)  # review id -> body (Review has none)
    threads: list = field(default_factory=list)
    source_marker: str = ""
    auto_complete: bool = False
    auto_merge_armed: bool = False
    review_nudge_count: int = 0
    diff: str = ""
    comments: list = field(default_factory=list)
    """Test-observable list of ``(author, body)`` pairs posted via
    :meth:`MockPRProvider.post_comment` (distinct from ``threads``, which are
    fabricated review-comment threads, not plain issue comments)."""
    """Test-observable counter: how many times :meth:`MockPRProvider.request_review`
    was called for this PR (does not fabricate a new review -- tests that want
    one call :meth:`add_review` separately, mirroring how a real reviewer's
    eventual verdict is a separate, asynchronous event from the request)."""


class MockPRProvider:
    """Fabricates PRs in memory; satisfies the full :class:`PRProvider` protocol."""

    name = "mock"

    def __init__(self) -> None:
        self._prs: dict[str, dict[int, _FakePR]] = {}
        self._next_number: dict[str, int] = {}
        self._forks: dict[str, tuple[str, str]] = {}
        self._next_thread_id = 1

    # -- internal helpers ---------------------------------------------------

    def _bucket(self, repo: str) -> dict[int, _FakePR]:
        return self._prs.setdefault(repo, {})

    def _get(self, repo: str, number: int) -> _FakePR:
        pr = self._bucket(repo).get(number)
        if pr is None:
            raise ProviderError(f"mock: no such PR {repo}#{number}.")
        return pr

    def _pull_result(self, pr: _FakePR) -> PullResult:
        return PullResult(
            url=pr.url,
            number=pr.number,
            state=pr.state,
            merged=pr.merged,
            head_sha=pr.head_sha,
            base_ref=pr.base,
            observed_at=pr.updated_at,
        )

    # -- author-side operations ----------------------------------------------

    def create_pull(self, scope: PRScope, *, token: str | None = None) -> PullResult:
        number = self._next_number.get(scope.repo, 0) + 1
        self._next_number[scope.repo] = number
        pr = _FakePR(
            number=number,
            repo=scope.repo,
            url=f"https://mock.local/{scope.repo}/pull/{number}",
            head=scope.head,
            base=scope.base,
            title=scope.title,
            body=scope.body,
            draft=scope.draft,
            labels=list(scope.labels),
            head_sha=f"sha-{scope.head}",
            updated_at=_now(),
        )
        self._bucket(scope.repo)[number] = pr
        return self._pull_result(pr)

    def get_pull(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PullResult:
        return self._pull_result(self._get(repo, number))

    def observe_head(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> PullResult:
        pr = self._get(repo, number)
        pr.updated_at = _now()
        return self._pull_result(pr)

    def authority_endpoint(self, api_base: str = "") -> str:
        return api_base or "mock://local"

    def publish_source_marker(
        self,
        repo: str,
        number: int,
        marker: str,
        *,
        api_base: str = "",
        token: str | None = None,
    ) -> str:
        self._get(repo, number).source_marker = marker
        return ""

    def remove_label(
        self, repo: str, number: int, label: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        pr = self._get(repo, number)
        if label in pr.labels:
            pr.labels.remove(label)
        return ""

    def add_label(
        self, repo: str, number: int, label: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        pr = self._get(repo, number)
        if label not in pr.labels:
            pr.labels.append(label)
        return ""

    def mark_ready(
        self, repo: str, number: int, *, api_base: str = "",
        token: str | None = None, title: str = "",
        wip_title_prefixes: tuple[str, ...] = (),
    ) -> str:
        pr = self._get(repo, number)
        if not pr.draft:
            return f"mock: PR {repo}#{number} is not a draft."
        pr.draft = False
        current = title or pr.title
        for prefix in wip_title_prefixes:
            if current.startswith(prefix):
                current = current[len(prefix):].lstrip()
                break
        pr.title = current
        return ""

    def get_snapshot(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> "PRSnapshot":
        from ..pr_contract import PRSnapshot

        pr = self._get(repo, number)
        return PRSnapshot(
            pr_state=pr.state,
            merged=pr.merged,
            head_sha=pr.head_sha,
            base_ref=pr.base,
            updated_at=pr.updated_at,
            reviews=tuple(pr.reviews),
            author=pr.author,
            mergeable=pr.mergeable,
            checks_state=pr.checks_state,
            labels=tuple(pr.labels),
            title=pr.title,
            draft=pr.draft,
        )

    def merge_pull(
        self, repo: str, number: int, *, squash: bool = True, admin: bool = False,
        api_base: str = "", token: str | None = None,
        delete_source_branch: bool = True, expected_head_sha: str = "",
    ) -> str:
        pr = self._get(repo, number)
        if pr.merged:
            return f"mock: PR {repo}#{number} is already merged."
        if expected_head_sha and pr.head_sha != expected_head_sha:
            return (
                f"mock: PR {repo}#{number} head {pr.head_sha!r} does not match "
                f"--match-head-commit {expected_head_sha!r}"
            )
        pr.merged = True
        pr.state = "closed"
        pr.updated_at = _now()
        return ""

    def close_pull(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None,
        comment: str = "",
    ) -> str:
        pr = self._get(repo, number)
        if pr.merged:
            return f"mock: PR {repo}#{number} is already merged, cannot close unmerged."
        if pr.state == "closed":
            return f"mock: PR {repo}#{number} is already closed."
        if comment:
            pr.threads.append({"author": "mock-actor", "body": comment})
        pr.state = "closed"
        pr.updated_at = _now()
        return ""

    def request_auto_complete(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None,
        automerge_label: str = "", squash: bool = True,
        delete_source_branch: bool = True, bypass_policy: bool = False,
        bypass_reason: str = "",
    ) -> str:
        pr = self._get(repo, number)
        pr.auto_complete = True
        if automerge_label and automerge_label not in pr.labels:
            pr.labels.append(automerge_label)
        return ""

    def enable_auto_merge(
        self, repo: str, number: int, *, squash: bool = True,
        api_base: str = "", token: str | None = None,
        delete_source_branch: bool = True, expected_head_sha: str = "",
    ) -> str:
        pr = self._get(repo, number)
        if expected_head_sha and pr.head_sha != expected_head_sha:
            return (
                f"mock: PR {repo}#{number} head {pr.head_sha!r} does not match "
                f"--match-head-commit {expected_head_sha!r}"
            )
        pr.auto_merge_armed = True
        return ""

    def get_repo_policy(
        self, repo: str, *, default_branch: str = "", api_base: str = "",
        token: str | None = None,
    ):
        from ..pr_contract import RepoPolicy

        return RepoPolicy(
            supported=True,
            allow_squash=True,
            allow_merge_commit=True,
            allow_rebase=True,
            allow_auto_merge=True,
            delete_branch_on_merge=True,
            required_approving_reviews=0,
            has_required_status_checks=False,
            viewer_permission="admin",
        )

    def head_contained_in_base(
        self, repo: str, base: str, head_sha: str, *, api_base: str = "",
        token: str | None = None,
    ) -> bool | None:
        for pr in self._bucket(repo).values():
            if pr.head_sha == head_sha and pr.base == base:
                return pr.merged
        return None

    def ensure_fork(
        self, repo: str, *, api_base: str = "", token: str | None = None,
    ) -> tuple[str, str] | None:
        if repo in self._forks:
            return self._forks[repo]
        if "/" not in repo:
            return None
        _, name = repo.split("/", 1)
        owner = "mock-owner"
        clone_url = f"https://mock.local/{owner}/{name}.git"
        self._forks[repo] = (owner, clone_url)
        return owner, clone_url

    def resolve_fork_owner(self, *, api_base: str = "", token: str | None = None) -> str | None:
        """Non-mutating counterpart to :meth:`ensure_fork` -- the mock
        always resolves to the same fixed login regardless of ``repo``."""
        return "mock-owner"

    def get_bar_snapshot(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> "Snapshot":
        """The merge-bar read from the in-memory PR: its fabricated reviews, threads,
        labels and ``checks_state`` (one synthetic check; none when unset)."""
        from ..pr_bar import Snapshot, unsupported

        try:
            pr = self._get(repo, number)
        except ProviderError as exc:  # the read never raises: an unreadable PR is named
            snap = unsupported(repo, number, self.name)
            snap.errors["pr"] = str(exc)
            return snap
        state = "MERGED" if pr.merged else ("CLOSED" if pr.state == "closed" else "OPEN")
        conclusion = {"success": "SUCCESS", "failure": "FAILURE"}.get(pr.checks_state, "")
        checks = [{"name": "mock-ci", "status": "COMPLETED" if conclusion else "IN_PROGRESS",
                   "conclusion": conclusion}] if pr.checks_state else []
        reviews = [{"id": r.id, "author": r.user, "state": "DISMISSED" if r.dismissed else r.state,
                    "commit": r.commit_id, "body": pr.review_bodies.get(r.id, ""), "at": r.submitted_at}
                   for r in pr.reviews]
        threads = [{"resolved": not t.is_active and t.status != "outdated", "outdated": t.status == "outdated",
                    "path": t.file_path, "author": t.comments[0].author if t.comments else ""}
                   for t in pr.threads]
        return Snapshot(repo=repo, number=number, state=state, head=pr.head_sha,
                        head_after=pr.head_sha, author=pr.author, draft=pr.draft, title=pr.title,
                        labels=sorted(pr.labels), checks=checks, reviews=reviews, threads=threads,
                        mergeable={True: "MERGEABLE", False: "CONFLICTING"}.get(pr.mergeable, "UNKNOWN"))

    def get_comment_threads(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> "ThreadsResult":
        from ..pr_contract import ThreadsResult

        pr = self._get(repo, number)
        return ThreadsResult(threads=tuple(pr.threads), supported=True)

    def resolve_threads(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None,
        thread_ids: tuple[int, ...] = (),
    ) -> str:
        pr = self._get(repo, number)
        targets = set(thread_ids) if thread_ids else None
        resolved = []
        for thread in pr.threads:
            if targets is None or thread.id in targets:
                resolved.append(replace(thread, status="resolved"))
            else:
                resolved.append(thread)
        pr.threads = resolved
        return ""

    def list_open_pulls(
        self, repo: str, *, api_base: str = "", token: str | None = None
    ) -> tuple[int, ...]:
        return tuple(
            sorted(n for n, pr in self._bucket(repo).items() if pr.state == "open")
        )

    def find_pull_by_head(
        self, repo: str, head: str, *, api_base: str = "", token: str | None = None
    ) -> PullResult | None:
        for pr in self._bucket(repo).values():
            if pr.head == head:
                return self._pull_result(pr)
        return None

    def request_review(
        self, repo: str, number: int, *, reviewer: str = "", api_base: str = "",
        token: str | None = None,
    ) -> "ReviewNudgeResult":
        from ..pr_contract import ReviewNudgeResult

        pr = self._get(repo, number)
        if not reviewer:
            return ReviewNudgeResult(
                supported=False, detail="mock: no pr.reviewer configured on this repo."
            )
        pr.review_nudge_count += 1
        return ReviewNudgeResult(
            supported=True, requested=True, reviewer=reviewer,
            detail=f"mock: requested a review from '{reviewer}' "
                   f"(nudge #{pr.review_nudge_count}).",
        )

    def get_diff(
        self, repo: str, number: int, *, api_base: str = "", token: str | None = None
    ) -> "PRDiff":
        from ..pr_contract import PRDiff

        pr = self._get(repo, number)
        return PRDiff(diff=pr.diff, supported=True)

    def post_comment(
        self, repo: str, number: int, body: str, *, api_base: str = "",
        token: str | None = None,
    ) -> str:
        pr = self._get(repo, number)
        pr.comments.append((pr.author, body))
        return ""

    _REVIEW_EVENT_STATES = ("APPROVED", "CHANGES_REQUESTED", "COMMENTED")

    def submit_review(
        self, repo: str, number: int, *, event: str, body: str = "",
        api_base: str = "", token: str | None = None,
    ) -> str:
        from ..pr_contract import Review

        canonical = event.upper()
        if canonical not in self._REVIEW_EVENT_STATES:
            return (
                f"mock: unknown review event {event!r} (expected one of "
                f"{self._REVIEW_EVENT_STATES})."
            )
        pr = self._get(repo, number)
        if body:
            pr.comments.append(("mock-reviewer", body))
        review_id = len(pr.reviews) + 1
        pr.reviews.append(
            Review(
                id=review_id, state=canonical, user="mock-reviewer",
                submitted_at=_now(), commit_id=pr.head_sha,
            )
        )
        return ""

    # -- test-only fabrication helpers (not part of the PRProvider protocol) --

    def set_diff(self, repo: str, number: int, diff_text: str) -> None:
        """Fabricate a PR's unified diff (conformance-test setup only)."""
        self._get(repo, number).diff = diff_text

    def add_review(
        self, repo: str, number: int, *, id: int, state: str, user: str,
        submitted_at: str = "", commit_id: str = "", dismissed: bool = False, body: str = "",
    ) -> None:
        """Fabricate a review on an existing PR (conformance-test setup only); *body*
        is what ``get_bar_snapshot`` reports as the review's summary."""
        from ..pr_contract import Review

        pr = self._get(repo, number)
        if body:
            pr.review_bodies[id] = body
        pr.reviews.append(
            Review(
                id=id, state=state, user=user, submitted_at=submitted_at,
                commit_id=commit_id or pr.head_sha, dismissed=dismissed,
            )
        )

    def add_thread(
        self, repo: str, number: int, *, id: int | None = None, status: str = "active",
        file_path: str = "", comments: tuple = (),
    ) -> int:
        """Fabricate a review comment thread (conformance-test setup only)."""
        from ..pr_contract import CommentThread

        pr = self._get(repo, number)
        if id is None:
            id = self._next_thread_id
            self._next_thread_id += 1
        pr.threads.append(
            CommentThread(id=id, status=status, file_path=file_path, comments=comments)
        )
        return id
