"""Source-bound procedural confirmation and private audit for PR recovery."""

from __future__ import annotations

import hashlib
import getpass
import json
import os
import re
import uuid
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from . import git_ops, output, tracking

_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_FINGERPRINT = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class RecoveryPreview:
    provider: str
    repo: str
    number: int
    branch: str
    destination_fingerprint: str
    source_head: str
    expected_head: str
    overrides: tuple[str, ...] = ()
    saved_head: str = ""
    publication_fingerprint: str = ""
    action: Literal["rewrite", "settle-publication"] = "rewrite"
    evidence_fingerprint: str = ""
    evidence_status: Literal["none", "backed-sync", "source-lineage", "unusable"] = "none"
    rebase_base_sha: str = ""

    def __post_init__(self) -> None:
        if any(not value.strip() for value in (self.provider, self.repo, self.branch)):
            raise ValueError("Recovery requires an explicit provider, repository and PR head.")
        if "://" in self.repo:
            raise ValueError("Recovery repository must be an identity, not a credential-bearing URL.")
        if isinstance(self.number, bool) or not isinstance(self.number, int) or self.number <= 0:
            raise ValueError("Recovery requires a positive existing PR number.")
        if not _FINGERPRINT.fullmatch(self.destination_fingerprint):
            raise ValueError("Recovery requires a full destination fingerprint.")
        if not all(_COMMIT.fullmatch(value) for value in (self.source_head, self.expected_head)):
            raise ValueError("Recovery requires full source and expected-head commit IDs.")
        if self.saved_head and not _COMMIT.fullmatch(self.saved_head):
            raise ValueError("Recovery requires a full original saved-head commit ID.")
        for fingerprint in (self.publication_fingerprint, self.evidence_fingerprint):
            if fingerprint and not _FINGERPRINT.fullmatch(fingerprint):
                raise ValueError("Recovery requires full publication and evidence fingerprints.")
        if self.action not in ("rewrite", "settle-publication"):
            raise ValueError("Unknown recovery action.")
        if self.evidence_status not in ("none", "backed-sync", "source-lineage", "unusable"):
            raise ValueError("Unknown recovery evidence status.")
        if self.action == "settle-publication" and not self.evidence_fingerprint:
            raise ValueError("Settlement requires independently validated publication evidence.")

    def confirmation(self, reason: str) -> str:
        payload = {"operation": asdict(self), "reason": _reason(reason)}
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class InvocationAttribution:
    user: str | None = None
    session_id: str | None = None
    worktree_id: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "provenance": "observed" if any(asdict(self).values()) else "unknown",
            "authenticated_identity": False,
        }


def invocation_attribution() -> InvocationAttribution:
    """Observe the caller context, never substitute the publication target."""
    try:
        user = getpass.getuser()
    except (OSError, KeyError) as exc:
        output.warn(f"Recovery invoker user is unavailable; attribution remains unknown: {exc}")
        user = None
    result = git_ops.git(
        "rev-parse", "--absolute-git-dir", cwd=str(Path.cwd()), check=False,
        isolated_repository=True,
    )
    directory = Path(result.stdout.strip()) if result.returncode == 0 else None
    worktree_id = directory.name if directory is not None and directory.parent.name == "worktrees" else None
    return InvocationAttribution(
        user=user,
        session_id=os.environ.get("COPILOT_AGENT_SESSION_ID") or None,
        worktree_id=worktree_id,
    )


AUTO_BOUND_BASIS = (
    "line-of-business author recovery from the owning worktree; exact-lease bound"
)
EXPLICIT_BASIS = (
    "line-of-business author recovery from the owning worktree; "
    "caller-pinned preview confirmation; exact-lease bound"
)


@dataclass(frozen=True)
class ConfirmedRecovery:
    operation: RecoveryPreview
    reason: str
    confirmation: str
    binding: Literal["auto-bound", "explicit"] = "explicit"

    @property
    def authorization_basis(self) -> str:
        return AUTO_BOUND_BASIS if self.binding == "auto-bound" else EXPLICIT_BASIS

    def preview(self, remote: str) -> dict[str, object]:
        """Structured dry-run preview: the bound operation and its pin."""
        return {
            "remote": remote,
            "operation": asdict(self.operation),
            "reason": self.reason,
            "confirmation": self.confirmation,
            "confirmation_binding": self.binding,
            "authorization_basis": self.authorization_basis,
        }


# Set by a recovery dry-run so a JSON caller can return the structured preview.
last_preview: ContextVar[dict[str, object] | None] = ContextVar("recovery_preview", default=None)


class ConfirmationRequired(ValueError):
    """A caller-pinned preview no longer matches the current recovery operation."""

    def __init__(self, operation: RecoveryPreview, reason: str, *, changed: bool = True) -> None:
        message = (
            "Recovery confirmation no longer matches the proposed operation; the preview changed. "
            "Re-preview with --dry-run and retry, or omit --confirm-break-glass to bind the "
            "current preview."
        )
        super().__init__(message)
        self.preview: dict[str, object] = {
            "confirmation_required": True,
            "preview_changed": changed,
            "operation": asdict(operation),
            "reason": reason,
            "confirmation": operation.confirmation(reason),
            "error": message,
        }


def _reason(reason: str) -> str:
    value = reason.strip()
    if not value:
        raise ValueError("A non-empty recovery reason is required.")
    return value


def confirm(
    operation: RecoveryPreview, reason: str, confirmation: str | None,
) -> ConfirmedRecovery:
    """Bind the exact preview for the owning author's recovery.

    Recovery of an agent's own existing PR is line-of-business: an omitted
    confirmation binds the freshly computed preview automatically. A
    caller-pinned confirmation must still match, so a stale preview never
    authorizes a different operation.
    """
    reason = _reason(reason)
    expected = operation.confirmation(reason)
    if confirmation is None:
        return ConfirmedRecovery(operation, reason, expected, binding="auto-bound")
    if confirmation != expected:
        raise ConfirmationRequired(operation, reason)
    return ConfirmedRecovery(operation, reason, expected)


def publish_recovery(
    worktree_id: str, config, record, *, reason: str | None,
    confirmation: str | None, title: str | None, dry_run: bool, allow_unsquashed: bool,
) -> bool | None:
    """Route an explicit recovery before ordinary publication can mutate state."""
    if confirmation is not None and reason is None:
        output.err("--confirm-break-glass requires --break-glass-reason.")
        return False
    if reason is None:
        return None
    last_preview.set(None)
    if title or allow_unsquashed:
        output.err("Recovery publishes the confirmed committed tip as-is; title/squash options are inapplicable.")
        return False
    from . import pr_rewrite

    return pr_rewrite.push_changes(
        worktree_id, config, record, dry_run=dry_run,
        recovery_reason=reason, confirmation=confirmation,
    )


AuditOutcome = Literal["attempted", "succeeded", "failed", "unknown"]


@dataclass
class RecoveryAudit:
    path: Path
    approved: ConfirmedRecovery
    invoker: InvocationAttribution
    started_at: str
    outcome: AuditOutcome = "attempted"
    finished_at: str | None = None
    source_backup_ref: str | None = None
    published_backup_ref: str | None = None
    saved_publication: dict[str, object] | None = None

    def _write(self) -> None:
        payload = {
            "schema": "agent-worktrees.pr-break-glass",
            "version": 1,
            "operation": asdict(self.approved.operation),
            "saved_publication": self.saved_publication,
            "reason": self.approved.reason,
            "confirmation": self.approved.confirmation,
            "authorization_basis": self.approved.authorization_basis,
            "confirmation_binding": self.approved.binding,
            "invoker": self.invoker.to_dict(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "outcome": self.outcome,
            "recovery_refs": {
                "source": self.source_backup_ref,
                "published": self.published_backup_ref,
            },
        }
        tracking._atomic_write(self.path, json.dumps(payload, sort_keys=True) + "\n")

    def retained(self, source_ref: str, published_ref: str) -> None:
        if self.outcome != "attempted" or not all(
            ref.startswith("refs/agent-worktrees/recovery/") for ref in (source_ref, published_ref)
        ):
            raise ValueError("Recovery audit requires retained, worktree-scoped commit refs.")
        self.source_backup_ref = source_ref
        self.published_backup_ref = published_ref
        self._write()

    def finish(self, outcome: Literal["succeeded", "failed", "unknown"]) -> RecoveryAudit:
        if outcome not in ("succeeded", "failed", "unknown"):
            raise ValueError("Recovery audit completion must record succeeded, failed or unknown.")
        if self.outcome != "attempted":
            raise ValueError("A completed recovery audit cannot be completed again.")
        completed = replace(
            self, outcome=outcome, finished_at=datetime.now(timezone.utc).isoformat(),
        )
        completed._write()
        self.outcome = completed.outcome
        self.finished_at = completed.finished_at
        return self


def begin_audit(
    git_dir: Path, approved: ConfirmedRecovery, invoker: InvocationAttribution,
    *, saved_publication: dict[str, object] | None = None,
) -> RecoveryAudit:
    """Persist an authorized attempt before publication; failures propagate."""
    if not git_dir.is_dir():
        raise ValueError("Recovery audit requires the selected worktree's Git metadata directory.")
    if approved.confirmation != approved.operation.confirmation(approved.reason):
        raise ValueError("Recovery audit cannot accept an unbound confirmation.")
    audit = RecoveryAudit(
        path=git_dir / f"agent-worktrees-pr-break-glass-{uuid.uuid4().hex}.json",
        approved=approved,
        invoker=invoker,
        started_at=datetime.now(timezone.utc).isoformat(),
        saved_publication=saved_publication,
    )
    audit._write()
    return audit
