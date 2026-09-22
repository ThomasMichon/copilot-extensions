"""Worktree tracking YAML -- read, write, and update operations.

Each worktree gets a YAML file at ~/.{project}/worktrees/{id}.yaml
tracking its lifecycle state.
"""

from __future__ import annotations

import math
import os
import re
import secrets
import tempfile
import threading
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml

from . import config as cfg
from . import disposition_history
from .effort_focus import ActiveEffort, active_effort_from_mapping

#: Max length of an AGENT-ASSERTED worktree title. Agent titles must fit the mux
#: status bar (120-col default) and the Worktree Picker's table rows; longer prose
#: belongs in the disposition ``summary`` (Picker actions menu / ``status
#: --history``). Enforced by :func:`cap_title` on the ``status --title`` write
#: paths (NOT on auto-derived session-summary titles, which the bar truncates for
#: display).
TITLE_MAX = 30

#: ``archived`` is post-``finalized`` -- the tombstone `retire_record`
#: writes for an unpaired reaped worktree instead of deleting it.
WorktreeStatus = Literal[
    "active", "complete", "pushed", "finalized", "orphaned", "archived",
]

# A Copilot session's asserted lifecycle state within its worktree
# (session-lifecycle / agent-fabric vision `single-current-session-per-worktree`):
#   * "active"     -- a current, resumable session (the default; a stopped or
#                     ended session is still active/resumable until concluded).
#   * "yielded"    -- this session has opened a handoff intent (see
#                     `open_handoff`) and is no longer the authoritative head,
#                     but has NOT concluded -- it may still be alive/resumable.
#                     Distinct from "handed-off": a yielded session's handoff
#                     may never be formally linked to a specific successor (a
#                     stored/paste handoff, an operator manually opening a new
#                     pane, etc.), and claiming head from a yielded state is a
#                     separate, non-blocking concern from consuming the actual
#                     handoff charter (gitea aperture-labs#7230).
#   * "handed-off" -- concluded *into* a successor via a handoff cutover.
#   * "concluded"  -- deliberately finished / sunset.
# Conclusion ("handed-off"/"concluded") is an ASSERTED act, never inferred from
# liveness. Absent (legacy records) = "active", so no migration is needed.
SessionState = Literal["active", "yielded", "handed-off", "concluded"]
HandoffState = Literal["pending", "linked", "cancelled"]
ProfileAssignmentDisposition = Literal["pending", "bound", "abandoned"]
ControllerRelationKind = Literal["worktree", "session"]
ControllerRelationSource = Literal[
    "explicit", "owner-ref", "caller-worktree", "parent-session"
]
ControllerRelationState = Literal["active", "ended"]

# States that mean "no longer the current session" -- a replayed head pointing
# at one resolves to no current session until an explicit successor/adoption.
_CONCLUDED_SESSION_STATES: tuple[SessionState, ...] = ("handed-off", "concluded")

# States that make a session ineligible to BE resolved as the current head --
# a superset of `_CONCLUDED_SESSION_STATES` that also excludes "yielded"
# (gitea aperture-labs#7230). Used only by head resolution
# (`resolved_head_session`/`replayed_head_session`); NOT used by
# `conclude_session` (a yielded session has not concluded -- it may still be
# alive) or `link_handoff`'s already-concluded successor check.
_HEAD_INELIGIBLE_STATES: tuple[SessionState, ...] = (*_CONCLUDED_SESSION_STATES, "yielded")

# A worktree's owner class. "session" = an interactive agent session (the
# default, shown in the launch Picker). "system" = a daemon-owned worktree
# created per work-session by a background service. "bridge" = an
# agent-bridge-owned worktree backing an ACP/remote agent session. "system" and
# "bridge" are both **managed** kinds: hidden from the launch Picker by default
# and exempt from routine cleanup (each is torn down by its owner). They are
# tracked as distinct kinds so the Picker can mark and manage them separately.
# See the agent-worktrees docs and the test-chamber system-worktrees effort.
WorktreeKind = Literal["session", "system", "bridge"]

# Agent/daemon-owned kinds: exempt from routine cleanup/reap and never
# fast-forwarded (their owning service or bridge manages their lifecycle).
# NOTE: this governs *lifecycle management*, NOT Picker visibility -- an
# operator-owned bridge (ACP) worktree is lifecycle-managed (here) yet still
# shown in the Picker (visibility keys on ``origin`` -- see MANAGED_ORIGINS).
MANAGED_KINDS: tuple[WorktreeKind, ...] = ("system", "bridge")

# A worktree's two orthogonal marks (see the test-chamber
# worktree-origin-interface-visibility effort / agent-fabric vision behavior
# ``origin-and-interface-are-marked``):
#
#   * interface -- how the work is *currently driven*: an interactive "cli" at a
#     terminal, or a programmatic "acp" client (Neuron Forge or a bridge).
#   * origin -- *who kicked it off*: the operator ("user", via NF or the Picker),
#     a background/scheduled process ("system"), or another agent ("delegate").
#
# The axes are independent: the operator may launch either a CLI or an ACP
# session, and an agent-spawned worktree may itself take either body. Both are
# optional stored fields -- when absent (legacy records) they are *derived* from
# ``kind`` (+ the caller heuristic for a bridge worktree) so existing YAMLs need
# no migration. See ``WorktreeRecord.resolved_interface`` / ``resolved_origin``.
WorktreeInterface = Literal["cli", "acp"]
WorktreeOrigin = Literal["user", "system", "delegate"]

# Origins tucked out of the everyday launch Picker + NF cockpit (the machine's
# own autonomous chatter), reachable through the explicit "System" affordance.
# The operator's own work (origin "user") is shown on *either* interface.
MANAGED_ORIGINS: tuple[WorktreeOrigin, ...] = ("system", "delegate")

_MAX_SESSION_ACTIVATIONS = 256
_MAX_HEAD_TRANSITIONS = 512
_MAX_HANDOFFS = 256
_MAX_PROFILE_ASSIGNMENTS = 128
_MAX_CONTROLLER_RELATIONS = 32
MAX_PERSISTED_COUNTER = (1 << 63) - 1
DISPATCH_PROVENANCE_TEXT_MAX = 512


def _bounded_nonnegative_int(value: object, *, field: str) -> int:
    """Parse one persisted counter without accepting coercions or infinities."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a numeric integer")
    if isinstance(value, float) and (
        not math.isfinite(value) or not value.is_integer()
    ):
        raise ValueError(f"{field} must be a finite integer")
    if value < 0 or value > MAX_PERSISTED_COUNTER:
        raise ValueError(f"{field} must be between 0 and {MAX_PERSISTED_COUNTER}")
    return int(value)


@dataclass(frozen=True)
class DispatchAttempt:
    """Immutable provenance for a worktree created by one dispatch attempt."""

    task_id: str
    reservation_key: str
    attempt: int
    driver: str
    supervisor: str
    creator_machine: str
    ownership: Literal["created"] = "created"

    def to_dict(self) -> dict[str, object]:
        return {
            "task_id": self.task_id,
            "reservation_key": self.reservation_key,
            "attempt": self.attempt,
            "driver": self.driver,
            "supervisor": self.supervisor,
            "creator_machine": self.creator_machine,
            "ownership": self.ownership,
        }


def _dispatch_attempt_from_mapping(value: object) -> DispatchAttempt | None:
    if not isinstance(value, dict):
        return None
    required = {
        "task_id",
        "reservation_key",
        "attempt",
        "driver",
        "supervisor",
        "creator_machine",
        "ownership",
    }
    if set(value) != required or value.get("ownership") != "created":
        return None
    strings: dict[str, str] = {}
    for key in required - {"attempt", "ownership"}:
        raw = value.get(key)
        normalized = raw.strip() if isinstance(raw, str) else ""
        if (
            not isinstance(raw, str)
            or not normalized
            or len(normalized) > DISPATCH_PROVENANCE_TEXT_MAX
        ):
            return None
        strings[key] = normalized
    try:
        attempt = _bounded_nonnegative_int(value.get("attempt"), field="dispatch_attempt.attempt")
    except (TypeError, ValueError, OverflowError):
        return None
    if attempt <= 0:
        return None
    return DispatchAttempt(
        task_id=strings["task_id"],
        reservation_key=strings["reservation_key"],
        attempt=attempt,
        driver=strings["driver"],
        supervisor=strings["supervisor"],
        creator_machine=strings["creator_machine"],
    )


@dataclass
class SessionActivation:
    """One observed interval in which a session was associated with a worktree.

    Hook delivery is at-least-once and a Copilot session may be resumed many
    times.  Keeping each interval append-only preserves that history instead of
    overwriting the session's original ``started_at`` on every resume.
    """

    ordinal: int
    started_at: str
    start_recorded_at: str
    start_source: str = "hook"
    ended_at: str | None = None
    end_recorded_at: str | None = None
    end_source: str | None = None


@dataclass
class SessionEntry:
    """A Copilot session associated with a worktree.

    ``state`` is the session's **asserted lifecycle** (session-lifecycle):
    ``active`` (the default -- a current, resumable session), ``handed-off``
    (concluded *into* a successor via a handoff cutover; ``successor`` names it),
    or ``concluded`` (deliberately finished/sunset). Conclusion is an **asserted**
    act, never inferred from liveness (a stopped/ended session is still ``active``
    -- i.e. resumable -- until concluded). ``successor`` / ``predecessor`` form
    the durable two-way chain of sessions in one worktree. All three default to
    the legacy shape (``active`` / no links) and are omitted from YAML unless set,
    so existing records stay byte-identical.
    """

    session_id: str
    started_at: str
    pid: int | None = None
    ended_at: str | None = None
    state: SessionState = "active"
    successor: str | None = None
    predecessor: str | None = None
    pane_id: str | None = None
    activations: list[SessionActivation] = field(default_factory=list)
    relation_revision: int = 0


@dataclass
class SessionBackendBinding:
    """Current externally hosted session bound to this worktree."""

    kind: str
    endpoint_url: str
    session_id: str
    protocol_version: str
    auth_account: str
    created_at: str
    last_seen_at: str
    state: str = "active"
    binding_revision: int = 1

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "kind": self.kind,
            "endpoint_url": self.endpoint_url,
            "session_id": self.session_id,
            "protocol_version": self.protocol_version,
            "auth_account": self.auth_account,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "state": self.state,
            "binding_revision": self.binding_revision,
        }


@dataclass
class ExecutionLegBinding:
    """Generic, provider-neutral externally hosted execution leg.

    Per ``visions/session-hosting`` §Concepts/*Host-owned execution identity*:
    agent-worktrees interprets only ``provider``, ``state``, and
    ``binding_revision``. ``blob`` is opaque, provider-owned payload -- never
    inspected, validated, or given typed fields here. This is the generic
    successor to :class:`SessionBackendBinding`, which is AHP-shaped (see
    ``efforts/active/worktree-manager-control-plane/phase-3b-ahp-relocation.md``).
    Nothing writes this field yet; this dataclass and its read path are
    additive-only groundwork for Phase 3b Slice 1 Step 1.
    """

    provider: str
    state: str = "active"
    binding_revision: int = 1
    blob: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "provider": self.provider,
            "state": self.state,
            "binding_revision": self.binding_revision,
            "blob": dict(self.blob),
        }


@dataclass
class HeadTransition:
    """A monotonic, replayable change to a worktree's current session."""

    revision: int
    session_id: str | None
    reason: str
    at: str
    handoff_ordinal: int | None = None


@dataclass
class SessionHandoff:
    """A numbered handoff intent and its eventual exact successor link."""

    ordinal: int
    token: str
    predecessor: str
    state: HandoffState
    opened_at: str
    successor: str | None = None
    linked_at: str | None = None
    candidate: str | None = None
    candidate_at: str | None = None


@dataclass
class ProfileAssignment:
    """One durable profile assignment for a Copilot launch generation."""

    policy: str
    assignment_label: str
    selected_profile: str
    bag_generation: int
    bag_position: int
    assigned_at: str
    disposition: ProfileAssignmentDisposition = "pending"
    session_id: str | None = None
    lane: str = ""
    abandoned_at: str | None = None
    bound_at: str | None = None
    predecessor_session_id: str | None = None


@dataclass
class PRRecord:
    """Pull-request metadata nested under a worktree record (PR mode).

    Present only when the worktree has entered the PR workflow.  ``state``
    tracks the PR lifecycle; ``branch`` is the pushed feature branch.  A
    worktree may carry several of these over its life (serial re-PRs) or at
    once (parallel PRs) -- see ``WorktreeRecord.prs``.
    """

    state: str = ""          # creating | open | merged | closed
    branch: str = ""
    base_sha: str = ""
    head_sha: str = ""
    head_observed_at: str = ""  # provider-clock timestamp observing this exact head
    head_observed_api_base: str = ""  # provider endpoint that issued the timestamp
    attribution_head: str = ""
    patch_id: str = ""       # squash-invariant patch-id of base..head (#898)
    url: str = ""
    number: int | None = None
    provider: str = ""
    repo: str = ""           # target repo "owner/name"; default = worktree repo
    opened_at: str = ""      # ISO timestamp the PR record was opened
    closed_at: str = ""      # ISO timestamp the PR reached a terminal state
    # codename-attribution-by-default (rounds 26-39): this PR's attribution
    # decision, FROZEN once at creation time and never re-derived from live
    # config afterward -- see design.md § Per-PR attribution freeze for the
    # full rationale. `attribution_mode` is one of the closed set
    # {"", "false", "true", "codename"} ("" is the empty legacy sentinel:
    # unset, predates this mechanism, OR a partial/malformed persisted
    # pair -- never treated as authorizing publication).
    # `attribution_explicit` records whether the frozen decision came from
    # an EXPLICIT per-call override/config key, versus the bare implicit
    # default -- itself frozen alongside the mode, since a `False` explicit
    # value is a legitimate frozen state (e.g. an implicit codename
    # decision) that must not be confused with the unset sentinel.
    attribution_mode: str = ""
    attribution_explicit: bool = False
    # A random UUID assigned ONCE when this entry is first created, and
    # NEVER touched by any later branch/number/provider/state correction
    # (round-36/37/38 findings: neither `branch` nor `number` is actually
    # immutable -- a manual `set-pr` correction can reassign either -- so
    # this is the SOLE stable per-entry merge identity `_save_record_unlocked`
    # uses to protect the frozen attribution pair across concurrent saves).
    # Backfilled INLINE by `_save_record_unlocked` itself for any entry that
    # predates this field, not via a separate migration pass (round-37
    # finding: this repo's config-migration framework explicitly excludes
    # tracking YAML, so no such pass has an actual entry point).
    pr_id: str = ""
    # Bumped every time this entry's attribution_mode/attribution_explicit
    # are (re)stamped -- following the `profile_assignment_revision`
    # pattern already established elsewhere in this file. Guards
    # `_save_record_unlocked`'s per-entry merge: a stale in-memory snapshot
    # never overwrites a matching on-disk entry whose `pr_revision` is
    # already at least as high.
    pr_revision: int = 0


# PR lifecycle states that are still live (the PR can still receive pushes).
# Anything else (merged/closed) is terminal.
_PR_NON_TERMINAL = ("", "creating", "open")


def _pr_is_terminal(pr: PRRecord) -> bool:
    """Return True when a PR has reached a terminal (merged/closed) state."""
    return pr.state not in _PR_NON_TERMINAL


def _attribution_mode_str(attribution: object) -> str:
    """Normalize an effective ``SourceAttribution`` value to one of the
    closed persisted ``attribution_mode`` strings
    (``_VALID_ATTRIBUTION_MODES``)."""
    if attribution == "codename":
        return "codename"
    return "true" if attribution is True else "false"


def attribution_from_frozen_mode(pr: PRRecord) -> object:
    """The inverse of :func:`_attribution_mode_str`: reconstruct an
    effective ``SourceAttribution`` value from a PR's FROZEN
    ``attribution_mode``, for a caller that must drive a decision off the
    frozen pair rather than live config (round-38 finding: e.g. the
    initial-open path, which previously recomputed a live value even
    though the PR may already carry an earlier frozen decision).
    """
    if pr.attribution_mode == "codename":
        return "codename"
    return pr.attribution_mode == "true"


def ensure_pr_id(pr: PRRecord) -> bool:
    """Assign ``pr_id`` if this entry doesn't already have one -- no other
    side effect (never touches ``attribution_mode``/``attribution_explicit``/
    ``pr_revision``, unlike :func:`stamp_frozen_attribution`). Returns
    whether an id was actually assigned.

    A PR #3037 review finding: a caller (manual ``set-pr``) that mutates
    an EXISTING legacy entry's ``branch``/``number`` must backfill and
    PERSIST this entry's ``pr_id`` in a separate save BEFORE applying that
    mutation -- if the id were assigned only as part of the same save that
    also renames the branch, `_save_record_unlocked`'s merge would load
    "current" (on-disk, pre-rename, ALSO still pr_id-less) and diff it
    against this in-memory copy (post-rename, pr_id-less too, if assigned
    only afterward): both sides genuinely blank but with DIFFERENT branch
    values, so the identity fallback would treat them as two unrelated
    entries and duplicate-append the stale on-disk one. Stamping and
    persisting the id FIRST, before any rename, closes that window.
    """
    if pr.pr_id:
        return False
    pr.pr_id = secrets.token_hex(16)
    return True


def stamp_frozen_attribution(
    pr: PRRecord, *, attribution: object, explicit: bool,
    assign_pr_id: bool = True,
) -> None:
    """Freeze this PR's attribution decision ONCE, at creation time (design.md
    § Per-PR attribution freeze, rounds 26-39). Must be called at every
    FRESH-construction site (``create_pr``, ``_push_existing_feature``'s
    fresh-target construction, manual ``set-pr``'s bare construction) --
    NEVER at deserialization (``_parse_pr_mapping`` stays strict read-only,
    round-33 finding).

    ``attribution`` is the caller's already-computed EFFECTIVE attribution
    (want_attribution) -- stamped VERBATIM, never re-derived from live
    config (round-31 finding: an override may itself be a ``SourceAttribution``
    value, not just a bool, once round-18's typing widening lands).
    ``explicit`` records whether that value came from an EXPLICIT per-call
    override or config key, versus the bare implicit default -- itself
    frozen alongside the mode (a ``False`` explicitness is a legitimate
    frozen state, e.g. an implicit ``codename`` decision).

    Also assigns a fresh ``pr_id`` (a random token, unique per entry, NEVER
    reassigned by this or any later call -- round-36 finding) if the entry
    doesn't already have one, and bumps ``pr_revision`` -- the same
    `_save_record_unlocked`-guarded counter a later re-stamp (e.g. a
    retroactive-change migration touch) also bumps.

    ``assign_pr_id=False`` (a fix-PR-#3037-review finding) is used ONLY by
    the legacy-freeze-on-first-touch call site
    (``refresh_source_attribution``): that call stamps an EXISTING,
    already-on-disk entry OUTSIDE the record lock, so two concurrent
    legacy-freeze calls for the SAME PR could otherwise each independently
    mint a DIFFERENT random ``pr_id`` before either saves -- once both
    in-memory copies have distinct non-empty ``pr_id`` values, the identity
    match's ``pr_id`` path (requiring exact equality) no longer falls back
    to the branch/number rule that would otherwise unify them, causing the
    loser's save to append a duplicate PR record instead of merging. With
    ``assign_pr_id=False``, both racing calls leave ``pr_id`` empty, so
    `_save_record_unlocked`'s own inline backfill (which runs serialized
    under the record lock) is the ONLY place that ever mints a real
    ``pr_id`` for a legacy entry -- race-free by construction. The three
    FRESH-construction sites keep the default ``True``: a brand-new
    ``PRRecord`` has no on-disk counterpart to race against at all.
    """
    pr.attribution_mode = _attribution_mode_str(attribution)
    pr.attribution_explicit = bool(explicit)
    if assign_pr_id and not pr.pr_id:
        pr.pr_id = secrets.token_hex(16)
    pr.pr_revision += 1


# ---------------------------------------------------------------------------
# Resource claims -- the outbound claim ledger (agent-fabric `resource-claims`)
# ---------------------------------------------------------------------------

# The kinds of outbound resource a worktree can own and claim. ``worktree`` is
# a cross-repo worktree it spun up; ``task`` is an external, task-queue-owned
# obligation (e.g. an agent-dispatch task suspended mid-flight, expecting to
# resume in this exact worktree later) -- deliberately left unresolved by the
# sweep (see sweep.py's per-kind handling: no branch = permanently ``spare``),
# since only the owning task system can know when it is genuinely done;
# ``session`` is a live Copilot session occupying this worktree (its ``ref``
# is a qualified ``<machine>/<project>/<worktree_id>#<session_id>`` claim ref,
# reusing the existing session-suffix grammar rather than a second
# ``sessions:`` list) -- see Phase 8 of
# ``efforts/active/worktree-finality-and-obligations/README.md``; the rest are
# placeholders the ledger view already understands so later phases can
# journal them without a schema change.
ResourceKind = Literal[
    "worktree", "codespace", "container", "ssh", "workdir", "pr", "task", "session"
]

# Claim disposition (resource-obligation-settlement): "active" while unsettled
# work still rides on the resource, "at-rest" once that work is safe (merged /
# off-box / itself finalized) but the claim is still held, "released" once the
# owner explicitly lets go. Unknown/absent degrades to "active" so a stray value
# never hides a live claim from the reap-safety check. The canonical vocabulary
# + predicates live in ``obligations``; this tuple is the set of dispositions
# that still mean "held" (claim not torn down) -- both active and at-rest -- used
# by ``is_live`` / ``live_resources`` for reap-safety.
_CLAIM_LIVE_STATES: tuple[str, ...] = ("", "active", "at-rest")

from .tracking_claims import (  # noqa: F401
    ANCHOR_ID,
    FOLLOW_UP_DISMISSED,
    FOLLOW_UP_OPEN,
    FOLLOW_UP_PENDING_TRANSFER,
    FOLLOW_UP_RESOLVED,
    FOLLOW_UP_TRANSFERRED,
    ClaimRef,
    FollowUpRecord,
    FollowUpRef,
    FollowUpRefKind,
    FollowUpState,
    ResourceClaim,
    _FOLLOW_UP_EFFECTIVE_OPEN,
    _TERMINAL_OWNER_STATUSES,
    add_follow_up,
    add_resource_claim,
    claim_handoff_reservation,
    dismiss_follow_up,
    effective_open_follow_up_count,
    find_orphaned_children,
    format_anchor_ref,
    format_claim_ref,
    load_or_create_anchor_record,
    load_orphaned_obligations,
    load_orphaned_obligations_strict,
    orphanage_path,
    parse_claim_ref,
    release_all_resources,
    release_resource_claim,
    remove_orphaned_obligations,
    rehome_abandoned_obligations,
    reopen_finalized_owner,
    resolve_follow_up,
    settle_resource_claim,
    sweep_abandoned_obligations,
)


@dataclass
class ControllerRelation:
    """One authoritative controller relationship for a child worktree.

    Control is deliberately separate from session binding. A controller may
    name a worktree, an exact Copilot session, or both; it never participates in
    ``sessions`` or ``head_session``. ``relation_revision`` is allocated from
    the record's monotonic ``controller_revision`` counter.
    """

    kind: ControllerRelationKind
    source: ControllerRelationSource
    relation_revision: int
    created_at: str
    controller_ref: str | None = None
    controller_session_id: str | None = None
    state: ControllerRelationState = "active"
    ended_at: str | None = None


class ControllerRelationError(ValueError):
    """A controller relation is invalid or cannot be mutated safely."""


def _owning_tracking_dir(worktree_id: str, repo: str | None = None) -> Path:
    """Resolve the tracking directory that actually owns ``worktree_id``.

    ``cfg.tracking_dir()`` is ambient: it reflects whichever project the
    *current process* resolved (``-p``/CWD), not the project that actually
    owns ``worktree_id``. A caller that knows the record's own ``repo``
    (e.g. an already-loaded :class:`WorktreeRecord`) gets an exact answer.
    Otherwise, prefer the ambient project's tracking dir (the fast, common
    case), and only fall back to scanning every other adopted project's
    tracking dir when the ambient one doesn't have the file -- so a
    cross-project caller (notably the machine-wide resident status-monitor,
    which sweeps every ``wt-*`` session from one process) can't silently
    duplicate a foreign project's record into its own ambient directory
    (see copilot-extensions#2788).
    """
    if repo:
        return cfg.project_dir(repo) / "worktrees"

    ambient = cfg.tracking_dir()
    if (ambient / f"{worktree_id}.yaml").exists():
        return ambient

    try:
        from . import repos as repos_mod

        candidate_names = repos_mod._adopted_project_names()
    except Exception:
        candidate_names = set()

    for name in candidate_names:
        try:
            candidate_dir = cfg.project_dir(name) / "worktrees"
        except Exception:
            continue
        if candidate_dir == ambient:
            continue
        if (candidate_dir / f"{worktree_id}.yaml").exists():
            return candidate_dir

    return ambient


@dataclass
class WorktreeRecord:
    """Parsed worktree tracking record."""

    worktree_id: str
    branch: str
    worktree_path: str
    repo: str
    machine: str
    platform: str
    started_at: str
    last_resumed_at: str
    resume_count: int
    title: str | None
    status: WorktreeStatus
    completed_at: str | None
    sessions: list[SessionEntry] | None = field(default=None)
    session_backend: SessionBackendBinding | None = None
    session_backend_opaque: bool = field(default=False, repr=False, compare=False)
    session_backend_raw: object = field(default=None, repr=False, compare=False)
    # Generic, provider-neutral successor to ``session_backend`` (see
    # ``ExecutionLegBinding``). Parsed only from an actual on-disk
    # ``execution_leg:`` key -- never derived from a legacy ``session_backend``
    # record here (that translation is a pure, on-demand read via
    # ``derive_execution_leg()`` so nothing here changes what gets written).
    execution_leg: ExecutionLegBinding | None = None
    execution_leg_opaque: bool = field(default=False, repr=False, compare=False)
    execution_leg_raw: object = field(default=None, repr=False, compare=False)
    # PR records (PR mode).  A worktree can track multiple PRs -- serially
    # (re-PR after a merge) or in parallel -- each self-describing (including
    # its target ``repo``).  Empty when the worktree has not entered the PR
    # workflow.  The legacy single ``pr:`` YAML block loads as a one-element
    # list; the ``pr`` property below preserves the old single-PR accessor.
    prs: list[PRRecord] = field(default_factory=list)
    kind: WorktreeKind = "session"
    owner: str | None = None  # owning service name, for system worktrees
    # #2668: the two orthogonal marks. Stored only when explicitly stamped
    # (e.g. agent-bridge stamping origin=user for an NF-launched session);
    # ``None`` means "derive from kind" via the resolved_* properties below, so
    # legacy YAMLs need no migration. Read them through resolved_interface /
    # resolved_origin, never these raw fields.
    interface: WorktreeInterface | None = None
    origin: WorktreeOrigin | None = None
    dispatch_attempt: DispatchAttempt | None = None
    dispatch_attempt_opaque: bool = field(
        default=False, repr=False, compare=False)
    dispatch_attempt_raw: object = field(
        default=None, repr=False, compare=False)
    dispatch_attempt_raw_present: bool = field(
        default=False, repr=False, compare=False)
    # False when an external host created and owns the checkout. We may track
    # its sessions, but cleanup must never remove its directory or branch.
    checkout_managed: bool = True
    # #1029: the Copilot session that originated this worktree's work. Seeded at
    # creation (the spawning session) and backfilled at PR-create, so a
    # PR/feedback worktree whose own ``sessions`` list is empty can still resume
    # with the source session's context instead of cold-starting.
    parent_session: str | None = None
    # session-lifecycle: the worktree's CURRENT session -- its head pointer. An
    # agent is a series of sessions in one worktree; this names the one that is
    # current *now*. It is an ASSERTED pointer (moved by an explicit conclude /
    # handoff / new-session), never inferred from timestamps. Absent (legacy) =
    # derive the head from the sessions list (newest non-concluded) via
    # ``resolved_head_session``, so existing YAMLs need no migration. Emitted
    # only when set, keeping the common-case YAML byte-identical.
    head_session: str | None = None
    # Session lifecycle is an append-only, monotonic ledger. ``head_session`` is
    # retained as a cheap materialized cache for existing consumers; when
    # transitions exist, replaying the highest revision is authoritative.
    lifecycle_revision: int = 0
    head_revision: int = 0
    head_transitions: list[HeadTransition] = field(default_factory=list)
    handoff_counter: int = 0
    handoffs: list[SessionHandoff] = field(default_factory=list)
    # Opt-in balanced profile assignment. The project-level allocator owns the
    # shuffled bag; this bounded record copy makes launch/session identity
    # available through the ordinary record and JSON status surfaces.
    profile_assignment_revision: int = 0
    profile_assignments: list[ProfileAssignment] = field(default_factory=list)
    # Reciprocal session/worktree metadata: controllers deliberately operate
    # this worktree without becoming bound sessions or affecting its head,
    # liveness, occupancy, or resume eligibility. The list is bounded; ended
    # relations are retained until displaced by newer history.
    controller_revision: int = 0
    controllers: list[ControllerRelation] = field(default_factory=list)
    controller_metadata_opaque: bool = field(
        default=False, repr=False, compare=False)
    controller_raw_revision: object = field(
        default=None, repr=False, compare=False)
    controller_raw_entries: object = field(
        default=None, repr=False, compare=False)
    controller_raw_revision_present: bool = field(
        default=False, repr=False, compare=False)
    controller_raw_entries_present: bool = field(
        default=False, repr=False, compare=False)
    # #2178: for a bridge-spawned worktree, the *caller* worktree that requested
    # it (agent-bridge's caller_id == the caller's WORKTREE_ID). Lets the Picker
    # "Jump to caller" from a bridge worktree back to the worktree that kicked it.
    caller_worktree: str | None = None
    # agent-fabric `resource-claims` -- the outbound claim ledger (both halves):
    #   * owner_ref -- the BACKWARD link: the qualified ref
    #     (machine/project/worktree_id[#session]) of the worktree that OWNS this
    #     one as a cross-repo resource. Generalizes the same-repo `caller_worktree`
    #     across repos/machines; read locally on this record so a reap sweep can
    #     resolve "who holds me?" without a fabric scan. Absent = unclaimed.
    #   * resources -- the FORWARD list: the outbound resources THIS worktree
    #     produced and owns (each a self-describing ResourceClaim, analogous to
    #     `prs`). Both are emitted only when set/non-empty, so legacy YAMLs load
    #     byte-identically.
    owner_ref: str | None = None
    resources: list[ResourceClaim] = field(default_factory=list)
    # agent-bridge-worktree-native-agents: the charter (agent-bridge spawn
    # profile name, e.g. "board-sweep-worker") bound to this worktree at
    # create/embody time. A charter is never itself a first-class fabric
    # target (see visions/agent-fabric `charter-is-a-profile-not-a-target`);
    # this is the persisted selection agent-bridge reads when spawning or
    # resuming a session here, instead of the venue's bare default. Absent =
    # no charter bound (the common case; the venue's default agent drives).
    bound_agent: str | None = None
    # worktree-status-core: the agent-asserted DISPOSITION overlay -- orthogonal
    # to git/session state (which cannot tell "done" from "finalized-with-
    # follow-ups"). Set via `agent-worktrees status`; absent (legacy) = the safe
    # default (not flagged, no summary). Rendered as a Picker overlay + fed to
    # the prune verdict. The live "pulse" (assistant.intent) is a SEPARATE
    # sidecar, never stored on this durable record.
    follow_up: bool = False
    summary: str = ""
    status_note_at: str | None = None
    # worktree-finality-and-obligations: the most recent timestamp this record
    # was `finalized` before being atomically reopened (a new/reactivated held
    # claim arrived after finalize). Preserves the historical fact "this was
    # finalized as of X" once `completed_at` is cleared by the reopen -- see
    # `reopen_finalized_owner`. Never set except by a reopen transition.
    last_finalized_at: str | None = None
    # worktree-finality-and-obligations, Phase 3: the itemized follow-up
    # ledger replacing the boolean-only flag above. `follow_up` (the legacy
    # boolean) is preserved as-is for back-compat callers/YAMLs and is treated
    # as one synthetic effective-open item when no explicit items exist (see
    # `effective_open_follow_up_count`) -- it is NOT auto-migrated into this
    # list. Absent/empty keeps legacy YAML byte-identical.
    follow_ups: list[FollowUpRecord] = field(default_factory=list)
    # One worktree-local pointer to the canonical effort and declared slice.
    # It is identity, not a second responsibility flag: an open binding derives
    # the existing follow_up/summary status core. Absent keeps legacy YAML
    # byte-identical.
    active_effort: ActiveEffort | None = None
    effort_revision: int = 0
    # worktree-status-core: True when the title was AGENT-ASSERTED via
    # `agent-worktrees status --title` (vs. auto-derived from a session summary).
    # An asserted title is authoritative: the status-updater's per-tick
    # `_persist_segment_title` must NOT clobber it with the live session summary.
    # Emitted only when True (like `follow_up`), so un-annotated YAMLs stay
    # byte-identical; absent (legacy) = False = auto-derive as before.
    title_asserted: bool = False
    # #4057 cached liveness (single-owning-layer): the last-known multiplexer
    # liveness for this worktree, stamped by the authoritative single-worktree
    # verify at the action moments (Actions-menu / Enter) and cleared on Stop, so
    # a follow-up populate can prefer this cached hint over a live probe. A
    # *hint*, never authority -- reconciled by the batched live scan / verify;
    # ``mux_live_at`` bounds its freshness. None (absent) = never stamped, so a
    # legacy YAML stays byte-identical.
    mux_live: bool | None = None
    mux_live_at: str | None = None
    # #4057/#1416 cached bound-Copilot liveness (tri-state, cwd-independent):
    # whether a live bound Copilot (mux OR bare) is attributed to this worktree
    # per the authoritative machine-wide ``reclaim.resolve_bound_copilots`` scan,
    # stamped by an OFF-HOT-PATH reconciler (never the populate path). Distinct
    # from ``mux_live``: a *bare* (un-muxed) Copilot has no mux to attach, so
    # folding it into ``mux_live`` would corrupt Open/Resume/Stop gating -- this
    # signal exists solely to surface a bare-resumed session (cwd=home, invisible
    # to the registered-session + mux scans) in the picker's Active section.
    # ``bound_live_at`` bounds its freshness. None (absent) = Unknown / never
    # reconciled -- Unknown is NEVER persisted, so a legacy YAML stays
    # byte-identical.
    bound_live: bool | None = None
    bound_live_at: str | None = None
    # picker-cache-first-paint (dotfiles#948): the session-derived render cache.
    # The Worktree Picker's first paint must read ONLY the per-worktree state
    # file (no events.jsonl turn-count, no process/mux scan) -- so the expensive
    # populate pass (or a per-worktree Refresh) stamps its results back here via
    # ``stamp_session_state``, and the cache-only load reads them directly.
    #   * ``session_turns`` -- cached user-turn count (drives WIP/CONVO + the
    #     Turns column). None = never populated -> the row renders **Unknown**.
    #   * ``session_summary`` -- cached latest-session summary (title fallback).
    #   * ``git_state`` -- cached git-classification state value (e.g. ``wip`` /
    #     ``clean``); None = never classified -> Unknown.
    #   * ``session_state_at`` -- freshness stamp for the whole bundle.
    # Unlike the liveness hints these are NOT aged out on read (a cached turn
    # count / last-known state is shown as-is until the next populate/Refresh
    # rewrites it); ``session_state_at`` exists for throttling + display only.
    # All emitted only when populated, so a never-populated worktree's YAML
    # stays byte-identical.
    session_turns: int | None = None
    session_summary: str | None = None
    git_state: str | None = None
    session_state_at: str | None = None
    # citadel paired -harness/-knowledge worktree lifecycle (#957): when a
    # stateless harness worktree is carved, its bound knowledge repo's worktree
    # (or, for a non-worktree-class knowledge repo, its anchor) is carved and
    # tracked together as a PAIR. These optional fields link the two records so
    # the pair can be resolved, tracked, and finalized together. Emitted only
    # when set, so an unpaired worktree's YAML stays byte-identical (legacy
    # records parse with all four None).
    #   * pair_id   -- the shared pair key: the ``<ts>-<suffix>`` stub both
    #                  sibling worktrees share (derived from the harness id).
    #   * pair_role -- this record's role in the pair: ``harness`` | ``knowledge``.
    #   * pair_ref  -- the canonical :class:`ClaimRef` of the SIBLING record
    #                  (``<machine>/<project>/<worktree_id>``), so the pair
    #                  resolves across repos/machines like the owner ledger.
    #   * pair_kind -- how the sibling is materialized: ``worktree`` (its own
    #                  carved worktree) | ``anchor`` (a non-worktree-class
    #                  knowledge repo paired at its anchor checkout).
    pair_id: str | None = None
    pair_role: str | None = None
    pair_ref: str | None = None
    pair_kind: str | None = None
    # citadel paired-worktree reap tombstone (#220 follow-up): stamped ONLY by
    # :func:`retire_record` when it tombstones (rather than deletes) a paired
    # record on reap. Deliberately distinct from ``status``/``completed_at``:
    # those can legitimately read ``finalized`` on a worktree that is still
    # fully alive on disk (merge-safe and "already reaped" are different
    # things -- see ``finalize``'s own contract). ``reaped_at`` being set is
    # unambiguous, positive proof that THIS record's worktree has actually
    # been removed, which lets the sibling's own later reap tell "my pair
    # partner is a live finalized worktree" apart from "my pair partner was
    # already reaped and is a pure tombstone" -- only the latter is safe to
    # hard-delete instead of re-tombstoning. Emitted only when set, so an
    # unpaired (or not-yet-reaped) record's YAML stays byte-identical.
    reaped_at: str | None = None
    # pr-attribution-codenames Phase 2 (#2838): the public-safe handle
    # assigned once per worktree (see ``agent_worktrees.codename``). Absent on
    # a pre-Phase-2 record until lazily backfilled (``ensure_codename`` in
    # ``codename_tracking.py``); emitted only when set, so a legacy YAML
    # stays byte-identical.
    codename: str | None = None
    # codename-attribution-by-default: which vocabulary `codename` was
    # drawn from at ASSIGNMENT time -- "built-in" (the plugin's own
    # organization-neutral generator) or "custom" (the owning repo's
    # `codename.wordlist_path`), captured once and never re-derived from
    # the repo's CURRENT config (which may have changed since). Absent on
    # a record predating this field -- treated PERMANENTLY as unsafe
    # ("custom") by every publish-time gate, never inferred from current
    # config (round-10 finding: no automated backfill, only a manual,
    # explicit, per-record operator edit ever promotes it). Emitted only
    # when set, so a legacy/pre-Phase-1 YAML stays byte-identical.
    codename_source: str | None = None

    @property
    def owner_claim_ref(self) -> ClaimRef | None:
        """The parsed backward owner link, or None when unclaimed."""
        return parse_claim_ref(self.owner_ref) if self.owner_ref else None

    @property
    def pair_claim_ref(self) -> ClaimRef | None:
        """The parsed sibling link of a paired worktree, or None when unpaired."""
        return parse_claim_ref(self.pair_ref) if self.pair_ref else None

    @property
    def is_paired(self) -> bool:
        """True when this record participates in a harness/knowledge pair."""
        return bool(self.pair_id and self.pair_ref)

    @property
    def live_resources(self) -> list[ResourceClaim]:
        """The outbound resources this worktree still actively holds."""
        return [r for r in self.resources if r.is_live]

    @property
    def active_controllers(self) -> list[ControllerRelation]:
        """Controller relations that have not been explicitly ended."""
        return [relation for relation in self.controllers
                if relation.state == "active"]

    def controller_for_session(
        self, session_id: str,
    ) -> ControllerRelation | None:
        """Return the newest relation for one exact controller session."""
        matching = [
            relation for relation in self.controllers
            if relation.controller_session_id == session_id
        ]
        return max(
            matching,
            key=lambda relation: relation.relation_revision,
            default=None,
        )

    @property
    def resolved_interface(self) -> WorktreeInterface:
        """The worktree's current interface -- stored stamp, else derived.

        Derivation from ``kind``: a bridge worktree is programmatically driven
        (``acp``); everything else defaults to an interactive terminal
        (``cli``). An explicit ``interface`` stamp always wins.
        """
        if self.interface in ("cli", "acp"):
            return self.interface  # type: ignore[return-value]
        return "acp" if self.kind == "bridge" else "cli"

    @property
    def resolved_origin(self) -> WorktreeOrigin:
        """Who kicked the work off -- stored stamp, else derived.

        Derivation from ``kind`` (+ the caller heuristic): a ``system`` worktree
        is daemon-owned; a ``bridge`` worktree is the operator's (``user``) when
        nothing spawned it, else another agent's (``delegate``) when it carries a
        ``caller_worktree`` (an agent-to-agent spawn -- #2178); a plain
        ``session`` is the operator's. An explicit ``origin`` stamp always wins
        (e.g. agent-bridge stamping the authoritative value at launch -- #2670).
        """
        if self.origin in ("user", "system", "delegate"):
            return self.origin  # type: ignore[return-value]
        if self.kind == "system":
            return "system"
        if self.kind == "bridge":
            return "delegate" if self.caller_worktree else "user"
        return "user"

    @property
    def is_picker_hidden(self) -> bool:
        """True when this worktree is tucked out of the everyday Picker/cockpit.

        Visibility keys on **origin**, not kind: the machine's autonomous work
        (``system`` / ``delegate``) is hidden behind the explicit System
        affordance, while the operator's own work (``user``) is shown on either
        interface -- so an NF-launched ACP (bridge) session is visible even
        though it stays lifecycle-managed (see ``MANAGED_KINDS``).
        """
        return self.resolved_origin in MANAGED_ORIGINS

    def session_entry(self, session_id: str) -> SessionEntry | None:
        """Return the tracked ``SessionEntry`` for ``session_id``, or None."""
        for entry in self.sessions or ():
            if entry.session_id == session_id:
                return entry
        return None

    @property
    def replayed_head_transition(self) -> HeadTransition | None:
        """Return the last transition by monotonic revision, if any.

        List position is a deterministic tie-breaker for a manually-corrupted
        record containing duplicate revisions. Writers allocate revisions under
        the record lock, so valid records never need the tie-breaker.
        """
        if not self.head_transitions:
            return None
        return max(
            enumerate(self.head_transitions),
            key=lambda item: (item[1].revision, item[0]),
        )[1]

    @property
    def replayed_head_session(self) -> str | None:
        """Replay the authoritative head from the transition ledger."""
        transition = self.replayed_head_transition
        if transition is None or transition.session_id is None:
            return None
        entry = self.session_entry(transition.session_id)
        if entry is None or entry.state in _HEAD_INELIGIBLE_STATES:
            return None
        return transition.session_id

    @property
    def resolved_head_session(self) -> str | None:
        """The worktree's current session -- replayed ledger, else legacy state.

        Resolution (session-lifecycle):
          1. when a transition ledger exists, replay its highest monotonic
             revision; ``head_session`` is only a repairable cache;
          2. otherwise the stored ``head_session`` when it names a session that still
             exists and is **not** concluded/handed-off/yielded (a stale head
             that concluded, or yielded to an intended-but-never-linked
             handoff, without advancing does not win);
          3. otherwise the **newest non-concluded, non-yielded** session in
             ``sessions`` (by list order -- registration order), preserving
             today's "latest is current" behavior for un-annotated records;
          4. otherwise None (no sessions, or all concluded/yielded).

        A "yielded" session (one that opened a handoff intent but was never
        formally linked to a specific successor) is deliberately excluded here
        so the *next* session to register in this worktree -- regardless of
        whether it consumes that handoff's charter -- can freely claim head
        (see `register_session`; gitea aperture-labs#7230). This is distinct
        from `_CONCLUDED_SESSION_STATES`, which `conclude_session` and
        `link_handoff` still use unchanged: a yielded session has not
        concluded and may still be alive/resumable.

        This is the record-local head. Filesystem-precise "latest by
        workspace.yaml mtime" resolution still lives in ``sessions.py``; this
        derivation is authoritative for the *asserted* lifecycle.
        """
        if self.head_transitions:
            return self.replayed_head_session
        if self.head_session:
            entry = self.session_entry(self.head_session)
            if entry is not None and entry.state not in _HEAD_INELIGIBLE_STATES:
                return self.head_session
        for entry in reversed(self.sessions or ()):
            if entry.state not in _HEAD_INELIGIBLE_STATES:
                return entry.session_id
        return None

    @property
    def pending_handoffs(self) -> list[SessionHandoff]:
        """Pending handoffs in stable ordinal order."""
        return sorted(
            (handoff for handoff in self.handoffs if handoff.state == "pending"),
            key=lambda handoff: handoff.ordinal,
        )

    def active_pr(self) -> PRRecord | None:
        """Return the PR a no-selector command should target.

        Rule (see the multi-PR effort): the most recent **non-terminal**
        (creating/open) PR; if none are live, the most recent overall.
        "Most recent" is by ``opened_at`` then list order, so a record with
        no timestamps resolves deterministically to the last-appended PR.
        """
        if not self.prs:
            return None
        pool = [p for p in self.prs if not _pr_is_terminal(p)] or self.prs
        return max(pool, key=lambda p: (p.opened_at or "", self.prs.index(p)))

    def has_live_pr(self) -> bool:
        """Return True if any tracked PR is still non-terminal (open/creating).

        A worktree with a live PR must not be reaped by cleanup -- the PR is
        still in review and its feature branch is the recovery source.
        """
        return any(not _pr_is_terminal(p) for p in self.prs)

    @property
    def pr(self) -> PRRecord | None:
        """Back-compat accessor: the active PR (see :meth:`active_pr`)."""
        return self.active_pr()

    @pr.setter
    def pr(self, value: PRRecord | None) -> None:
        """Back-compat mutator: replace the active PR, or append/clear.

        Mirrors the old single-slot semantics for call sites that still do
        ``record.pr = PRRecord(...)``: with an active PR present the value
        replaces it in place (preserving list position); with none, the value
        is appended.  Assigning ``None`` drops the active PR from the list.
        Write sites that intend a *new* PR (serial/parallel) mutate ``prs``
        directly instead.
        """
        active = self.active_pr()
        if value is None:
            if active is not None:
                self.prs = [p for p in self.prs if p is not active]
            return
        if active is not None:
            self.prs[self.prs.index(active)] = value
        else:
            self.prs.append(value)

    @property
    def yaml_path(self) -> Path:
        """Path to this record's YAML file in the tracking directory.

        Prefers the exact file this record was loaded from (set by
        :func:`load_record`/:func:`save_record`), so a read-modify-write via
        a bare ``save_record(record)`` always writes back to the same file
        it came from -- even one found via :func:`_owning_tracking_dir`'s
        cross-project fallback scan. Only a record that was never loaded or
        saved (e.g. freshly constructed, not yet persisted) falls back to
        resolving from its own ``repo``, never the ambient/current process's
        project -- see copilot-extensions#2788.
        """
        loaded_from = getattr(self, "_loaded_from", None)
        if loaded_from is not None:
            return loaded_from
        return _owning_tracking_dir(self.worktree_id, self.repo) / f"{self.worktree_id}.yaml"


from .tracking_controller_relations import (
    _limit_controller_relations,
    _mark_controller_projection_dirty,
    _normalize_controller_ref,
    _valid_relation_session_id,
    _validate_controller_relation_set,
    backfill_legacy_controller_relations,  # noqa: F401 -- re-export for tests
    controller_relation_to_dict,  # noqa: F401 -- re-export for tests
    derive_legacy_controller_relations,  # noqa: F401 -- re-export for callers (e.g. __main__)
    end_controller_relation,  # noqa: F401 -- re-export for tests
    remove_controller_relation,  # noqa: F401 -- re-export for tests
    set_controller_relation,  # noqa: F401 -- re-export for tests
)


def _controller_metadata(
    rec: WorktreeRecord,
) -> list[dict[str, object]]:
    """Normalized controller relations shared by machine-readable surfaces.

    Moved from ``__main__.py`` (module-size split, copilot-extensions#2614):
    lives alongside ``WorktreeRecord``/``controller_relation_to_dict`` rather
    than being re-imported back from the CLI entry point by sibling CLI
    modules (e.g. ``session_tracking_cli.py``).
    """
    return [controller_relation_to_dict(relation) for relation in rec.controllers]


def _controller_findings(
    rec: WorktreeRecord,
) -> list[dict[str, object]]:
    """Derived terminal-controller findings shared by JSON surfaces."""
    from . import controller_lineage

    try:
        return controller_lineage.controller_findings(rec)
    except Exception:
        return []


def _repo_for_record(config, record):
    """Resolve a record's repository with the legacy/default fallback.

    Moved from ``__main__.py`` (module-size split, copilot-extensions#2614):
    a pure ``WorktreeRecord``-shaped helper with no entry-point dependency.
    """
    repos = getattr(config, "repos", {})
    record_repo = getattr(record, "repo", "")
    repo = repos.get(record_repo) if hasattr(repos, "get") else None
    if repo is None and (not record_repo or record_repo == getattr(config, "repo_name", None)):
        try:
            repo = config.default_repo
        except (AttributeError, KeyError, ValueError):
            repo = None
    return repo


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# In-process per-YAML write serialization (dotfiles#948 follow-up). The picker's
# background populate/repoll threads stamp the session-render cache while a
# foreground op (e.g. a resume's ``mark_resumed``) writes the SAME record -- all
# in the one picker process. On Windows a concurrent temp+replace collides
# (``WinError 32``/``5``), so serialize every write to a given path with an
# in-process lock keyed by the normalized path. Cross-PROCESS races (a separate
# CLI writing the same YAML) are the rarer case, still covered by the atomic
# replace's bounded retry below.
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_THREAD_SIDECARS = threading.local()


def _thread_sidecar_counts() -> dict[str, int]:
    counts = getattr(_THREAD_SIDECARS, "counts", None)
    if counts is None:
        counts = {}
        _THREAD_SIDECARS.counts = counts
    return counts


def _path_write_lock(path: Path) -> threading.RLock:
    key = os.path.normcase(os.path.abspath(str(path)))
    with _PATH_LOCKS_GUARD:
        lk = _PATH_LOCKS.get(key)
        if lk is None:
            lk = threading.RLock()  # re-entrant: _RecordLock + nested _atomic_write
            _PATH_LOCKS[key] = lk
        return lk


def _atomic_write(path: Path, content: str) -> None:
    """Write content to a file atomically via temp + atomic replace.

    Serialized per path by an in-process lock (so the picker's own background
    stamp threads and a foreground write never collide), then ``os.replace``
    (atomic even over an existing target on Windows) with a bounded retry on a
    transient Windows sharing violation (``WinError 32``/``5`` /
    ``PermissionError``) for the rarer cross-process race. Without this a
    foreground write -- e.g. a Picker resume's ``mark_resumed`` -- failed hard
    with "the process cannot access the file because it is being used by another
    process". POSIX has no such sharing restriction.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with _path_write_lock(path):
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            os.write(fd, content.encode())
            os.close(fd)
            _replace_with_retry(tmp, str(path))
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise


def _replace_with_retry(src: str, dst: str, *, attempts: int = 20,
                        delay: float = 0.05) -> None:
    """``os.replace(src, dst)`` with a bounded, jittered retry on a transient
    Windows sharing violation. Raises the last error if every attempt loses the
    race."""
    import random as _random
    import time as _time
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            # WinError 32/5: the target is momentarily held open by a concurrent
            # reader/writer. Back off briefly and retry -- the holder's op is
            # short (a read or a temp+replace), so a few retries win the race.
            # Jitter breaks lockstep with a steadily-looping reader/writer.
            if i == attempts - 1:
                raise
            _time.sleep(delay + _random.uniform(0, delay))


def _read_text_with_retry(path: Path, *, attempts: int = 20,
                          delay: float = 0.05) -> str:
    """Read a tracking YAML's text, minimizing the reader's handle-hold window
    (read bytes then close BEFORE parsing) with a bounded retry on a transient
    Windows sharing violation.

    dotfiles#948 follow-up: a reader that holds the file handle open across the
    (slow) YAML parse widens the window in which a concurrent ``os.replace``
    (a foreground save or a background stamp) collides with it -- and the reader
    itself can momentarily see ``WinError 32``/``5`` while the destination is
    being swapped. Reading the whole file up front shrinks the collision window
    to a few milliseconds; the retry covers the rare transient failure. POSIX has
    no such sharing restriction, so this is effectively a no-op there.
    """
    import random as _random
    import time as _time
    for i in range(attempts):
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except PermissionError:
            if i == attempts - 1:
                raise
            _time.sleep(delay + _random.uniform(0, delay))


def _parse_pr_mapping(raw: dict, default_repo: str) -> PRRecord:
    """Parse one PR mapping (from a ``prs:`` item or legacy ``pr:`` block)."""
    num = raw.get("number")
    if num in (None, "", "null"):
        num_val: int | None = None
    else:
        try:
            num_val = int(num)
        except (TypeError, ValueError):
            num_val = None
    pr_id_raw = raw.get("pr_id", "")
    try:
        pr_revision = _bounded_nonnegative_int(
            raw.get("pr_revision", 0), field="pr_revision",
        )
    except (TypeError, ValueError, OverflowError):
        pr_revision = 0
    return PRRecord(
        state=str(raw.get("state", "")),
        branch=str(raw.get("branch", "")),
        base_sha=str(raw.get("base_sha", "")),
        head_sha=str(raw.get("head_sha", "")),
        head_observed_at=str(raw.get("head_observed_at", "")),
        head_observed_api_base=str(raw.get("head_observed_api_base", "")),
        attribution_head=str(raw.get("attribution_head", "")),
        patch_id=str(raw.get("patch_id", "")),
        url=str(raw.get("url", "")),
        number=num_val,
        provider=str(raw.get("provider", "")),
        # A legacy record without a per-PR repo targets the worktree's repo.
        repo=str(raw.get("repo", "")) or default_repo,
        opened_at=str(raw.get("opened_at", "")),
        closed_at=str(raw.get("closed_at", "")),
        **_parse_frozen_attribution_pair(raw),
        pr_id=pr_id_raw if isinstance(pr_id_raw, str) else "",
        pr_revision=pr_revision,
    )


#: The closed set of valid persisted ``attribution_mode`` values -- this is
#: STRICT READ-ONLY validation (round-30 finding), never round-tripped
#: as-is: anything outside this set (a hand-edited typo, a future value
#: this code doesn't know about) is migrated exactly like a missing value,
#: never accepted as authorizing publication.
_VALID_ATTRIBUTION_MODES = frozenset({"", "false", "true", "codename"})


def _parse_frozen_attribution_pair(raw: dict) -> dict[str, object]:
    """Strictly validate the persisted ``attribution_mode``/
    ``attribution_explicit`` pair (round-30 finding, corrected round-33)
    -- the same never-treat-unknown-as-safe discipline round-12
    established for ``codename_source``, applied here to a PAIR of fields
    that must migrate together:

    * ``attribution_explicit`` is read via a STRICT boolean check -- a
      value that is not literally a Python ``bool`` (a hand-edited
      ``attribution_explicit: "false"``, a truthy STRING) invalidates the
      WHOLE pair back to the empty legacy sentinel, not just itself
      (fix-PR-#3037-review finding): a naive "coerce non-True to False"
      would leave `attribution_mode` VALID while only neutralizing
      `attribution_explicit` -- but the raw-marker (`"true"`) mode
      publishes unconditionally on mode alone, never consulting
      `attribution_explicit` first, so that shape would still let a
      malformed record authorize a privacy-sensitive marker.
    * ``attribution_mode`` is validated against the closed
      ``_VALID_ATTRIBUTION_MODES`` set -- an unrecognized value is
      migrated exactly like a missing one (never read as one of the
      three known modes, never perpetually re-derived from live config
      on every load).
    * A PARTIAL pair (only one of the two fields present) is ALSO treated
      as the empty legacy sentinel -- never let a half-written record
      produce a mode without its matching explicitness, or an
      explicitness without its matching mode.

    Returns a dict of the two fields' constructor kwargs (empty-string
    mode + ``False`` explicitness for every "not a clean, complete,
    known-valid pair" case), for the empty-legacy-sentinel migration path
    ``refresh_source_attribution``/``_open_via_provider`` lazily freezes
    on first touch.
    """
    mode_present = "attribution_mode" in raw
    explicit_present = "attribution_explicit" in raw
    if not (mode_present and explicit_present):
        return {"attribution_mode": "", "attribution_explicit": False}
    mode_raw = raw.get("attribution_mode")
    mode = mode_raw if isinstance(mode_raw, str) else ""
    if mode not in _VALID_ATTRIBUTION_MODES:
        return {"attribution_mode": "", "attribution_explicit": False}
    explicit_raw = raw.get("attribution_explicit")
    if not isinstance(explicit_raw, bool):
        return {"attribution_mode": "", "attribution_explicit": False}
    return {"attribution_mode": mode, "attribution_explicit": explicit_raw}


def _pr_to_yaml_dict(pr: PRRecord) -> dict[str, object]:
    """Serialize a PRRecord to a YAML-friendly mapping (lean: omit empties)."""
    d: dict[str, object] = {
        "state": pr.state,
        "branch": pr.branch,
        "base_sha": pr.base_sha,
        "head_sha": pr.head_sha,
        "url": pr.url,
    }
    if pr.head_observed_at:
        d["head_observed_at"] = pr.head_observed_at
    if pr.head_observed_api_base:
        d["head_observed_api_base"] = pr.head_observed_api_base
    if pr.patch_id:
        d["patch_id"] = pr.patch_id
    if pr.attribution_head:
        d["attribution_head"] = pr.attribution_head
    if pr.number is not None:
        d["number"] = pr.number
    d["provider"] = pr.provider
    if pr.repo:
        d["repo"] = pr.repo
    if pr.opened_at:
        d["opened_at"] = pr.opened_at
    if pr.closed_at:
        d["closed_at"] = pr.closed_at
    # codename-attribution-by-default: emit `attribution_explicit` whenever
    # `attribution_mode` is non-empty, NOT only when `attribution_explicit`
    # is itself truthy (round-34 finding) -- a `False` explicitness is a
    # legitimately-frozen state (e.g. an implicit `codename` decision), and
    # omitting it would strand `attribution_mode` without its partner on
    # reload, silently re-triggering the lazy-backfill freeze.
    if pr.attribution_mode:
        d["attribution_mode"] = pr.attribution_mode
        d["attribution_explicit"] = pr.attribution_explicit
    if pr.pr_id:
        d["pr_id"] = pr.pr_id
    if pr.pr_revision:
        d["pr_revision"] = pr.pr_revision
    return d



def _parse_claim_mapping(raw: dict) -> ResourceClaim:
    """Parse one ResourceClaim mapping from a ``resources:`` list item."""
    kind = str(raw.get("kind", "worktree")) or "worktree"
    state_raw = raw.get("state")
    state = state_raw if state_raw in (
        "active", "at-rest", "released", "abandoned") else "active"
    return ResourceClaim(
        kind=kind,
        ref=str(raw.get("ref", "")),
        created_at=str(raw.get("created_at", "")),
        state=state,
        note=str(raw.get("note", "")),
        handoff_bundle=str(raw.get("handoff_bundle", "")),
    )


def _claim_to_yaml_dict(claim: ResourceClaim) -> dict[str, object]:
    """Serialize a ResourceClaim to a YAML-friendly mapping (omit empties)."""
    d: dict[str, object] = {"kind": claim.kind, "ref": claim.ref}
    if claim.created_at:
        d["created_at"] = claim.created_at
    # Emit state only when it deviates from the default ("active") to keep the
    # common-case entry lean.
    if claim.state and claim.state != "active":
        d["state"] = claim.state
    if claim.note:
        d["note"] = claim.note
    if claim.handoff_bundle:
        d["handoff_bundle"] = claim.handoff_bundle
    return d


def derive_execution_leg(record: WorktreeRecord) -> ExecutionLegBinding | None:
    """Compute the generic execution-leg view for one worktree record.

    Prefers an actual on-disk ``execution_leg:`` when present. Otherwise
    translates a legacy AHP ``session_backend`` binding into the generic
    provider-id-plus-opaque-blob shape on demand -- a pure, read-time
    compatibility view, never stored back to the record or serialized.
    Returns ``None`` when the record's ``session_backend`` is an opaque
    (unrecognized) shape, since agent-worktrees cannot honestly name a
    provider for it. See
    ``efforts/active/worktree-manager-control-plane/phase-3b-ahp-relocation.md``
    (Phase 3b Slice 1) for the migration this groundwork serves.
    """
    execution_leg = getattr(record, "execution_leg", None)
    if execution_leg is not None:
        return execution_leg
    if getattr(record, "execution_leg_opaque", False):
        return None
    backend = getattr(record, "session_backend", None)
    if backend is None or getattr(record, "session_backend_opaque", False):
        return None
    return ExecutionLegBinding(
        provider=backend.kind,
        state=backend.state,
        binding_revision=backend.binding_revision,
        blob={
            "endpoint_url": backend.endpoint_url,
            "session_id": backend.session_id,
            "protocol_version": backend.protocol_version,
            "auth_account": backend.auth_account,
            "created_at": backend.created_at,
            "last_seen_at": backend.last_seen_at,
        },
    )


#: The resident status-monitor calls `load_record` for every registered
#: worktree on every sweep interval (copilot-extensions#2615): `yaml.safe_load`
#: always uses PyYAML's pure-Python `SafeLoader`, even when the much faster
#: libyaml-backed `CSafeLoader` is available, which py-spy profiling showed
#: accounted for ~86% of the daemon's sampled CPU time. Prefer `CSafeLoader`
#: where the C extension is present; fall back to the pure-Python loader on a
#: host without libyaml bindings (e.g. some non-Windows/non-x64 builds).
_FastSafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def _yaml_safe_load(raw: str) -> object:
    """`yaml.safe_load`, but via the C-accelerated loader when available."""
    return yaml.load(raw, Loader=_FastSafeLoader)


def load_record(path: Path) -> WorktreeRecord:
    """Load a worktree tracking record from a YAML file."""
    raw = _read_text_with_retry(path)
    try:
        data = _yaml_safe_load(raw)
    except yaml.reader.ReaderError:
        # alice_example/dotfiles#1789: a stray C0 control char (e.g. BEL)
        # persisted into a
        # value makes the YAML reader raise on every load, wedging all future
        # disposition writes. Self-heal by stripping the illegal control chars
        # and re-parsing; the next save then rewrites the file cleanly. Do not
        # reinterpret unrelated malformed YAML as a repairable record.
        repaired = _strip_control_chars(raw)
        if repaired == raw:
            raise
        data = _yaml_safe_load(repaired)

    if not isinstance(data, dict):
        raise yaml.YAMLError("worktree tracking record must be a YAML mapping")

    title = data.get("title")
    if title == "null" or title is None:
        title = None

    started_at_raw = data.get("started_at", "")
    if hasattr(started_at_raw, "isoformat"):
        started_at_raw = started_at_raw.isoformat()

    last_resumed_raw = data.get("last_resumed_at", "")
    if hasattr(last_resumed_raw, "isoformat"):
        last_resumed_raw = last_resumed_raw.isoformat()

    completed_raw = data.get("completed_at")
    if completed_raw == "null" or completed_raw is None:
        completed_raw = None
    elif hasattr(completed_raw, "isoformat"):
        completed_raw = completed_raw.isoformat()

    # worktree-finality-and-obligations: same YAML-parses-bare-timestamp
    # gotcha as completed_at above.
    last_finalized_raw = data.get("last_finalized_at")
    if hasattr(last_finalized_raw, "isoformat"):
        last_finalized_raw = last_finalized_raw.isoformat()

    # #4057: YAML may parse an ISO timestamp into a datetime -- normalize back to
    # an isoformat string (mirrors started_at/last_resumed_at handling) so the
    # stamp round-trips as text, not "2026-07-31 20:00:00".
    mux_live_at_raw = data.get("mux_live_at")
    if hasattr(mux_live_at_raw, "isoformat"):
        mux_live_at_raw = mux_live_at_raw.isoformat()
    elif mux_live_at_raw in (None, "", "null"):
        mux_live_at_raw = None

    # #4057/#1416: same datetime->isoformat normalization for the bound-Copilot
    # liveness stamp so it round-trips as text.
    bound_live_at_raw = data.get("bound_live_at")
    if hasattr(bound_live_at_raw, "isoformat"):
        bound_live_at_raw = bound_live_at_raw.isoformat()
    elif bound_live_at_raw in (None, "", "null"):
        bound_live_at_raw = None

    # picker-cache-first-paint (dotfiles#948): datetime->isoformat normalization
    # for the session-render-cache freshness stamp so it round-trips as text.
    session_state_at_raw = data.get("session_state_at")
    if hasattr(session_state_at_raw, "isoformat"):
        session_state_at_raw = session_state_at_raw.isoformat()
    elif session_state_at_raw in (None, "", "null"):
        session_state_at_raw = None

    # Parse sessions list -- None means "not yet indexed" (pre-registry),
    # [] means "indexed, no sessions recorded".  This distinction drives
    # fallback: None -> full scan, [] -> skip scan.
    raw_sessions = data.get("sessions")
    sessions_list: list[SessionEntry] | None = None
    if raw_sessions is not None:
        sessions_list = []
        if isinstance(raw_sessions, list):
            for entry in raw_sessions:
                if isinstance(entry, dict) and "session_id" in entry:
                    sa = entry.get("started_at", "")
                    if hasattr(sa, "isoformat"):
                        sa = sa.isoformat()
                    ea = entry.get("ended_at")
                    if ea and hasattr(ea, "isoformat"):
                        ea = ea.isoformat()
                    elif ea == "null" or ea is None:
                        ea = None
                    # session-lifecycle: state + two-way links. Unknown/absent
                    # state degrades to "active" so a stray value never hides a
                    # resumable session.
                    st_raw = entry.get("state")
                    st_val: SessionState = (
                        st_raw
                        if st_raw in ("active", "yielded", "handed-off", "concluded")
                        else "active")
                    succ = entry.get("successor")
                    pred = entry.get("predecessor")
                    pane = entry.get("pane_id")
                    activations: list[SessionActivation] = []
                    raw_activations = entry.get("activations")
                    if isinstance(raw_activations, list):
                        for raw_activation in raw_activations:
                            if not isinstance(raw_activation, dict):
                                continue
                            act_started = raw_activation.get("started_at", "")
                            if act_started in (None, "", "null"):
                                continue
                            if hasattr(act_started, "isoformat"):
                                act_started = act_started.isoformat()
                            act_ended = raw_activation.get("ended_at")
                            if hasattr(act_ended, "isoformat"):
                                act_ended = act_ended.isoformat()
                            elif act_ended in (None, "", "null"):
                                act_ended = None
                            start_recorded = raw_activation.get(
                                "start_recorded_at", act_started
                            )
                            if start_recorded in (None, "", "null"):
                                start_recorded = act_started
                            if hasattr(start_recorded, "isoformat"):
                                start_recorded = start_recorded.isoformat()
                            end_recorded = raw_activation.get("end_recorded_at")
                            if hasattr(end_recorded, "isoformat"):
                                end_recorded = end_recorded.isoformat()
                            elif end_recorded in (None, "", "null"):
                                end_recorded = None
                            try:
                                ordinal = int(raw_activation.get("ordinal", 0))
                            except (TypeError, ValueError):
                                ordinal = 0
                            if ordinal <= 0:
                                ordinal = len(activations) + 1
                            activations.append(SessionActivation(
                                ordinal=ordinal,
                                started_at=str(act_started),
                                start_recorded_at=str(start_recorded or act_started),
                                start_source=str(
                                    raw_activation.get("start_source") or "hook"
                                ),
                                ended_at=str(act_ended) if act_ended else None,
                                end_recorded_at=(
                                    str(end_recorded) if end_recorded else None
                                ),
                                end_source=(
                                    str(raw_activation["end_source"])
                                    if raw_activation.get("end_source") else None
                                ),
                            ))
                    sessions_list.append(SessionEntry(
                        session_id=str(entry["session_id"]),
                        started_at=str(sa),
                        pid=int(entry["pid"]) if entry.get("pid") else None,
                        ended_at=str(ea) if ea else None,
                        state=st_val,
                        successor=str(succ) if succ else None,
                        predecessor=str(pred) if pred else None,
                        pane_id=str(pane) if pane else None,
                        activations=activations,
                        relation_revision=_bounded_nonnegative_int(
                            entry.get("relation_revision", 0),
                            field="session relation_revision",
                        ),
                    ))

    raw_session_backend = data.get("session_backend")
    session_backend: SessionBackendBinding | None = None
    session_backend_opaque = False
    if raw_session_backend is not None:
        session_backend_opaque = True
    if isinstance(raw_session_backend, dict):
        if raw_session_backend.get("version", 1) != 1:
            pass
        elif raw_session_backend.get("kind") == "ahp":
            required = (
                "endpoint_url",
                "session_id",
                "protocol_version",
                "auth_account",
                "created_at",
                "last_seen_at",
            )
            if all(raw_session_backend.get(name) for name in required):
                state = str(raw_session_backend.get("state", "active"))
                if state not in {"active", "disposed", "unknown"}:
                    state = "unknown"
                session_backend = SessionBackendBinding(
                    kind="ahp",
                    endpoint_url=str(raw_session_backend["endpoint_url"]),
                    session_id=str(raw_session_backend["session_id"]),
                    protocol_version=str(
                        raw_session_backend["protocol_version"]
                    ),
                    auth_account=str(raw_session_backend["auth_account"]),
                    created_at=str(raw_session_backend["created_at"]),
                    last_seen_at=str(raw_session_backend["last_seen_at"]),
                    state=state,
                    binding_revision=_bounded_nonnegative_int(
                        raw_session_backend.get("binding_revision", 1),
                        field="session backend binding_revision",
                    ),
                )
                session_backend_opaque = False

    # Generic ``execution_leg:`` parsing (session-hosting vision). Parsed
    # independently of ``session_backend`` above -- this key is not written by
    # any shipping code yet, so this block only guards against a future
    # writer's shape. agent-worktrees interprets only ``provider``, ``state``,
    # and ``binding_revision``; ``blob`` is never inspected.
    raw_execution_leg = data.get("execution_leg")
    execution_leg: ExecutionLegBinding | None = None
    execution_leg_opaque = False
    if raw_execution_leg is not None:
        execution_leg_opaque = True
    if isinstance(raw_execution_leg, dict):
        if raw_execution_leg.get("version", 1) != 1:
            pass
        else:
            provider = raw_execution_leg.get("provider")
            blob = raw_execution_leg.get("blob", {})
            if (
                isinstance(provider, str)
                and provider
                and isinstance(blob, dict)
            ):
                state = str(raw_execution_leg.get("state", "active"))
                if state not in {"active", "disposed", "unknown"}:
                    state = "unknown"
                execution_leg = ExecutionLegBinding(
                    provider=provider,
                    state=state,
                    binding_revision=_bounded_nonnegative_int(
                        raw_execution_leg.get("binding_revision", 1),
                        field="execution leg binding_revision",
                    ),
                    blob=dict(blob),
                )
                execution_leg_opaque = False

    # Parse PR records -- the multi-PR ``prs:`` list (preferred) or a legacy
    # single ``pr:`` mapping (loaded as a one-element list).  Absent in
    # non-PR worktrees.
    default_repo = data.get("repo") or cfg.project_name()
    prs_list: list[PRRecord] = []
    raw_prs = data.get("prs")
    if isinstance(raw_prs, list):
        for raw in raw_prs:
            if isinstance(raw, dict):
                prs_list.append(_parse_pr_mapping(raw, default_repo))
    elif isinstance(data.get("pr"), dict):
        prs_list.append(_parse_pr_mapping(data["pr"], default_repo))

    # Owner class -- absent (legacy records) defaults to "session". Unknown
    # values degrade to "session" so a stray kind can never hide a real worktree.
    kind_raw = data.get("kind")
    kind_val: WorktreeKind = kind_raw if kind_raw in ("system", "bridge") else "session"
    owner_raw = data.get("owner")
    if owner_raw in (None, "", "null"):
        owner_raw = None

    # #2668: the two orthogonal marks. Absent (legacy) or unknown values stay
    # None so the resolved_* properties derive them from kind.
    iface_raw = data.get("interface")
    iface_val: WorktreeInterface | None = (
        iface_raw if iface_raw in ("cli", "acp") else None)
    origin_raw = data.get("origin")
    origin_val: WorktreeOrigin | None = (
        origin_raw if origin_raw in ("user", "system", "delegate") else None)
    dispatch_attempt_raw_present = "dispatch_attempt" in data
    dispatch_attempt_raw = data.get("dispatch_attempt")
    dispatch_attempt = _dispatch_attempt_from_mapping(dispatch_attempt_raw)
    dispatch_attempt_opaque = (
        dispatch_attempt_raw_present and dispatch_attempt is None
    )

    controller_metadata_opaque = False
    controllers_list: list[ControllerRelation] = []
    raw_controllers = data.get("controllers")
    if "controllers" in data and not isinstance(raw_controllers, list):
        controller_metadata_opaque = True
    if isinstance(raw_controllers, list):
        for raw in raw_controllers:
            try:
                if not isinstance(raw, dict):
                    raise ControllerRelationError("controller entry must be a mapping")
                allowed_keys = {
                    "kind",
                    "source",
                    "controller_ref",
                    "controller_session_id",
                    "state",
                    "relation_revision",
                    "created_at",
                    "ended_at",
                }
                if set(raw) - allowed_keys:
                    controller_metadata_opaque = True
                raw_kind = raw.get("kind")
                if raw_kind not in ("worktree", "session"):
                    raise ControllerRelationError("invalid controller kind")
                source = raw.get("source")
                if source not in ("explicit", "owner-ref", "caller-worktree", "parent-session"):
                    raise ControllerRelationError("invalid controller source")
                ref = raw.get("controller_ref")
                sid = raw.get("controller_session_id")
                if ref is not None and not isinstance(ref, str):
                    raise ControllerRelationError("controller_ref must be a string")
                if sid is not None and not isinstance(sid, str):
                    raise ControllerRelationError("controller_session_id must be a string")
                if ref:
                    ref, parsed = _normalize_controller_ref(ref, session_id=sid)
                    sid = parsed.session
                elif not sid or not _valid_relation_session_id(sid):
                    raise ControllerRelationError("controller identity is required")
                derived_kind = "worktree" if ref else "session"
                if raw_kind != derived_kind:
                    raise ControllerRelationError(
                        "controller kind does not match its identity"
                    )
                revision = _bounded_nonnegative_int(
                    raw.get("relation_revision", 0), field="controller relation_revision"
                )
                if revision <= 0 or raw.get("state", "active") not in ("active", "ended"):
                    raise ControllerRelationError("invalid controller relation state")
                created = raw.get("created_at") or started_at_raw
                ended = raw.get("ended_at")
                controllers_list.append(ControllerRelation(
                    kind=derived_kind, source=source,
                    controller_ref=ref, controller_session_id=sid,
                    state=raw.get("state", "active"), relation_revision=revision,
                    created_at=str(created), ended_at=str(ended) if ended else None,
                ))
            except (ControllerRelationError, TypeError, ValueError, OverflowError):
                controller_metadata_opaque = True
                continue
    # agent-fabric resource-claims: the forward outbound list. Absent in
    # worktrees that own nothing (the common case), so legacy records parse to
    # an empty list and re-serialize byte-identically.
    resources_list: list[ResourceClaim] = []
    raw_resources = data.get("resources")
    if isinstance(raw_resources, list):
        for raw in raw_resources:
            if isinstance(raw, dict) and raw.get("ref"):
                resources_list.append(_parse_claim_mapping(raw))

    # worktree-finality-and-obligations Phase 3: itemized follow-up ledger.
    # Absent in un-annotated worktrees, so legacy records parse to an empty
    # list and re-serialize byte-identically.
    follow_ups_list: list[FollowUpRecord] = []
    raw_follow_ups = data.get("follow_ups")
    if isinstance(raw_follow_ups, list):
        for raw in raw_follow_ups:
            if isinstance(raw, dict) and raw.get("id"):
                follow_ups_list.append(FollowUpRecord.from_dict(raw))

    head_transitions: list[HeadTransition] = []
    raw_transitions = data.get("head_transitions")
    if isinstance(raw_transitions, list):
        for raw in raw_transitions:
            if not isinstance(raw, dict):
                continue
            try:
                revision = int(raw.get("revision", 0))
            except (TypeError, ValueError):
                continue
            if revision <= 0:
                continue
            at = raw.get("at", "")
            if hasattr(at, "isoformat"):
                at = at.isoformat()
            handoff_ordinal = raw.get("handoff_ordinal")
            try:
                handoff_ordinal = (
                    int(handoff_ordinal) if handoff_ordinal is not None else None
                )
            except (TypeError, ValueError):
                handoff_ordinal = None
            session_value = raw.get("session_id")
            head_transitions.append(HeadTransition(
                revision=revision,
                session_id=(
                    str(session_value) if session_value not in (None, "", "null")
                    else None
                ),
                reason=str(raw.get("reason") or "unknown"),
                at=str(at),
                handoff_ordinal=handoff_ordinal,
            ))

    handoffs: list[SessionHandoff] = []
    raw_handoffs = data.get("handoffs")
    if isinstance(raw_handoffs, list):
        for raw in raw_handoffs:
            if not isinstance(raw, dict) or not raw.get("token"):
                continue
            try:
                ordinal = int(raw.get("ordinal", 0))
            except (TypeError, ValueError):
                continue
            if ordinal <= 0:
                continue
            state = raw.get("state")
            if state not in ("pending", "linked", "cancelled"):
                state = "pending"
            opened_at = raw.get("opened_at", "")
            linked_at = raw.get("linked_at")
            if hasattr(opened_at, "isoformat"):
                opened_at = opened_at.isoformat()
            if hasattr(linked_at, "isoformat"):
                linked_at = linked_at.isoformat()
            elif linked_at in (None, "", "null"):
                linked_at = None
            candidate_at = raw.get("candidate_at")
            if hasattr(candidate_at, "isoformat"):
                candidate_at = candidate_at.isoformat()
            elif candidate_at in (None, "", "null"):
                candidate_at = None
            handoffs.append(SessionHandoff(
                ordinal=ordinal,
                token=str(raw["token"]),
                predecessor=str(raw.get("predecessor") or ""),
                state=state,
                opened_at=str(opened_at),
                successor=(
                    str(raw["successor"]) if raw.get("successor") else None
                ),
                linked_at=str(linked_at) if linked_at else None,
                candidate=(
                    str(raw["candidate"]) if raw.get("candidate") else None
                ),
                candidate_at=str(candidate_at) if candidate_at else None,
            ))

    profile_assignments: list[ProfileAssignment] = []
    raw_assignments = data.get("profile_assignments")
    if isinstance(raw_assignments, list):
        for raw in raw_assignments:
            if not isinstance(raw, dict):
                continue
            policy = str(raw.get("policy") or "")
            selected_profile = str(raw.get("selected_profile") or "")
            assigned_at = raw.get("assigned_at") or ""
            if hasattr(assigned_at, "isoformat"):
                assigned_at = assigned_at.isoformat()
            if not (policy and selected_profile and assigned_at):
                continue
            disposition = raw.get("disposition")
            if disposition not in ("pending", "bound", "abandoned"):
                disposition = "pending"
            abandoned_at = raw.get("abandoned_at")
            if hasattr(abandoned_at, "isoformat"):
                abandoned_at = abandoned_at.isoformat()
            elif abandoned_at in (None, "", "null"):
                abandoned_at = None
            try:
                generation = _bounded_nonnegative_int(
                    raw.get("bag_generation"),
                    field="profile_assignments[].bag_generation",
                )
                position = _bounded_nonnegative_int(
                    raw.get("bag_position"),
                    field="profile_assignments[].bag_position",
                )
            except (TypeError, ValueError, OverflowError):
                continue
            profile_assignments.append(ProfileAssignment(
                policy=policy,
                assignment_label=str(raw.get("assignment_label") or ""),
                selected_profile=selected_profile,
                bag_generation=generation,
                bag_position=position,
                assigned_at=str(assigned_at),
                disposition=disposition,
                session_id=(
                    str(raw["session_id"]) if raw.get("session_id") else None
                ),
                lane=str(raw.get("lane") or ""),
                abandoned_at=(
                    str(abandoned_at) if abandoned_at else None
                ),
                bound_at=(
                    str(raw["bound_at"]) if raw.get("bound_at") else None
                ),
                predecessor_session_id=(
                    str(raw["predecessor_session_id"])
                    if raw.get("predecessor_session_id")
                    else None
                ),
            ))

    try:
        profile_assignment_revision = _bounded_nonnegative_int(
            data.get("profile_assignment_revision", 0),
            field="profile_assignment_revision",
        )
    except (TypeError, ValueError, OverflowError):
        profile_assignment_revision = 0
        profile_assignments = []

    try:
        controller_revision = _bounded_nonnegative_int(
            data.get("controller_revision", 0),
            field="controller_revision",
        )
        controller_revision = max(
            controller_revision,
            max(
                (
                    relation.relation_revision
                    for relation in controllers_list
                ),
                default=0,
            ),
        )
        _validate_controller_relation_set(controllers_list)
        controllers_list, _removed = _limit_controller_relations(
            controllers_list
        )
    except (
        ControllerRelationError,
        TypeError,
        ValueError,
        OverflowError,
    ):
        controller_metadata_opaque = True
        controller_revision = max(
            (
                relation.relation_revision
                for relation in controllers_list
            ),
            default=0,
        )
    if controller_metadata_opaque:
        try:
            _validate_controller_relation_set(controllers_list)
        except ControllerRelationError:
            controllers_list = []

    record = WorktreeRecord(
        worktree_id=data["worktree_id"],
        branch=data["branch"],
        worktree_path=data.get("worktree_path", ""),
        repo=default_repo,
        machine=data.get("machine", ""),
        platform=data.get("platform", ""),
        started_at=str(started_at_raw),
        last_resumed_at=str(last_resumed_raw),
        resume_count=int(data.get("resume_count", 0)),
        title=title,
        status=data.get("status", "active"),
        completed_at=str(completed_raw) if completed_raw else None,
        sessions=sessions_list,
        session_backend=session_backend,
        session_backend_opaque=session_backend_opaque,
        session_backend_raw=raw_session_backend,
        execution_leg=execution_leg,
        execution_leg_opaque=execution_leg_opaque,
        execution_leg_raw=raw_execution_leg,
        prs=prs_list,
        kind=kind_val,
        owner=str(owner_raw) if owner_raw else None,
        interface=iface_val,
        origin=origin_val,
        dispatch_attempt=dispatch_attempt,
        dispatch_attempt_opaque=dispatch_attempt_opaque,
        dispatch_attempt_raw=dispatch_attempt_raw,
        dispatch_attempt_raw_present=dispatch_attempt_raw_present,
        checkout_managed=data.get("checkout_managed", True) is not False,
        parent_session=(str(data["parent_session"])
                        if data.get("parent_session") else None),
        head_session=(str(data["head_session"])
                      if data.get("head_session") else None),
        lifecycle_revision=int(data.get("lifecycle_revision", 0) or 0),
        head_revision=int(data.get("head_revision", 0) or 0),
        head_transitions=head_transitions,
        handoff_counter=int(data.get("handoff_counter", 0) or 0),
        handoffs=handoffs,
        profile_assignment_revision=profile_assignment_revision,
        profile_assignments=profile_assignments[-_MAX_PROFILE_ASSIGNMENTS:],
        controller_revision=controller_revision,
        controllers=controllers_list,
        controller_metadata_opaque=controller_metadata_opaque,
        controller_raw_revision=data.get("controller_revision"),
        controller_raw_entries=raw_controllers,
        controller_raw_revision_present=("controller_revision" in data),
        controller_raw_entries_present=("controllers" in data),
        caller_worktree=(str(data["caller_worktree"])
                         if data.get("caller_worktree") else None),
        owner_ref=(str(data["owner_ref"])
                   if data.get("owner_ref") else None),
        resources=resources_list,
        bound_agent=(str(data["bound_agent"]).strip() or None
                     if data.get("bound_agent") else None),
        follow_up=bool(data.get("follow_up", False)),
        follow_ups=follow_ups_list,
        summary=str(data.get("summary", "") or ""),
        active_effort=active_effort_from_mapping(data.get("active_effort")),
        effort_revision=int(data.get("effort_revision", 0) or 0),
        title_asserted=bool(data.get("title_asserted", False)),
        status_note_at=(str(data["status_note_at"])
                        if data.get("status_note_at") else None),
        last_finalized_at=(str(last_finalized_raw) if last_finalized_raw else None),
        mux_live=(bool(data["mux_live"])
                  if data.get("mux_live") is not None else None),
        mux_live_at=(str(mux_live_at_raw) if mux_live_at_raw else None),
        bound_live=(bool(data["bound_live"])
                    if data.get("bound_live") is not None else None),
        bound_live_at=(str(bound_live_at_raw) if bound_live_at_raw else None),
        session_turns=(int(data["session_turns"])
                       if data.get("session_turns") is not None else None),
        session_summary=(str(data["session_summary"])
                         if data.get("session_summary") else None),
        git_state=(str(data["git_state"])
                   if data.get("git_state") else None),
        session_state_at=(str(session_state_at_raw)
                          if session_state_at_raw else None),
        pair_id=(str(data["pair_id"]) if data.get("pair_id") else None),
        pair_role=(data["pair_role"]
                   if data.get("pair_role") in ("harness", "knowledge") else None),
        pair_ref=(str(data["pair_ref"]) if data.get("pair_ref") else None),
        pair_kind=(data["pair_kind"]
                   if data.get("pair_kind") in ("worktree", "anchor") else None),
        reaped_at=(str(data["reaped_at"]) if data.get("reaped_at") else None),
        codename=(str(data["codename"]) if data.get("codename") else None),
        codename_source=(str(data["codename_source"])
                         if data.get("codename_source") else None),
    )
    record._loaded_from = path
    return record


def resolve_worktree_path(worktree_id: str, worktree_root: str) -> str:
    """Return the authoritative on-disk path for ``worktree_id``.

    The tracking record's recorded ``worktree_path`` is the source of truth:
    it stays correct even when the default worktree layout changes (e.g. a
    worktree created under the older ``<srcroot>/.worktrees/<project>/`` scheme
    remains reachable after the default moved to ``<anchor>.worktrees/``).

    Resolution order (#3026):
      1. the tracking record's ``worktree_path``, when the record exists and
         that path is present and exists on disk;
      2. otherwise the ``worktree_root / worktree_id`` derivation -- the
         fallback for untracked worktrees or a record missing the path.

    The derivation is returned as-is when nothing better is found, so callers'
    existing "path not found" checks still fire for a genuinely-absent worktree.
    """
    record_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if record_path.exists():
        try:
            record = load_record(record_path)
        except Exception:
            record = None
        if record and record.worktree_path:
            recorded = Path(record.worktree_path)
            if recorded.exists():
                return str(recorded)
    return str(Path(worktree_root) / worktree_id)


def _yaml_scalar(v: str) -> str:
    """Render a string scalar for the hand-rolled record YAML, quoting only when
    a plain scalar would be mis-tokenized.

    The reserved ``@anchor`` owner id (and any value with a leading YAML
    indicator character) would otherwise be emitted bare and crash
    ``yaml.safe_load`` on read (``found character '@' that cannot start any
    token``). Values that start with a letter/digit -- every real
    worktree_id/branch/ref today -- are returned unquoted, so existing records
    stay byte-identical.
    """
    if v and v[0] in "@`-?:,[]{}#&*!|>%'\" ":
        return "'" + v.replace("'", "''") + "'"
    return v


def _pr_identity_match(record_pr: PRRecord, current_pr: PRRecord) -> bool:
    """Is ``record_pr`` (in-memory, possibly stale) the SAME tracked PR as
    ``current_pr`` (on-disk)? (codename-attribution-by-default, rounds
    36-38.)

    ``pr_id`` is the canonical identity once both sides have one -- a
    random token assigned ONCE at creation and never mutated by any later
    ``branch``/``number``/``provider``/``state`` correction, unlike EITHER
    of those fields (a manual ``set-pr`` correction can reassign either
    one). If either side lacks a ``pr_id`` (a legacy in-memory snapshot
    predating this field, mid-migration), fall back to the round-35
    identity rule as a ONE-TIME bridging match: equal NON-EMPTY ``branch``,
    or -- only when ``branch`` is empty on both sides -- equal ``number``.
    Two entries with NO identity established at all (no ``pr_id``, no
    non-empty ``branch``, no ``number`` on either side) never match.
    """
    if record_pr.pr_id and current_pr.pr_id:
        return record_pr.pr_id == current_pr.pr_id
    if record_pr.branch and current_pr.branch:
        return record_pr.branch == current_pr.branch
    if not record_pr.branch and not current_pr.branch:
        if record_pr.number is not None and current_pr.number is not None:
            return record_pr.number == current_pr.number
    return False


def _merge_pr_attribution_state(
    record: WorktreeRecord, current: WorktreeRecord,
) -> None:
    """Merge the frozen attribution fields (and ``pr_id``/``pr_revision``)
    PER-ENTRY across the full ``prs`` list, protecting a concurrently-
    stamped frozen pair from a stale in-memory writer (design.md § Per-PR
    attribution freeze, rounds 26-39).

    Runs as part of every locked ``save_record`` call (this function's
    caller already holds the record lock), so it also backfills a
    ``pr_id``-less on-disk entry INLINE here rather than via a separate
    migration mechanism (round-37 finding: this repo's config-migration
    framework explicitly excludes tracking YAML, so no such mechanism has
    an actual entry point to invoke it).

    For each ``current.prs`` (on-disk) entry:

    1. Backfill a fresh ``pr_id`` if it doesn't have one yet.
    2. Find the matching ``record.prs`` (in-memory) entry via
       :func:`_pr_identity_match`.
    3. If matched and ``current``'s ``pr_revision`` is greater than OR
       EQUAL TO the matched entry's, overwrite that entry's frozen fields
       (``attribution_mode``/``attribution_explicit``/``pr_id``/
       ``pr_revision``) from ``current``'s copy -- never the reverse. An
       EQUAL on-disk revision is also authoritative, not only a strictly
       greater one (a PR #3037 review finding): two concurrent first-touch
       freezes of the same legacy PR can each independently bump their own
       copy from 0 to 1, so a strict ``>`` would let whichever stale
       in-memory snapshot happens to save SECOND silently overwrite the
       already-persisted first decision merely because the revisions tie;
       the value already durably on disk wins that tie. Only a STRICTLY
       LOWER ``current`` revision leaves the in-memory entry unchanged. If
       the match came via the legacy branch/number fallback (record's
       entry had no ``pr_id``), write the resolved ``pr_id`` back onto it
       regardless of the revision comparison, so the in-memory object is
       no longer legacy on a later save from the same caller.
    4. If UNMATCHED (a concurrent writer created this PR after the stale
       snapshot was taken), append ``current``'s entry into
       ``record.prs`` unchanged.
    """
    # Round-5 review finding: the legacy branch/number fallback in
    # `_pr_identity_match` can match MORE than one on-disk `current_pr` to
    # the SAME in-memory `record.prs` entry when legacy tracking reuses one
    # branch across sequential PRs (a terminal PR followed by a fresh one
    # on the same branch, both still lacking `pr_id`). Track which
    # `record.prs` entries this call has already claimed so matching stays
    # one-to-one; a `current_pr` that can only find an already-claimed
    # candidate is treated as unmatched (appended as its own entry) rather
    # than silently overwriting/dropping the earlier match's frozen state.
    claimed_match_ids: set[int] = set()
    for current_pr in current.prs:
        match = next(
            (
                rp for rp in record.prs
                if id(rp) not in claimed_match_ids
                and _pr_identity_match(rp, current_pr)
            ),
            None,
        )
        if match is not None:
            claimed_match_ids.add(id(match))
        if match is None:
            # A concurrent writer created this PR after the stale snapshot
            # was taken -- append it unchanged (backfilling pr_id first if
            # it's a legacy entry with none). Claim it immediately too, so
            # a LATER same-branch current_pr in this same loop can't match
            # this freshly-appended entry via the legacy branch fallback.
            if not current_pr.pr_id:
                current_pr.pr_id = secrets.token_hex(16)
            record.prs.append(current_pr)
            claimed_match_ids.add(id(current_pr))
            continue
        # Reconcile pr_id onto whichever side is missing it -- this is the
        # bridging step for a legacy match (identity was established via
        # the branch/number fallback because one or both sides lacked a
        # pr_id): both sides converge on the SAME id, so a later save from
        # either the in-memory object or a fresh on-disk load no longer
        # needs the fallback (round-38 finding).
        if match.pr_id and not current_pr.pr_id:
            current_pr.pr_id = match.pr_id
        elif current_pr.pr_id and not match.pr_id:
            match.pr_id = current_pr.pr_id
        elif not match.pr_id and not current_pr.pr_id:
            fresh_id = secrets.token_hex(16)
            match.pr_id = fresh_id
            current_pr.pr_id = fresh_id
        # An EQUAL on-disk revision is also authoritative, not only a
        # strictly greater one (fix-PR-#3037-review finding): two
        # concurrent first-touch freezes of the same legacy PR can each
        # independently bump their own copy from 0 to 1, so a strict `>`
        # would let whichever stale in-memory snapshot happens to save
        # SECOND silently overwrite the already-persisted first decision
        # merely because the revisions tie. The frozen pair is meant to be
        # decided ONCE; on a tie, the value already durably on disk (this
        # save's own lock-serialized predecessor) wins over an in-memory
        # value that has not yet been persisted.
        if current_pr.pr_revision >= match.pr_revision:
            match.attribution_mode = current_pr.attribution_mode
            match.attribution_explicit = current_pr.attribution_explicit
            match.pr_id = current_pr.pr_id
            match.pr_revision = current_pr.pr_revision


def _save_record_unlocked(
    record: WorktreeRecord,
    path: Path | None = None,
    *,
    preserve_handoff_reservations: bool = True,
) -> None:
    """Write a worktree tracking record to YAML (atomic).

    Ordinary full-record writers may hold a stale in-memory snapshot. Preserve
    every claim currently reserved by a handoff from the on-disk record,
    including its exact metadata/disposition, so an unrelated save cannot erase
    or mutate the offered bundle. The claim-handoff transaction alone passes
    ``preserve_handoff_reservations=False`` while setting/clearing reservations
    under the required record lock.
    """
    if path is None:
        path = record.yaml_path
    if preserve_handoff_reservations and path.exists():
        current = load_record(path)
        if current.effort_revision > record.effort_revision:
            record.active_effort = current.active_effort
            record.effort_revision = current.effort_revision
            record.follow_up = current.follow_up
            record.summary = current.summary
            record.status_note_at = current.status_note_at
        # Lifecycle writers advance ``lifecycle_revision`` under the record
        # lock. An unrelated writer may have loaded an older snapshot before
        # that transition; never let its later save roll the append-only ledger
        # or session activation history backward.
        if current.lifecycle_revision > record.lifecycle_revision:
            record.sessions = current.sessions
            record.lifecycle_revision = current.lifecycle_revision
            record.head_revision = current.head_revision
            record.head_session = current.head_session
            record.head_transitions = current.head_transitions
            record.handoff_counter = current.handoff_counter
            record.handoffs = current.handoffs
        current_backend = current.session_backend
        record_backend = record.session_backend
        if current.session_backend_opaque:
            record.session_backend = None
            record.session_backend_opaque = True
            record.session_backend_raw = current.session_backend_raw
        elif (
            current_backend is not None
            and (
                record_backend is None
                or current_backend.binding_revision
                > record_backend.binding_revision
            )
        ):
            record.session_backend = current_backend
            record.session_backend_opaque = False
            record.session_backend_raw = None
        current_leg = current.execution_leg
        record_leg = record.execution_leg
        if current.execution_leg_opaque:
            record.execution_leg = None
            record.execution_leg_opaque = True
            record.execution_leg_raw = current.execution_leg_raw
        elif (
            current_leg is not None
            and (
                record_leg is None
                or current_leg.binding_revision > record_leg.binding_revision
            )
        ):
            record.execution_leg = current_leg
            record.execution_leg_opaque = False
            record.execution_leg_raw = None
        if (
            current.profile_assignment_revision
            > record.profile_assignment_revision
        ):
            record.profile_assignment_revision = (
                current.profile_assignment_revision
            )
            record.profile_assignments = current.profile_assignments
        if current.controller_revision > record.controller_revision:
            record.controller_revision = current.controller_revision
            record.controllers = current.controllers
            record.controller_metadata_opaque = (
                current.controller_metadata_opaque)
            record.controller_raw_revision = current.controller_raw_revision
            record.controller_raw_entries = current.controller_raw_entries
            record.controller_raw_revision_present = (
                current.controller_raw_revision_present)
            record.controller_raw_entries_present = (
                current.controller_raw_entries_present)
        elif current.controller_metadata_opaque:
            record.controller_metadata_opaque = True
            record.controller_raw_revision = current.controller_raw_revision
            record.controller_raw_entries = current.controller_raw_entries
            record.controller_raw_revision_present = (
                current.controller_raw_revision_present)
            record.controller_raw_entries_present = (
                current.controller_raw_entries_present)
        current_by_ref = {claim.ref: claim for claim in current.resources}
        reserved = {
            claim.ref: claim for claim in current.resources
            if claim.handoff_bundle
        }
        merged: list[ResourceClaim] = []
        for claim in record.resources:
            authoritative = reserved.pop(claim.ref, None)
            if authoritative is not None:
                merged.append(authoritative)
            else:
                # Current disk state is also authoritative when a terminal
                # transition cleared a reservation. Never let a stale
                # writer resurrect its old bundle marker.
                current_claim = current_by_ref.get(claim.ref)
                if (claim.handoff_bundle and
                        (current_claim is None
                         or not current_claim.handoff_bundle)):
                    claim.handoff_bundle = ""
                merged.append(claim)
        merged.extend(reserved.values())
        record.resources = merged
        # codename-attribution-by-default (round-11 finding): a codename is
        # assigned AT MOST ONCE and never reassigned afterward, unlike the
        # revision-tracked fields above -- so the merge rule is simply
        # "never let a stale in-memory record with no codename yet overwrite
        # an on-disk record that a concurrent lazy-backfill already
        # assigned one to." Never the reverse (an in-memory codename always
        # wins over a current on-disk absence): a writer that itself just
        # allocated the codename in this same call chain must not have its
        # own fresh assignment discarded.
        if not record.codename and current.codename:
            record.codename = current.codename
            record.codename_source = current.codename_source
        elif (
            record.codename
            and record.codename == current.codename
            and current.codename_source
            and record.codename_source != current.codename_source
        ):
            # fix-PR-#3037-review finding: the SAME codename is already
            # assigned on both sides, but the on-disk copy carries a
            # DIFFERENT known codename_source (e.g. an operator's manual
            # per-record promotion, or a concurrent writer's
            # classification landing moments before this stale save) --
            # not only when this snapshot's own source was unset, but ALSO
            # when it holds a now-STALE value that disagrees with the
            # on-disk one (round-7 review finding: a stale writer holding
            # e.g. "built-in" must not silently overwrite an on-disk
            # reclassification to "custom", which could let
            # `may_publish_codename` authorize a marker for a custom
            # vocabulary it should have blocked). Preserve that known
            # provenance rather than silently overwriting it with an
            # unset value merely because this snapshot never saw it.
            record.codename_source = current.codename_source
        _merge_pr_attribution_state(record, current)

    for session in record.sessions or ():
        if len(session.activations) > _MAX_SESSION_ACTIVATIONS:
            session.activations = session.activations[-_MAX_SESSION_ACTIVATIONS:]
    if len(record.head_transitions) > _MAX_HEAD_TRANSITIONS:
        record.head_transitions = record.head_transitions[-_MAX_HEAD_TRANSITIONS:]
    if len(record.handoffs) > _MAX_HANDOFFS:
        record.handoffs = record.handoffs[-_MAX_HANDOFFS:]
    if len(record.profile_assignments) > _MAX_PROFILE_ASSIGNMENTS:
        record.profile_assignments = record.profile_assignments[
            -_MAX_PROFILE_ASSIGNMENTS:
        ]
    _validate_controller_relation_set(record.controllers)
    record.controllers, removed_controllers = _limit_controller_relations(
        record.controllers
    )
    record.controller_revision = _bounded_nonnegative_int(
        max(
            record.controller_revision,
            max(
                (
                    relation.relation_revision
                    for relation in record.controllers
                ),
                default=0,
            ),
        ),
        field="controller_revision",
    )
    _mark_controller_projection_dirty(
        record,
        *(
            relation.controller_session_id
            for relation in removed_controllers
        ),
    )

    title_val = record.title or "null"
    # Quote titles that contain YAML-special characters (colons, etc.)
    if title_val != "null" and any(ch in title_val for ch in ":{}[]#&*!|>',\""):
        safe_title = title_val.replace("'", "''")
        title_val = f"'{safe_title}'"

    content = (
        f"worktree_id: {_yaml_scalar(record.worktree_id)}\n"
        f"branch: {_yaml_scalar(record.branch)}\n"
        f"worktree_path: {record.worktree_path}\n"
        f"repo: {record.repo}\n"
        f"machine: {record.machine}\n"
        f"platform: {record.platform}\n"
        f"started_at: {record.started_at}\n"
        f"last_resumed_at: {record.last_resumed_at}\n"
        f"resume_count: {record.resume_count}\n"
        f"title: {title_val}\n"
        f"status: {record.status}\n"
        f"completed_at: {record.completed_at or 'null'}\n"
    )

    # Owner class -- only emit for managed (system/bridge) worktrees so existing
    # session-record YAMLs stay byte-identical (no churn for the common case).
    if record.kind in MANAGED_KINDS:
        content += f"kind: {record.kind}\n"
        if record.owner:
            content += f"owner: {record.owner}\n"

    # #2668: the two orthogonal marks -- emitted only when explicitly stamped
    # (not derived), so a plain session YAML stays byte-identical while an
    # authoritative stamp (e.g. agent-bridge's origin=user for an NF session)
    # persists across reloads.
    if record.interface in ("cli", "acp"):
        content += f"interface: {record.interface}\n"
    if record.origin in ("user", "system", "delegate"):
        content += f"origin: {record.origin}\n"
    if record.dispatch_attempt is not None:
        content += yaml.safe_dump(
            {"dispatch_attempt": record.dispatch_attempt.to_dict()},
            default_flow_style=False,
            sort_keys=False,
        )
    elif record.dispatch_attempt_opaque and record.dispatch_attempt_raw_present:
        content += yaml.safe_dump(
            {"dispatch_attempt": record.dispatch_attempt_raw},
            default_flow_style=False,
            sort_keys=False,
        )
    if not record.checkout_managed:
        content += "checkout_managed: false\n"
    if record.session_backend_opaque:
        content += yaml.safe_dump(
            {"session_backend": record.session_backend_raw},
            default_flow_style=False,
            sort_keys=False,
        )
    elif record.session_backend is not None:
        content += yaml.safe_dump(
            {"session_backend": record.session_backend.to_dict()},
            default_flow_style=False,
            sort_keys=False,
        )
    if record.execution_leg_opaque:
        content += yaml.safe_dump(
            {"execution_leg": record.execution_leg_raw},
            default_flow_style=False,
            sort_keys=False,
        )
    elif record.execution_leg is not None:
        content += yaml.safe_dump(
            {"execution_leg": record.execution_leg.to_dict()},
            default_flow_style=False,
            sort_keys=False,
        )

    # worktree-status-core: the agent-asserted disposition overlay -- emitted
    # only when explicitly set, so an un-annotated session YAML stays
    # byte-identical (no churn for the common case).
    if record.follow_up:
        content += "follow_up: true\n"
    if record.summary:
        safe_summary = record.summary.replace("'", "''")
        content += f"summary: '{safe_summary}'\n"
    if record.title_asserted:
        content += "title_asserted: true\n"
    if record.status_note_at:
        content += f"status_note_at: {record.status_note_at}\n"
    if record.last_finalized_at:
        content += f"last_finalized_at: {record.last_finalized_at}\n"
    if record.active_effort is not None:
        content += yaml.safe_dump(
            {"active_effort": record.active_effort.to_dict()},
            default_flow_style=False,
            sort_keys=False,
        )
    if record.effort_revision:
        content += f"effort_revision: {record.effort_revision}\n"
    # #4057 cached liveness -- emitted only when stamped (None absent), so a
    # never-verified worktree's YAML stays byte-identical.
    if record.mux_live is not None:
        content += f"mux_live: {'true' if record.mux_live else 'false'}\n"
        if record.mux_live_at:
            content += f"mux_live_at: {record.mux_live_at}\n"
    # #4057/#1416 cached bound-Copilot liveness -- emitted only when reconciled
    # (None absent, never persisted as Unknown), so an un-reconciled worktree's
    # YAML stays byte-identical.
    if record.bound_live is not None:
        content += f"bound_live: {'true' if record.bound_live else 'false'}\n"
        if record.bound_live_at:
            content += f"bound_live_at: {record.bound_live_at}\n"
    # picker-cache-first-paint (dotfiles#948) session-render cache -- emitted
    # only when populated (None absent), so a never-populated worktree's YAML
    # stays byte-identical. Read directly by the cache-only first-paint load.
    if record.session_turns is not None:
        content += f"session_turns: {int(record.session_turns)}\n"
    if record.session_summary:
        safe_ss = record.session_summary.replace("'", "''")
        content += f"session_summary: '{safe_ss}'\n"
    if record.git_state:
        content += f"git_state: {record.git_state}\n"
    if record.session_state_at:
        content += f"session_state_at: {record.session_state_at}\n"

    # #1029: originating-session pointer. Emitted only when set, so the
    # common-case session-record YAML stays byte-identical (no churn).
    if record.parent_session:
        content += f"parent_session: {record.parent_session}\n"
    if record.controller_metadata_opaque:
        raw_controller_data = {}
        if record.controller_raw_revision_present:
            raw_controller_data["controller_revision"] = (
                record.controller_raw_revision)
        if record.controller_raw_entries_present:
            raw_controller_data["controllers"] = record.controller_raw_entries
        if raw_controller_data:
            content += yaml.safe_dump(
                raw_controller_data,
                default_flow_style=False,
                sort_keys=False,
            )
    elif record.controller_revision:
        content += f"controller_revision: {record.controller_revision}\n"
    if record.controllers and not record.controller_metadata_opaque:
        content += yaml.safe_dump(
            {"controllers": [
                {
                    "kind": relation.kind,
                    "source": relation.source,
                    **(
                        {"controller_ref": relation.controller_ref}
                        if relation.controller_ref else {}
                    ),
                    **(
                        {
                            "controller_session_id":
                                relation.controller_session_id
                        }
                        if relation.controller_session_id else {}
                    ),
                    "state": relation.state,
                    "relation_revision": relation.relation_revision,
                    "created_at": relation.created_at,
                    **(
                        {"ended_at": relation.ended_at}
                        if relation.ended_at else {}
                    ),
                }
                for relation in record.controllers
            ]},
            default_flow_style=False,
            sort_keys=False,
        )
    # session-lifecycle: the current-session head pointer. Emitted only when
    # explicitly set (absent = derived), keeping legacy YAMLs byte-identical.
    if record.head_session:
        content += f"head_session: {record.head_session}\n"
    if record.lifecycle_revision:
        content += f"lifecycle_revision: {record.lifecycle_revision}\n"
    if record.head_revision:
        content += f"head_revision: {record.head_revision}\n"
    if record.handoff_counter:
        content += f"handoff_counter: {record.handoff_counter}\n"
    if record.head_transitions:
        content += yaml.safe_dump(
            {"head_transitions": [
                {
                    "revision": transition.revision,
                    "session_id": transition.session_id,
                    "reason": transition.reason,
                    "at": transition.at,
                    **(
                        {"handoff_ordinal": transition.handoff_ordinal}
                        if transition.handoff_ordinal is not None else {}
                    ),
                }
                for transition in record.head_transitions
            ]},
            default_flow_style=False,
            sort_keys=False,
        )
    if record.handoffs:
        content += yaml.safe_dump(
            {"handoffs": [
                {
                    "ordinal": handoff.ordinal,
                    "token": handoff.token,
                    "predecessor": handoff.predecessor,
                    "state": handoff.state,
                    "opened_at": handoff.opened_at,
                    **(
                        {"successor": handoff.successor}
                        if handoff.successor else {}
                    ),
                    **(
                        {"linked_at": handoff.linked_at}
                        if handoff.linked_at else {}
                    ),
                    **(
                        {"candidate": handoff.candidate}
                        if handoff.candidate else {}
                    ),
                    **(
                        {"candidate_at": handoff.candidate_at}
                        if handoff.candidate_at else {}
                    ),
                }
                for handoff in record.handoffs
            ]},
            default_flow_style=False,
            sort_keys=False,
        )
    if record.profile_assignment_revision:
        content += (
            "profile_assignment_revision: "
            f"{record.profile_assignment_revision}\n"
        )
    if record.profile_assignments:
        content += yaml.safe_dump(
            {"profile_assignments": [
                {
                    "policy": assignment.policy,
                    "assignment_label": assignment.assignment_label,
                    "selected_profile": assignment.selected_profile,
                    "bag_generation": assignment.bag_generation,
                    "bag_position": assignment.bag_position,
                    "assigned_at": assignment.assigned_at,
                    "disposition": assignment.disposition,
                    **(
                        {"session_id": assignment.session_id}
                        if assignment.session_id else {}
                    ),
                    **({"lane": assignment.lane} if assignment.lane else {}),
                    **(
                        {"abandoned_at": assignment.abandoned_at}
                        if assignment.abandoned_at else {}
                    ),
                    **(
                        {"bound_at": assignment.bound_at}
                        if assignment.bound_at else {}
                    ),
                    **(
                        {
                            "predecessor_session_id":
                                assignment.predecessor_session_id
                        }
                        if assignment.predecessor_session_id else {}
                    ),
                }
                for assignment in record.profile_assignments
            ]},
            default_flow_style=False,
            sort_keys=False,
        )
    # #2178: bridge caller-worktree pointer. Emitted only when set.
    if record.caller_worktree:
        content += f"caller_worktree: {_yaml_scalar(record.caller_worktree)}\n"
    # agent-fabric resource-claims: the backward owner link. Emitted only when
    # set, so an unclaimed worktree's YAML stays byte-identical.
    if record.owner_ref:
        content += f"owner_ref: {_yaml_scalar(record.owner_ref)}\n"
    # agent-bridge-worktree-native-agents: the bound charter. Emitted only
    # when set, so an unbound worktree's YAML stays byte-identical.
    if record.bound_agent:
        content += f"bound_agent: {_yaml_scalar(record.bound_agent)}\n"
    # citadel paired -harness/-knowledge worktree lifecycle (#957): the pair
    # linkage. Emitted only when set, so an unpaired worktree's YAML stays
    # byte-identical (the common case is unpaired).
    if record.pair_id:
        content += f"pair_id: {_yaml_scalar(record.pair_id)}\n"
    if record.pair_role in ("harness", "knowledge"):
        content += f"pair_role: {record.pair_role}\n"
    if record.pair_ref:
        content += f"pair_ref: {_yaml_scalar(record.pair_ref)}\n"
    if record.pair_kind in ("worktree", "anchor"):
        content += f"pair_kind: {record.pair_kind}\n"
    # #220 follow-up: reap tombstone marker. Emitted only when set (a record
    # that was tombstoned by retire_record's paired-reap path).
    if record.reaped_at:
        content += f"reaped_at: {_yaml_scalar(record.reaped_at)}\n"
    # pr-attribution-codenames Phase 2: emitted only when assigned, so a
    # legacy/pre-Phase-2 worktree's YAML stays byte-identical.
    if record.codename:
        content += f"codename: {_yaml_scalar(record.codename)}\n"
    # codename-attribution-by-default: emitted only when set, so a legacy/
    # pre-Phase-1 worktree's YAML stays byte-identical.
    if record.codename_source:
        content += f"codename_source: {_yaml_scalar(record.codename_source)}\n"
    # agent-fabric resource-claims: the forward outbound list. Emitted only when
    # non-empty (the common case owns nothing), keeping legacy YAMLs identical.
    if record.resources:
        content += yaml.safe_dump(
            {"resources": [_claim_to_yaml_dict(c) for c in record.resources]},
            default_flow_style=False,
            sort_keys=False,
        )

    # worktree-finality-and-obligations Phase 3: itemized follow-up ledger.
    # Emitted only when non-empty, keeping legacy YAMLs (boolean-only
    # `follow_up`) identical.
    if record.follow_ups:
        content += yaml.safe_dump(
            {"follow_ups": [fu.to_dict() for fu in record.follow_ups]},
            default_flow_style=False,
            sort_keys=False,
        )

    # Serialize PR records.  Emit the multi-PR ``prs:`` list and mirror the
    # active PR to a legacy ``pr:`` block for one release, so a same-machine
    # tool *downgrade* still finds the active PR.  Zero-PR worktrees emit
    # neither, keeping the common-case YAML byte-identical.
    if record.prs:
        content += yaml.safe_dump(
            {"prs": [_pr_to_yaml_dict(p) for p in record.prs]},
            default_flow_style=False,
            sort_keys=False,
        )
        active = record.active_pr()
        if active is not None:
            content += yaml.safe_dump(
                {"pr": _pr_to_yaml_dict(active)},
                default_flow_style=False,
                sort_keys=False,
            )

    # Serialize sessions list -- None omitted (not yet indexed),
    # [] written as empty list (indexed, no sessions).
    if record.sessions is not None:
        entries = [
            {
                "session_id": s.session_id,
                "started_at": s.started_at,
                **({"pid": s.pid} if s.pid else {}),
                **({"ended_at": s.ended_at} if s.ended_at else {}),
                **({"state": s.state} if s.state != "active" else {}),
                **({"successor": s.successor} if s.successor else {}),
                **({"predecessor": s.predecessor} if s.predecessor else {}),
                **({"pane_id": s.pane_id} if s.pane_id else {}),
                **(
                    {"relation_revision": s.relation_revision}
                    if s.relation_revision else {}
                ),
                **(
                    {"activations": [
                        {
                            "ordinal": activation.ordinal,
                            "started_at": activation.started_at,
                            "start_recorded_at": activation.start_recorded_at,
                            "start_source": activation.start_source,
                            **(
                                {"ended_at": activation.ended_at}
                                if activation.ended_at else {}
                            ),
                            **(
                                {"end_recorded_at": activation.end_recorded_at}
                                if activation.end_recorded_at else {}
                            ),
                            **(
                                {"end_source": activation.end_source}
                                if activation.end_source else {}
                            ),
                        }
                        for activation in s.activations
                    ]}
                    if s.activations else {}
                ),
            }
            for s in record.sessions
        ]
        content += yaml.safe_dump(
            {"sessions": entries},
            default_flow_style=False,
            sort_keys=False,
        )

    _atomic_write(path, content)


def _flush_session_projections(record: WorktreeRecord) -> None:
    """Flush exact dirty session projections after authoritative persistence."""
    dirty_sessions = getattr(record, "_session_projection_dirty", set())
    initial_sessions = getattr(
        record, "_session_projection_initial_registration", set()
    )
    dirty_controllers = getattr(
        record, "_controller_projection_dirty", set()
    )
    if dirty_sessions or dirty_controllers:
        remaining_sessions = set(dirty_sessions)
        remaining_initial_sessions = set(initial_sessions)
        remaining_controllers = set(dirty_controllers)
        try:
            from . import session_projection

            for session_id in sorted(dirty_sessions):
                outcome = session_projection.sync_bound(
                    record,
                    session_id,
                    initial_registration=session_id in initial_sessions,
                )
                if outcome in {"written", "current", "blocked"}:
                    remaining_sessions.discard(session_id)
                    remaining_initial_sessions.discard(session_id)
            for session_id in sorted(dirty_controllers):
                outcome = session_projection.sync_controller(
                    record, session_id
                )
                if outcome in {"written", "current", "blocked"}:
                    remaining_controllers.discard(session_id)
        except Exception:
            pass
        finally:
            record._session_projection_dirty = remaining_sessions
            record._session_projection_initial_registration = (
                remaining_initial_sessions
            )
            record._controller_projection_dirty = remaining_controllers


def save_record(
    record: WorktreeRecord,
    path: Path | None = None,
    *,
    preserve_handoff_reservations: bool = True,
) -> None:
    """Locked cross-process CAS for one complete worktree record."""
    if path is None:
        path = record.yaml_path
    with _RecordLock(path, require_sidecar=True):
        _save_record_unlocked(
            record,
            path,
            preserve_handoff_reservations=preserve_handoff_reservations,
        )
    record._loaded_from = path
    _flush_session_projections(record)


def list_records(
    tracking_path: Path,
    *,
    status_filter: WorktreeStatus | None = None,
    platform_filter: str | None = None,
    repo_filter: str | None = None,
    kind_filter: WorktreeKind | None = None,
) -> list[WorktreeRecord]:
    """List all worktree records, optionally filtered by status/platform/repo/kind."""
    records: list[WorktreeRecord] = []
    if not tracking_path.exists():
        return records

    for yaml_file in sorted(tracking_path.glob("*.yaml")):
        try:
            rec = load_record(yaml_file)
        except Exception:
            continue
        if status_filter and rec.status != status_filter:
            continue
        if platform_filter and rec.platform != platform_filter:
            continue
        if repo_filter and rec.repo != repo_filter:
            continue
        if kind_filter and rec.kind != kind_filter:
            continue
        records.append(rec)

    return records


def find_worktree_id_by_cwd(cwd: str, *, project: str | None = None) -> str | None:
    """Resolve a worktree_id from a session cwd.

    Matches *cwd* (or any worktree root that is an ancestor of it) against
    the tracked ``worktree_path`` values.  Used by the sessionStart hook to
    associate a session with its worktree when the ``WORKTREE_ID`` env var
    is not present in the hook environment -- the Copilot CLI delivers the
    cwd via the hook's stdin payload instead. ``project`` scopes the lookup
    to a given project (an out-of-context caller, e.g. a sync process)
    instead of the ambient one. Deepest (longest) match wins on overlap;
    None if no worktree contains *cwd*.
    """
    if not cwd:
        return None
    tracking_path = cfg.project_dir(project) / "worktrees" if project else cfg.tracking_dir()
    if not tracking_path.exists():
        return None

    norm = os.path.normcase(os.path.normpath(cwd)).rstrip("/\\")
    best_id: str | None = None
    best_len = -1
    for rec in list_records(tracking_path):
        wp = rec.worktree_path
        if not wp:
            continue
        wnorm = os.path.normcase(os.path.normpath(wp)).rstrip("/\\")
        if norm == wnorm or norm.startswith(wnorm + os.sep):
            if len(wnorm) > best_len:
                best_len = len(wnorm)
                best_id = rec.worktree_id
    return best_id


def load_record_by_id(
    worktree_id: str,
    *,
    tracking_path: Path | None = None,
) -> WorktreeRecord | None:
    """Load a tracked worktree record by id from a tracking directory.

    Returns ``None`` when the id is empty, no record file exists, or the file
    is unreadable/malformed. Fail-safe -- never raises.
    """
    if not worktree_id:
        return None
    path = (tracking_path or cfg.tracking_dir()) / f"{worktree_id}.yaml"
    if not path.exists():
        return None
    try:
        return load_record(path)
    except Exception:
        return None


def find_paired_record(record: WorktreeRecord) -> WorktreeRecord | None:
    """Resolve the SIBLING record of a paired worktree, or ``None``.

    Reads ``record.pair_ref`` (a :class:`ClaimRef` to the sibling) and loads that
    worktree's record from the referenced project's tracking directory.
    Only same-machine qualified refs resolve. A legacy record misplaced in the
    current project's directory is deliberately not accepted:
    ``state-root --pair`` must surface that broken pair until ``doctor --fix``
    copies the record to its owning project registry.
    """
    ref = record.pair_claim_ref
    if ref is None:
        return None
    if ref.is_qualified and ref.machine == record.machine and ref.project:
        return load_record_by_id(
            ref.worktree_id,
            tracking_path=cfg.project_dir(ref.project) / "worktrees",
        )
    return None


def retire_record(record: WorktreeRecord, tracking_path: Path) -> bool:
    """Retire a reaped worktree's tracking record: tombstone it as
    ``archived`` (unpaired) or ``finalized`` when paired (BOTH-gate
    follow-up, #957/#220). An unpaired record becomes a minimal
    ``archived`` tombstone (``reaped_at`` stamped), not a deletion --
    identity, lineage, and session history stay queryable after the
    checkout is gone. A **paired** record's fate depends on whether its
    sibling is ITSELF already reaped:

    * If the sibling record is missing, not yet ``finalized``, or ``finalized``
      but not marked :attr:`WorktreeRecord.reaped_at` (i.e. still a live,
      merge-safe-but-not-yet-cleaned worktree), this record is instead
      rewritten as a minimal ``finalized`` tombstone (with ``reaped_at``
      stamped) rather than unlinked. :func:`find_paired_record` resolves a
      sibling purely by whether ``<id>.yaml`` exists in the *other* project's
      tracking directory, so deleting it outright would leave the sibling
      side with no way to distinguish "this half of the pair was never
      carved" from "this half was already reaped" --
      :func:`default_paired_sibling_final` reports ``None`` ("unknown")
      either way, and a `None` permanently spares the sibling's own
      paired-worktree gate. A tombstoned ``finalized`` record lets that probe
      resolve ``sibling.status == "finalized"`` -> ``True`` and unblock the
      sibling's cleanup, without weakening the gate for a pair that
      genuinely hasn't been carved yet.
    * If the sibling record exists AND already carries ``reaped_at`` (proof
      it is itself a tombstone left by this exact function, not a live
      worktree that merely reads ``finalized``), nothing depends on
      resolving *this* record any more: the sibling's own reap has already
      happened and only ever needed to be observed once, by this reap. Both
      records are deleted outright rather than leaving two dangling
      tombstones behind forever.

      ``reaped_at`` -- rather than ``status`` -- is the signal this decision
      keys on precisely because ``status == "finalized"`` alone is
      ambiguous: a live, not-yet-cleaned worktree can legitimately carry it
      for a long time before ``cleanup`` ever removes its directory (merge
      -safe and already-reaped are different things -- see ``finalize``'s
      own contract). Peeking at status alone to decide on a hard delete
      would delete live tracking metadata for a worktree still on disk.
      ``reaped_at`` is set in exactly one place (this function's tombstone
      branch), so its presence is unambiguous, positive proof of an actual
      reap.

    Any resolution failure (an unreadable sibling record, a cross-machine
    pair, etc.) is treated as "not confirmed reaped" and falls back to the
    tombstone path -- matching :func:`default_paired_sibling_final`'s own
    "unknown -> spare" philosophy.

    Fail-safe: if a tombstone write raises for any reason, falls back to a
    plain unlink -- a reap must never be blocked *indefinitely* by this
    bookkeeping.

    **Locking (pr-attribution-codenames Phase 2 follow-up).** Every delete
    (including the sibling delete, in the both-reaped hard-delete branch) is
    taken under a REAL cross-process ``_RecordLock`` (``require_sidecar=True``)
    -- not just the tombstone rewrite, which already went through
    ``save_record``'s own locking. Two properties this closes:

    * Without any lock, a concurrent reader/backfiller (e.g.
      ``codename_tracking.ensure_codename``, which holds the SAME record's
      lock across its own existence-check + save) could observe the record
      present an instant before this function unlinks it, or recreate one
      this function just deleted.
    * ``require_sidecar=True`` makes that guarantee REAL cross-process, not
      just same-thread. A contended lock (this record's, or -- both-reaped
      branch -- the sibling's) makes this call **defer**: return ``False``
      without deleting anything, rather than blocking indefinitely or
      proceeding without real exclusivity. The caller's reap runs on a
      cadence (`cleanup`/`gc`), so a deferred record retries later.
    * The two locks in the both-reaped hard-delete branch are acquired in a
      **deterministic order** (sorted by path) via one ``ExitStack``,
      all-or-nothing -- never unconditionally, which would let two
      concurrent calls on the two halves of the SAME pair deadlock each
      other.

    Returns ``True`` once the record is durably retired (tombstoned or
    deleted); ``False`` if deferred by a contended lock -- callers treat
    that as "retry on a later reap pass," not a failure.
    """
    path = tracking_path / f"{record.worktree_id}.yaml"
    sibling_path: Path | None = None
    hard_delete_sibling = False
    if record.is_paired:
        sibling: WorktreeRecord | None = None
        try:
            sibling = find_paired_record(record)
        except Exception:
            sibling = None
        if sibling is not None and sibling.reaped_at:
            hard_delete_sibling = True
            ref = record.pair_claim_ref
            if ref is not None and ref.is_qualified and ref.project:
                sibling_path = (
                    cfg.project_dir(ref.project) / "worktrees"
                    / f"{ref.worktree_id}.yaml"
                )

    lock_paths = sorted({path, sibling_path} - {None}, key=str)
    try:
        with ExitStack() as stack:
            for lock_path in lock_paths:
                stack.enter_context(_RecordLock(lock_path, require_sidecar=True))

            if hard_delete_sibling:
                if sibling_path is not None:
                    sibling_path.unlink(missing_ok=True)
                path.unlink(missing_ok=True)
                return True

            def _write_tombstone(status: WorktreeStatus) -> bool:
                # A raising tombstone write falls back to a plain unlink
                # rather than blocking the reap indefinitely.
                try:
                    now = _now_iso()
                    record.status = status
                    if record.completed_at is None:
                        record.completed_at = now
                    record.reaped_at = now
                    save_record(record, path=path)
                    return True
                except Exception:
                    path.unlink(missing_ok=True)
                    return True

            # Paired -> "finalized" (a sibling-detection signal, #957/#220);
            # unpaired -> "archived" (tombstones identity/lineage/session
            # history rather than discarding them when the checkout is gone).
            return _write_tombstone("finalized" if record.is_paired else "archived")
    except TimeoutError:
        return False


def find_worktree_id_by_session(session_id: str, *, project: str | None = None) -> str | None:
    """Resolve a session ID from the active project's tracked worktrees.

    This is the identity fallback for bare resume: the resumed session may keep
    HOME as its recorded cwd, but the sessionStart hook has explicitly bound
    that exact session ID to its intended worktree. Ambiguous or absent matches
    return ``None`` rather than guessing. ``project`` overrides the ambient
    active project -- see :func:`find_worktree_id_by_cwd`.
    """
    if not session_id:
        return None
    tracking_path = cfg.project_dir(project) / "worktrees" if project else cfg.tracking_dir()
    matches = {
        rec.worktree_id
        for rec in list_records(tracking_path)
        if any(s.session_id == session_id for s in (rec.sessions or ()))
    }
    return next(iter(matches)) if len(matches) == 1 else None


def update_status(
    record: WorktreeRecord,
    new_status: WorktreeStatus,
    *,
    save: bool = True,
) -> None:
    """Update a record's status and save it.

    ``save=False`` lets a foreground caller do the whole read-modify-write under
    a single :class:`_RecordLock` (load -> update_status(save=False) -> save),
    keeping the cross-process lock scoped to the RMW window (#4547)."""
    record.status = new_status
    if new_status == "active":
        record.completed_at = None
    elif new_status in ("finalized", "orphaned", "complete", "pushed"):
        if record.completed_at is None:
            record.completed_at = _now_iso()
    if save:
        save_record(record)


#: C0 control characters that must never reach the tracking YAML. TAB (\x09),
#: LF (\x0a) and CR (\x0d) are legitimate YAML stream characters and are kept;
#: the rest (BEL \x07, etc.) are illegal in a YAML scalar and, once persisted,
#: make ``yaml.safe_load`` raise a ``ReaderError`` on EVERY subsequent read --
#: wedging all future disposition writes (alice_example/dotfiles#1789). A
#: stray BEL is easy to
#: introduce from a caller (e.g. PowerShell renders a literal backtick-a ``` `a ```
#: as \x07), so sanitize defensively on write and self-heal on read.
_ILLEGAL_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _strip_control_chars(text: str | None) -> str | None:
    """Drop C0 control chars (except TAB/LF/CR) that corrupt the tracking YAML.

    Used on the write path (disposition ``summary`` / ``title``) to prevent a
    control char from being persisted, and on the read path to self-heal a file
    that was poisoned before this guard existed. See :data:`_ILLEGAL_CTRL_RE`.
    """
    if text is None:
        return None
    return _ILLEGAL_CTRL_RE.sub("", text)


def cap_title(title: str | None) -> str | None:
    """Normalize + cap an agent-asserted worktree title at :data:`TITLE_MAX`.

    Agent-written titles must stay short so they fit the mux status bar (120-col
    default) and the Worktree Picker's table rows -- longer prose belongs in the
    disposition ``summary`` (shown in the Picker's actions menu and via
    ``status --history``). Collapses newlines, strips, and truncates to **at
    most** ``TITLE_MAX`` chars with a trailing ellipsis (an rstrip before the
    ellipsis can make it slightly shorter). Empty/whitespace -> ``None``.
    """
    if not title:
        return None
    t = re.sub(r"[\t\r\n]+", " ", _strip_control_chars(title)).strip()
    if not t:
        return None
    if len(t) > TITLE_MAX:
        t = t[: TITLE_MAX - 1].rstrip() + "\u2026"
    return t


def normalize_title(title: str | None) -> str | None:
    """Normalize a title for a publication surface (PR title, branch-name
    slug, commit message) WITHOUT truncating it -- unlike :func:`cap_title`,
    which is specifically for the mux status bar / Picker's short display
    limit. Collapses newlines, strips, and returns ``None`` for empty or
    whitespace-only input, so a whitespace-only ``--title`` can't be
    mistaken for a real one.
    """
    if not title:
        return None
    t = re.sub(r"[\t\r\n]+", " ", _strip_control_chars(title)).strip()
    return t or None


def set_disposition(
    record: WorktreeRecord,
    *,
    summary: str | None = None,
    title: str | None = None,
    follow_up: bool | None = None,
    session_id: str | None = None,
    kind: str = "status",
    save: bool = True,
) -> None:
    """Set the agent-asserted disposition overlay (summary / title / follow-up)
    and save.

    Orthogonal to git/session state -- this records what only the agent knows:
    whether the worktree is genuinely *resolved* or still has *actionable
    follow-ups*, plus a one-line summary of what it is/left at and (optionally) a
    fresh ``title`` when the worktree's focus changes. ``summary``, ``title`` and
    ``follow_up`` are each applied only when not None, so a caller may update one
    without disturbing the others. An asserted ``title`` is capped at
    :data:`TITLE_MAX` (:func:`cap_title`) so it fits the status bar / Picker rows.
    Stamps ``status_note_at`` (which the postToolUse nudge watches to reset its
    drift counter) and appends a durable entry to the worktree's
    disposition-history sidecar (see :mod:`agent_worktrees.disposition_history`).
    """
    changed: list[str] = []
    if summary is not None:
        record.summary = _strip_control_chars(summary).replace("\n", " ").strip()
        changed.append("summary")
    if title is not None:
        record.title = cap_title(title)
        # An explicit --title assertion is authoritative; a cleared (empty)
        # title re-enables auto-derivation from the session summary.
        record.title_asserted = record.title is not None
        changed.append("title")
    if follow_up is not None:
        record.follow_up = follow_up
        changed.append("follow_up")
        # worktree-finality-and-obligations Phase 3: any caller that asserts a
        # NEW open obligation via the legacy boolean (manual `status
        # --follow-up`, or `effort-focus bind`'s automatic follow_up=True)
        # reopens a finalized owner too -- not just the itemized ledger path
        # in `add_follow_up`. Idempotent/no-op when already non-finalized.
        if follow_up and record.status == "finalized":
            reopen_finalized_owner(record, reason="follow_up flag set")
    record.status_note_at = _now_iso()
    if changed:
        disposition_history.append(
            record.worktree_id,
            at=record.status_note_at,
            summary=record.summary,
            title=record.title,
            follow_up=record.follow_up,
            changed=changed,
            kind=kind,
            session_id=session_id,
        )
    if save:
        save_record(record)


def mark_resumed(record: WorktreeRecord, *, save: bool = True) -> None:
    """Increment resume count and update last_resumed_at.

    ``save=False`` lets a foreground caller enclose the whole read-modify-write
    in one :class:`_RecordLock` (#4547)."""
    record.resume_count += 1
    record.last_resumed_at = _now_iso()
    if save:
        save_record(record)


def _stamp_liveness(
    worktree_id: str, live: bool, *,
    live_attr: str, at_attr: str, refresh: bool, throttle_secs: float,
) -> None:
    """Shared read-modify-write for a cached liveness stamp (#4057).

    Backs both :func:`stamp_mux_live` (``mux_live``) and :func:`stamp_bound_live`
    (``bound_live``): a best-effort, locked persist of ``<live_attr>`` +
    ``<at_attr>``. Never raises; no-ops when the record is absent, and skips the
    write when the value is unchanged unless ``refresh`` is set AND the existing
    stamp has aged past ``throttle_secs`` (a value CHANGE always writes). See the
    public wrappers for the semantics.
    """
    yaml_path = _owning_tracking_dir(worktree_id) / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return
    try:
        # Best-effort background writer (#4547): a Picker sweep's liveness cache.
        # Skip on contention rather than block a critical updater -- the hint is
        # idempotent and the next authoritative observation re-stamps it.
        with _RecordLock(yaml_path, blocking=False) as lk:
            if not lk.acquired:
                return
            record = load_record(yaml_path)
            if getattr(record, live_attr) is live and getattr(record, at_attr):
                if not refresh:
                    return  # unchanged, no refresh requested -- skip
                # Same value: renew freshness only once the stamp has aged past
                # the throttle, so a repeat authoritative observation keeps the
                # hint fresh without rewriting the YAML on every call.
                if not _stamp_older_than(getattr(record, at_attr), throttle_secs):
                    return
            setattr(record, live_attr, live)
            setattr(record, at_attr, _now_iso())
            save_record(record)
    except Exception:
        pass


def stamp_mux_live(
    worktree_id: str, live: bool, *,
    refresh: bool = False, throttle_secs: float = 60.0, sync: bool = False,
) -> None:
    """Cache the last-known multiplexer liveness on a worktree's record (#4057).

    A best-effort, locked read-modify-write that persists ``mux_live`` +
    ``mux_live_at`` so a follow-up picker populate can prefer this cached hint
    over a live probe. Called by the authoritative single-worktree verify at the
    action moments (Actions-menu / Enter -> ``live=True``/``False``), by Stop
    (``live=False``), and at confirmed mux teardown (the idle-gated reaper ->
    ``live=False``). It is a *hint*, always reconciled by the batched live scan;
    never raises, and no-ops when the record is absent or unchanged (so it adds
    no YAML churn when the liveness has not moved).

    **Async by default** (dotfiles#948 follow-up): every caller is a
    fire-and-forget cache warm (the action-moment verb set uses the LIVE verdict,
    not this cached value), and one site -- opening the Actions menu -- runs on
    the picker's UI thread. So the YAML write is kept off the caller's thread and
    serialized through the shared single-writer :data:`_STAMP_QUEUE` (coalesced
    per worktree with the session-state stamp). Pass ``sync=True`` to apply inline
    (tests, or a caller that needs the write durable before it returns).

    ``refresh`` (default False) additionally renews the freshness timestamp when
    the value is UNCHANGED, so a steadily-live worktree observed authoritatively
    (e.g. a repeat Actions-menu verify) keeps a fresh stamp instead of aging past
    the populate-hint TTL while genuinely live -- without which the same-value
    no-op means the hint can never stay fresh for a long-lived session. To bound
    YAML churn the same-value renewal is **throttled**: it rewrites only when the
    existing stamp is older than ``throttle_secs``. A value CHANGE always writes
    (it records the transition). ``refresh`` is for low-frequency authoritative
    observation points only -- never the populate hot path.
    """
    if sync:
        _stamp_liveness(
            worktree_id, live, live_attr="mux_live", at_attr="mux_live_at",
            refresh=refresh, throttle_secs=throttle_secs,
        )
        return
    _STAMP_QUEUE.submit_mux(
        worktree_id, live, refresh=refresh, throttle_secs=throttle_secs)


def stamp_bound_live(
    worktree_id: str, live: bool, *,
    refresh: bool = False, throttle_secs: float = 60.0,
) -> None:
    """Cache the last-known bound-Copilot liveness on a worktree's record (#4057).

    The bare-resume counterpart of :func:`stamp_mux_live`: persists ``bound_live``
    + ``bound_live_at`` (see :class:`WorktreeRecord`). Stamped by two
    authoritative, off-the-populate-hot-path callers -- both sourced from the
    same ``reclaim.resolve_bound_copilots`` scan: the OFF-HOT-PATH reconciler
    (:func:`picker_tui.data_local.reconcile_bound_live`), which resolves every
    live bound Copilot on the machine so a bare-resumed session (cwd=home,
    invisible to the registered-session + mux scans) still surfaces in the Active
    section from cache alone (#1416); and the Enter-time resume verify in
    ``_resolve_resume``, which writes back the single worktree's fresh verdict so
    the next paint can offer Reclaim on a bound/bare Copilot even if this launch
    crashed. Same best-effort / refresh / throttle semantics as
    :func:`stamp_mux_live`; never the populate hot path.
    """
    _stamp_liveness(
        worktree_id, live, live_attr="bound_live", at_attr="bound_live_at",
        refresh=refresh, throttle_secs=throttle_secs,
    )


def stamp_session_state(
    worktree_id: str, *,
    turns: int | None = None,
    summary: str | None = None,
    git_state: str | None = None,
    throttle_secs: float = 30.0,
    sync: bool = False,
) -> bool:
    """Persist the picker's session-render cache back onto a worktree's record.

    picker-cache-first-paint (dotfiles#948): the populate pass and a per-worktree
    Refresh call this to cache ``session_turns`` / ``session_summary`` /
    ``git_state`` for the cache-only first paint. Only provided fields update;
    ``None`` means "leave as-is". Writes **only when a value actually changed**
    (the render cache never ages out on read, so there is no freshness renewal --
    which also avoids churning every YAML on every populate).

    **Async by default.** File writes are kept OFF the caller's thread (user
    interaction / render) and serialized through a single background writer
    (:data:`_STAMP_QUEUE`), so a frequent background stamp never blocks a
    keystroke and never collides with a foreground YAML write (a resume's
    ``mark_resumed``). The mutation is coalesced per worktree and applied by the
    writer thread. Pass ``sync=True`` to apply inline (tests, or a caller that
    needs the write durable before it returns). Returns True when it wrote (sync)
    or when the mutation was enqueued (async); best-effort, never raises.
    """
    if sync:
        return _apply_session_state_stamp(
            worktree_id, turns=turns, summary=summary, git_state=git_state)
    _STAMP_QUEUE.submit(worktree_id, turns=turns, summary=summary,
                        git_state=git_state)
    return True


def _apply_session_state_stamp(
    worktree_id: str, *,
    turns: int | None = None,
    summary: str | None = None,
    git_state: str | None = None,
) -> bool:
    """The synchronous read-modify-write for :func:`stamp_session_state` --
    run by the async writer thread (or inline when ``sync=True``). Serialized
    per path (``_RecordLock`` -> in-process lock) and best-effort; returns True
    iff the record was rewritten."""
    yaml_path = _owning_tracking_dir(worktree_id) / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return False
    try:
        # Best-effort background writer (#4547): the Picker's session-render
        # cache. Skip on contention so a sweep never blocks a critical updater;
        # the next populate re-stamps the (idempotent) cache.
        with _RecordLock(yaml_path, blocking=False) as lk:
            if not lk.acquired:
                return False
            record = load_record(yaml_path)
            changed = False
            if turns is not None and record.session_turns != int(turns):
                record.session_turns = int(turns)
                changed = True
            if summary is not None and (record.session_summary or "") != summary:
                record.session_summary = summary or None
                changed = True
            if git_state is not None and (record.git_state or "") != git_state:
                record.git_state = git_state or None
                changed = True
            if not changed:
                return False
            record.session_state_at = _now_iso()
            save_record(record)
            return True
    except Exception:
        return False


class _StampWriteQueue:
    """Single-writer async queue for the session-render-cache stamps.

    Per the harness design guidance, YAML writes are kept off the
    user-interaction/render path and serialized through one background worker: a
    frequent stamp from the picker's populate/repoll threads never blocks a
    keystroke, and -- because one thread performs every stamp write -- stamps
    never race each other. Pending mutations are coalesced per worktree (only the
    latest merged fields are written), so a burst collapses to a single write.
    The worker is started lazily on first use and flushed at interpreter exit.
    """

    def __init__(self) -> None:
        import queue as _queue
        self._q: _queue.Queue = _queue.Queue()
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None

    def _ensure_worker(self) -> None:
        if self._worker is not None:
            return
        with self._lock:
            if self._worker is not None:
                return
            import atexit
            self._worker = threading.Thread(
                target=self._run, name="yaml-stamp-writer", daemon=True)
            self._worker.start()
            atexit.register(self.flush)

    def submit(self, worktree_id: str, **fields) -> None:
        provided = {k: v for k, v in fields.items() if v is not None}
        with self._lock:
            cur = self._pending.setdefault(worktree_id, {})
            cur.update(provided)
        self._q.put(worktree_id)
        self._ensure_worker()

    def submit_mux(self, worktree_id: str, live: bool, *,
                   refresh: bool, throttle_secs: float) -> None:
        """Coalesce a cached mux-liveness intent for a worktree (last wins).

        Stored under the reserved ``_mux`` key so it is applied (via the existing
        sync :func:`_stamp_liveness`) in the same single-writer drain as the
        session-state fields -- serialized against every other YAML write, off
        the caller's (UI) thread.
        """
        with self._lock:
            cur = self._pending.setdefault(worktree_id, {})
            cur["_mux"] = (bool(live), bool(refresh), float(throttle_secs))
        self._q.put(worktree_id)
        self._ensure_worker()

    def _run(self) -> None:
        while True:
            worktree_id = self._q.get()
            try:
                self._apply(worktree_id)
            finally:
                self._q.task_done()

    def _apply(self, worktree_id: str) -> None:
        with self._lock:
            fields = self._pending.pop(worktree_id, None)
        if not fields:
            return
        mux = fields.pop("_mux", None)
        try:
            if fields:
                _apply_session_state_stamp(worktree_id, **fields)
            if mux is not None:
                live, refresh, throttle = mux
                _stamp_liveness(
                    worktree_id, live, live_attr="mux_live",
                    at_attr="mux_live_at", refresh=refresh,
                    throttle_secs=throttle)
        except Exception:
            pass

    def flush(self) -> None:
        """Block until every queued stamp has been applied (tests / shutdown).

        Waits for the worker to drain the queue -- including any write already
        in flight (so a caller that reads the YAML right after sees the value) --
        then applies any straggler still pending (e.g. enqueued but the worker
        never started, as at interpreter exit).
        """
        if self._worker is not None:
            self._q.join()
        while True:
            with self._lock:
                if not self._pending:
                    return
                worktree_id = next(iter(self._pending))
            self._apply(worktree_id)


_STAMP_QUEUE = _StampWriteQueue()


def flush_stamp_writes() -> None:
    """Flush any queued session-render-cache stamps (tests / graceful teardown)."""
    _STAMP_QUEUE.flush()


def _stamp_older_than(stamped: str, secs: float) -> bool:
    """True when the ISO ``stamped`` time is older than ``secs`` ago.

    Best-effort: an unparseable stamp counts as stale (allow the refresh), so a
    malformed value self-heals on the next authoritative observation.
    """
    try:
        dt = datetime.fromisoformat(str(stamped))
    except (ValueError, TypeError):
        return True
    now = datetime.now(dt.tzinfo) if dt.tzinfo is not None else datetime.now()
    return (now - dt).total_seconds() > secs


# ---------------------------------------------------------------------------
# Session lifecycle -- asserted head pointer + conclusion + two-way chain
# (agent-fabric vision `single-current-session-per-worktree`). These are the
# ground-layer PRIMITIVES; higher layers (agent-bridge creation guard,
# context-handoff cutover) call them and DERIVE from ``resolved_head_session``
# rather than keeping a rival notion of "current". Each persists via
# ``save_record`` unless ``save=False`` (batch several then save once).
# ---------------------------------------------------------------------------

class _RecordLock:
    """Short-lived lock for a read-modify-write on a tracking YAML.

    Acquires an **in-process** re-entrant per-path lock (``_path_write_lock``) so
    concurrent read-modify-writes in the SAME process -- the picker's background
    stamp threads and a foreground resume -- are serialized on every platform.
    It ALSO takes a real **cross-process** advisory lock on a ``.lock`` sidecar so
    that two *separate* processes (e.g. two Picker reconcilers, or a Picker and a
    foreground CLI) can't interleave their read -> modify -> write and clobber one
    another's update:

    - **POSIX** -- ``fcntl.flock(LOCK_EX)`` on the sidecar fd.
    - **Windows** -- ``msvcrt.locking(LK_NBLCK)`` on the sidecar fd. Windows has
      no ``fcntl``; before dotfiles#1860 the Windows path held ONLY the
      in-process RLock, which is a no-op across processes, so concurrent Picker
      reconcilers' RMW cycles clobbered each other (last-writer-wins silently
      dropping the other's update). The ``msvcrt`` byte-range lock closes that
      gap so cross-process exclusion holds on Windows too.

    **Criticality-aware acquisition (#4547).** The caller picks how it competes:

    - ``blocking=True`` (default) -- a **critical writer** (finalize, a lifecycle
      transition, a handoff). It waits up to ``timeout`` for the sidecar, then
      **proceeds anyway** on the in-process lock alone (graceful degradation; the
      atomic temp+replace retry in ``_atomic_write`` is the last line of defence)
      -- so a critical update is never dropped. ``acquired`` is always True.
      Callers that cannot safely degrade may set ``require_sidecar=True`` to
      raise :class:`TimeoutError` instead.
    - ``blocking=False`` -- a **best-effort background writer** (a Picker sweep's
      liveness/session-state stamp). It makes a **single** non-blocking attempt at
      both the in-process and the sidecar lock; if either is already held it
      **skips** -- ``acquired`` is False and the caller must no-op its write this
      pass (these writers are idempotent and self-heal next sweep). This is the
      guarantee that an inconsequential sweep never blocks, nor is blocked by, a
      critical updater.

    Always check ``acquired`` inside a ``blocking=False`` ``with`` block before
    writing; for the default ``blocking=True`` it is always True.

    **Scope -- keep the lock window to the RMW only (#4547).** Wrap exactly the
    ``load_record -> mutate -> save_record`` window and **never hold the lock
    across network or git I/O** (a fetch, push, rebase, or provider call). Two
    consequences follow:

    - **Foreground CLI verbs** whose RMW is self-contained (``set-pr``,
      ``set-disposition``, ``mark-complete``, resume's ``mark_resumed``, the
      resource-claim verbs, ``set-pr --title-only``) load *inside* a blocking
      lock and save before releasing -- so a best-effort sweep skips while they
      hold it, completing the cooperative protocol.
    - **Long I/O-spanning flows** (``create_pr`` / ``_push_changes_pr`` writes
      that follow a rebase+push, ``finalize``'s terminal status write) load the
      record once and thread it across heavy I/O by design, so they *cannot* be
      one short locked RMW. Their write-atomicity is guaranteed by
      ``_atomic_write`` and their staleness is self-healed by the reconcile
      guards; they deliberately stay outside the fine lock rather than hold it
      across I/O. A pre-I/O sub-write (e.g. ``push_changes`` setting the title
      before the push) is reload-merged under a tight lock instead.
    """

    def __init__(
        self,
        yaml_path: Path,
        timeout: float = 2.0,
        *,
        blocking: bool = True,
        require_sidecar: bool = False,
    ):
        self._yaml_path = yaml_path
        self._lock_path = yaml_path.with_suffix(".lock")
        self._timeout = timeout
        self._blocking = blocking
        self._require_sidecar = require_sidecar
        self._sidecar_key = os.path.normcase(os.path.abspath(str(yaml_path)))
        self._nested_sidecar = False
        self._fd: int | None = None
        self._plock: threading.RLock | None = None
        self._plock_held = False
        # Which cross-process backend actually holds the sidecar, so release
        # frees exactly what was acquired: "posix", "windows", or None.
        self._held: str | None = None
        #: Whether this lock is held (write is safe). Always True for a blocking
        #: acquire; reflects a successful try for a best-effort one.
        self.acquired = False

    def __enter__(self) -> _RecordLock:
        # In-process serialization first (serializes same-process threads on
        # every platform; the cross-process sidecar lock below adds inter-process
        # exclusion).
        self._plock = _path_write_lock(self._yaml_path)
        if self._blocking:
            acquired = (
                self._plock.acquire(timeout=max(0.0, self._timeout))
                if self._require_sidecar
                else self._plock.acquire()
            )
            if not acquired:
                raise TimeoutError(
                    f"timed out acquiring in-process lock {self._yaml_path}"
                )
            self._plock_held = True
        elif self._plock.acquire(blocking=False):
            self._plock_held = True
        else:
            # Another same-process thread holds it -- best-effort skip.
            return self

        try:
            counts = _thread_sidecar_counts()
            if counts.get(self._sidecar_key, 0) > 0:
                counts[self._sidecar_key] += 1
                self._nested_sidecar = True
                self.acquired = True
                return self
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            self._fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR)

            if self._blocking:
                sidecar_acquired = self._acquire_sidecar_blocking()
                if not sidecar_acquired and self._require_sidecar:
                    raise TimeoutError(
                        f"timed out acquiring cross-process lock {self._lock_path}"
                    )
                self.acquired = True  # proceeds even if the sidecar timed out
            elif self._sidecar_try():
                self.acquired = True
            else:
                # Sidecar held by another process -- best-effort skip: drop the
                # fd and the in-process lock so we hold nothing.
                self._release()
            if self.acquired and self._held is not None:
                counts[self._sidecar_key] = (
                    counts.get(self._sidecar_key, 0) + 1)
            return self
        except BaseException:
            self._release()
            raise

    def _sidecar_try(self) -> bool:
        """One **non-blocking** attempt at the cross-process sidecar lock.

        Returns True (and sets ``_held``) on success, False if it is currently
        held by another process. On an exotic platform with neither ``fcntl`` nor
        ``msvcrt`` there is no cross-process backend, so the in-process lock is
        the ceiling and this returns True (proceed).
        """
        try:
            import fcntl as _fcntl
        except ImportError:
            return self._sidecar_try_windows()
        try:
            _fcntl.flock(self._fd, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
            self._held = "posix"
            return True
        except (OSError, BlockingIOError):
            return False

    def _sidecar_try_windows(self) -> bool:
        try:
            import msvcrt as _msvcrt
        except ImportError:
            return True  # no cross-process backend; proceed on the in-process lock
        try:
            os.lseek(self._fd, 0, os.SEEK_SET)
            _msvcrt.locking(self._fd, _msvcrt.LK_NBLCK, 1)
            self._held = "windows"
            return True
        except OSError:
            return False

    def _acquire_sidecar_blocking(self) -> bool:
        """Retry :meth:`_sidecar_try` until it wins or ``timeout`` elapses; on
        timeout, report failure so the caller can degrade or fail closed."""
        import time
        deadline = time.monotonic() + self._timeout
        while True:
            if self._sidecar_try():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def _release(self) -> None:
        counts = _thread_sidecar_counts()
        if self._nested_sidecar:
            depth = counts.get(self._sidecar_key, 0)
            if depth <= 1:
                counts.pop(self._sidecar_key, None)
            else:
                counts[self._sidecar_key] = depth - 1
            self._nested_sidecar = False
            self.acquired = False
            if self._plock_held and self._plock is not None:
                self._plock.release()
                self._plock_held = False
            return
        try:
            if self._fd is not None:
                try:
                    if self._held == "posix":
                        try:
                            import fcntl as _fcntl
                            _fcntl.flock(self._fd, _fcntl.LOCK_UN)
                        except (ImportError, OSError):
                            pass
                    elif self._held == "windows":
                        try:
                            import msvcrt as _msvcrt
                            os.lseek(self._fd, 0, os.SEEK_SET)
                            _msvcrt.locking(self._fd, _msvcrt.LK_UNLCK, 1)
                        except (ImportError, OSError):
                            pass
                    os.close(self._fd)
                finally:
                    if self._held is not None:
                        depth = counts.get(self._sidecar_key, 0)
                        if depth <= 1:
                            counts.pop(self._sidecar_key, None)
                        else:
                            counts[self._sidecar_key] = depth - 1
                    self._fd = None
                    self._held = None
        finally:
            if self._plock_held and self._plock is not None:
                self._plock.release()
                self._plock_held = False

    def __exit__(self, *_: object) -> None:
        self._release()


from .tracking_lifecycle import (  # noqa: F401
    SessionLifecycleError,
    _append_head_transition,
    _cancel_pending_handoffs,
    _ensure_head_ledger,
    _handoff_state,
    _next_lifecycle_revision,
    _pending_handoffs_all_from_yielded,
    associate_handoff_candidate,
    conclude_session,
    create_new_record,
    create_new_record_if_absent,
    link_handoff,
    link_succession,
    open_handoff,
    repair_head_cache,
    set_head_session,
)
from .tracking_session_registry import (  # noqa: F401
    REPO_FRESHNESS_MAX_AGE_S,
    _end_session_activation,
    _ensure_activation_history,
    _load_repo_freshness,
    _repo_freshness_path,
    _start_session_activation,
    deregister_session,
    is_repo_fetch_fresh,
    record_repo_fetch_confirmed,
    register_session,
    repo_fetch_confirmed_at,
    seal_worktree_identity,
    stop_fsmonitor_daemon,
)
