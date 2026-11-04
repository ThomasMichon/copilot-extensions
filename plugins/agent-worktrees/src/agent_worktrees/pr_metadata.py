"""Metadata-only author operations on an explicitly resolved existing PR."""
from __future__ import annotations

from dataclasses import dataclass, field

from .providers.base import PullResult, ProviderError


@dataclass
class MetadataPullResult(PullResult):
    """A created PR whose independently verified metadata may need repair."""

    metadata: dict = field(default_factory=dict)


def include_publication_metadata(result: dict, pull) -> None:
    if getattr(pull, "label_error", ""):
        result["pr_label_error"] = pull.label_error
    if getattr(pull, "metadata", None):
        result["author_metadata"] = pull.metadata


def update_metadata(config, repo: str, number: int, *, title: str | None = None,
                    body: str | None = None, reviewers=(), work_items=(),
                    publication_origin=None, publication_labels=()) -> dict:
    """Apply explicitly selected origin policy and update an existing PR.

    ``publication_origin`` is the same origin ID accepted by the existing
    publication-label operation. Work items accept ``project/id`` strings or
    positive organization-wide integer IDs; reviewers are identity GUIDs.
    """
    from . import pr_publication_labels as labels

    config = labels.activate(config, publication_origin, publication_labels)
    return update(config, repo, number, title=title, description=body,
                  reviewers=reviewers, work_items=work_items,
                  origin=labels.selected(config.default_repo.pr))


def update(config, repo: str, number: int, *, title: str | None = None,
           description: str | None = None, reviewers=(), work_items=(),
           origin=None) -> dict:
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
    if not resolution.resolved:
        raise ValueError(resolution.error or "Metadata target needs a registered PR binding")
    prcfg = resolution.repo_config.pr
    if not prcfg.enabled:
        raise ValueError("PR mode is not enabled for the selected metadata target")
    if origin is not None:
        pr_publication_labels.validate_policy(prcfg, origin)
    provider = providers.get_provider(prcfg.provider)
    operation = getattr(provider, "update_pull_metadata", None)
    if operation is None:
        raise ProviderError(f"{prcfg.provider} does not support author metadata updates")
    token = providers.account_token_for_slug(repo, prcfg)
    return operation(repo, number, title=title, description=description,
                     reviewers=reviewers, work_items=work_items, origin=origin,
                     api_base=prcfg.api_base, token=token)
