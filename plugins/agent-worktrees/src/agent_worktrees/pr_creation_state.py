"""Versioned evidence for a local attempt that has not entered publication."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

from . import git_ops, pr_publication_state, pr_publish, providers, tracking

CONTRACT = "agent-worktrees.pr-creation-cancellation.v1"
_RESERVATION_KEYS = {"contract", "phase", "owner", "remote", "identity", "api_base", "binding"}
_CANCELLATION_KEYS = {"reason", "at", "actor", "revision"}
_PUBLICATION_KEYS = {"push_head", "push_source", "push_base", "push_binding"}


def parse(raw: object) -> dict[str, str]:
    """A lossy legacy identity parse must never preserve cancellation proof."""
    if not isinstance(raw, dict):
        return {}
    revision, number = raw.get("pr_revision", 0), raw.get("number")
    if type(revision) is not int or not 0 <= revision <= tracking.MAX_PERSISTED_COUNTER:
        return {}
    if number is not None and (type(number) is not int or number <= 0):
        return {}
    receipt = raw.get("creation")
    if not isinstance(receipt, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in receipt.items()
    ):
        return {}
    return dict(receipt)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode("utf-8")).hexdigest()


def owner(record: tracking.WorktreeRecord) -> str:
    return _digest([
        record.repo, record.worktree_id, record.worktree_path, record.branch,
        record.machine, getattr(record, "creation_nonce", ""),
    ])


def _binding(pr: tracking.PRRecord) -> str:
    return _digest([
        pr.pr_id, pr.branch, pr.provider, pr.repo,
        pr.attribution_mode, pr.attribution_explicit,
        *(pr.creation.get(k, "") for k in ("owner", "remote", "identity", "api_base")),
    ])


def reserve(record: tracking.WorktreeRecord, pr: tracking.PRRecord, remote: str, prcfg) -> None:
    """Stamp only a newly allocated attempt, before any publication is possible."""
    provider = providers.get_provider(pr.provider)
    pr.creation = {
        "contract": CONTRACT, "phase": "reserved", "owner": owner(record),
        "remote": remote,
        "identity": pr_publish.push_identity(remote, cwd=record.worktree_path),
        "api_base": provider.authority_endpoint(prcfg.api_base),
    }
    pr.creation["binding"] = _binding(pr)


def _bound(pr: tracking.PRRecord) -> bool:
    return bool(
        pr.pr_id and pr.branch and pr.provider and pr.repo
        and pr.attribution_mode in ("false", "true", "codename")
        and pr.creation.get("contract") == CONTRACT
        and all(pr.creation.get(k) for k in ("owner", "remote", "identity", "api_base"))
        and pr.creation.get("binding") == _binding(pr)
    )


def reserved(pr: tracking.PRRecord, record: tracking.WorktreeRecord) -> bool:
    return (
        set(pr.creation) == _RESERVATION_KEYS and _bound(pr)
        and pr.creation["owner"] == owner(record)
        and pr.creation["phase"] == "reserved"
        and pr_publication_state.unpublished(pr)
    )


def cancelled(pr: tracking.PRRecord) -> bool:
    """A local cancellation is never provider closure or evidence of merge."""
    if not getattr(pr, "creation", None):
        return False
    if not (
        set(pr.creation) == _RESERVATION_KEYS | _CANCELLATION_KEYS
        and _bound(pr) and pr.creation["phase"] == "cancelled"
        and pr.state == "abandoned" and pr.closed_at == pr.creation["at"]
        and all(pr.creation[k].strip() for k in ("reason", "actor", "at"))
        and pr_publication_state.unpublished(replace(pr, state="creating", closed_at=""))
    ):
        return False
    revision = pr.creation["revision"]
    return revision.isascii() and revision.isdecimal() and int(revision) < pr.pr_revision


def _publication_binding(pr: tracking.PRRecord) -> str:
    return _digest([
        pr.creation.get("binding", ""),
        *(pr.creation.get(k, "") for k in ("push_head", "push_source", "push_base")),
        *([pr.creation["push_backup"]] if "push_backup" in pr.creation else []),
    ])


def publication_source(
    record: tracking.WorktreeRecord | None, pr: tracking.PRRecord | None,
    remote: str, branch: str, cwd: str, prcfg,
) -> str:
    """Return only the unchanged source of a durably prepared first push."""
    if pr is None or pr.branch != branch or not pr_publication_state.unpublished(pr):
        return ""
    if not _PUBLICATION_KEYS.intersection(pr.creation):
        return ""
    if (
        record is None or set(pr.creation) not in (
            _RESERVATION_KEYS | _PUBLICATION_KEYS,
            _RESERVATION_KEYS | _PUBLICATION_KEYS | {"push_backup"},
        )
        or not _bound(pr) or pr.creation["phase"] != "publishing"
        or pr.creation["owner"] != owner(record)
        or pr.creation["remote"] != remote
        or pr.creation["identity"] != pr_publish.push_identity(remote, cwd=cwd)
        or prcfg is None or pr.provider != prcfg.provider
        or pr.creation["api_base"] != providers.get_provider(pr.provider).authority_endpoint(prcfg.api_base)
        or pr.creation["push_binding"] != _publication_binding(pr)
        or pr.creation["push_source"] not in (record.branch, pr.branch)
        or not pr.creation["push_head"]
        or len([p for p in record.prs if p.branch == branch and not tracking._pr_is_terminal(p)]) != 1
    ):
        raise ValueError("Prepared first-push authority changed; retain the attempt for explicit recovery.")
    pr_publication_state.require_current(record, pr)
    source = pr.creation["push_source"]
    for ref in (source, "HEAD"):
        observed = git_ops.git(
            "rev-parse", "--verify", f"{ref}^{{commit}}", cwd=cwd, check=False,
            isolated_repository=True,
        )
        if observed.returncode or observed.stdout.strip() != pr.creation["push_head"]:
            raise ValueError("Prepared first-push source changed; retain the attempt for explicit recovery.")
    return source


def start_publication(
    record: tracking.WorktreeRecord, pr: tracking.PRRecord | None, *,
    remote: str = "", source_ref: str = "", head_sha: str = "", base_sha: str = "",
    backup_sha: str = "",
) -> None:
    """Invalidate cancellation and retain first-push intent before transport."""
    if pr is None:
        return
    path = record.yaml_path
    with pr_publish.metadata_lock(record.worktree_id, project=record.repo), tracking._RecordLock(
        path, require_sidecar=True,
    ):
        fresh = tracking.load_record(path)
        current = pr_publish._publication_pr(fresh, pr)
        if owner(fresh) != owner(record) or not pr_publication_state.matches(current, pr):
            raise ValueError("PR creation authority changed before publication; reload the attempt.")
        if current.creation and current.creation.get("phase") != "publishing":
            if _bound(current) and current.creation["owner"] == owner(fresh):
                if remote != current.creation["remote"] or current.creation["identity"] != pr_publish.push_identity(
                    remote, cwd=record.worktree_path,
                ):
                    raise ValueError("PR creation destination changed before publication.")
                if head_sha and source_ref in (record.branch, pr.branch):
                    current.creation = {
                        **current.creation, "push_head": head_sha,
                        "push_source": source_ref, "push_base": base_sha,
                    }
                    if backup_sha:
                        current.creation["push_backup"] = backup_sha
                    current.creation["push_binding"] = _publication_binding(current)
            current.creation = {**current.creation, "phase": "publishing"}
            current.pr_revision += 1
            tracking.save_record(fresh)
        pr_publication_state.copy_pr(pr, current)


def release_backup(expected: str, *, cwd: str) -> None:
    """Release only the recorded backup of a successfully published squash."""
    if not expected:
        return
    result = git_ops.git(
        "update-ref", "-d", "refs/pre-squash-backup", expected, cwd=cwd,
        check=False, isolated_repository=True,
    )
    if result.returncode:
        from . import output

        output.warn("Publication succeeded; the changed or unconfirmed pre-squash backup was retained.")
