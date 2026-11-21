"""Metadata-only author operations on an explicitly resolved existing PR."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from .config import Config
from .pr_publication_labels import PublicationOrigin
from .providers.base import PullResult, ProviderError

if TYPE_CHECKING:
    from .tracking import PRRecord


def pr_to_dict(pr: PRRecord) -> dict[str, Any]:
    from . import pr_creation_reconcile
    return {
        "pr_id": pr.pr_id, "pr_revision": pr.pr_revision, "creation": dict(pr.creation),
        **({"local_disposition": "retired", "publication_outcome": "unknown"}
           if pr_creation_reconcile.retired(pr) else {}),
        "state": pr.state, "branch": pr.branch, "base_sha": pr.base_sha,
        "head_sha": pr.head_sha, "patch_id": pr.patch_id, "url": pr.url,
        "number": pr.number, "provider": pr.provider, "repo": pr.repo,
        "opened_at": pr.opened_at, "closed_at": pr.closed_at,
    }


@dataclass
class MetadataPullResult(PullResult):
    """A created PR whose independently verified metadata may need repair."""

    metadata: dict[str, Any] = field(default_factory=dict)


def draft_confirmation(observed: object) -> dict[str, Any] | None:
    if observed is True:
        return None
    return {
        "status": "failed" if observed is False else "unknown",
        "error": "PR did not confirm the requested native draft state; reconcile this PR before readiness.",
    }


def include_publication_metadata(result: dict[str, Any], pull: PullResult | None, *,
                                 requested_draft: bool = False,
                                 require_draft_observation: bool = False) -> None:
    observed_draft = getattr(pull, "draft", None)
    if type(observed_draft) is bool:
        result["draft"] = observed_draft
    label_error = getattr(pull, "label_error", "")
    if label_error:
        result["pr_label_error"] = label_error
    metadata = getattr(pull, "metadata", None) or {}
    if requested_draft and require_draft_observation:
        action = draft_confirmation(getattr(pull, "draft", None))
        if action:
            metadata = {
                **metadata, "ok": False,
                "actions": {**metadata.get("actions", {}), "draft": action},
            }
    if metadata:
        result["author_metadata"] = metadata
        if metadata.get("actions", {}).get("draft", {}).get("status") == "unknown":
            result["draft"] = False
            result["draft_unconfirmed"] = True


def update_metadata(config: Config, repo: str, number: int, *, title: str | None = None,
                    body: str | None = None, reviewers: tuple[str, ...] = (),
                    work_items: tuple[str | int, ...] = (),
                    publication_origin: str | None = None,
                    publication_labels: tuple[str, ...] = ()) -> dict[str, Any]:
    """Apply explicitly selected origin policy and update an existing PR.

    ``publication_origin`` is the same origin ID accepted by the existing
    publication-label operation. Work items accept ``project/id`` strings or
    positive organization-wide integer IDs; reviewers are identity GUIDs.
    """
    from . import pr_publication_labels as labels

    config = labels.activate(config, publication_origin, publication_labels)
    return update(config, repo, number, title=title, description=body,
                  reviewers=reviewers, work_items=work_items,
                  origin=labels.selected_for_config(config))


def update(config: Config, repo: str, number: int, *, title: str | None = None,
           description: str | None = None, reviewers: tuple[str, ...] = (),
           work_items: tuple[str | int, ...] = (),
           origin: PublicationOrigin | None = None) -> dict[str, Any]:
    """Resolve target policy/auth and update metadata without pushing or creating.

    Reviewers are explicit ADO identity GUIDs; work items are organization-wide
    positive integer IDs or ``project/id`` strings. ``None`` leaves text unchanged; an empty description
    deliberately clears it. Publication origin is an explicitly selected
    ``PublicationOrigin``, not implicit merge consent.
    """
    from . import pr_config, pr_publication_labels, providers

    if not repo or type(number) is not int or number < 1:
        raise ValueError("Metadata update requires --repo and a positive --number")
    resolution = pr_config.resolve_repo_config_for_slug(config, repo)
    if not resolution.resolved or resolution.repo_config is None:
        raise ValueError(resolution.error or "Metadata target needs a registered PR binding")
    prcfg = resolution.repo_config.pr
    if not prcfg.enabled:
        raise ValueError("PR mode is not enabled for the selected metadata target")
    if origin is not None:
        pr_publication_labels.validate_policy(prcfg, origin)
    provider = providers.get_provider(prcfg.provider)
    operation = getattr(provider, "update_pull_metadata", None)
    if prcfg.provider == "gitea" and operation is None:
        from .providers import gitea_metadata
        token = providers.account_token_for_slug(repo, prcfg)
        return gitea_metadata.update(
            provider, repo, number, title=title, description=description,
            reviewers=reviewers, work_items=work_items, origin=origin,
            api_base=prcfg.api_base, token=token,
            wip_title_prefixes=prcfg.wip_title_prefixes,
        )
    if prcfg.provider == "github" and operation is None:
        from .providers import github_metadata
        token = providers.account_token_for_slug(repo, prcfg)
        return github_metadata.update(
            provider, repo, number, title=title, description=description,
            reviewers=reviewers, work_items=work_items, origin=origin,
            api_base=prcfg.api_base, token=token, run=providers.run_cli,
        )
    if operation is None:
        raise ProviderError(f"{prcfg.provider} does not support author metadata updates")
    token = providers.account_token_for_slug(repo, prcfg)
    result: dict[str, Any] = operation(
        repo, number, title=title, description=description,
        reviewers=reviewers, work_items=work_items, origin=origin,
        api_base=prcfg.api_base, token=token,
    )
    return result
