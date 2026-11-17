"""Typed contracts for creation's dynamic composition and replay boundaries."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypedDict

from . import config as cfg, tracking
from .creation_allocation import AdmittedAllocation
from .creation_identity import IdentityMode
from .launch_preflight import LaunchPreflight
from .receiving_allocation import ReceivingAllocation
from .tracking_claims import ClaimRef
from .worktree_machine_identity import WorktreeMachineIdentity


class CreationOptions(TypedDict):
    profile: cfg.CopilotProfile | None
    profile_is_explicit: bool
    no_mux: bool
    kind: tracking.WorktreeKind
    owner: str | None
    interface: tracking.WorktreeInterface | None
    origin: tracking.WorktreeOrigin | None
    name: str | None
    parent_session: str | None
    inherit_parent_session: bool
    caller_worktree: str | None
    owner_ref: str | None
    dispatch_attempt: dict[str, object] | None
    launch_preflight: LaunchPreflight | None
    recovery: bool
    bound_agent: str | None
    no_pair: bool
    pending_seed: str | None
    defer_projections: bool
    pending_seed_id: str | None
    allocation: tuple[str, str] | None
    identity_mode: IdentityMode
    admitted_allocation: AdmittedAllocation | None
    build_launch_plan: bool


class ReceivingCreationOptions(CreationOptions, total=False):
    receiving_allocation: ReceivingAllocation


class CreationRecordOptions(TypedDict, total=False):
    machine_identity: WorktreeMachineIdentity
    receiving_allocation: ReceivingAllocation


class ConfigPlatform(Protocol):
    def detect_platform(self) -> str: ...


class TrackingCreation(Protocol):
    """The existing facade stays live so replacements retain their call sites."""

    def format_claim_ref(
        self, machine: str | None, project: str | None, worktree_id: str,
        session: str | None = None,
    ) -> str: ...

    def parse_claim_ref(self, ref: str) -> ClaimRef | None: ...

    def create_new_record(
        self, worktree_id: str, branch: str, worktree_path: str, repo: str, machine: str,
        platform_name: str, tracking_path: Path, *,
        kind: tracking.WorktreeKind = "session", owner: str | None = None,
        interface: tracking.WorktreeInterface | None = None,
        origin: tracking.WorktreeOrigin | None = None,
        dispatch_attempt: tracking.DispatchAttempt | None = None,
        parent_session: str | None = None, caller_worktree: str | None = None,
        owner_ref: str | None = None, pair_id: str | None = None, pair_role: str | None = None,
        pair_ref: str | None = None, pair_kind: str | None = None, codename: str | None = None,
        codename_source: str | None = None, bound_agent: str | None = None,
        pending_seed: str | None = None, machine_identity: WorktreeMachineIdentity | None = None,
        receiving_allocation: ReceivingAllocation | None = None, creation_nonce: str | None = None,
    ) -> tracking.WorktreeRecord: ...


class CreationCleanupContext(Protocol):
    worktree: dict[str, str]
    seed_id: str


CreationIntent = Callable[[dict[str, Any]], None]
