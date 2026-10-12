"""Fresh proof for a previously attested custom PR head."""

from __future__ import annotations

from . import config as cfg
from . import git_ops, pr_authority, pr_publish, providers
from .providers.pr_source import same_repository


def saved_attestation(record, repo, pr, expected: str) -> bool:
    """Require complete historical publication identity before credential resolution."""
    return not (
        (pr.branch.startswith("feature/") and not any(
            suffix and (pr.branch.endswith("-" + suffix) or pr.branch == f"feature/{suffix}")
            for suffix in (git_ops.worktree_suffix(record.worktree_id), record.codename)
        ))
        or
        pr.rewrite_owner != f"{record.worktree_id}:{pr.branch}"
        or not pr.rewrite_identity or not pr.number or not pr.repo or not pr.url
        or pr.head_sha != expected or pr.state not in ("open", "active")
        or pr.provider != repo.pr.provider
    )


def verify_attested(record, repo, pr, remote: str, expected: str, *, cwd: str,
                    config=None, observed=None, remote_tip: str | None = None) -> bool:
    """Require exact saved publication ownership and live provider/remote agreement.

    Missing historical attestation is not replaced by set-pr strings or a
    guessed provider fallback; this is not an adoption or recovery path.
    """
    if not saved_attestation(record, repo, pr, expected):
        return False
    try:
        identity = pr_publish.push_identity(remote, cwd=cwd)
        if identity != pr.rewrite_identity or not identity:
            return False
        if pr.repo.lower() != (git_ops.remote_slug(repo.remote, cwd=cwd) or "").lower():
            return False
        pr_authority.assert_exclusive(
            config or cfg.load_config(project=record.repo), record, pr, identity=identity,
        )
        if observed is None:
            provider = providers.get_provider(pr.provider)
            observed = provider.get_pull(
                pr.repo, int(pr.number), api_base=repo.pr.api_base,
                token=providers.account_token_for_slug(pr.repo, repo.pr),
            )
        if remote_tip is None:
            remote_tip = pr_publish._tip(remote, pr.branch, cwd)
        return (
            observed.number == pr.number and observed.state.lower() == "open"
            and observed.url.rstrip("/") == pr.url.rstrip("/")
            and not observed.merged and observed.head_sha == expected
            and observed.head_ref == pr.branch and bool(observed.head_url)
            and same_repository(pr_publish._identity(observed.head_url, cwd), identity, pr.provider)
            and remote_tip == expected
        )
    except (OSError, ValueError, RuntimeError):
        return False
