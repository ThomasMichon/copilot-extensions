"""Explicit, additive publication-origin metadata, independent of merge consent."""
from __future__ import annotations

from dataclasses import dataclass, replace
from contextvars import ContextVar
import re
from urllib.parse import unquote

last_result: ContextVar[dict | None] = ContextVar("publication_label_result", default=None)

@dataclass(frozen=True)
class PublicationOrigin:
    id: str
    labels: tuple[str, ...]


def parse_origin(raw) -> PublicationOrigin | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != {"id", "labels"}:
        raise ValueError("publication_origin requires exactly id and labels")
    identity, labels = raw["id"], raw["labels"]
    if not isinstance(identity, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", identity):
        raise ValueError("publication_origin.id must be a portable identifier")
    if (not isinstance(labels, (list, tuple)) or not labels
            or any(not isinstance(label, str) or not label.strip()
                   or label != label.strip() or any(ord(c) < 32 for c in label)
                   for label in labels)):
        raise ValueError("publication_origin.labels must be nonempty label strings")
    return PublicationOrigin(identity, tuple(dict.fromkeys(labels)))


def origin_dict(origin: PublicationOrigin | None) -> dict | None:
    return {"id": origin.id, "labels": list(origin.labels)} if origin else None


def activate(config, identity: str | None, labels=()):
    """Select policy for this command only; exported configuration stays inert."""
    last_result.set(None)
    if identity is None:
        if labels:
            raise ValueError("--publication-label requires --publication-origin")
        if not config.default_repo.pr.publication_origin_active:
            return config
        prcfg = replace(config.default_repo.pr, publication_origin_active=False)
        return replace(config, repos={**config.repos, config.repo_name: replace(config.default_repo, pr=prcfg)})
    prcfg = config.default_repo.pr
    origin = parse_origin({"id": identity, "labels": list(labels)}) if labels else prcfg.publication_origin
    if origin is None or origin.id != identity:
        raise ValueError("No matching pr.publication_origin is configured")
    validate_policy(prcfg, origin)
    prcfg = replace(prcfg, publication_origin=origin, publication_origin_active=True)
    return replace(config, repos={**config.repos, config.repo_name: replace(config.default_repo, pr=prcfg)})


def selected(prcfg) -> PublicationOrigin | None:
    return prcfg.publication_origin if getattr(prcfg, "publication_origin_active", False) else None


def validate_policy(prcfg, origin):
    forbidden = {value.casefold() for value in
                 (prcfg.automerge_label, *prcfg.hold_labels, "auto-complete") if value}
    if forbidden.intersection(label.casefold() for label in origin.labels):
        raise ValueError("Publication-origin labels cannot be merge-consent or hold labels")


def reconcile(provider, repo: str, number: int | None, origin: PublicationOrigin | None,
              *, api_base: str = "", token=None) -> dict | None:
    if origin is None:
        return None
    result = {"origin": origin.id, "repo": repo, "number": number,
              "requested": list(origin.labels), "ok": False}
    try:
        from .providers.base import ProviderError

        if not repo or number is None:
            raise ProviderError("Publication labels need an existing PR identity")
        read = getattr(provider, "get_publication_labels", None)
        add = getattr(provider, "add_publication_labels", None)
        if read is None or add is None:
            raise ProviderError("Provider does not support publication labels")
        before = {label.casefold() for label in read(repo, number, api_base=api_base, token=token)}
        missing = tuple(label for label in origin.labels if label.casefold() not in before)
        if missing:
            add(repo, number, missing, api_base=api_base, token=token)
        after = {label.casefold() for label in read(repo, number, api_base=api_base, token=token)} if missing else before
        absent = [label for label in origin.labels if label.casefold() not in after]
        if absent:
            raise ProviderError("Publication-label read-back is missing: " + ", ".join(absent))
        result.update(ok=True, added=list(missing))
    except (ProviderError, OSError, ValueError, TypeError) as exc:
        result.update(error=str(exc), repair="pr-publication-labels", publication_retained=True)
    return result


def validate_scope(prcfg, target) -> None:
    desired = selected(prcfg)
    if desired and target and target.publication_origin and parse_origin(target.publication_origin) != desired:
        raise ValueError("Publication origin is already frozen with a different policy")


def refresh(config, record, target) -> dict | None:
    from .providers.base import ProviderError

    try:
        result = _refresh(config, record, target)
    except (ProviderError, OSError, ValueError, TypeError) as exc:
        result = {"ok": False, "repo": target.repo if target else "",
                  "number": target.number if target else None, "error": str(exc),
                  "publication_retained": True, "repair": "pr-publication-labels"}
    if result is not None:
        last_result.set(result)
    return result


def _refresh(config, record, target) -> dict | None:
    """Attach selected scope after publication, retaining identity on metadata failure."""
    from . import claims_find_cli, pr_config, pr_publish, pr_sweep, providers, tracking
    from .pr_publication_state import authority, copy_pr

    if target is None:
        return None
    desired = selected(config.default_repo.pr)
    frozen = parse_origin(target.publication_origin) if target.publication_origin else None
    if desired and frozen and desired != frozen:
        return {"ok": False, "repo": target.repo, "number": target.number,
                "error": "Publication origin is already frozen with a different policy",
                "publication_retained": True, "repair": "pr-publication-labels"}
    origin = frozen or desired
    if origin is None:
        return None
    resolution = pr_config.resolve_repo_config_for_slug(config, target.repo)
    if not resolution.resolved:
        raise ValueError(resolution.error or "Tracked PR has no owning repository binding")
    prcfg = resolution.repo_config.pr
    if target.provider and target.provider != prcfg.provider:
        raise ValueError("Tracked PR provider differs from the current binding; resolve its owning binding before repair")
    validate_policy(prcfg, origin)
    provider = providers.get_provider(target.provider or prcfg.provider)
    if target.number is not None:
        endpoint = claims_find_cli.canonical_authority(provider.authority_endpoint(prcfg.api_base))
        identity = pr_sweep.pr_identity(target.url)
        if identity and prcfg.provider == "azure-devops":
            identity = (identity[0], unquote(identity[1], errors="strict").lower(), identity[2])
        if not endpoint or identity != (endpoint, target.repo.lower(), target.number):
            raise ValueError("Tracked PR identity differs from its owning binding endpoint; repair its binding before labeling")
    if record is not None:
        with pr_publish.metadata_lock(record.worktree_id, project=config.repo_name), tracking._RecordLock(
                record.yaml_path, require_sidecar=True):
            fresh = tracking.load_record(record.yaml_path)
            current = pr_publish._publication_pr(fresh, target)
            if current is None or authority(current) != authority(target):
                return {"ok": False, "repo": target.repo, "number": target.number,
                        "error": "PR authority changed before publication-label attachment",
                        "publication_retained": True, "repair": "pr-publication-labels"}
            current.publication_origin = origin_dict(origin)
            tracking.save_record(fresh)
            copy_pr(target, current)
    else:
        target.publication_origin = origin_dict(origin)
    if target.number is None:
        return None
    try:
        token = providers.account_token_for_slug(target.repo, prcfg)
        result = reconcile(provider, target.repo, target.number, origin,
                           api_base=prcfg.api_base, token=token)
    except (providers.ProviderError, OSError, ValueError) as exc:
        result = {"ok": False, "repo": target.repo, "number": target.number,
                  "origin": origin.id, "error": str(exc), "publication_retained": True,
                  "repair": "pr-publication-labels"}
    target.publication_labels = result or {}
    if record is not None:
        with pr_publish.metadata_lock(record.worktree_id, project=config.repo_name), tracking._RecordLock(
                record.yaml_path, require_sidecar=True):
            fresh = tracking.load_record(record.yaml_path)
            current = pr_publish._publication_pr(fresh, target)
            if current is not None and authority(current) == authority(target):
                current.publication_labels = target.publication_labels
                tracking.save_record(fresh)
    return result
