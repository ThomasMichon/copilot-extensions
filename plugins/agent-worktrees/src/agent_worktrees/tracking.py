"""Worktree tracking YAML -- read, write, and update operations.

Each worktree gets a YAML file at ~/.{project}/worktrees/{id}.yaml
tracking its lifecycle state.
"""

from __future__ import annotations

import math
import os
import re
import tempfile
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml

from . import config as cfg
from . import disposition_history, obligations
from .effort_focus import ActiveEffort, active_effort_from_mapping

#: Max length of an AGENT-ASSERTED worktree title. Agent titles must fit the mux
#: status bar (120-col default) and the Worktree Picker's table rows; longer prose
#: belongs in the disposition ``summary`` (Picker actions menu / ``status
#: --history``). Enforced by :func:`cap_title` on the ``status --title`` write
#: paths (NOT on auto-derived session-summary titles, which the bar truncates for
#: display).
TITLE_MAX = 30

WorktreeStatus = Literal["active", "complete", "pushed", "finalized", "orphaned"]

# A Copilot session's asserted lifecycle state within its worktree
# (session-lifecycle / agent-fabric vision `single-current-session-per-worktree`):
#   * "active"     -- a current, resumable session (the default; a stopped or
#                     ended session is still active/resumable until concluded).
#   * "handed-off" -- concluded *into* a successor via a handoff cutover.
#   * "concluded"  -- deliberately finished / sunset.
# Conclusion is an ASSERTED act, never inferred from liveness. Absent (legacy
# records) = "active", so no migration is needed.
SessionState = Literal["active", "handed-off", "concluded"]
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
        raise ValueError(
            f"{field} must be between 0 and {MAX_PERSISTED_COUNTER}"
        )
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
        attempt = _bounded_nonnegative_int(
            value.get("attempt"), field="dispatch_attempt.attempt"
        )
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
    head_pushed_at: str = ""  # ISO timestamp when this exact head was published
    attribution_head: str = ""
    patch_id: str = ""       # squash-invariant patch-id of base..head (#898)
    url: str = ""
    number: int | None = None
    provider: str = ""
    repo: str = ""           # target repo "owner/name"; default = worktree repo
    opened_at: str = ""      # ISO timestamp the PR record was opened
    closed_at: str = ""      # ISO timestamp the PR reached a terminal state


# PR lifecycle states that are still live (the PR can still receive pushes).
# Anything else (merged/closed) is terminal.
_PR_NON_TERMINAL = ("", "creating", "open")


def _pr_is_terminal(pr: PRRecord) -> bool:
    """Return True when a PR has reached a terminal (merged/closed) state."""
    return pr.state not in _PR_NON_TERMINAL


# ---------------------------------------------------------------------------
# Resource claims -- the outbound claim ledger (agent-fabric `resource-claims`)
# ---------------------------------------------------------------------------

# The kinds of outbound resource a worktree can own and claim. ``worktree`` is
# a cross-repo worktree it spun up; the others are placeholders the ledger view
# already understands so later phases can journal them without a schema change.
ResourceKind = Literal["worktree", "codespace", "container", "ssh", "workdir", "pr"]

# Claim disposition (resource-obligation-settlement): "active" while unsettled
# work still rides on the resource, "at-rest" once that work is safe (merged /
# off-box / itself finalized) but the claim is still held, "released" once the
# owner explicitly lets go. Unknown/absent degrades to "active" so a stray value
# never hides a live claim from the reap-safety check. The canonical vocabulary
# + predicates live in ``obligations``; this tuple is the set of dispositions
# that still mean "held" (claim not torn down) -- both active and at-rest -- used
# by ``is_live`` / ``live_resources`` for reap-safety.
_CLAIM_LIVE_STATES: tuple[str, ...] = ("", "active", "at-rest")


@dataclass
class ClaimRef:
    """A parsed qualified reference to a claimed resource / owning worktree.

    Canonical string form: ``<machine>/<project>/<worktree_id>[#<session>]`` --
    enough to resolve **across repos and machines**, unlike the bare same-repo
    ``caller_worktree`` (which parses here as machine=None, project=None so both
    can share one parser). ``worktree_id`` is always present; the coarser fields
    are None when the ref was bare.
    """

    worktree_id: str
    machine: str | None = None
    project: str | None = None
    session: str | None = None

    @property
    def is_qualified(self) -> bool:
        """True when the ref carries machine + project (cross-repo-resolvable)."""
        return bool(self.machine and self.project)

    @property
    def is_anchor(self) -> bool:
        """True when this ref names a repo's **anchor** checkout, not a worktree.

        An anchor owner uses the reserved ``worktree_id`` sentinel
        :data:`ANCHOR_ID` (``@anchor``) -- a singleton/whole-repo enlistment that
        is worked in place and, unlike an ephemeral worktree, is **permanent**.
        Liveness and reclaim treat it differently (see
        :func:`claimant.local_claimant_alive`).
        """
        return self.worktree_id == ANCHOR_ID

    def canonical(self) -> str:
        """Render back to the canonical string form."""
        return format_claim_ref(
            self.machine, self.project, self.worktree_id, self.session
        )


#: Reserved ``worktree_id`` sentinel naming a repo's **anchor** checkout (its
#: permanent, whole-repo enlistment) as a claimable owner -- as opposed to an
#: ephemeral worktree. It can't collide with a real worktree_id (those are
#: timestamped ``<host>-<os>-<ts>-<hash>``) and ``@`` is filesystem-safe, so the
#: anchor's per-project claim ledger lives at
#: ``project_dir(project)/worktrees/@anchor.yaml`` and its ref is the ordinary
#: qualified ``<machine>/<project>/@anchor`` (no ref-grammar change).
ANCHOR_ID = "@anchor"


def format_anchor_ref(
    machine: str | None,
    project: str | None,
    session: str | None = None,
) -> str:
    """Build a canonical **anchor** owner ref ``<machine>/<project>/@anchor``.

    Thin wrapper over :func:`format_claim_ref` with the reserved
    :data:`ANCHOR_ID` sentinel -- the accountable-owner analog of a worktree ref
    for a singleton/whole-repo enlistment worked in its anchor checkout.
    """
    return format_claim_ref(machine, project, ANCHOR_ID, session)


def format_claim_ref(
    machine: str | None,
    project: str | None,
    worktree_id: str,
    session: str | None = None,
) -> str:
    """Build the canonical ``<machine>/<project>/<worktree_id>[#<session>]`` ref.

    When machine/project are absent the ref degrades to the bare
    ``worktree_id`` (the legacy same-repo form), so a same-repo owner reads
    identically through :func:`parse_claim_ref`.
    """
    core = worktree_id
    if machine and project:
        core = f"{machine}/{project}/{worktree_id}"
    return f"{core}#{session}" if session else core


def parse_claim_ref(ref: str) -> ClaimRef | None:
    """Parse a claim ref string into a :class:`ClaimRef`, or None if empty.

    Accepts both the qualified ``machine/project/worktree_id[#session]`` form
    and a bare ``worktree_id`` (legacy / same-repo). A malformed value never
    raises -- the worktree_id is recovered best-effort so a stray ref cannot
    crash a reap/ledger read.
    """
    if not ref:
        return None
    body, _, session = ref.partition("#")
    session_val = session or None
    parts = body.split("/")
    if len(parts) >= 3:
        machine, project = parts[0], parts[1]
        worktree_id = "/".join(parts[2:])
        return ClaimRef(
            worktree_id=worktree_id,
            machine=machine or None,
            project=project or None,
            session=session_val,
        )
    return ClaimRef(worktree_id=body, session=session_val)


@dataclass
class ResourceClaim:
    """One outbound resource a worktree owns (an entry in its claim ledger).

    Self-describing like ``PRRecord``: it carries its own ``kind`` and a
    qualified ``ref`` (for a ``worktree`` kind, a
    ``machine/project/worktree_id`` ref; for others a URL / host / path). The
    forward list of these lives on the *owner's* record; the matching backward
    link lives as ``owner_ref`` on the *resource's* record.
    """

    kind: str = "worktree"      # ResourceKind
    ref: str = ""               # qualified target ref (kind-specific)
    created_at: str = ""        # ISO timestamp the claim was journaled
    state: str = "active"       # disposition: active | at-rest | released
    note: str = ""              # optional human label
    handoff_bundle: str = ""    # offered bundle reserving state mutation

    @property
    def is_live(self) -> bool:
        """True while the owner still **holds** this resource (not released).

        Both ``active`` and ``at-rest`` are held -- ``at-rest`` means the *work*
        is settled but the claim itself is not yet torn down. Only ``released``
        (an explicit hand-back) is not live. Reap-safety reads this.
        """
        return self.state in _CLAIM_LIVE_STATES

    @property
    def is_unsettled(self) -> bool:
        """True when unsettled work still rides on the resource (blocks finalize).

        The gate predicate (resource-obligation-settlement): ``active`` -- and a
        missing/unknown disposition, conservatively -- blocks; ``at-rest`` and
        ``released`` do not.
        """
        return obligations.blocks_finalize(self.state)

    @property
    def is_at_rest(self) -> bool:
        """True when the resource's work is safe but the claim is still held."""
        return obligations.is_at_rest(self.state)

    @property
    def is_abandoned(self) -> bool:
        """True when the reclaim sweep abandoned this obligation (Phase 4)."""
        return obligations.is_abandoned(self.state)


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
    session_backend_opaque: bool = field(
        default=False, repr=False, compare=False
    )
    session_backend_raw: object = field(
        default=None, repr=False, compare=False
    )
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
    # worktree-status-core: the agent-asserted DISPOSITION overlay -- orthogonal
    # to git/session state (which cannot tell "done" from "finalized-with-
    # follow-ups"). Set via `agent-worktrees status`; absent (legacy) = the safe
    # default (not flagged, no summary). Rendered as a Picker overlay + fed to
    # the prune verdict. The live "pulse" (assistant.intent) is a SEPARATE
    # sidecar, never stored on this durable record.
    follow_up: bool = False
    summary: str = ""
    status_note_at: str | None = None
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
        if entry is None or entry.state in _CONCLUDED_SESSION_STATES:
            return None
        return transition.session_id

    @property
    def resolved_head_session(self) -> str | None:
        """The worktree's current session -- replayed ledger, else legacy state.

        Resolution (session-lifecycle):
          1. when a transition ledger exists, replay its highest monotonic
             revision; ``head_session`` is only a repairable cache;
          2. otherwise the stored ``head_session`` when it names a session that still
             exists and is **not** concluded/handed-off (a stale head that was
             concluded without advancing does not win);
          3. otherwise the **newest non-concluded** session in ``sessions``
             (by list order -- registration order), preserving today's
             "latest is current" behavior for un-annotated records;
          4. otherwise None (no sessions, or all concluded).

        This is the record-local head. Filesystem-precise "latest by
        workspace.yaml mtime" resolution still lives in ``sessions.py``; this
        derivation is authoritative for the *asserted* lifecycle.
        """
        if self.head_transitions:
            return self.replayed_head_session
        if self.head_session:
            entry = self.session_entry(self.head_session)
            if entry is not None and entry.state not in _CONCLUDED_SESSION_STATES:
                return self.head_session
        for entry in reversed(self.sessions or ()):
            if entry.state not in _CONCLUDED_SESSION_STATES:
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
        """Path to this record's YAML file in the tracking directory."""
        from . import config as cfg

        return cfg.tracking_dir() / f"{self.worktree_id}.yaml"


def _valid_relation_session_id(session_id: str) -> bool:
    return bool(
        session_id
        and session_id not in {".", ".."}
        and "/" not in session_id
        and "\\" not in session_id
        and "\x00" not in session_id
        and Path(session_id).name == session_id
    )


def _normalize_controller_ref(
    controller_ref: str,
    *,
    session_id: str | None = None,
) -> tuple[str, ClaimRef]:
    """Validate and canonicalize one controller ClaimRef."""
    if not controller_ref or controller_ref.count("#") > 1:
        raise ControllerRelationError(
            "controller_ref must be a worktree ClaimRef"
        )
    body, _, ref_session = controller_ref.partition("#")
    parts = body.split("/")
    if len(parts) not in (1, 3) or any(not part for part in parts):
        raise ControllerRelationError(
            "controller_ref must be worktree_id or "
            "machine/project/worktree_id[#session]"
        )
    parsed = parse_claim_ref(controller_ref)
    if parsed is None or not parsed.worktree_id:
        raise ControllerRelationError(
            "controller_ref must identify a worktree"
        )
    if any(token in parsed.worktree_id for token in ("/", "\\", "\x00")):
        raise ControllerRelationError(
            "controller_ref worktree_id must be one path-safe identifier"
        )
    exact_session = session_id or ref_session or None
    if session_id and ref_session and session_id != ref_session:
        raise ControllerRelationError(
            "controller_ref session does not match controller_session_id"
        )
    if exact_session and not _valid_relation_session_id(exact_session):
        raise ControllerRelationError(
            f"invalid controller session id {exact_session!r}"
        )
    normalized = format_claim_ref(
        parsed.machine,
        parsed.project,
        parsed.worktree_id,
        exact_session,
    )
    return normalized, ClaimRef(
        worktree_id=parsed.worktree_id,
        machine=parsed.machine,
        project=parsed.project,
        session=exact_session,
    )


def _controller_ref_key(controller_ref: str | None) -> tuple[
    str | None, str | None, str
] | None:
    if not controller_ref:
        return None
    _normalized, parsed = _normalize_controller_ref(controller_ref)
    return parsed.machine, parsed.project, parsed.worktree_id


def _controller_refs_overlap(
    left: str | None,
    right: str | None,
) -> bool:
    left_key = _controller_ref_key(left)
    right_key = _controller_ref_key(right)
    if left_key is None or right_key is None:
        return False
    if left_key[2] != right_key[2]:
        return False
    left_qualified = bool(left_key[0] and left_key[1])
    right_qualified = bool(right_key[0] and right_key[1])
    return (
        left_key == right_key
        if left_qualified and right_qualified
        else True
    )


def controller_relation_to_dict(
    relation: ControllerRelation,
) -> dict[str, object]:
    """Render one normalized controller relation for JSON surfaces."""
    return {
        "kind": relation.kind,
        "source": relation.source,
        "controller_ref": relation.controller_ref,
        "controller_session_id": relation.controller_session_id,
        "state": relation.state,
        "relation_revision": relation.relation_revision,
        "created_at": relation.created_at,
        "ended_at": relation.ended_at,
    }


def _mark_controller_projection_dirty(
    record: WorktreeRecord,
    *session_ids: str | None,
) -> None:
    dirty = set(getattr(record, "_controller_projection_dirty", set()))
    dirty.update(
        session_id for session_id in session_ids
        if session_id and _valid_relation_session_id(session_id)
    )
    record._controller_projection_dirty = dirty


def _next_controller_revision(record: WorktreeRecord) -> int:
    highest = max(
        (relation.relation_revision for relation in record.controllers),
        default=0,
    )
    revision = max(record.controller_revision, highest) + 1
    if revision > MAX_PERSISTED_COUNTER:
        raise ControllerRelationError("controller revision counter is exhausted")
    record.controller_revision = revision
    return revision


def _limit_controller_relations(
    relations: list[ControllerRelation],
) -> tuple[list[ControllerRelation], list[ControllerRelation]]:
    if len(relations) <= _MAX_CONTROLLER_RELATIONS:
        return relations, []
    active = sorted(
        (relation for relation in relations if relation.state == "active"),
        key=lambda relation: relation.relation_revision,
    )
    if len(active) > _MAX_CONTROLLER_RELATIONS:
        raise ControllerRelationError(
            f"at most {_MAX_CONTROLLER_RELATIONS} active controller relations "
            "may be recorded"
        )
    ended = sorted(
        (relation for relation in relations if relation.state == "ended"),
        key=lambda relation: relation.relation_revision,
    )
    keep_ended = _MAX_CONTROLLER_RELATIONS - len(active)
    retained = active + ended[-keep_ended:] if keep_ended else active
    retained_ids = {id(relation) for relation in retained}
    removed = [
        relation for relation in relations if id(relation) not in retained_ids
    ]
    retained.sort(key=lambda relation: relation.relation_revision)
    return retained, removed


def _validate_controller_relation_set(
    relations: list[ControllerRelation],
) -> None:
    for index, relation in enumerate(relations):
        for prior in relations[:index]:
            if (
                relation.controller_session_id
                and relation.controller_session_id
                == prior.controller_session_id
            ):
                raise ControllerRelationError(
                    "controller session is assigned to multiple relations"
                )
            if _controller_refs_overlap(
                relation.controller_ref, prior.controller_ref
            ):
                raise ControllerRelationError(
                    "controller worktree is assigned to multiple relations"
                )


def _derive_initial_controller_relations(
    *,
    machine: str,
    project: str,
    owner_ref: str | None,
    caller_worktree: str | None,
    parent_session: str | None,
    created_at: str,
) -> tuple[list[ControllerRelation], int]:
    """Derive deterministic initial control from legacy creation metadata."""
    if parent_session and not _valid_relation_session_id(parent_session):
        raise ControllerRelationError(
            f"invalid controller session id {parent_session!r}"
        )
    relations: list[ControllerRelation] = []

    def add_reference(
        source: ControllerRelationSource,
        raw_ref: str,
    ) -> None:
        normalized, parsed = _normalize_controller_ref(raw_ref)
        for relation in relations:
            if _controller_refs_overlap(relation.controller_ref, normalized):
                if parsed.session and not relation.controller_session_id:
                    relation.controller_session_id = parsed.session
                    relation.controller_ref = normalized
                elif (
                    parsed.is_qualified
                    and relation.controller_ref
                    and not all(
                        _controller_ref_key(relation.controller_ref)[:2]
                    )
                ):
                    relation.controller_ref = normalized
                return
            if (parsed.session and
                    relation.controller_session_id == parsed.session):
                if relation.controller_ref is None:
                    relation.controller_ref = normalized
                    relation.kind = "worktree"
                return
        relations.append(ControllerRelation(
            kind="worktree",
            source=source,
            controller_ref=normalized,
            controller_session_id=parsed.session,
            relation_revision=len(relations) + 1,
            created_at=created_at,
        ))

    if owner_ref:
        add_reference("owner-ref", owner_ref)
    if caller_worktree:
        caller_ref = caller_worktree
        parsed_caller = parse_claim_ref(caller_worktree)
        matches_richer_owner = bool(
            parsed_caller is not None
            and not parsed_caller.is_qualified
            and any(
                (
                    key := _controller_ref_key(
                        relation.controller_ref
                    )
                ) is not None
                and key[2] == parsed_caller.worktree_id
                for relation in relations
            )
        )
        if (
            parsed_caller is not None
            and not parsed_caller.is_qualified
            and machine
            and project
            and not matches_richer_owner
        ):
            caller_ref = format_claim_ref(
                machine,
                project,
                parsed_caller.worktree_id,
                parsed_caller.session,
            )
        add_reference("caller-worktree", caller_ref)
    if parent_session:
        matching = next((relation for relation in relations
                         if relation.source == "caller-worktree"), None)
        if matching is None:
            matching = next((relation for relation in relations
                             if relation.controller_session_id == parent_session), None)
        if matching is None:
            unbound_refs = [
                relation for relation in relations
                if relation.controller_session_id is None
            ]
            if len(unbound_refs) == 1:
                matching = unbound_refs[0]
                matching.controller_session_id = parent_session
                normalized, _parsed = _normalize_controller_ref(
                    matching.controller_ref or "",
                    session_id=parent_session,
                )
                matching.controller_ref = normalized
            else:
                relations.append(ControllerRelation(
                    kind="session",
                    source="parent-session",
                    controller_session_id=parent_session,
                    relation_revision=len(relations) + 1,
                    created_at=created_at,
                ))
    return relations, len(relations)


def derive_legacy_controller_relations(
    record: WorktreeRecord,
) -> list[ControllerRelation]:
    """Derive explicit controller state from one legacy creation record.

    Ordinary loads and saves deliberately leave legacy creation fields alone.
    This helper is reserved for explicit doctor/backfill flows.
    """
    if record.controller_metadata_opaque:
        raise ControllerRelationError(
            "controller metadata contains unsupported entries"
        )
    if record.controller_revision or record.controllers:
        return []
    if (
        record.controller_raw_revision_present
        or record.controller_raw_entries_present
    ):
        return []
    if not (record.owner_ref or record.caller_worktree or record.parent_session):
        return []
    relations, _revision = _derive_initial_controller_relations(
        machine=record.machine,
        project=record.repo,
        owner_ref=record.owner_ref,
        caller_worktree=record.caller_worktree,
        parent_session=record.parent_session,
        created_at=record.started_at,
    )
    return relations


def backfill_legacy_controller_relations(
    record: WorktreeRecord,
    *,
    path: Path | None = None,
) -> list[ControllerRelation]:
    """Persist controller relations derived from legacy creation metadata."""
    target = path or record.yaml_path
    with _RecordLock(target, require_sidecar=True):
        authoritative = load_record(target) if target.exists() else record
        relations = derive_legacy_controller_relations(authoritative)
        if not relations:
            _sync_record_instance(record, authoritative)
            return []
        authoritative.controllers = relations
        authoritative.controller_revision = max(
            relation.relation_revision for relation in relations
        )
        _mark_controller_projection_dirty(
            authoritative,
            *(
                relation.controller_session_id
                for relation in relations
                if relation.controller_session_id
            ),
        )
        _save_record_unlocked(authoritative, target)
    _flush_session_projections(authoritative)
    _sync_record_instance(record, authoritative)
    return relations


def _controller_relation_matches(
    relation: ControllerRelation,
    *,
    controller_ref: str | None,
    controller_session_id: str | None,
) -> bool:
    selector_session = controller_session_id
    if controller_ref is not None:
        normalized, parsed = _normalize_controller_ref(
            controller_ref,
            session_id=controller_session_id,
        )
        selector_session = parsed.session
        if not _controller_refs_overlap(
            relation.controller_ref, normalized
        ):
            return False
    if (selector_session is not None and
            relation.controller_session_id != selector_session):
        return False
    return True


def _sync_record_instance(
    target: WorktreeRecord,
    source: WorktreeRecord,
) -> None:
    """Refresh a caller-held record after a relation-only transaction."""
    if target is source:
        return
    target.controller_revision = source.controller_revision
    target.controllers = source.controllers
    target.controller_metadata_opaque = source.controller_metadata_opaque
    target.controller_raw_revision = source.controller_raw_revision
    target.controller_raw_entries = source.controller_raw_entries
    target.controller_raw_revision_present = (
        source.controller_raw_revision_present)
    target.controller_raw_entries_present = (
        source.controller_raw_entries_present)
    target._controller_projection_dirty = (
        set(getattr(target, "_controller_projection_dirty", set()))
        | set(getattr(source, "_controller_projection_dirty", set()))
    )


def set_controller_relation(
    record: WorktreeRecord,
    *,
    controller_ref: str | None = None,
    controller_session_id: str | None = None,
    source: ControllerRelationSource = "explicit",
    created_at: str | None = None,
    save: bool = True,
    path: Path | None = None,
) -> ControllerRelation:
    """Add or refresh a controller without changing session binding or head.

    The default write path is a locked reload-mutate-save transaction, so two
    processes cannot allocate the same revision from stale snapshots. Callers
    using ``save=False`` must already hold the record lock through their final
    :func:`save_record`.
    """
    if save:
        target = path or record.yaml_path
        with _RecordLock(target, require_sidecar=True):
            authoritative = load_record(target) if target.exists() else record
            relation = set_controller_relation(
                authoritative,
                controller_ref=controller_ref,
                controller_session_id=controller_session_id,
                source=source,
                created_at=created_at,
                save=False,
                path=target,
            )
            _save_record_unlocked(authoritative, target)
        _flush_session_projections(authoritative)
        _sync_record_instance(record, authoritative)
        return relation
    if source not in (
        "explicit", "owner-ref", "caller-worktree", "parent-session"
    ):
        raise ControllerRelationError(f"unsupported controller source {source!r}")
    if record.controller_metadata_opaque:
        raise ControllerRelationError(
            "controller metadata contains unsupported entries; explicit repair "
            "is required before mutation"
        )
    normalized_ref = None
    parsed = None
    if controller_ref:
        normalized_ref, parsed = _normalize_controller_ref(
            controller_ref,
            session_id=controller_session_id,
        )
        controller_session_id = parsed.session
    elif controller_session_id:
        if not _valid_relation_session_id(controller_session_id):
            raise ControllerRelationError(
                f"invalid controller session id {controller_session_id!r}"
            )
    else:
        raise ControllerRelationError(
            "controller_ref or controller_session_id is required"
        )

    ref_matches = [
        candidate for candidate in record.controllers
        if normalized_ref is not None and _controller_refs_overlap(
            candidate.controller_ref, normalized_ref
        )
    ]
    session_matches = [
        candidate for candidate in record.controllers
        if (
            controller_session_id is not None
            and candidate.controller_session_id == controller_session_id
        )
    ]
    if len(ref_matches) > 1 or len(session_matches) > 1:
        raise ControllerRelationError(
            "existing controller identity is ambiguous"
        )
    if (
        ref_matches
        and session_matches
        and ref_matches[0] is not session_matches[0]
    ):
        raise ControllerRelationError(
            "controller_ref and controller_session_id identify "
            "different relations"
        )
    relation = (
        ref_matches[0]
        if ref_matches
        else session_matches[0] if session_matches else None
    )
    prior_session = relation.controller_session_id if relation else None
    if (
        relation is None
        and len(record.active_controllers) >= _MAX_CONTROLLER_RELATIONS
    ):
        raise ControllerRelationError(
            f"at most {_MAX_CONTROLLER_RELATIONS} active controller relations "
            "may be recorded"
        )
    revision = _next_controller_revision(record)
    if relation is None:
        relation = ControllerRelation(
            kind="worktree" if normalized_ref else "session",
            source=source,
            controller_ref=normalized_ref,
            controller_session_id=controller_session_id,
            relation_revision=revision,
            created_at=created_at or _now_iso(),
        )
        record.controllers.append(relation)
    else:
        if normalized_ref:
            relation.controller_ref = normalized_ref
            relation.kind = "worktree"
        if controller_session_id:
            relation.controller_session_id = controller_session_id
        relation.source = source
        relation.state = "active"
        relation.ended_at = None
        relation.relation_revision = revision
        if created_at:
            relation.created_at = created_at

    _validate_controller_relation_set(record.controllers)
    record.controllers, removed = _limit_controller_relations(record.controllers)
    _mark_controller_projection_dirty(
        record,
        prior_session,
        relation.controller_session_id,
        *(item.controller_session_id for item in removed),
    )
    if save:
        save_record(record)
    return relation


def end_controller_relation(
    record: WorktreeRecord,
    *,
    controller_ref: str | None = None,
    controller_session_id: str | None = None,
    ended_at: str | None = None,
    save: bool = True,
    path: Path | None = None,
) -> ControllerRelation:
    """End one exact controller relation without altering the bound head."""
    if save:
        target = path or record.yaml_path
        with _RecordLock(target, require_sidecar=True):
            authoritative = load_record(target) if target.exists() else record
            relation = end_controller_relation(
                authoritative,
                controller_ref=controller_ref,
                controller_session_id=controller_session_id,
                ended_at=ended_at,
                save=False,
                path=target,
            )
            _save_record_unlocked(authoritative, target)
        _flush_session_projections(authoritative)
        _sync_record_instance(record, authoritative)
        return relation
    if controller_ref is None and controller_session_id is None:
        raise ControllerRelationError(
            "controller_ref or controller_session_id is required"
        )
    if record.controller_metadata_opaque:
        raise ControllerRelationError(
            "controller metadata contains unsupported entries; explicit repair "
            "is required before mutation"
        )
    if (controller_session_id is not None and
            not _valid_relation_session_id(controller_session_id)):
        raise ControllerRelationError(
            f"invalid controller session id {controller_session_id!r}"
        )
    matching = [
        relation for relation in record.controllers
        if _controller_relation_matches(
            relation,
            controller_ref=controller_ref,
            controller_session_id=controller_session_id,
        )
    ]
    if len(matching) != 1:
        raise ControllerRelationError(
            "controller relation was not found"
            if not matching else "controller relation selector is ambiguous"
        )
    relation = matching[0]
    revision = _next_controller_revision(record)
    relation.state = "ended"
    relation.ended_at = ended_at or _now_iso()
    relation.relation_revision = revision
    _mark_controller_projection_dirty(
        record, relation.controller_session_id
    )
    if save:
        save_record(record)
    return relation


def remove_controller_relation(
    record: WorktreeRecord,
    *,
    controller_ref: str | None = None,
    controller_session_id: str | None = None,
    save: bool = True,
    path: Path | None = None,
) -> None:
    """Remove one relation for explicit repair and retract its projection."""
    if save:
        target = path or record.yaml_path
        with _RecordLock(target, require_sidecar=True):
            authoritative = load_record(target) if target.exists() else record
            remove_controller_relation(
                authoritative,
                controller_ref=controller_ref,
                controller_session_id=controller_session_id,
                save=False,
                path=target,
            )
            _save_record_unlocked(authoritative, target)
        _flush_session_projections(authoritative)
        _sync_record_instance(record, authoritative)
        return
    if controller_ref is None and controller_session_id is None:
        raise ControllerRelationError(
            "controller_ref or controller_session_id is required"
        )
    if record.controller_metadata_opaque:
        raise ControllerRelationError(
            "controller metadata contains unsupported entries; explicit repair "
            "is required before mutation"
        )
    if (controller_session_id is not None and
            not _valid_relation_session_id(controller_session_id)):
        raise ControllerRelationError(
            f"invalid controller session id {controller_session_id!r}"
        )
    matching = [
        relation for relation in record.controllers
        if _controller_relation_matches(
            relation,
            controller_ref=controller_ref,
            controller_session_id=controller_session_id,
        )
    ]
    if len(matching) != 1:
        raise ControllerRelationError(
            "controller relation was not found"
            if not matching else "controller relation selector is ambiguous"
        )
    relation = matching[0]
    _next_controller_revision(record)
    record.controllers.remove(relation)
    _mark_controller_projection_dirty(
        record, relation.controller_session_id
    )
    if save:
        save_record(record)


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
    return PRRecord(
        state=str(raw.get("state", "")),
        branch=str(raw.get("branch", "")),
        base_sha=str(raw.get("base_sha", "")),
        head_sha=str(raw.get("head_sha", "")),
        head_pushed_at=str(raw.get("head_pushed_at", "")),
        attribution_head=str(raw.get("attribution_head", "")),
        patch_id=str(raw.get("patch_id", "")),
        url=str(raw.get("url", "")),
        number=num_val,
        provider=str(raw.get("provider", "")),
        # A legacy record without a per-PR repo targets the worktree's repo.
        repo=str(raw.get("repo", "")) or default_repo,
        opened_at=str(raw.get("opened_at", "")),
        closed_at=str(raw.get("closed_at", "")),
    )


def _pr_to_yaml_dict(pr: PRRecord) -> dict[str, object]:
    """Serialize a PRRecord to a YAML-friendly mapping (lean: omit empties)."""
    d: dict[str, object] = {
        "state": pr.state,
        "branch": pr.branch,
        "base_sha": pr.base_sha,
        "head_sha": pr.head_sha,
        "url": pr.url,
    }
    if pr.head_pushed_at:
        d["head_pushed_at"] = pr.head_pushed_at
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


def load_record(path: Path) -> WorktreeRecord:
    """Load a worktree tracking record from a YAML file."""
    raw = _read_text_with_retry(path)
    try:
        data = yaml.safe_load(raw)
    except yaml.reader.ReaderError:
        # tmichon_microsoft/dotfiles#1789: a stray C0 control char (e.g. BEL)
        # persisted into a
        # value makes the YAML reader raise on every load, wedging all future
        # disposition writes. Self-heal by stripping the illegal control chars
        # and re-parsing; the next save then rewrites the file cleanly. Do not
        # reinterpret unrelated malformed YAML as a repairable record.
        repaired = _strip_control_chars(raw)
        if repaired == raw:
            raise
        data = yaml.safe_load(repaired)

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
                        st_raw if st_raw in ("active", "handed-off", "concluded")
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
                revision = _bounded_nonnegative_int(raw.get("relation_revision", 0), field="controller relation_revision")
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
        follow_up=bool(data.get("follow_up", False)),
        summary=str(data.get("summary", "") or ""),
        active_effort=active_effort_from_mapping(data.get("active_effort")),
        effort_revision=int(data.get("effort_revision", 0) or 0),
        title_asserted=bool(data.get("title_asserted", False)),
        status_note_at=(str(data["status_note_at"])
                        if data.get("status_note_at") else None),
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
    )
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
    # agent-fabric resource-claims: the forward outbound list. Emitted only when
    # non-empty (the common case owns nothing), keeping legacy YAMLs identical.
    if record.resources:
        content += yaml.safe_dump(
            {"resources": [_claim_to_yaml_dict(c) for c in record.resources]},
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


def find_worktree_id_by_cwd(cwd: str) -> str | None:
    """Resolve a worktree_id from a session cwd.

    Matches *cwd* (or any worktree root that is an ancestor of it) against
    the tracked ``worktree_path`` values.  Used by the sessionStart hook to
    associate a session with its worktree when the ``WORKTREE_ID`` env var
    is not present in the hook environment -- the Copilot CLI delivers the
    cwd via the hook's stdin payload instead.

    When several worktree roots match (nested trees), the deepest
    (longest) match wins.  Returns None if no worktree contains *cwd*.
    """
    if not cwd:
        return None
    tracking_path = cfg.tracking_dir()
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


def find_worktree_id_by_session(session_id: str) -> str | None:
    """Resolve a session ID from the active project's tracked worktrees.

    This is the identity fallback for bare resume: the resumed session may keep
    HOME as its recorded cwd, but the sessionStart hook has explicitly bound
    that exact session ID to its intended worktree. Ambiguous or absent matches
    return ``None`` rather than guessing.
    """
    if not session_id:
        return None
    tracking_path = cfg.tracking_dir()
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
#: wedging all future disposition writes (tmichon_microsoft/dotfiles#1789). A
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
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
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
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
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

class SessionLifecycleError(ValueError):
    """Raised when an asserted session transition names an unknown session."""


def _next_lifecycle_revision(
    record: WorktreeRecord,
    *session_ids: str,
) -> int:
    highest = max(
        (transition.revision for transition in record.head_transitions),
        default=0,
    )
    record.lifecycle_revision = max(record.lifecycle_revision, highest) + 1
    dirty = set(getattr(record, "_session_projection_dirty", set()))
    for session_id in session_ids:
        entry = record.session_entry(session_id)
        if entry is None:
            continue
        entry.relation_revision = record.lifecycle_revision
        dirty.add(session_id)
    record._session_projection_dirty = dirty
    return record.lifecycle_revision


def _append_head_transition(
    record: WorktreeRecord,
    session_id: str | None,
    *,
    reason: str,
    handoff_ordinal: int | None = None,
    at: str | None = None,
    related_session_ids: tuple[str, ...] = (),
) -> HeadTransition:
    if session_id is not None and record.session_entry(session_id) is None:
        raise SessionLifecycleError(
            f"session {session_id} is not tracked on worktree "
            f"{record.worktree_id}"
        )
    prior_head = record.resolved_head_session
    affected = tuple(dict.fromkeys(
        session
        for session in (prior_head, session_id, *related_session_ids)
        if session is not None
    ))
    transition = HeadTransition(
        revision=_next_lifecycle_revision(record, *affected),
        session_id=session_id,
        reason=reason,
        at=at or _now_iso(),
        handoff_ordinal=handoff_ordinal,
    )
    record.head_transitions.append(transition)
    record.head_session = session_id
    record.head_revision = transition.revision
    return transition


def repair_head_cache(record: WorktreeRecord) -> bool:
    """Repair the materialized head cache from the authoritative ledger."""
    transition = record.replayed_head_transition
    if transition is None:
        expected = record.resolved_head_session
        if record.head_session is None or record.head_session == expected:
            return False
        _append_head_transition(
            record, expected, reason="legacy-cache-repair",
        )
        return True
    expected = record.replayed_head_session
    if (
        record.head_session == expected
        and record.head_revision == transition.revision
    ):
        return False
    record.head_session = expected
    record.head_revision = transition.revision
    return True


def _ensure_head_ledger(record: WorktreeRecord) -> None:
    """Seed a legacy record's ledger from its current deterministic head."""
    if record.head_transitions:
        return
    legacy_head = record.resolved_head_session
    if legacy_head is not None:
        _append_head_transition(
            record, legacy_head, reason="legacy-import",
        )


def open_handoff(
    record: WorktreeRecord,
    predecessor_id: str,
    token: str,
    *,
    opened_at: str | None = None,
    save: bool = True,
) -> SessionHandoff:
    """Open an idempotent, numbered handoff intent for one predecessor."""
    if not token:
        raise SessionLifecycleError("handoff token must not be empty")
    predecessor = record.session_entry(predecessor_id)
    if predecessor is None:
        raise SessionLifecycleError(
            f"predecessor {predecessor_id} is not tracked on worktree "
            f"{record.worktree_id}"
        )
    for existing in record.handoffs:
        if existing.token != token:
            continue
        if existing.predecessor != predecessor_id:
            raise SessionLifecycleError(
                f"handoff token {token} already belongs to predecessor "
                f"{existing.predecessor}"
            )
        return existing
    _ensure_head_ledger(record)
    for existing in record.handoffs:
        if (
            existing.predecessor == predecessor_id
            and existing.state == "pending"
        ):
            existing.state = "cancelled"
    record.handoff_counter = max(
        record.handoff_counter,
        max((handoff.ordinal for handoff in record.handoffs), default=0),
    ) + 1
    handoff = SessionHandoff(
        ordinal=record.handoff_counter,
        token=token,
        predecessor=predecessor_id,
        state="pending",
        opened_at=opened_at or _now_iso(),
    )
    record.handoffs.append(handoff)
    _next_lifecycle_revision(record, predecessor_id)
    if save:
        save_record(record)
    return handoff


def link_handoff(
    record: WorktreeRecord,
    token: str,
    successor_id: str,
    *,
    linked_at: str | None = None,
    save: bool = True,
) -> SessionHandoff:
    """Link the exact token's predecessor to ``successor_id`` atomically."""
    successor = record.session_entry(successor_id)
    if successor is None:
        raise SessionLifecycleError(
            f"successor {successor_id} is not tracked on worktree "
            f"{record.worktree_id}"
        )
    if successor.state in _CONCLUDED_SESSION_STATES:
        raise SessionLifecycleError(
            f"successor {successor_id} is already {successor.state}"
        )
    handoff = next(
        (candidate for candidate in record.handoffs
         if candidate.token == token),
        None,
    )
    if handoff is None:
        raise SessionLifecycleError(
            f"handoff token {token} is not tracked on worktree "
            f"{record.worktree_id}"
        )
    if handoff.state == "linked":
        if handoff.successor != successor_id:
            raise SessionLifecycleError(
                f"handoff token {token} is already linked to "
                f"{handoff.successor}"
            )
        return handoff
    if handoff.state != "pending":
        raise SessionLifecycleError(
            f"handoff token {token} is {handoff.state}, not pending"
        )
    if handoff.candidate and handoff.candidate != successor_id:
        raise SessionLifecycleError(
            f"handoff token {token} is associated with candidate "
            f"{handoff.candidate}, not {successor_id}"
        )
    predecessor = record.session_entry(handoff.predecessor)
    if predecessor is None:
        raise SessionLifecycleError(
            f"handoff predecessor {handoff.predecessor} is not tracked on "
            f"worktree {record.worktree_id}"
        )
    if predecessor.state == "concluded":
        raise SessionLifecycleError(
            f"handoff predecessor {predecessor.session_id} was explicitly "
            "concluded"
        )
    if (
        predecessor.successor is not None
        and predecessor.successor != successor_id
    ):
        raise SessionLifecycleError(
            f"handoff predecessor {predecessor.session_id} already links to "
            f"{predecessor.successor}"
        )
    if (
        successor.predecessor is not None
        and successor.predecessor != predecessor.session_id
    ):
        raise SessionLifecycleError(
            f"handoff successor {successor_id} already follows "
            f"{successor.predecessor}"
        )
    predecessor.state = "handed-off"
    predecessor.successor = successor_id
    successor.state = "active"
    successor.predecessor = predecessor.session_id
    handoff.state = "linked"
    handoff.successor = successor_id
    handoff.linked_at = linked_at or _now_iso()
    _append_head_transition(
        record,
        successor_id,
        reason="handoff-linked",
        handoff_ordinal=handoff.ordinal,
        at=handoff.linked_at,
        related_session_ids=(predecessor.session_id,),
    )
    if save:
        save_record(record)
    return handoff


def associate_handoff_candidate(
    record: WorktreeRecord,
    token: str,
    session_id: str,
    *,
    associated_at: str | None = None,
    save: bool = True,
) -> SessionHandoff:
    """Associate a started successor with a pending token without takeover."""
    successor = record.session_entry(session_id)
    if successor is None:
        raise SessionLifecycleError(
            f"candidate {session_id} is not tracked on worktree "
            f"{record.worktree_id}"
        )
    handoff = next(
        (candidate for candidate in record.handoffs if candidate.token == token),
        None,
    )
    if handoff is None:
        raise SessionLifecycleError(
            f"handoff token {token} is not tracked on worktree "
            f"{record.worktree_id}"
        )
    if handoff.state == "linked":
        if handoff.successor != session_id:
            raise SessionLifecycleError(
                f"handoff token {token} is already linked to "
                f"{handoff.successor}"
            )
        return handoff
    if handoff.state != "pending":
        raise SessionLifecycleError(
            f"handoff token {token} is {handoff.state}, not pending"
        )
    if handoff.candidate and handoff.candidate != session_id:
        raise SessionLifecycleError(
            f"handoff token {token} already has candidate {handoff.candidate}"
        )
    if handoff.candidate != session_id:
        handoff.candidate = session_id
        handoff.candidate_at = associated_at or _now_iso()
        _next_lifecycle_revision(record)
    if save:
        save_record(record)
    return handoff


def _cancel_pending_handoffs(record: WorktreeRecord) -> bool:
    changed = False
    for handoff in record.handoffs:
        if handoff.state == "pending":
            handoff.state = "cancelled"
            changed = True
    return changed


def set_head_session(
    record: WorktreeRecord, session_id: str, *, save: bool = True
) -> None:
    """Assert ``session_id`` as the worktree's current (head) session.

    The session must already be tracked. This is the explicit head move a
    caller makes when it adopts / takes over a worktree.
    """
    _ensure_head_ledger(record)
    if record.resolved_head_session != session_id:
        _append_head_transition(record, session_id, reason="adopted")
    if save:
        save_record(record)


def conclude_session(
    record: WorktreeRecord,
    session_id: str,
    *,
    state: SessionState = "concluded",
    handoff_token: str | None = None,
    save: bool = True,
) -> None:
    """Assert a session's conclusion (``concluded`` or ``handed-off``).

    Conclusion is a deliberate act, never inferred from liveness. When the
    concluded session was the head, a transition explicitly clears the head.
    Another active session is never promoted by list or timestamp order; a
    successor or adopter must assert the next transition.
    """
    if state not in _CONCLUDED_SESSION_STATES:
        raise SessionLifecycleError(
            f"conclude state must be one of {_CONCLUDED_SESSION_STATES}, "
            f"got {state!r}"
        )
    entry = record.session_entry(session_id)
    if entry is None:
        raise SessionLifecycleError(
            f"session {session_id} is not tracked on worktree "
            f"{record.worktree_id}"
        )
    current = record.resolved_head_session
    prior_state = entry.state
    _ensure_head_ledger(record)
    entry.state = state
    if state == "handed-off" and handoff_token and not any(
        handoff.predecessor == session_id
        and handoff.state in ("pending", "linked")
        for handoff in record.handoffs
    ):
        open_handoff(record, session_id, handoff_token, save=False)
    # Ending or handing off the head does not guess a replacement from list
    # order. A successor/adopter must assert the next transition explicitly.
    if current == session_id:
        pending = next(
            (
                handoff for handoff in reversed(record.handoffs)
                if handoff.predecessor == session_id
                and handoff.state == "pending"
            ),
            None,
        )
        _append_head_transition(
            record,
            None,
            reason=state,
            handoff_ordinal=pending.ordinal if pending else None,
        )
    elif prior_state != state:
        _next_lifecycle_revision(record, session_id)
    if save:
        save_record(record)


def link_succession(
    record: WorktreeRecord,
    predecessor_id: str,
    successor_id: str,
    *,
    predecessor_state: SessionState = "handed-off",
    handoff_token: str | None = None,
    save: bool = True,
) -> None:
    """Record a handoff: chain predecessor -> successor and move the head.

    Writes the durable **two-way link** (``predecessor.successor`` and
    ``successor.predecessor``), concludes the predecessor (default
    ``handed-off``), and moves the head to the successor. This is the primitive
    context-handoff's cutover calls so the lineage of sessions in a worktree is
    traversable in both directions. Both sessions must be tracked.
    """
    pred = record.session_entry(predecessor_id)
    succ = record.session_entry(successor_id)
    if pred is None:
        raise SessionLifecycleError(
            f"predecessor {predecessor_id} is not tracked on worktree "
            f"{record.worktree_id}"
        )
    if succ is None:
        raise SessionLifecycleError(
            f"successor {successor_id} is not tracked on worktree "
            f"{record.worktree_id}"
        )
    if predecessor_state == "handed-off":
        token = handoff_token or f"manual-{record.handoff_counter + 1}"
        handoff = open_handoff(
            record, predecessor_id, token, save=False,
        )
        link_handoff(
            record, handoff.token, successor_id, save=False,
        )
    else:
        pred.successor = successor_id
        pred.state = predecessor_state
        succ.predecessor = predecessor_id
        _ensure_head_ledger(record)
        _append_head_transition(
            record, successor_id, reason="succession-linked",
            related_session_ids=(pred.session_id,),
        )
    if save:
        save_record(record)


def create_new_record(
    worktree_id: str,
    branch: str,
    worktree_path: str,
    repo: str,
    machine: str,
    platform_name: str,
    tracking_path: Path,
    *,
    kind: WorktreeKind = "session",
    owner: str | None = None,
    interface: WorktreeInterface | None = None,
    origin: WorktreeOrigin | None = None,
    dispatch_attempt: DispatchAttempt | None = None,
    parent_session: str | None = None,
    caller_worktree: str | None = None,
    owner_ref: str | None = None,
    pair_id: str | None = None,
    pair_role: str | None = None,
    pair_ref: str | None = None,
    pair_kind: str | None = None,
) -> WorktreeRecord:
    """Create and save a new worktree tracking record."""
    now = _now_iso()
    normalized_parent_session = parent_session or None
    normalized_caller_worktree = caller_worktree or None
    normalized_owner_ref = owner_ref or None
    try:
        controllers, controller_revision = _derive_initial_controller_relations(
            machine=machine,
            project=repo,
            owner_ref=normalized_owner_ref,
            caller_worktree=normalized_caller_worktree,
            parent_session=normalized_parent_session,
            created_at=now,
        )
    except ControllerRelationError:
        controllers, controller_revision = [], 0
    record = WorktreeRecord(
        worktree_id=worktree_id,
        branch=branch,
        worktree_path=worktree_path,
        repo=repo,
        machine=machine,
        platform=platform_name,
        started_at=now,
        last_resumed_at=now,
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
        kind=kind,
        owner=owner,
        interface=interface,
        origin=origin,
        dispatch_attempt=dispatch_attempt,
        parent_session=normalized_parent_session,
        controller_revision=controller_revision,
        controllers=controllers,
        caller_worktree=normalized_caller_worktree,
        owner_ref=normalized_owner_ref,
        pair_id=pair_id or None,
        pair_role=pair_role or None,
        pair_ref=pair_ref or None,
        pair_kind=pair_kind or None,
    )
    _mark_controller_projection_dirty(
        record,
        *(
            relation.controller_session_id
            for relation in controllers
        ),
    )
    path = tracking_path / f"{worktree_id}.yaml"
    save_record(record, path)
    return record


def create_new_record_if_absent(
    worktree_id: str,
    branch: str,
    worktree_path: str,
    repo: str,
    machine: str,
    platform_name: str,
    tracking_path: Path,
    *,
    kind: WorktreeKind = "session",
    owner: str | None = None,
    interface: WorktreeInterface | None = None,
    origin: WorktreeOrigin | None = None,
    checkout_managed: bool = True,
) -> tuple[WorktreeRecord, bool]:
    """Create a tracking record once, preserving a concurrent creator's row."""
    path = tracking_path / f"{worktree_id}.yaml"
    tracking_path.mkdir(parents=True, exist_ok=True)
    with _RecordLock(path, require_sidecar=True):
        if path.exists():
            return load_record(path), False
        now = _now_iso()
        record = WorktreeRecord(
            worktree_id=worktree_id,
            branch=branch,
            worktree_path=worktree_path,
            repo=repo,
            machine=machine,
            platform=platform_name,
            started_at=now,
            last_resumed_at=now,
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
            kind=kind,
            owner=owner,
            interface=interface,
            origin=origin,
            checkout_managed=checkout_managed,
        )
        _save_record_unlocked(record, path)
    _flush_session_projections(record)
    return record, True


def load_or_create_anchor_record(
    anchor_path: str,
    repo: str,
    machine: str,
    platform_name: str,
    tracking_path: Path,
) -> WorktreeRecord:
    """Load (or lazily create) a repo's ``@anchor`` claim-ledger record.

    The anchor ledger is an ordinary :class:`WorktreeRecord` keyed by the
    reserved :data:`ANCHOR_ID` sentinel and stamped ``pair_kind="anchor"``,
    stored at ``<tracking_path>/@anchor.yaml`` -- the accountable owner for a
    singleton / whole-repo enlistment worked in its anchor checkout. Created on
    first use so a repo that never journals an anchor claim pays nothing;
    idempotent (an existing ledger is returned as-is). ``branch`` is the
    sentinel :data:`ANCHOR_ID` -- an anchor has no feature branch and its own
    branch is never a settlement input (anchor claims settle by the resource's
    own proof).
    """
    path = tracking_path / f"{ANCHOR_ID}.yaml"
    if path.exists():
        return load_record(path)
    return create_new_record(
        ANCHOR_ID, ANCHOR_ID, anchor_path, repo, machine, platform_name,
        tracking_path, pair_kind="anchor",
    )


def claim_handoff_reservation(
    record: WorktreeRecord, claim: ResourceClaim,
) -> str:
    """Resolve the authoritative nonterminal bundle reserving ``claim``.

    The ledger field is a fast cache. The transaction registry is consulted
    when the cache is absent so the crash window between intent and cache write
    remains mutation-safe. Registry read failures fail closed.
    """
    if claim.handoff_bundle:
        return claim.handoff_bundle
    try:
        from . import claim_handoffs
        source = format_claim_ref(
            record.machine, record.repo, record.worktree_id)
        return claim_handoffs.active_bundle_for_claim(source, claim.ref)
    except Exception:
        return "unverified-handoff-registry"


def add_resource_claim(
    record: WorktreeRecord,
    claim: ResourceClaim,
    *,
    save: bool = True,
) -> ResourceClaim:
    """Journal an outbound resource claim onto ``record`` (dedup by ref).

    If a claim with the same ``ref`` already exists it is refreshed in place
    (kind/state/note/created_at) rather than duplicated, so re-running the
    owning ``run`` wrapper is idempotent. Returns the stored claim.
    """
    if (
        record.status in {"finalizing", "finalized", "orphaned"}
        or (
            record.kind in MANAGED_KINDS
            and record.status in {"complete", "completed"}
        )
    ):
        raise ValueError(
            f"owner worktree {record.worktree_id} is {record.status}; "
            "creator ownership is frozen")
    for existing in record.resources:
        if existing.ref == claim.ref:
            reservation = claim_handoff_reservation(record, existing)
            if reservation:
                equivalent = (
                    existing.kind == claim.kind
                    and existing.state == claim.state
                    and (not claim.note or existing.note == claim.note)
                )
                if equivalent:
                    return existing
                raise ValueError(
                    f"claim {claim.ref} is reserved by handoff bundle "
                    f"{reservation}")
            existing.kind = claim.kind
            existing.state = claim.state
            if claim.note:
                existing.note = claim.note
            if claim.created_at:
                existing.created_at = claim.created_at
            if save:
                save_record(record)
            return existing
    record.resources.append(claim)
    if save:
        save_record(record)
    return claim


def settle_resource_claim(
    record: WorktreeRecord,
    ref: str,
    disposition: str = obligations.AT_REST,
    *,
    save: bool = True,
    path: Path | None = None,
) -> ResourceClaim | None:
    """Set the disposition of one outbound claim by ``ref`` (Phase 3 settlement).

    Flips the matching claim's ``state`` to ``disposition`` (default ``at-rest``:
    the resource's work is safe but the claim is still held) so the owner's
    finalize gate no longer treats it as unsettled. This is the **incremental
    settlement** primitive every hook calls when a resource reaches its own
    close-out. Returns the settled claim, or ``None`` when no claim matches the
    ref (a no-op, degrade-safe). Idempotent -- re-settling to the same value is
    harmless.
    """
    match = next((c for c in record.resources if c.ref == ref), None)
    if match is None:
        return None
    if claim_handoff_reservation(record, match):
        return None
    match.state = obligations.normalize(disposition)
    if save:
        save_record(record, path)
    return match


def sweep_abandoned_obligations(
    record: WorktreeRecord,
    *,
    gone_of: Callable[[ResourceClaim], bool | None],
    safe_of: Callable[[ResourceClaim], bool | None],
    save: bool = True,
    path: Path | None = None,
) -> list[ResourceClaim]:
    """Reclaim ``active`` obligations whose holder is gone AND resource safe (Ph4).

    The never-wedge sweep: for each ``active`` outbound claim on ``record``, the
    injected resolvers report whether the claim's resource holder is **provably
    gone** (``gone_of(claim)`` -- tri-state ``True``/``False``/``None``) and whether
    the resource is **provably safe** (``safe_of(claim)`` -- tri-state). A claim
    is flipped to ``abandoned`` **only** on a definitive *gone-and-safe* verdict
    (:func:`obligations.should_abandon`); an unconfirmed holder or unproven-safe
    resource is left untouched (unknown is spare -- the sweep never fabricates an
    ``at-rest``/``released`` verdict and never abandons on a guess). Both
    resolvers receive the **whole claim** (not just its ref) so they can route by
    *kind* -- e.g. a worktree to the same-machine claimant-liveness check, a
    leaseable resource (codespace/container) to its cross-machine lease
    disposition mirror. Returns the claims it abandoned (empty on a no-op).
    Best-effort: a resolver that raises is treated as ``None`` (spare).
    """
    reclaimed: list[ResourceClaim] = []
    for c in record.resources:
        if not c.is_unsettled:  # only active (blocking) obligations
            continue
        if claim_handoff_reservation(record, c):
            # Offered claims stay creator-owned until accepted/declined.
            continue
        try:
            gone = gone_of(c)
        except Exception:
            gone = None
        try:
            safe = safe_of(c)
        except Exception:
            safe = None
        if obligations.should_abandon(gone=gone, safe=safe):
            c.state = obligations.ABANDONED
            reclaimed.append(c)
    if reclaimed and save:
        save_record(record, path)
    return reclaimed


# Terminal statuses for the CASCADE/orphan model (citadel E1b, #877): a parent
# in one of these states has finished its work, so the outbound worktree
# resources it owned are no longer actively held -- its children are orphans
# (protected henceforth only by their OWN git/PR/session safety, not the
# parent's liveness).
_TERMINAL_OWNER_STATUSES: frozenset[str] = frozenset({"finalized", "orphaned"})


def release_all_resources(
    record: WorktreeRecord, *, save: bool = True
) -> list[ResourceClaim]:
    """Release every live outbound resource claim on ``record`` (cascade).

    Marks each still-live :class:`ResourceClaim` ``released`` so the owner's
    ledger stops asserting it holds those cross-repo worktrees -- used when a
    parent worktree is finalized (citadel E1b, #877): the parent is done, so it
    hands its children back rather than pinning them as claimed forever. The
    child records are untouched (they keep their own ``owner_ref``; the
    claimant-liveness gate now sees the parent as terminal -> gone). Idempotent:
    returns the claims it flipped this call (empty when none were live).
    """
    released = [c for c in record.resources if c.is_live]
    for c in released:
        c.state = "released"
    if released and save:
        save_record(record)
    return released


# ── Durable orphanage: re-homed (abandoned) obligations ──────────────────────
# When a worktree finalizes with ``--abandon``, its still-unsettled outbound
# obligations are released, but the resources they named must not be silently
# *dropped*. They are re-homed to a durable, per-project registry so a later
# cleanup/adoption pass can find and reclaim them ("abandon re-homes
# responsibility rather than dropping it"; resource-obligation-settlement).

def orphanage_path(project: str | None = None) -> Path:
    """The durable per-project registry of re-homed (abandoned) obligations."""
    return cfg.project_dir(project) / "orphaned-obligations.yaml"


def load_orphaned_obligations(project: str | None = None) -> list[dict]:
    """Read the durable orphanage registry (empty when absent/unreadable)."""
    try:
        return load_orphaned_obligations_strict(project)
    except Exception:
        return []


def load_orphaned_obligations_strict(
    project: str | None = None,
) -> list[dict]:
    """Read the orphanage, distinguishing absence from corruption.

    Missing is a valid empty registry. Any read/parse/shape failure raises so an
    ownership-transfer RMW can fail closed instead of overwriting obligations.
    """
    path = orphanage_path(project)
    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid orphanage mapping: {path}")
    items = data.get("orphaned", [])
    if not isinstance(items, list) or not all(
            isinstance(item, dict) for item in items):
        raise ValueError(f"invalid orphanage entries: {path}")
    return list(items)


def rehome_abandoned_obligations(
    claims: Iterable[ResourceClaim],
    *,
    source_worktree: str,
    config: object,
    handoff_to: str | None = None,
    project: str | None = None,
) -> list[dict]:
    """Durably re-home abandoned obligations to the control-plane orphanage.

    Appends each claim to :func:`orphanage_path` with provenance (source
    worktree, machine, project, timestamp) plus the affirmative recipient/flow
    in ``handoff_to`` so the orphaned resource it named is recorded rather than
    dropped. **Idempotent** (dedups by
    ``source_worktree`` + ``ref``). Returns the entries **newly** written.
    Best-effort: any IO failure returns ``[]`` and never raises -- re-homing must
    never break the finalize it rides on.
    """
    try:
        path = orphanage_path(project)
        with _RecordLock(path, require_sidecar=True):
            existing = load_orphaned_obligations_strict(project)
            by_key = {
                (e.get("source_worktree"), e.get("ref")): e for e in existing
            }
            machine = getattr(config, "machine", None)
            proj = project or getattr(config, "repo_name", None)
            now = _now_iso()
            target = (handoff_to or "").strip()
            added: list[dict] = []
            changed = False
            for c in claims:
                key = (source_worktree, c.ref)
                prior = by_key.get(key)
                if prior is not None:
                    # Legacy orphan entries had no target. An explicit retry may
                    # upgrade that empty field, but never overwrite a different
                    # affirmative recipient.
                    if target and not (prior.get("handoff_to") or "").strip():
                        prior["handoff_to"] = target
                        changed = True
                    continue
                entry = {
                    "kind": c.kind, "ref": c.ref, "note": c.note or "",
                    "source_worktree": source_worktree, "machine": machine,
                    "project": proj, "disposition": "abandoned",
                    "abandoned_at": now, "handoff_to": target,
                }
                existing.append(entry)
                added.append(entry)
                by_key[key] = entry
                changed = True
            if changed:
                _atomic_write(
                    path,
                    yaml.safe_dump({"orphaned": existing}, sort_keys=False),
                )
            return added
    except Exception:
        return []


def remove_orphaned_obligations(
    keys: Iterable[tuple[str | None, str | None]],
    *,
    project: str | None = None,
) -> int:
    """Drop settled entries from the durable orphanage registry (the write side
    of the cleanup consumer, resource-obligation-settlement dotfiles#1161).

    ``keys`` is an iterable of ``(source_worktree, ref)`` pairs -- the same
    identity :func:`rehome_abandoned_obligations` dedups on. Every matching
    entry is removed and the file rewritten (deleted when it empties). Returns
    the number of entries removed. **Best-effort**: any IO failure returns ``0``
    and never raises -- a cleanup consumer must never break on a registry write.
    """
    try:
        drop = {(k[0], k[1]) for k in keys}
        if not drop:
            return 0
        path = orphanage_path(project)
        with _RecordLock(path, require_sidecar=True):
            existing = load_orphaned_obligations_strict(project)
            kept = [e for e in existing
                    if (e.get("source_worktree"), e.get("ref")) not in drop]
            removed = len(existing) - len(kept)
            if removed <= 0:
                return 0
            if kept:
                _atomic_write(
                    path,
                    yaml.safe_dump({"orphaned": kept}, sort_keys=False),
                )
            elif path.exists():
                path.unlink()
            return removed
    except Exception:
        return 0


def find_orphaned_children(
    tracking_path: Path,
) -> list[tuple[WorktreeRecord, WorktreeRecord | None]]:
    """Find tracked worktrees whose owning parent is finalized/orphaned/gone.

    The read side of the citadel E1b cascade (#877): scans the local tracking
    dir for records carrying an ``owner_ref`` (they were created as another
    worktree's outbound resource) and returns those whose **same-machine** parent
    is either absent locally or in a terminal status -- i.e. orphaned children a
    caller (picker / doctor / cleanup) should surface. Each result pairs the
    child with its parent record (``None`` when the parent has no local record).

    A **cross-machine** owner is skipped (this local read cannot judge it; the
    fabric claimant probe owns that). Fail-safe: unreadable records are skipped.
    """
    out: list[tuple[WorktreeRecord, WorktreeRecord | None]] = []
    try:
        this_machine = cfg.load_config().machine
    except Exception:
        this_machine = None
    for child in list_records(tracking_path):
        ref = child.owner_claim_ref
        if ref is None:
            continue
        # Same-machine only: a qualified ref naming a different machine is not
        # judgeable here (parse_claim_ref leaves machine=None for a bare ref,
        # which we treat as same-machine/local).
        if ref.machine and this_machine and ref.machine != this_machine:
            continue
        parent = load_record_by_id(ref.worktree_id)
        if parent is None:
            out.append((child, None))
        elif parent.status in _TERMINAL_OWNER_STATUSES:
            out.append((child, parent))
    return out


# ---------------------------------------------------------------------------
# Session registry -- per-worktree session tracking via hooks
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


def _ensure_activation_history(entry: SessionEntry) -> None:
    """Promote a legacy mutable start/end pair into activation ordinal 1."""
    if entry.activations or not entry.started_at:
        return
    entry.activations.append(SessionActivation(
        ordinal=1,
        started_at=entry.started_at,
        start_recorded_at=entry.started_at,
        start_source="legacy",
        ended_at=entry.ended_at,
        end_recorded_at=entry.ended_at,
        end_source="legacy" if entry.ended_at else None,
    ))


def _start_session_activation(
    entry: SessionEntry,
    *,
    event_at: str,
    recorded_at: str,
    source: str,
) -> bool:
    """Append a resume interval, or dedupe a repeated start delivery."""
    _ensure_activation_history(entry)
    latest = max(entry.activations, key=lambda item: item.ordinal, default=None)
    if latest is not None and latest.ended_at is None:
        if latest.started_at == event_at or source in (
            "bind", "handoff", "reconciled"
        ):
            entry.ended_at = None
            return False
        inferred_end = event_at
        try:
            if datetime.fromisoformat(event_at) < datetime.fromisoformat(
                latest.started_at
            ):
                inferred_end = recorded_at
        except (TypeError, ValueError):
            pass
        latest.ended_at = inferred_end
        latest.end_recorded_at = recorded_at
        latest.end_source = "inferred:next-start"
    ordinal = (latest.ordinal if latest is not None else 0) + 1
    entry.activations.append(SessionActivation(
        ordinal=ordinal,
        started_at=event_at,
        start_recorded_at=recorded_at,
        start_source=source,
    ))
    if not entry.started_at:
        entry.started_at = event_at
    entry.ended_at = None
    return True


def _end_session_activation(
    entry: SessionEntry,
    *,
    event_at: str,
    recorded_at: str,
    source: str,
) -> bool:
    """Close the latest open interval, idempotently."""
    _ensure_activation_history(entry)
    latest = max(entry.activations, key=lambda item: item.ordinal, default=None)
    if latest is None:
        entry.ended_at = event_at
        return True
    if latest.ended_at is not None:
        return False
    latest.ended_at = event_at
    latest.end_recorded_at = recorded_at
    latest.end_source = source
    entry.ended_at = event_at
    return True


def seal_worktree_identity(record: WorktreeRecord | None) -> dict:
    """Deterministically seal a worktree's durable identity from session-state.

    The per-session hooks (``register-session`` / ``deregister-session``) are
    best-effort: a dispatched or crashed session, a bare-resume cwd, or a
    startup that never fully initialized hooks can leave a worktree with an
    empty ``sessions`` registry *and* a ``null`` title -- so the Picker renders
    it as "(untitled)" with no way to tell what it was for. ``finalize`` calls
    this as a **backstop** so a finalized/pruned worktree always retains a
    human-readable title and its session linkage, independent of when
    session-state is later reaped.

    Gap-filling and idempotent: it only populates an **empty** registry and an
    **unset** title, never overwriting an asserted title or existing sessions.
    It **mutates ``record`` in place** (so a caller that later saves the same
    object -- e.g. ``update_status(record, "finalized")`` -- preserves the seal)
    and also persists immediately. Reuses the sanctioned session-state sweep
    (``sessions.backfill_sessions``) and the Picker's own title derivation
    (``sessions.scan_sessions_fast``), so a sealed title matches what the Picker
    would otherwise show live. Never raises.

    Returns ``{"sessions": N, "titled": bool}`` describing what was filled.
    """
    from . import sessions as _sessions

    result = {"sessions": 0, "titled": False}
    if record is None or not record.worktree_path:
        return result

    # Pass 1 -- session registry (only when empty).
    if not record.sessions:
        try:
            ids = _sessions.backfill_sessions([record]).get(record.worktree_id, [])
        except Exception:
            ids = []
        if ids:
            record.sessions = [
                SessionEntry(session_id=sid, started_at="") for sid in ids
            ]
            result["sessions"] = len(ids)

    # Pass 2 -- title slot (only when missing). Same derivation the Picker reads.
    if not (record.title and record.title != "null"):
        summary = ""
        try:
            ctx = _sessions.scan_sessions_fast([record])
            summary = ctx.latest_summary.get(
                _sessions._normalize_path(record.worktree_path), ""
            )
        except Exception:
            summary = ""
        if summary and summary != "null":
            record.title = summary
            result["titled"] = True

    if result["sessions"] or result["titled"]:
        try:
            save_record(record)
        except Exception:
            pass
    return result


def register_session(
    worktree_id: str,
    session_id: str,
    pid: int | None = None,
    pane_id: str | None = None,
    *,
    started_at: str | None = None,
    source: str = "hook",
    recorded_at: str | None = None,
    handoff_token: str | None = None,
    initial_projection: bool = False,
) -> None:
    """Register a Copilot session against a worktree (called from sessionStart hook)."""
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return

    with _RecordLock(yaml_path):
        record = load_record(yaml_path)
        if (
            record.kind in MANAGED_KINDS
            and record.status in {"complete", "completed", "finalized"}
        ):
            raise SessionLifecycleError(
                f"worktree {worktree_id} is terminal and managed; "
                "refusing new session activation"
            )
        if record.sessions is None:
            record.sessions = []
        event_at = started_at or _now_iso()
        observed_at = recorded_at or _now_iso()
        _ensure_head_ledger(record)

        # Dedupe -- update existing entry instead of appending
        for entry in record.sessions:
            if entry.session_id == session_id:
                activation_added = _start_session_activation(
                    entry,
                    event_at=event_at,
                    recorded_at=observed_at,
                    source=source,
                )
                if pid:
                    entry.pid = pid
                if pane_id:
                    entry.pane_id = pane_id
                if handoff_token:
                    try:
                        link_handoff(
                            record, handoff_token, session_id,
                            linked_at=event_at, save=False,
                        )
                    except SessionLifecycleError:
                        if activation_added:
                            _next_lifecycle_revision(record, session_id)
                        save_record(record)
                        raise
                elif (
                    record.resolved_head_session is None
                    and entry.state == "active"
                    and (
                        not record.pending_handoffs
                        or source == "bind"
                    )
                ):
                    _cancel_pending_handoffs(record)
                    _append_head_transition(
                        record, session_id, reason="rebind", at=event_at,
                    )
                elif activation_added:
                    _next_lifecycle_revision(record, session_id)
                save_record(record)
                return

        # session-lifecycle: capture whether the worktree already has a current
        # session BEFORE appending, so we can initialize the head for a fresh
        # worktree (or one whose prior sessions all concluded) without moving an
        # existing active head.
        had_active_head = record.resolved_head_session is not None
        new_entry = SessionEntry(
            session_id=session_id,
            started_at=event_at,
            pid=pid,
            pane_id=pane_id,
            activations=[SessionActivation(
                ordinal=1,
                started_at=event_at,
                start_recorded_at=observed_at,
                start_source=source,
            )],
        )
        record.sessions.append(new_entry)
        _next_lifecycle_revision(record, session_id)
        if (
            initial_projection
            and record.controller_for_session(session_id) is None
        ):
            initial_sessions = set(getattr(
                record,
                "_session_projection_initial_registration",
                set(),
            ))
            initial_sessions.add(session_id)
            record._session_projection_initial_registration = initial_sessions
        # A successor claims one exact, previously opened handoff token. Merely
        # starting another session never steals the head.
        if handoff_token:
            try:
                link_handoff(
                    record, handoff_token, session_id,
                    linked_at=event_at, save=False,
                )
            except SessionLifecycleError:
                save_record(record)
                raise
        elif not had_active_head and (
            not record.pending_handoffs or source == "bind"
        ):
            _cancel_pending_handoffs(record)
            _append_head_transition(
                record,
                session_id,
                reason="rebind" if source == "bind" else "initial",
                at=event_at,
            )
        save_record(record)


def deregister_session(
    worktree_id: str,
    session_id: str,
    *,
    ended_at: str | None = None,
    source: str = "hook",
    recorded_at: str | None = None,
) -> None:
    """Mark a session as ended on a worktree (called from sessionEnd hook)."""
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return

    with _RecordLock(yaml_path):
        record = load_record(yaml_path)
        if record.sessions is None:
            return

        for entry in record.sessions:
            if entry.session_id == session_id:
                changed = _end_session_activation(
                    entry,
                    event_at=ended_at or _now_iso(),
                    recorded_at=recorded_at or _now_iso(),
                    source=source,
                )
                if changed:
                    _next_lifecycle_revision(record, session_id)
                    save_record(record)
                return
