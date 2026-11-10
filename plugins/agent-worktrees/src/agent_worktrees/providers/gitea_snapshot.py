"""Gitea snapshot acquisition under the provider's existing native read owner."""

import json

from ..pr_contract import PRSnapshot
from .base import ProviderError
from .observation_credentials import ObservationCredentials


def snapshot(owner, repo, number, *, api_base="", token=None, credential_source=None):
    if credential_source is not None:
        if token is not None:
            raise ProviderError("explicit observation credentials cannot select a legacy token")
        token = ObservationCredentials(
            credential_source, provider="gitea",
            api_base=owner.authority_endpoint(api_base), repository=repo,
        )
    if not token:
        raise ProviderError("Gitea provider needs a token to query a PR.")
    status, body = owner._curl(
        "GET", owner._api(api_base, f"/repos/{repo}/pulls/{number}"), token,
    )
    if status != 200:
        raise ProviderError(
            f"Gitea PR #{number} snapshot failed (HTTP {status}).",
            transient=status in (0, 408, 429) or status >= 500,
        )
    try:
        pr = json.loads(body)
    except json.JSONDecodeError as error:
        raise ProviderError("Gitea returned non-JSON PR payload") from error
    if not isinstance(pr, dict):
        raise ProviderError("unexpected Gitea PR payload")
    head, base = pr.get("head") or {}, pr.get("base") or {}
    labels = tuple(
        str(label["name"]) for label in pr.get("labels") or []
        if isinstance(label, dict) and label.get("name")
    )
    mergeable = pr.get("mergeable")
    return PRSnapshot(
        pr_state=str(pr.get("state", "")) or "open", endpoint=owner.authority_endpoint(api_base),
        merged=bool(pr.get("merged", False)), head_sha=str(head.get("sha", "")),
        base_ref=str(base.get("ref", "")), base_sha=str(base.get("sha") or ""),
        updated_at=str(pr.get("updated_at", "") or ""),
        reviews=owner._all_review_objs(repo, number, api_base, token),
        author=str((pr.get("user") or {}).get("login", "")),
        mergeable=mergeable if isinstance(mergeable, bool) else None,
        checks_state=owner._combined_status_state(repo, str(head.get("sha", "")), api_base, token),
        labels=labels, title=str(pr.get("title", "")), draft=bool(pr.get("draft", False)),
    )
