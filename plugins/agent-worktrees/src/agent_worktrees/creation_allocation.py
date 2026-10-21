"""Complete launch allocations retained by the existing admission writer."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from . import config as cfg, execution_spaces, tracking
from .creation_identity import IdentityMode, new_machine_identity
from .worktree_machine_identity import WorktreeMachineIdentity, parse_machine_identity
from .worktree_naming import prospective_session_worktree_id, session_worktree_id

_RELATIONSHIP_FIELDS = ("owner_ref", "pair_id", "pair_role", "pair_ref", "pair_kind")
_TUPLE_FIELDS = {"machine_identity_version", "environment", "execution_machine"}


def _receipt_identity(fields: dict) -> WorktreeMachineIdentity | None:
    present = set(fields) & _TUPLE_FIELDS
    if present and present != _TUPLE_FIELDS:
        raise ValueError("Incomplete tuple identity in creation receipt")
    return parse_machine_identity(fields)


def record_evidence(record: tracking.WorktreeRecord) -> dict:
    return {
        "machine": record.machine, "platform": record.platform,
        **(record.machine_identity.to_record_fields() if record.machine_identity else {}),
        **{field: getattr(record, field) for field in _RELATIONSHIP_FIELDS},
    }


def validate_record_evidence(fields: object) -> dict:
    required = {"machine", "platform", *_RELATIONSHIP_FIELDS}
    if (
        not isinstance(fields, dict) or not required.issubset(fields)
        or set(fields) - required - _TUPLE_FIELDS
        or any(not isinstance(fields[key], str) for key in ("machine", "platform"))
        or any(fields[key] is not None and not isinstance(fields[key], str) for key in _RELATIONSHIP_FIELDS)
    ):
        raise ValueError("Invalid retained creation record identity")
    _receipt_identity(fields)
    return fields


def require_record_evidence(record: tracking.WorktreeRecord, fields: object) -> None:
    if record_evidence(record) != validate_record_evidence(fields):
        raise ValueError("Stored allocation conflicts with its original owner or pair identity")


def require_result_identity(record: tracking.WorktreeRecord, result: dict) -> None:
    """Use original projection evidence even when an old receipt has no snapshot."""
    if any(key in result for key in ("machine", "platform", "machine_identity_version",
                                     "environment", "execution_machine")):
        if _receipt_identity(result) != record.machine_identity:
            raise ValueError("Stored allocation changed its original owner representation")
    current = {**record_evidence(record), "creation_nonce": record.creation_nonce}
    if any(key in result and result[key] != value for key, value in current.items()):
        raise ValueError("Stored allocation changed its original owner or pair identity")


def _canonical_path(value: str) -> str:
    return os.path.normcase(os.path.realpath(value))


@dataclass(frozen=True)
class AdmittedAllocation:
    project: str
    anchor: str
    worktree_root: str
    worktree_id: str
    branch: str
    path: str
    machine: str
    platform: str
    timestamp: str
    suffix: str
    machine_identity: WorktreeMachineIdentity | None = None

    def to_fields(self) -> dict:
        return {
            "version": 1, "project": self.project, "anchor": self.anchor,
            "worktree_root": self.worktree_root, "worktree_id": self.worktree_id,
            "branch": self.branch, "path": self.path, "machine": self.machine,
            "platform": self.platform, "timestamp": self.timestamp, "suffix": self.suffix,
            **(self.machine_identity.to_record_fields() if self.machine_identity else {}),
        }

    @classmethod
    def from_fields(cls, fields: object) -> AdmittedAllocation:
        required = {
            "version", "project", "anchor", "worktree_root", "worktree_id", "branch",
            "path", "machine", "platform", "timestamp", "suffix",
        }
        if (
            not isinstance(fields, dict) or not required.issubset(fields)
            or set(fields) - required - _TUPLE_FIELDS
            or type(fields["version"]) is not int or fields["version"] != 1
            or any(not isinstance(fields[key], str) or not fields[key] or "\0" in fields[key]
                   for key in required - {"version"})
            or fields["platform"] not in ("windows", "wsl", "linux")
            or re.fullmatch(r"\d{8}-\d{6}", fields["timestamp"]) is None
            or re.fullmatch(r"[0-9a-f]{4}|[0-9a-f]{32}", fields["suffix"]) is None
        ):
            raise ValueError("Invalid complete admitted allocation")
        identity = _receipt_identity(fields)
        expected = (
            prospective_session_worktree_id(identity, fields["timestamp"], fields["suffix"])
            if identity else session_worktree_id(
                fields["machine"], fields["platform"], fields["timestamp"], fields["suffix"],
                legacy=len(fields["suffix"]) == 32,
            )
        )
        if (
            fields["worktree_id"] != expected
            or "/" in expected or "\\" in expected or Path(expected).name != expected
            or fields["branch"] != f"worktree/{expected}"
            or fields["path"] != str(Path(fields["worktree_root"]) / expected)
        ):
            raise ValueError("Invalid complete admitted allocation namespace")
        return cls(
            **{key: fields[key] for key in required - {"version"}},
            machine_identity=identity,
        )

    def require_current(self, config: cfg.Config) -> None:
        """Revalidate namespace and owner without choosing another representation."""
        self.from_fields(self.to_fields())
        execution_spaces.require_current_execution_space(config)
        if (
            self.project != config.repo_name
            or _canonical_path(self.anchor) != _canonical_path(config.default_repo.anchor)
            or _canonical_path(self.worktree_root) != _canonical_path(config.default_repo.worktree_root)
            or self.platform != cfg.detect_platform()
            or self.platform != config.platform
        ):
            raise ValueError("Stored New allocation conflicts with the current project/platform namespace")
        if self.machine_identity is not None:
            if new_machine_identity(config, "tuple-v1") != self.machine_identity:
                raise ValueError("Stored New allocation conflicts with its original machine identity")
        elif self.machine != config.machine or not execution_spaces.require_owner_identity(self.machine, config):
            raise ValueError("Stored New allocation conflicts with its original execution owner")

    def require_record(self, record: tracking.WorktreeRecord, *, nonce: str | None = None) -> None:
        if (
            record.worktree_id != self.worktree_id or record.repo != self.project
            or record.branch != self.branch or record.worktree_path != self.path
            or record.machine != self.machine or record.platform != self.platform
            or record.machine_identity != self.machine_identity
            or (nonce is not None and record.creation_nonce != nonce)
        ):
            raise ValueError("Stored New allocation conflicts with its original record identity")


def admit(
    config: cfg.Config, timestamp: str, suffix: str, mode: IdentityMode,
) -> AdmittedAllocation:
    """Called by the locked launch writer, not from an arbitrary caller ID."""
    execution_spaces.require_current_execution_space(config)
    identity = new_machine_identity(config, mode)
    platform = cfg.detect_platform()
    wid = (
        prospective_session_worktree_id(identity, timestamp, suffix) if identity
        else session_worktree_id(config.machine, platform, timestamp, suffix, legacy=len(suffix) == 32)
    )
    allocation = AdmittedAllocation(
        config.repo_name, config.default_repo.anchor, config.default_repo.worktree_root,
        wid, f"worktree/{wid}", str(Path(config.default_repo.worktree_root) / wid),
        identity.machine if identity else config.machine, platform, timestamp, suffix, identity,
    )
    allocation.require_current(config)
    return allocation


def creation_parameters(
    config: cfg.Config, *, kind: str, machine_identity: WorktreeMachineIdentity | None,
    timestamp: str, suffix: str, partial: bool, system_slug: str,
    admitted: AdmittedAllocation | None,
) -> tuple[str, str, str]:
    if admitted is not None:
        return admitted.worktree_id, admitted.branch, admitted.path
    if partial and (
        kind != "session" or re.fullmatch(r"\d{8}-\d{6}", timestamp) is None
        or re.fullmatch(r"[0-9a-f]{4}|[0-9a-f]{32}", suffix) is None
    ):
        raise ValueError("Invalid admitted worktree allocation")
    if kind == "system":
        wid = f"sys-{system_slug}-{timestamp}-{suffix}"
    elif machine_identity is not None:
        wid = prospective_session_worktree_id(machine_identity, timestamp, suffix)
    else:
        wid = session_worktree_id(
            config.machine, cfg.detect_platform(), timestamp, suffix,
            legacy=partial and len(suffix) == 32,
        )
    return wid, f"worktree/{wid}", str(Path(config.default_repo.worktree_root) / wid)
