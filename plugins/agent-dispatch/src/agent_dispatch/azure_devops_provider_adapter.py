"""Azure DevOps adapter for the reviewer-side PR state machine.

This mirrors :mod:`agent_dispatch.github_provider_adapter`'s deliberately
narrow split:

- :func:`observe_pr_state` is pure: raw Azure DevOps PR / reviewer / thread
  payloads in, a provider-neutral :class:`PRObservation` out.
- :class:`AzureDevOpsPRAdapter` is the thin authenticated ``az``/REST
  wrapper that fetches that raw shape and calls the classifier.

Scope is intentionally the same read-only slice as the GitHub adapter:
observe the current provider state relevant to the declared approval /
mergeability / hold / revision machine, but do not post votes, mutate the
PR, or own close/merge actions. The standing reviewer worker still performs
those through its own direct tool access, exactly as the GitHub-backed flow
already does today.
"""

from __future__ import annotations

from datetime import datetime
import json
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

from .github_provider_adapter import PRObservation, _has_wip_marker
from .provider_state_machine import ApprovalStatus, HoldReason, Mergeability, Revision

_AZURE_DEVOPS_RESOURCE = "499b84ac-1321-427f-aa17-267ca6975798"
_POSITIVE_VOTES = frozenset({5, 10})
_NEGATIVE_VOTES = frozenset({-10, -5})
_RESOLVED_THREAD_STATUSES = frozenset({"fixed", "wontFix", "closed", "byDesign"})
_BLOCKING_THREAD_STATUSES = frozenset({"unknown", "active", "pending"})
_MERGE_STATUS_TO_MERGEABILITY: dict[str, Mergeability] = {
    "notSet": Mergeability.UNKNOWN,
    "queued": Mergeability.CHECKS_PENDING,
    "conflicts": Mergeability.CONFLICTED,
    "succeeded": Mergeability.CLEAN,
    "rejectedByPolicy": Mergeability.CHECKS_FAILED,
    "failure": Mergeability.CHECKS_FAILED,
}


class AzureDevOpsPRObservationError(RuntimeError):
    """Raised when a raw Azure DevOps PR payload cannot be classified."""


def _split(repo: str) -> tuple[str, str, str]:
    parts = repo.split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError(
            "Azure DevOps reviewer repo must be 'organization/project/repository'"
        )
    return parts[0], parts[1], parts[2]


def _identity_key(identity: Mapping[str, Any]) -> tuple[str, str]:
    return (
        str(identity.get("id") or "").casefold(),
        str(identity.get("uniqueName") or "").casefold(),
    )


def _parse_vote(reviewer: Mapping[str, Any]) -> int:
    vote = reviewer.get("vote")
    if isinstance(vote, bool) or not isinstance(vote, int):
        raise AzureDevOpsPRObservationError(
            f"reviewer payload has invalid vote {vote!r}"
        )
    if vote not in {-10, -5, 0, 5, 10}:
        raise AzureDevOpsPRObservationError(f"unrecognized Azure DevOps reviewer vote {vote!r}")
    return vote


def _relevant_reviewers(
    pull_request: Mapping[str, Any], reviewers: list[Mapping[str, Any]]
) -> list[Mapping[str, Any]]:
    author = pull_request.get("createdBy")
    author_key = _identity_key(author) if isinstance(author, Mapping) else ("", "")
    relevant = []
    for reviewer in reviewers:
        if not isinstance(reviewer, Mapping):
            raise AzureDevOpsPRObservationError("reviewers payload must contain mappings")
        if reviewer.get("hasDeclined") is True:
            continue
        reviewer_key = _identity_key(reviewer)
        if reviewer_key == author_key:
            continue
        relevant.append(reviewer)
    if not relevant:
        return []
    required = [reviewer for reviewer in relevant if reviewer.get("isRequired") is True]
    if required:
        relevant = required
    non_container = [
        reviewer for reviewer in relevant if reviewer.get("isContainer") is not True
    ]
    if not non_container:
        return relevant
    required_containers = [
        reviewer
        for reviewer in relevant
        if reviewer.get("isContainer") is True and reviewer.get("isRequired") is True
    ]
    return [*non_container, *required_containers]


def _approval_status(
    pull_request: Mapping[str, Any], reviewers: list[Mapping[str, Any]]
) -> ApprovalStatus:
    relevant = _relevant_reviewers(pull_request, reviewers)
    if not relevant:
        return ApprovalStatus.NONE
    votes = [_parse_vote(reviewer) for reviewer in relevant]
    if any(vote in _NEGATIVE_VOTES for vote in votes):
        return ApprovalStatus.CHANGES_REQUESTED
    if any(vote in _POSITIVE_VOTES for vote in votes):
        return (
            ApprovalStatus.APPROVED
            if all(vote in _POSITIVE_VOTES for vote in votes)
            else ApprovalStatus.PENDING
        )
    if any(vote == 0 for vote in votes):
        return ApprovalStatus.PENDING
    return ApprovalStatus.NONE


def _mergeability(pull_request: Mapping[str, Any]) -> Mergeability:
    merge_status = pull_request.get("mergeStatus")
    try:
        base = _MERGE_STATUS_TO_MERGEABILITY[str(merge_status)]
    except KeyError:
        raise AzureDevOpsPRObservationError(
            f"unrecognized Azure DevOps mergeStatus {merge_status!r}"
        ) from None
    if base in {Mergeability.CONFLICTED, Mergeability.CHECKS_FAILED}:
        return base
    policy_evaluations = pull_request.get("policyEvaluations") or []
    if not isinstance(policy_evaluations, list):
        raise AzureDevOpsPRObservationError("policyEvaluations must be a list when present")
    blocking = []
    for evaluation in policy_evaluations:
        if not isinstance(evaluation, Mapping):
            raise AzureDevOpsPRObservationError("policyEvaluations must contain mappings")
        configuration = evaluation.get("configuration") or {}
        if isinstance(configuration, Mapping) and configuration.get("isBlocking") is False:
            continue
        blocking.append(evaluation)
    if not blocking:
        return base
    statuses = {str(evaluation.get("status") or "") for evaluation in blocking}
    if statuses & {"rejected", "broken"}:
        return Mergeability.CHECKS_FAILED
    if statuses & {"queued", "running"}:
        return Mergeability.CHECKS_PENDING
    if statuses <= {"approved", "notApplicable"}:
        return base
    raise AzureDevOpsPRObservationError(
        f"unrecognized Azure DevOps policy evaluation statuses {sorted(statuses)!r}"
    )


def _last_commit_at(pull_request: Mapping[str, Any]) -> float | None:
    commit = pull_request.get("lastMergeSourceCommitDetail")
    if commit is None:
        return None
    if not isinstance(commit, Mapping):
        raise AzureDevOpsPRObservationError("lastMergeSourceCommitDetail must be a mapping")
    author = commit.get("author")
    committer = commit.get("committer")
    date = None
    if isinstance(author, Mapping):
        date = author.get("date")
    if date is None and isinstance(committer, Mapping):
        date = committer.get("date")
    if date is None:
        return None
    if not isinstance(date, str) or not date:
        raise AzureDevOpsPRObservationError(
            "PR payload has invalid lastMergeSourceCommitDetail.*.date"
        )
    try:
        return datetime.fromisoformat(date.replace("Z", "+00:00")).timestamp()
    except ValueError as exc:
        raise AzureDevOpsPRObservationError(
            f"PR payload has invalid lastMergeSourceCommitDetail date {date!r}"
        ) from exc


def _holds(pull_request: Mapping[str, Any], threads: list[Mapping[str, Any]]) -> frozenset[HoldReason]:
    holds: set[HoldReason] = set()
    if pull_request.get("isDraft"):
        holds.add(HoldReason.DRAFT)
    labels = tuple(
        str(label.get("name"))
        for label in (pull_request.get("labels") or [])
        if isinstance(label, Mapping) and label.get("name")
    )
    title = str(pull_request.get("title") or "")
    if _has_wip_marker(title, labels):
        holds.add(HoldReason.WIP)
    for thread in threads:
        if not isinstance(thread, Mapping):
            raise AzureDevOpsPRObservationError("threads payload must contain mappings")
        if thread.get("isDeleted") is True:
            continue
        status = thread.get("status")
        if status is None:
            continue
        if status in _BLOCKING_THREAD_STATUSES:
            holds.add(HoldReason.BLOCKING_THREADS)
            break
        if status in _RESOLVED_THREAD_STATUSES:
            continue
        raise AzureDevOpsPRObservationError(
            f"unrecognized Azure DevOps thread status {status!r}"
        )
    return frozenset(holds)


def observe_pr_state(
    pull_request: Mapping[str, Any],
    reviewers: list[Mapping[str, Any]] | None = None,
    threads: list[Mapping[str, Any]] | None = None,
) -> PRObservation:
    """Classify one raw Azure DevOps PR payload against the declared machine."""
    number = pull_request.get("pullRequestId")
    if not isinstance(number, int):
        raise AzureDevOpsPRObservationError("PR payload missing an integer pullRequestId")
    head_commit = pull_request.get("lastMergeSourceCommit")
    base_commit = pull_request.get("lastMergeTargetCommit")
    if not isinstance(head_commit, Mapping):
        raise AzureDevOpsPRObservationError("PR payload missing lastMergeSourceCommit")
    if not isinstance(base_commit, Mapping):
        raise AzureDevOpsPRObservationError("PR payload missing lastMergeTargetCommit")
    head_sha = head_commit.get("commitId")
    base_sha = base_commit.get("commitId")
    if not isinstance(head_sha, str) or not head_sha:
        raise AzureDevOpsPRObservationError("PR payload missing lastMergeSourceCommit.commitId")
    if not isinstance(base_sha, str) or not base_sha:
        raise AzureDevOpsPRObservationError("PR payload missing lastMergeTargetCommit.commitId")
    raw_reviewers = reviewers
    if raw_reviewers is None:
        value = pull_request.get("reviewers")
        raw_reviewers = list(value) if isinstance(value, list) else []
    raw_threads = threads
    if raw_threads is None:
        value = pull_request.get("threads")
        raw_threads = list(value) if isinstance(value, list) else []
    return PRObservation(
        number=number,
        approval_status=_approval_status(pull_request, list(raw_reviewers)),
        mergeability=_mergeability(pull_request),
        holds=_holds(pull_request, list(raw_threads)),
        revision=Revision(diff_hash=head_sha, base_sha=base_sha),
        last_commit_at=_last_commit_at(pull_request),
    )


class AzureDevOpsPRAdapter:
    """Read-only Azure DevOps PR-state adapter implemented through ``az``."""

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

    @staticmethod
    def _org_url(organization: str) -> str:
        return f"https://dev.azure.com/{organization}"

    def _az(self, *args: str) -> Any:
        az_executable = shutil.which("az") or "az"
        completed = self.runner(
            [az_executable, *args, "--output", "json"],
            check=False,
            capture_output=True,
            text=True,
        )
        if int(completed.returncode) != 0:
            raise RuntimeError(
                f"Azure DevOps operation failed: {str(completed.stderr or '').strip()}"
            )
        return completed

    def _rest_get(self, url: str) -> Any:
        return self._az(
            "rest",
            "--method",
            "GET",
            "--url",
            url,
            "--resource",
            _AZURE_DEVOPS_RESOURCE,
        )

    def _verify_identity(self, repo: str) -> None:
        if repo in self._verified_repos:
            return
        organization, project, repository = _split(repo)
        org_url = self._org_url(organization)
        connection = self._rest_get(
            f"{org_url}/_apis/connectionData?api-version=7.1-preview.1"
        )
        data = json.loads(connection.stdout or "{}")
        display_name = str(
            ((data.get("authenticatedUser") or {}).get("providerDisplayName")) or ""
        ).strip()
        if display_name.casefold() != self.expected_login.casefold():
            raise RuntimeError(
                "Azure DevOps adapter identity mismatch: expected "
                f"{self.expected_login!r}, got {display_name!r}"
            )
        project_resp = self._az(
            "devops",
            "project",
            "show",
            "--project",
            project,
            "--organization",
            org_url,
        )
        project_name = str(json.loads(project_resp.stdout or "{}").get("name") or "").strip()
        if project_name.casefold() != project.casefold():
            raise RuntimeError(
                "Azure DevOps project identity mismatch: expected "
                f"{project!r}, got {project_name!r}"
            )
        repo_resp = self._rest_get(
            f"{org_url}/{quote(project, safe='')}/_apis/git/repositories/"
            f"{quote(repository, safe='')}?api-version=7.1"
        )
        repo_data = json.loads(repo_resp.stdout or "{}")
        repo_name = str(repo_data.get("name") or "").strip()
        if repo_name.casefold() != repository.casefold():
            raise RuntimeError(
                "Azure DevOps repository identity mismatch: expected "
                f"{repository!r}, got {repo_name!r}"
            )
        self._verified_repos.add(repo)

    def fetch_pr(self, repo: str, number: int) -> dict[str, Any]:
        """Fetch the raw PR payload for ``repo``/``number``."""
        self._verify_identity(repo)
        organization, project, repository = _split(repo)
        org_url = self._org_url(organization)
        pr_resp = self._rest_get(
            f"{org_url}/{quote(project, safe='')}/_apis/git/repositories/"
            f"{quote(repository, safe='')}/pullRequests/{number}?api-version=7.1"
        )
        pull_request = json.loads(pr_resp.stdout or "{}")
        if not isinstance(pull_request, Mapping) or not pull_request:
            raise RuntimeError(f"Azure DevOps PR fetch returned no pull request for {repo}#{number}")
        returned_repo = pull_request.get("repository")
        returned_repo_name = str(
            returned_repo.get("name") if isinstance(returned_repo, Mapping) else ""
        ).strip()
        returned_project_name = str(
            ((returned_repo.get("project") or {}).get("name"))
            if isinstance(returned_repo, Mapping)
            else ""
        ).strip()
        if returned_repo_name.casefold() != repository.casefold():
            raise RuntimeError(
                f"Azure DevOps repository mismatch for {repo}#{number}: "
                f"got repository {returned_repo_name!r}"
            )
        if returned_project_name.casefold() != project.casefold():
            raise RuntimeError(
                f"Azure DevOps project mismatch for {repo}#{number}: "
                f"got project {returned_project_name!r}"
            )
        repository_id = str(
            returned_repo.get("id") if isinstance(returned_repo, Mapping) else ""
        ).strip()
        if not repository_id:
            raise RuntimeError("Azure DevOps PR fetch returned no repository id")
        reviewers_resp = self._rest_get(
            f"{org_url}/{quote(project, safe='')}/_apis/git/repositories/"
            f"{quote(repository_id, safe='')}/pullRequests/{number}/reviewers?api-version=7.1"
        )
        reviewers = json.loads(reviewers_resp.stdout or "{}").get("value")
        if not isinstance(reviewers, list):
            raise RuntimeError("Azure DevOps reviewers fetch returned no list")
        threads_resp = self._rest_get(
            f"{org_url}/{quote(project, safe='')}/_apis/git/repositories/"
            f"{quote(repository_id, safe='')}/pullRequests/{number}/threads?api-version=7.1"
        )
        threads = json.loads(threads_resp.stdout or "{}").get("value")
        if not isinstance(threads, list):
            raise RuntimeError("Azure DevOps threads fetch returned no list")
        commit_id = str(
            ((pull_request.get("lastMergeSourceCommit") or {}).get("commitId")) or ""
        ).strip()
        commit_detail = None
        if commit_id:
            commit_resp = self._rest_get(
                f"{org_url}/{quote(project, safe='')}/_apis/git/repositories/"
                f"{quote(repository_id, safe='')}/commits/{quote(commit_id, safe='')}"
                "?api-version=7.1"
            )
            commit_detail = json.loads(commit_resp.stdout or "{}")
        return {
            **dict(pull_request),
            "reviewers": reviewers,
            "threads": threads,
            "lastMergeSourceCommitDetail": commit_detail,
            "policyEvaluations": self._policy_evaluations(org_url, project, pull_request),
        }

    def observe(self, repo: str, number: int) -> PRObservation:
        """Fetch and classify ``repo``/``number`` in one call."""
        return observe_pr_state(self.fetch_pr(repo, number))

    def _policy_evaluations(
        self, org_url: str, project: str, pull_request: Mapping[str, Any]
    ) -> list[dict[str, Any]]:
        repository = pull_request.get("repository") or {}
        project_id = (
            str((repository.get("project") or {}).get("id") or "")
            if isinstance(repository, Mapping)
            else ""
        ).strip()
        number = pull_request.get("pullRequestId")
        artifact_id = (
            f"vstfs:///CodeReview/CodeReviewId/{project_id}/{number}"
            if project_id and isinstance(number, int)
            else str(pull_request.get("artifactId") or "").strip()
        )
        if not artifact_id:
            return []
        policies_resp = self._rest_get(
            f"{org_url}/{quote(project, safe='')}/_apis/policy/evaluations"
            f"?artifactId={quote(artifact_id, safe='')}"
            "&includeNotApplicable=true&api-version=7.1-preview.1"
        )
        data = json.loads(policies_resp.stdout or "[]")
        if isinstance(data, list):
            return [dict(item) for item in data if isinstance(item, Mapping)]
        value = data.get("value") if isinstance(data, Mapping) else None
        if isinstance(value, list):
            return [dict(item) for item in value if isinstance(item, Mapping)]
        raise RuntimeError("Azure DevOps policy evaluations fetch returned no list")
