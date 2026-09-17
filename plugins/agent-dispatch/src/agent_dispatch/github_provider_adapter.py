"""GitHub adapter for the Phase 9 provider/PR-target state machine.

This module is Phase 10 item 3 (``review-automation-reliability`` effort,
``efforts/active/review-automation-reliability/phase-10-live-wiring.md``):
:mod:`agent_dispatch.provider_state_machine` declared ``ApprovalStatus``,
``Mergeability``, ``HoldReason``, and ``Revision`` as pure data with no
adapter reading real PR state into them. This is the **first slice** of
that adapter -- a read-only *observer*, not the full evaluator.

Scope, deliberately narrow:

- :func:`observe_pr_state` is a pure function: raw GitHub GraphQL PR data in,
  a :class:`PRObservation` out. No network, no ``gh`` invocation -- fully
  testable against plain dict fixtures, the same way
  :mod:`agent_dispatch.provider_state_machine` itself is tested against pure
  data before any adapter exists.
- :class:`GitHubPRAdapter` is the thin ``gh``-CLI wrapper that fetches the
  raw data and calls :func:`observe_pr_state`, following the same
  injectable-runner pattern :class:`agent_dispatch.repository_issue_loops.
  GitHubProvider` already established for issue polling (a fresh, narrow
  fetch/verify pair rather than a shared base class -- issues and PR review
  state are different read shapes with different verification needs, and
  Phase 10's own scope boundary says this phase adds new adapter code, it
  does not refactor existing adapters).

What this slice deliberately does **not** do, left to later Phase 10 item 3
slices:

- It does not decide ``ApprovalStatus.STALE``. Staleness is a function of
  *two* observations over time (:func:`agent_dispatch.provider_state_machine.
  classify_revision_change` against a previously-recorded
  :class:`~agent_dispatch.provider_state_machine.Revision`) -- something only
  an evaluator holding prior state can compute. This adapter reports what the
  provider says *right now*; GitHub has no "stale" concept of its own.
- It does not call any ``APPROVAL_TRANSITIONS``/``MERGEABILITY_TRANSITIONS``
  transition, write anything back to GitHub, or feed a task/coordinator loop.
  It is a read model only, exactly the "declare it, prove it sound, wire it
  later" sequencing Phase 9 itself used for the declared tables.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .provider_state_machine import (
    ApprovalStatus,
    HoldReason,
    Mergeability,
    Revision,
)

_GITHUB_PR_QUERY = """
query($owner:String!,$name:String!,$number:Int!) {
  repository(owner:$owner,name:$name) {
    pullRequest(number:$number) {
      number
      title
      isDraft
      headRefOid
      baseRefOid
      reviewDecision
      mergeable
      mergeStateStatus
      labels(first:20) { nodes { name } }
      commits(last:1) {
        nodes {
          commit {
            statusCheckRollup { state }
          }
        }
      }
      reviewThreads(first:50) {
        nodes { isResolved }
      }
    }
  }
}
""".strip()

#: GitHub's own possible ``reviewDecision`` values, mapped onto the declared
#: ``ApprovalStatus`` dimension. ``None`` (no reviews requested/submitted at
#: all) maps to ``NONE`` separately, below -- it is not a key here because a
#: JSON ``null`` is not a string key.
_REVIEW_DECISION_TO_APPROVAL: dict[str, ApprovalStatus] = {
    "APPROVED": ApprovalStatus.APPROVED,
    "CHANGES_REQUESTED": ApprovalStatus.CHANGES_REQUESTED,
    "REVIEW_REQUIRED": ApprovalStatus.PENDING,
}

#: GitHub's own possible ``statusCheckRollup.state`` values, mapped onto the
#: declared ``Mergeability`` dimension's checks sub-states. A rollup state
#: this table does not recognize is a genuine adapter gap (per Phase 9's
#: "never assume the safer state without evidence" rule), not silently
#: treated as clean or pending -- see :func:`observe_pr_state`.
_CHECK_ROLLUP_TO_MERGEABILITY: dict[str, Mergeability] = {
    "SUCCESS": Mergeability.CLEAN,
    "PENDING": Mergeability.CHECKS_PENDING,
    "EXPECTED": Mergeability.CHECKS_PENDING,
    "FAILURE": Mergeability.CHECKS_FAILED,
    "ERROR": Mergeability.CHECKS_FAILED,
}

#: Label names treated as an explicit work-in-progress hold, independent of
#: any title marker. Lowercased for a case-insensitive match.
_WIP_LABELS = frozenset({"wip", "work-in-progress", "do-not-merge"})


class GitHubPRObservationError(RuntimeError):
    """Raised when a raw PR payload cannot be classified against the
    declared provider machine -- an adapter gap, not a caller error."""


@dataclass(frozen=True)
class PRObservation:
    """One point-in-time read of a PR-target's provider-machine-relevant
    state. Carries the raw :class:`~agent_dispatch.provider_state_machine.
    Revision` fingerprints alongside the classified dimensions so a caller
    holding a previously-recorded ``Revision`` can call
    ``classify_revision_change`` itself -- this observation does not compare
    against history."""

    number: int
    approval_status: ApprovalStatus
    mergeability: Mergeability
    holds: frozenset[HoldReason]
    revision: Revision


def _has_wip_marker(title: str, labels: tuple[str, ...]) -> bool:
    normalized_title = title.strip().casefold()
    if normalized_title.startswith("wip") or normalized_title.startswith("[wip]"):
        return True
    return any(label.casefold() in _WIP_LABELS for label in labels)


def _approval_status(review_decision: str | None) -> ApprovalStatus:
    if review_decision is None:
        return ApprovalStatus.NONE
    try:
        return _REVIEW_DECISION_TO_APPROVAL[review_decision]
    except KeyError:
        raise GitHubPRObservationError(
            f"unrecognized GitHub reviewDecision {review_decision!r}"
        ) from None


def _mergeability(pull_request: Mapping[str, Any]) -> Mergeability:
    mergeable = pull_request.get("mergeable")
    if mergeable == "CONFLICTING":
        return Mergeability.CONFLICTED
    if mergeable == "UNKNOWN":
        return Mergeability.UNKNOWN
    if mergeable != "MERGEABLE":
        raise GitHubPRObservationError(f"unrecognized GitHub mergeable value {mergeable!r}")
    commits = ((pull_request.get("commits") or {}).get("nodes")) or []
    rollup = None
    if commits:
        rollup = ((commits[-1].get("commit") or {}).get("statusCheckRollup")) or None
    if rollup is None:
        # No checks configured for this PR at all: nothing is blocking it.
        return Mergeability.CLEAN
    state = rollup.get("state")
    try:
        return _CHECK_ROLLUP_TO_MERGEABILITY[state]
    except KeyError:
        raise GitHubPRObservationError(
            f"unrecognized GitHub statusCheckRollup state {state!r}"
        ) from None


def _holds(pull_request: Mapping[str, Any]) -> frozenset[HoldReason]:
    holds: set[HoldReason] = set()
    if pull_request.get("isDraft"):
        holds.add(HoldReason.DRAFT)
    title = str(pull_request.get("title") or "")
    labels = tuple(
        str(label["name"])
        for label in (pull_request.get("labels") or {}).get("nodes") or []
        if label.get("name")
    )
    if _has_wip_marker(title, labels):
        holds.add(HoldReason.WIP)
    threads = ((pull_request.get("reviewThreads") or {}).get("nodes")) or []
    if any(not thread.get("isResolved") for thread in threads):
        holds.add(HoldReason.BLOCKING_THREADS)
    return frozenset(holds)


def observe_pr_state(pull_request: Mapping[str, Any]) -> PRObservation:
    """Classify one raw GitHub GraphQL ``pullRequest`` node against the
    declared provider machine. Pure: no network, no ``gh`` invocation.

    Raises :class:`GitHubPRObservationError` for any field value this
    adapter does not recognize, rather than guessing a state -- an
    unrecognized value is a real adapter gap that must be fixed here
    (Phase 10's own scope boundary), not silently absorbed into a plausible-
    looking default.
    """
    number = pull_request.get("number")
    if not isinstance(number, int):
        raise GitHubPRObservationError("PR payload missing an integer number")
    head_sha = pull_request.get("headRefOid")
    base_sha = pull_request.get("baseRefOid")
    if not isinstance(head_sha, str) or not head_sha:
        raise GitHubPRObservationError("PR payload missing headRefOid")
    if not isinstance(base_sha, str) or not base_sha:
        raise GitHubPRObservationError("PR payload missing baseRefOid")
    return PRObservation(
        number=number,
        approval_status=_approval_status(pull_request.get("reviewDecision")),
        mergeability=_mergeability(pull_request),
        holds=_holds(pull_request),
        revision=Revision(diff_hash=head_sha, base_sha=base_sha),
    )


class GitHubPRAdapter:
    """Narrow, read-only GitHub PR-state adapter implemented through the
    authenticated ``gh`` CLI, mirroring :class:`agent_dispatch.
    repository_issue_loops.GitHubProvider`'s injectable-runner shape so it is
    testable without any network access."""

    def __init__(
        self,
        expected_login: str,
        runner: Callable[..., Any] = subprocess.run,
    ):
        if not expected_login:
            raise ValueError("expected_login must be non-empty")
        self.expected_login = expected_login
        self.runner = runner
        self._verified_repos: set[str] = set()

    def _gh(self, *args: str) -> Any:
        completed = self.runner(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
        )
        if int(completed.returncode) != 0:
            raise RuntimeError(f"GitHub operation failed: {str(completed.stderr or '').strip()}")
        return completed

    def _verify_identity(self, repo: str) -> None:
        if repo in self._verified_repos:
            return
        login = str(self._gh("api", "user", "--jq", ".login").stdout or "").strip()
        if login.casefold() != self.expected_login.casefold():
            raise RuntimeError(
                "GitHub adapter identity mismatch: expected "
                f"{self.expected_login!r}, got {login!r}"
            )
        full_name = str(
            self._gh("api", f"repos/{repo}", "--jq", ".full_name").stdout or ""
        ).strip()
        if full_name.casefold() != repo.casefold():
            raise RuntimeError(
                f"GitHub repository identity mismatch: expected {repo!r}, got {full_name!r}"
            )
        self._verified_repos.add(repo)

    def fetch_pr(self, repo: str, number: int) -> dict[str, Any]:
        """Fetch the raw ``pullRequest`` GraphQL node for ``repo``/``number``."""
        self._verify_identity(repo)
        owner, name = repo.split("/", 1)
        response = self._gh(
            "api",
            "graphql",
            "-f",
            f"query={_GITHUB_PR_QUERY}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={number}",
        )
        data = json.loads(response.stdout or "{}")
        if data.get("errors"):
            raise RuntimeError(f"GitHub PR fetch failed: {data['errors']}")
        repository = (data.get("data") or {}).get("repository")
        pull_request = repository.get("pullRequest") if isinstance(repository, Mapping) else None
        if not isinstance(pull_request, Mapping):
            raise RuntimeError(f"GitHub PR fetch returned no pullRequest for {repo}#{number}")
        return dict(pull_request)

    def observe(self, repo: str, number: int) -> PRObservation:
        """Fetch and classify ``repo``/``number`` in one call."""
        return observe_pr_state(self.fetch_pr(repo, number))
