"""Where a PR-mode worktree's work landed: every base it may be on, and the
evidence for each.

A PR targets the branch the provider says it does, which need not be the repo's
configured default branch (a repo whose PRs land on ``dev`` and are promoted to
``main`` later). ``finalize``'s precondition validates the worktree's content
against both: the configured ``<remote>/<default>`` first, then the PR's own
base when it differs. Each check's verdicts are kept (:class:`~agent_worktrees.
finalize_ref.Landing`) so a refusal says what was tested, and
``finalize --explain-landing`` reports it without finalizing.

Split out of ``finalize.py`` (module-size guard).
"""

from __future__ import annotations

from . import git_ops
from .finalize_ref import Landing, creation_point, landing


def pr_base(record, repo) -> str:
    """``<remote>/<PR base>`` when the tracked PR targets a branch other than the
    configured default one (read from the provider); "" otherwise or unknown."""
    from . import finalize_open_pr_gate as fopg
    try:
        base = fopg.pr_base_ref(getattr(record, "pr", None), repo)
    except Exception:
        return ""
    return f"{repo.remote}/{base}" if base and base != repo.default_branch else ""


def describe(evidence: list[Landing]) -> str:
    return "Landing checks: " + "; ".join(e.describe() for e in evidence) if evidence else ""


def pr_content_landed(
    record, repo, content_ref: str | None, *, cwd: str,
) -> tuple[bool | None, str | None, list[Landing]]:
    """Steps (1) and (2) of ``finalize._pr_finalize_precondition`` on every base
    the PR's work may have landed on.

    1. **Fast path** -- *content_ref*'s content is on a base, gated on the
       tracked merged head (#4400, ``upstream_match_is_trustworthy``).
    2. **Authoritative** -- the PR merged, and *content_ref* carries no commits
       beyond its merged head that aren't on one of the bases.

    Returns ``(True, None, evidence)`` when landed; ``(False, message, evidence)``
    when the PR merged but its content can't be certified; ``(None, None,
    evidence)`` when the PR isn't (confirmed) merged -- the caller's later steps
    decide. The provider is asked for the PR's base only when the default
    branch doesn't already hold the work, and each tracked PR is read from it at
    most once per call."""
    from . import finalize_open_pr_gate as fopg
    with fopg.one_read_per_pr():
        return _pr_content_landed(record, repo, content_ref, cwd=cwd)


def _pr_content_landed(
    record, repo, content_ref: str | None, *, cwd: str,
) -> tuple[bool | None, str | None, list[Landing]]:
    from . import finalize_open_pr_gate as fopg
    evidence: list[Landing] = []

    def fast(upstream: str) -> bool:
        if content_ref is None or not upstream or not git_ops.ref_exists(upstream, cwd=cwd):
            return False
        result = landing(content_ref, upstream, cwd)
        evidence.append(result)
        return result.landed and fopg.upstream_match_is_trustworthy(
            record, content_ref, upstream, cwd=cwd, repo=repo)

    default = f"{repo.remote}/{repo.default_branch}"
    if fast(default):
        return True, None, evidence
    other = pr_base(record, repo)
    if fast(other):
        return True, None, evidence
    if fopg.pr_merge_status(record, repo) is not True:
        return None, None, evidence
    bases = [b for b in (default, other) if b and git_ops.ref_exists(b, cwd=cwd)] or [default]
    for upstream in bases:
        if not fopg.merged_content_exceeds(record, content_ref, upstream, cwd=cwd, repo=repo):
            return True, None, evidence
    message = fopg.merged_pr_block_message(record, content_ref, bases[-1], cwd=cwd)
    detail = describe(evidence)
    return False, f"{message}\n{detail}" if detail else message, evidence


def explain(record, repo, worktree_path: str, anchor: str) -> dict:
    """Read-only: what ``finalize`` would test to decide whether this PR-mode
    worktree's work landed, and every check's verdict -- nothing is fetched,
    finalized or recorded."""
    from . import finalize_open_pr_gate as fopg
    with fopg.one_read_per_pr():
        return _explain(record, repo, worktree_path, anchor)


def _explain(record, repo, worktree_path: str, anchor: str) -> dict:
    from pathlib import Path

    from . import finalize_open_pr_gate as fopg
    cwd = worktree_path if Path(worktree_path).exists() else anchor
    pr = getattr(record, "pr", None)
    content_ref = fopg.resolve_precondition_ref(
        getattr(pr, "branch", "") or "", record.worktree_id, worktree_path, cwd=cwd)
    default = f"{repo.remote}/{repo.default_branch}"
    bases = [b for b in (default, pr_base(record, repo)) if b]
    checks = [landing(content_ref, b, cwd, explain=True).to_dict()
              for b in bases if content_ref and git_ops.ref_exists(b, cwd=cwd)]
    ok, message, _ = pr_content_landed(record, repo, content_ref, cwd=cwd) if pr else (None, None, [])
    return {
        "worktree_id": record.worktree_id,
        "content_ref": content_ref,
        "created_from": creation_point(content_ref, cwd) if content_ref else "",
        "bases": bases,
        "missing_bases": [b for b in bases if not git_ops.ref_exists(b, cwd=cwd)],
        "checks": checks,
        "pr": {"number": getattr(pr, "number", None), "state": getattr(pr, "state", ""),
               "head_sha": getattr(pr, "head_sha", "")} if pr else None,
        "landed": ok,
        "message": message,
    }
