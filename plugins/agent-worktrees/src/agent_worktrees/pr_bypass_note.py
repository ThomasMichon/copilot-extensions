"""Bounded self-merge exception guidance."""

from __future__ import annotations


def self_merge_bypass_note(
    flow, provider, repo: str, number: int | None, *,
    api_base: str = "", token: str | None = None,
    expected_head_sha: str = "", hold_labels: tuple[str, ...] = (),
    wip_title_prefixes: tuple[str, ...] = (),
) -> str | None:
    from . import pr_contract as pc

    if flow.profile != pc.PROFILE_PR_SELF_MERGE:
        return None
    if number is None or not hasattr(provider, "pull_review_exception"):
        return None
    if not expected_head_sha:
        try:
            expected_head_sha = provider.get_snapshot(
                repo, number, api_base=api_base, token=token,
                strict_safety=True,
            ).head_sha
        except Exception:
            return None
    try:
        authorized, unsafe, _ = provider.pull_review_exception(
            repo, number, expected_head_sha=expected_head_sha,
            hold_labels=hold_labels, wip_title_prefixes=wip_title_prefixes,
            api_base=api_base, token=token,
        )
    except Exception:
        return None
    if not authorized or unsafe:
        return None
    return (
        "This identity holds repository-authorized Maintainer bypass rights "
        "for the provider-verified missing CODEOWNER approval. After the "
        "configured review and required CI pass, use `pr-merge <#> --now`; "
        "this does not override a change request, hold, conflict or failed check."
    )


__all__ = ["self_merge_bypass_note"]
