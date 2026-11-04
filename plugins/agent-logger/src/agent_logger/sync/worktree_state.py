"""Typed, revision-bound worktree exports and effect-side collection admission."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent_logger.aggregate import (
    ExecutionMode, OwnershipDimension, ResolvedPlan, ResourceBinding,
    canonical_repository_identity,
)
from agent_logger.config import Config
from agent_logger.aggregate_machine import resolve_machine_identity
from agent_logger.sync.targets import build_target
from agent_logger.sync.targets.base import Target

MAX_MANIFEST_BYTES = 16384
MAX_MEMBER_BYTES = 1 << 30
MAX_REFERENCES = 256


class WorktreeStateError(ValueError):
    """Collection is unsupported, unadmitted, incomplete or unverifiable."""


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True,
                       separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def decode_object(raw: bytes) -> dict[str, Any]:
    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise WorktreeStateError("duplicate collection metadata field")
            value[key] = item
        return value

    value = json.loads(raw, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise WorktreeStateError("collection metadata must be an object")
    return value


def _segment(value: str, label: str) -> None:
    if not isinstance(value, str) or re.fullmatch(
        r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}", value,
    ) is None:
        raise WorktreeStateError(f"invalid {label}")


@dataclass(frozen=True)
class WorktreeStateRevision:
    """Identity from the selected owner, never a path or execution grant."""

    repository: str
    worktree: str
    creation_nonce: str
    store_id: str
    revision: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "repository", canonical_repository_identity(self.repository))
        _segment(self.worktree, "full worktree identity")
        _segment(self.creation_nonce, "worktree incarnation")
        if not isinstance(self.store_id, str) or re.fullmatch("[0-9a-f]{32}", self.store_id) is None:
            raise WorktreeStateError("invalid agenda store identity")
        if type(self.revision) is not int or not 1 <= self.revision <= 2**63 - 1:
            raise WorktreeStateError("revision must name an accepted integer revision")

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository, "worktree": self.worktree,
            "creation_nonce": self.creation_nonce, "store_id": self.store_id,
            "revision": self.revision, "domain": "agendas",
        }

    @property
    def relative_directory(self) -> Path:
        identity = self.as_dict()
        identity.pop("revision")
        key = hashlib.sha256(canonical_bytes(identity)).hexdigest()
        return Path("worktree-state") / key / str(self.revision)


class ExportVerifier(Protocol):
    """An independently selected domain owner's complete readback boundary."""

    def __call__(
        self, directory: Path, *, worktree: str, store_id: str, expected_revision: int,
    ) -> dict[str, Any]: ...


@dataclass(frozen=True)
class WorktreeStateReceipt:
    identity: WorktreeStateRevision
    destination: str
    manifest_sha256: str
    member_sha256: str
    member_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "agent-logger.worktree-state-receipt", "version": 1,
            "identity": self.identity.as_dict(), "destination": self.destination,
            "manifest_sha256": self.manifest_sha256,
            "members": {"agenda.sqlite3": {
                "sha256": self.member_sha256, "bytes": self.member_bytes,
            }},
            "local_readback_verified": True,
            "remote_archive_acknowledged": False,
            "reclamation_authorized": False,
        }


def contributing_sessions(sessions: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(sessions, tuple) or not sessions or len(sessions) > MAX_REFERENCES:
        raise WorktreeStateError("collection requires a bounded nonempty session-reference set")
    for session in sessions:
        _segment(session, "contributing session identity")
    return tuple(sorted(set(sessions)))


def _native_root(value: Path) -> Path:
    path = value.expanduser().absolute()
    if ".." in path.parts:
        raise WorktreeStateError("collection destination cannot contain parent traversal")
    return path


def admit_collection(
    cfg: Config, target: Target, plan: ResolvedPlan, identity: WorktreeStateRevision,
) -> Path:
    """Resolve the actual target only after all supported policies admit effects."""
    if plan.mode != ExecutionMode.ENFORCE or not plan.authorized or plan.passive:
        raise WorktreeStateError("worktree collection requires a nonpassive authorized enforce plan")
    if plan.machine != resolve_machine_identity(cfg):
        raise WorktreeStateError("collection plan belongs to a different executing machine")
    if os.name != "posix":
        raise WorktreeStateError("typed worktree collection has no qualified native durability adapter")
    if cfg.sync_protection.enabled:
        raise WorktreeStateError("privacy policy withholds lossless worktree-state databases")
    if cfg.sync_repo_allowlist or cfg.sync_repo_denylist or cfg.sync_require_repo_opt_in:
        raise WorktreeStateError("legacy session-scope filters do not admit typed worktree collection")
    if cfg.sync_target != target.name:
        raise WorktreeStateError("collection target differs from the active sync target")
    root = target.worktree_state_root()
    if root is None:
        raise WorktreeStateError("active target does not support typed worktree-state collection")
    root = _native_root(root)
    configured = build_target(cfg.sync_target, cfg.target_options(cfg.sync_target))
    configured_root = configured.worktree_state_root()
    if configured_root is None or _native_root(configured_root) != root:
        raise WorktreeStateError("supplied destination differs from the current sync configuration")
    destination = ResourceBinding("filesystem", str(root)).canonical_identity
    matches = [
        claim for claim in plan.claims
        if claim.dimension == OwnershipDimension.COLLECTION
        and (
            identity.repository in claim.sources.repositories
            or claim.sources.wildcard and identity.repository not in claim.sources.exclusions
        )
    ]
    if len(matches) != 1:
        raise WorktreeStateError("source repository requires exactly one collection claim")
    claim = matches[0]
    if (
        not claim.resource.ready or claim.resource.kind != "filesystem"
        or claim.resource.canonical_identity != destination
        or not Path(claim.resource.identity.strip()).is_absolute()
        or _native_root(Path(claim.resource.identity.strip())) != root
    ):
        raise WorktreeStateError("admitted collection destination differs from the active target")
    if any(value is not None for value in (
        claim.profile, claim.landing, claim.retention, claim.mutation,
    )):
        raise WorktreeStateError("collection claim has unsupported content or mutation policy")
    return root
