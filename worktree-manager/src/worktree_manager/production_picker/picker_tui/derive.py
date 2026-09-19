#!/usr/bin/env python3
"""Display-field derivation for the Worktree Picker TUI.

Turns a raw ``list --json`` worktree dict (``_worktree_to_dict`` shape, with the
canonical ``state`` from ``--classify``) into the normalized record the engine
renders. The state-label vocabulary mirrors the PSMux/TMux status segment's
``_SEGMENT_STYLE`` so the picker and the status bar never disagree.

Ported from the test-chamber prototype's ``mockdata`` derivation; the
fixture-loading half is replaced by real data sources (``data_local`` / SSH).
"""
from __future__ import annotations

import datetime as _dt

from rich.cells import cell_len

from .. import prune
from . import reciprocal
from . import source_identity

# The "now" derived ages are measured against. Data sources refresh this to the
# real clock before normalizing a batch (see ``data_local``).
NOW = _dt.datetime.now()

# worktree-status-core live pulse: how long an agent-intent pulse stays "fresh"
# (rendered bright-dim) before it greys to "stale". copilot-extensions#228: the
# line no longer expires on AGE -- a worktree where any work happened keeps
# showing its last reported intent (greyed) so the picker always answers "what
# was this doing?". The graded ``live_rest`` (busy/idle/awaiting-operator) and
# this age boundary only pick the COLOUR/glyph, not aging-out; the line is still
# absent only when there is no intent TEXT to show (or, lacking any graded rest,
# an unparseable/missing timestamp leaves the freshness unknown).
_PULSE_FRESH_SECS = 90


def _pulse_level(w):
    """Classify the live agent-intent pulse: 'awaiting', 'fresh', 'stale', None.

    'awaiting' -- the session is parked on a human (``live_rest`` ==
                  ``awaiting-operator``): the standout "this needs me" cue.
    'fresh'    -- a recent intent from an active turn (bright-dim live line).
    'stale'    -- the intent has aged, its session is idle/at-rest, or its
                  freshness can no longer be graded (greyed either way).
    None       -- no intent TEXT to show. This is the only case the line is
                  absent -- see copilot-extensions#228 and its Picker-side
                  follow-up (context-handoff bug #2): an intent that exists
                  must never silently disappear merely because its freshness
                  is unknown.

    copilot-extensions#228: the line does NOT expire on AGE -- a worktree that
    ever reported an intent keeps showing its last one (greyed) whenever the
    freshness is knowable, so the graded ``live_rest`` and the age boundary only
    pick fresh vs. stale, never None-on-age. The crisp ``live_rest``
    (busy/idle/awaiting-operator) is preferred for the colour and, when present,
    always yields a level; the intent's own age + idle flag are the coarse
    fallback when no graded rest is present. An unparseable/missing timestamp
    (and no graded rest) used to drop the line entirely even though the intent
    TEXT existed -- the operator-visible "ephemeral current task line" bug.
    Grading is now degrade-to-'stale' (unknown freshness reads the same as
    aged), never degrade-to-absent, so the text keeps showing per the class's
    own contract above.

    The pulse is a *derived* signal (assistant.intent + the rest register); it is
    never conflated with the agent-asserted ``follow_up`` disposition.
    """
    intent = (w.get("live_intent") or "").strip()
    if not intent:
        return None
    rest = (w.get("live_rest") or "").strip()
    if rest == "awaiting-operator":
        return "awaiting"
    if rest == "busy":
        return "fresh"
    if rest == "idle":
        return "stale"
    dt = _parse_pulse_ts(w.get("live_intent_at"))
    if dt is None:
        return "stale"
    age = (NOW - dt).total_seconds()
    if age < 0:
        age = 0
    if w.get("live_intent_idle") or age > _PULSE_FRESH_SECS:
        return "stale"
    return "fresh"



def _parse_pulse_ts(ts):
    """Parse a pulse timestamp to a *naive local* datetime, or None.

    The live-pulse extension stamps ``new Date().toISOString()`` -- a UTC,
    tz-aware ``...Z`` value -- so a tz-aware parse is normalized to local naive
    to be comparable with ``NOW`` (naive local). A naive input (e.g. in tests)
    is returned as-is. Never raises.
    """
    if not ts or not isinstance(ts, str):
        return None
    try:
        dt = _dt.datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt

# Canonical git WorktreeState value -> picker display label. Mirrors the
# PSMux/TMux status segment's _SEGMENT_STYLE labels (COMPLETED renders as FINAL;
# CONVO is a turns>0 refinement of UNUSED).
_STATE_LABEL = {
    "dirty": "DIRTY",
    "wip": "WIP",
    "completed": "FINAL",
    "unused": "UNUSED",
    "orphan": "ORPHAN",
    "active": "ACTIVE",
    "gone": "GONE",
    "unknown": "?",
}


def _age(ts):
    if not ts:
        return "-"
    try:
        t = _dt.datetime.fromisoformat(ts)
    except ValueError:
        return "?"
    s = (NOW - t).total_seconds()
    if s < 3600:
        return f"{int(s // 60)}m"
    if s < 86400:
        return f"{int(s // 3600)}h"
    return f"{int(s // 86400)}d"


def _pr(w):
    pr = w.get("pr") or {}
    n = pr.get("number")
    if not n:
        st = pr.get("state") or ""
        return "#…cr" if st == "creating" else "—"
    st = pr.get("state") or ""
    tag = {"merged": "✓", "open": "·op", "closed": "✗"}.get(st, st[:2])
    return f"#{n}{tag}"


def _state(w):
    """Display label aligned with the canonical git WorktreeState vocabulary.

    Prefers the ``state`` field from ``list --json --classify`` (computed where
    git access exists, incl. per remote machine). Falls back to an approximation
    from tracking fields when classification is absent.
    """
    # A live process owns the worktree regardless of a cached or concurrently
    # derived git/tracking state. Check this before the explicit ``state`` field
    # so a first-paint WIP/FINAL cannot hide a live PID lock.
    # ``git_ops.classify_worktree``'s active_paths precedence (it returns ACTIVE
    # before any git status/PR consideration). A live mux, a live
    # ``inuse.<pid>.lock`` binding, the cached bound-Copilot hint, OR a live
    # bridge-lock means a live Copilot session.
    if (w.get("mux_session") or w.get("mux_attached")
            or w.get("session_lock_live") or w.get("session_bound_live")
            or w.get("session_bridge_live") or w.get("session_ahp_live")
            or w.get("execution_leg_live")
            or w.get("session_bare_orphan")):
        return "ACTIVE"
    st = (w.get("state") or "").lower()
    if st:
        # Conversation-only refinement: an UNUSED worktree whose session held
        # turns is not idle -- it's CONVO.
        if st == "unused" and w.get("turn_count", 0) > 0:
            return "CONVO"
        if st == "completed":
            # worktree-finality-and-obligations Phase 5: mirror the PSMux/TMux
            # status segment's FINAL vs MERGED split via the same canonical
            # closure descriptor (`list --json --classify`'s additive
            # ``closure`` field), instead of always collapsing COMPLETED to
            # FINAL. Routed through ``prune.interpret_descriptor_payload`` for
            # mixed-version fleet safety (a remote on an older/newer
            # ``agent-worktrees`` never gets its raw ``closure.label`` trusted
            # directly) -- degrades to MERGED (never FINAL) when the
            # descriptor is absent or unsupported.
            interpreted = prune.interpret_descriptor_payload(w.get("closure"))
            if interpreted["supported"] and interpreted["label"] in ("FINAL", "MERGED"):
                return interpreted["label"]
            return "MERGED"
        return _STATE_LABEL.get(st, st.upper()[:6])
    pr = w.get("pr") or {}
    status = w.get("status")
    if pr.get("state") == "merged" or status == "finalized":
        # The unclassified-legacy-row fallback (no canonical ``state`` field --
        # an older remote or a pre-``--classify`` row) must be gated through
        # the same descriptor check as the classified ``completed`` path
        # above; otherwise an absent/unsupported descriptor could still
        # render FINAL through this back door.
        interpreted = prune.interpret_descriptor_payload(w.get("closure"))
        if interpreted["supported"] and interpreted["label"] in ("FINAL", "MERGED"):
            return interpreted["label"]
        return "MERGED"
    if status == "active":
        return "WIP" if w.get("turn_count", 0) > 0 else "UNUSED"
    return (status or "?").upper()[:6]


def _state_style(w):
    """The closure descriptor's validated ``style`` (e.g. ``merged-blocked``),
    surfaced only when ``_state()`` itself actually resolved to FINAL/MERGED
    through the descriptor. Reuses ``_state()``'s own resolution (rather than
    re-deriving the same precedence) so a live mux/lock session -- which
    ``_state()`` reports as ACTIVE regardless of a stale ``completed``/
    ``finalized`` tracking field -- can never get a completed-descriptor style
    here either. ``None`` for anything else (absent/unsupported descriptor, or
    a state that never consults one), so a consumer that keys color purely off
    ``rec["state"]`` remains correct with no ``state_style`` present.

    Carried on the normalized record for a future renderer to key semantic
    styling off of; the engine does not yet consume this field to recolor a
    row -- it still colors by the plain ``state`` label alone.
    """
    if _state(w) not in ("FINAL", "MERGED"):
        return None
    interpreted = prune.interpret_descriptor_payload(w.get("closure"))
    if interpreted["supported"] and interpreted["label"] in ("FINAL", "MERGED"):
        return interpreted["style"]
    return None


def _status_markers(w):
    """The closure descriptor's per-fact freshness markers (worktree-finality-
    and-obligations Phase 9), e.g. ``"C1 U* OC*"`` -- held-claim/follow-up
    counts plus an unconfirmed upstream-containment/open-claims marker.
    Everything in ``compact`` AFTER the base label, which is already rendered
    separately via ``_state()``/the ``state`` column -- never duplicate the
    label itself. Empty string when there's nothing to show (no descriptor,
    an unsupported/mixed-version one, or a clean/confirmed one) rather than
    repeating the bare label. Routed through
    ``prune.interpret_descriptor_payload`` for the same mixed-version fleet
    safety as ``_state()`` -- never reads the raw ``closure`` dict directly.
    """
    interpreted = prune.interpret_descriptor_payload(w.get("closure"))
    if not interpreted["supported"]:
        return ""
    compact = interpreted["compact"]
    label = interpreted["label"]
    if not compact or not label or not compact.startswith(label):
        return ""
    return compact[len(label):].strip()


def truncate_text(s, w):
    """Truncate ``s`` to at most ``w`` display cells, ellipsizing when
    clipped -- used when concatenating several variable-length segments onto
    one detail line (the Worktree tile's status_markers/asset_hints run) so
    no single segment can overflow the row and crowd out the ones after it.
    Deliberately non-padding (unlike ``engine.py``'s column-fitting
    ``_clip``, which always pads its result out to exactly ``w``): padding
    mid-line here would insert unwanted blank space between segments instead
    of just bounding this one's length. Uses Rich's ``cell_len`` throughout
    (not ``len()``) so a wide/double-width character is measured by its
    actual display width, not counted as one cell."""
    s = str(s)
    if cell_len(s) <= w:
        return s
    if w <= 1:
        return s[:w]
    # Binary-search the longest prefix whose cell width leaves room for the
    # ellipsis -- cheap and exact for the short strings this renders (marker
    # phrases, asset-hint runs), unlike slicing by character count.
    lo, hi = 0, len(s)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if cell_len(s[:mid]) <= w - 1:
            lo = mid
        else:
            hi = mid - 1
    return s[:lo] + "…"


#: The raw ``status_markers`` tokens above (``C<N>``/``F<N>``/``U*``/``OC*``)
#: are a closure-descriptor-internal wire shorthand -- meaningful to whoever
#: wrote the descriptor, opaque to an operator glancing at the tile's second
#: line. When the marker string is the ONLY content on that line (no asset
#: hints, no live pulse), a bare "C1 U* OC*" reads as noise. This map/helper
#: expands each token into a short human phrase for the picker's RENDER layer
#: only -- ``status_markers`` itself stays the compact wire format other
#: tests/consumers read.
_STATUS_MARKER_TEXT = {
    "U*": "merge unconfirmed",
    "OC*": "claims unconfirmed",
}


def describe_status_marker(tok):
    """Expand one ``status_markers`` token into ``(text, is_warning)``. An
    unrecognized token (a future marker this helper doesn't know about yet)
    degrades to itself verbatim, still flagged as a warning if it carries the
    ``*`` unconfirmed-fact suffix, so a new marker never vanishes silently --
    it just isn't prettied up yet.

    ``compact`` (the source of these tokens) is only validated as a ``str``
    by ``prune.interpret_descriptor_payload`` -- a malformed/remote descriptor
    could hand this an absurdly long ``C``/``F`` digit run. No realistic
    held-claim/follow-up count is more than a few digits, so the numeric
    suffix is length-bounded BEFORE conversion (not just wrapped in a
    ``try``/``except``): CPython 3.11+ raises ``ValueError`` past its
    configured int-string-conversion digit limit, but an older interpreter
    has no such limit and would otherwise happily (if slowly) convert an
    arbitrarily long digit run. An over-length or unparseable suffix simply
    degrades to the verbatim fallback below, same as any other unrecognized
    token, instead of crashing (or stalling) the picker's render.
    """
    _MAX_MARKER_DIGITS = 12
    if tok in _STATUS_MARKER_TEXT:
        return _STATUS_MARKER_TEXT[tok], True
    if (len(tok) > 1 and tok[0] in ("C", "F")
            and 0 < len(tok) - 1 <= _MAX_MARKER_DIGITS and tok[1:].isdigit()):
        try:
            n = int(tok[1:])
        except ValueError:
            return tok, tok.endswith("*")
        noun = "held claim" if tok[0] == "C" else "follow-up"
        return f"{n} {noun}{'s' if n != 1 else ''}", False
    return tok, tok.endswith("*")


def status_marker_segments(markers, width):
    """Expand a raw ``status_markers`` string into ``(text, is_warning)``
    render segments (comma separators included as their own non-warning
    segments), each already truncated so the whole run never exceeds
    ``width`` cells -- the human-readable expansion is longer than the
    compact wire tokens it replaces, so this segment must never be allowed to
    crowd out whatever the picker's render layer appends after it (asset
    hints, the live-pulse line). Cell-width measured throughout, matching
    ``truncate_text``."""
    segments = []
    used = 0
    for j, tok in enumerate(markers.split()):
        prefix = ", " if j else ""
        budget = max(0, width - used) - cell_len(prefix)
        if budget <= 0:
            break
        if prefix:
            segments.append((prefix, False))
            used += cell_len(prefix)
        text, is_warn = describe_status_marker(tok)
        clipped = truncate_text(text, budget)
        segments.append((clipped, is_warn))
        used += cell_len(clipped)
    return segments


def status_line_segments(markers, asset_hints, overflow, width):
    """Combine the ``status_markers`` expansion with the tile's asset-hint
    text into one bounded run of ``(text, is_warning)`` segments -- the
    picker's detail-line render layer just appends these in order. Asset
    hints are already bounded (<=4 tokens + an overflow count) and take
    priority: their width is reserved BEFORE budgeting the (unbounded-length)
    readable marker text, so a long marker expansion can't crowd them out."""
    asset_text = ""
    if asset_hints:
        asset_text = " ".join(asset_hints) + (f" +{overflow}" if overflow else "")
    segments = []
    if markers:
        reserve = cell_len(asset_text) + 2 if asset_text else 0
        segments = status_marker_segments(markers, max(0, width - reserve))
    if asset_text:
        prefix = "  " if segments else ""
        used = sum(cell_len(t) for t, _ in segments)
        segments.append((truncate_text(prefix + asset_text, max(0, width - used)), False))
    return segments


#: Bounded per-kind short codes for the tile asset-hint line (#6443/upstream
#: #1979). Falls back to an upper-cased 4-char code for a kind this map
#: doesn't recognize, so a future ``ResourceKind`` addition degrades safely
#: instead of vanishing from the tile.
_ASSET_CODES = {
    "pr": "PR",
    "worktree": "WT",
    "codespace": "CS",
    "container": "CTR",
    "ssh": "SSH",
    "workdir": "DIR",
}

#: Held-claim dispositions: a claim not yet released/abandoned still rides on
#: the worktree, so it belongs in the tile's asset summary. An empty state is
#: the legacy default and normalizes to "active" (held). Mirrors the
#: ``resources`` ledger's own held-vs-released disposition convention
#: (``active``/``at-rest`` = held; ``released``/``abandoned`` = not held).
_HELD_CLAIM_STATES = ("", "active", "at-rest")


def _asset_hints(w):
    """Bounded type/count breakdown of the worktree's HELD outbound claims
    (the ``resources`` ledger -- #6443/upstream #1979 Phase 6), e.g.
    ``["PR", "WT×2"]`` -- distinct from ``status_markers``'s bare ``C<N>``
    held-claims COUNT: this groups the same held claims by ``kind`` so a
    cross-repo PR reads differently from a child worktree or a Codespace at a
    glance. Bounded to 4 hint tokens (with an ``overflow`` count) so a
    worktree carrying many claim kinds can never blow out the tile's width.
    Also returns the full per-claim detail (kind/ref/note/state) for a
    width-constrained consumer (the action menu) to render in full. Returns
    ``{"hints": [...], "overflow": int, "details": [...]}`` -- empty lists
    when the worktree holds no claims (never omitted, so a caller need not
    guard for the key's absence).
    """
    raw = w.get("resources")
    kind_counts: dict[str, int] = {}
    details = []
    if isinstance(raw, list):
        for claim in raw:
            if not isinstance(claim, dict):
                continue
            state = str(claim.get("state") or "").strip()
            if state not in _HELD_CLAIM_STATES:
                continue
            kind = str(claim.get("kind") or "").strip() or "resource"
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            details.append({
                "kind": kind,
                "ref": str(claim.get("ref") or "").strip(),
                "note": str(claim.get("note") or "").strip(),
                "state": state or "active",
            })
    # Count by the raw ``kind`` first (above), then resolve a display code
    # only now -- never the reverse. Counting straight into the code would
    # silently merge two DISTINCT kinds whose fallback 4-char codes happen to
    # collide (e.g. "workspace" and "workflow" both -> "WORK"), understating
    # the real per-kind breakdown the operator is reading. Aggregating by
    # code here is still an explicit, intentional step -- multiple raw kinds
    # sharing one code are summed into that code's token, but never silently
    # dropped or conflated during counting itself.
    code_counts: dict[str, int] = {}
    for kind, n in kind_counts.items():
        code = _ASSET_CODES.get(kind, kind.upper()[:4] or "RES")
        code_counts[code] = code_counts.get(code, 0) + n
    # Deterministic hint order (never insertion/dict order, which tracks
    # arbitrary ``resources`` list order and would make the tile line and any
    # snapshot/TUI test brittle): the curated ``_ASSET_CODES`` kinds first, in
    # their declared (most-common-first) order, then any remaining
    # unrecognized-kind fallback codes, alphabetically.
    ordered_codes = [
        c for c in _ASSET_CODES.values() if c in code_counts
    ] + sorted(c for c in code_counts if c not in _ASSET_CODES.values())
    hints = [
        code if code_counts[code] == 1 else f"{code}×{code_counts[code]}"
        for code in ordered_codes
    ]
    return {
        "hints": hints[:4],
        "overflow": max(0, len(hints) - 4),
        "details": details,
    }


def _sess(w):
    if w.get("mux_attached"):
        return f"●{w.get('mux_clients', 1)}"
    if w.get("mux_session"):
        return "○"
    if (w.get("session_lock_live") or w.get("session_bound_live")
            or w.get("session_bridge_live") or w.get("session_ahp_live")
            or w.get("execution_leg_live")
            or w.get("session_bare_orphan")):
        return "PROC"
    if w.get("session_lock_stale"):
        return "LOCK"
    return "·"


def _age_secs(w):
    ts = (w.get("completed_at") if w.get("status") == "finalized"
          else w.get("started_at"))
    if not ts:
        return 1 << 40
    try:
        return (NOW - _dt.datetime.fromisoformat(ts)).total_seconds()
    except ValueError:
        return 1 << 40


def _bucket_from_raw(w):
    """Cleanup bucket for a raw worktree dict.

    Prefers the authoritative ``cleanup_bucket`` emitted by
    ``list --json --classify`` (computed by ``prune.cleanup_disposition``).

    When git classification is absent (a remote too old to emit ``--classify``,
    so there is no ``state`` field), fall back to tracking *status* + PR -- never
    claim ``unmerged`` on missing evidence. Unknowable cases return ``unknown``
    (neutral: shown without a chip, never offered for cleanup). With a ``state``
    present, mirror ``prune``'s mapping. Buckets: clean / unused / conversation /
    open-pr / closed-unmerged / dirty / wip / orphan / active / gone / unknown.
    """
    b = w.get("cleanup_bucket")
    if b:
        return b
    # worktree-status-core: on the fallback path (old remote w/o an authoritative
    # cleanup_bucket), an agent-asserted follow-up downgrades a would-be clean
    # verdict to the REVIEW-class 'follow-up' bucket. (The authoritative path is
    # already handled by prune.cleanup_disposition above.)
    _follow = bool(w.get("follow_up"))
    st = (w.get("state") or "").lower()
    pr = (w.get("pr") or {})
    prst = pr.get("state")
    # No git classification (old remote): trust tracking status + PR only.
    if not st:
        status = (w.get("status") or "").lower()
        if prst == "merged" or status in ("finalized", "pushed"):
            return "follow-up" if _follow else "clean"
        if prst == "open":
            return "open-pr"
        return "unknown"
    if st == "active":
        return "active"
    if st == "gone":
        return "gone"
    if st == "dirty":
        return "dirty"
    if st == "orphan":
        return "orphan"
    if prst == "open":
        return "open-pr"
    if prst == "merged" or st == "completed":
        return "follow-up" if _follow else "clean"
    if prst == "closed":
        return "closed-unmerged"
    if st == "wip":
        return "wip"
    if st == "unused":
        return "conversation" if w.get("turn_count", 0) > 0 else "unused"
    return "unknown"


def _ff_from_raw(w):
    """Whether a raw worktree dict is fast-forward eligible.

    Prefers the authoritative ``ff_eligible`` field; else mirrors
    ``git_ops.can_fast_forward`` (clean, no local commits ahead, strictly
    behind) plus "no live session".
    """
    if "ff_eligible" in w:
        return bool(w["ff_eligible"])
    return (
        w.get("dirty", 0) == 0
        and w.get("ahead", 0) == 0
        and w.get("behind", 0) > 0
        and (w.get("state") or "").lower() != "active"
    )


# Cleanup bucket -> Maintenance disposition chip. open-pr is a healthy end
# state (in review): no flag. Cleanable buckets are SAFE/REVIEW; work-bearing or
# in-use buckets are UNSAFE (never auto-pruned).
BUCKET_DISPO = {
    "clean": "SAFE",
    "unused": "REVIEW",
    "conversation": "REVIEW",
    "follow-up": "REVIEW",
    "closed-unmerged": "REVIEW",
    "gone": "REVIEW",
    "dirty": "UNSAFE",
    "wip": "UNSAFE",
    "unmerged": "UNSAFE",
    "orphan": "UNSAFE",
    "active": "UNSAFE",
    "open-pr": "",
    "unknown": "",
}

# Cleanup bucket -> short reason shown in the disposition chip.
BUCKET_REASON = {
    "clean": "on default branch",
    "unused": "idle · no commits/turns",
    "conversation": "chat history, no commits",
    "follow-up": "agent flagged follow-ups",
    "closed-unmerged": "PR closed unmerged",
    "gone": "dir missing",
    "dirty": "uncommitted work",
    "wip": "unmerged commits",
    "unmerged": "commits not on default branch",
    "orphan": "no merge base",
    "active": "live session",
    "open-pr": "open PR",
    "unknown": "unclassified (remote needs update)",
}


def _sessionless(w):
    """True when we positively know a worktree has **no owning Copilot session**
    and is not otherwise in use -- the #1026 cold-start hazard.

    Only flagged when ``session_count`` is present and 0 (real data always
    carries it now that the session-start hook is reliable, #662); an absent
    count -- a fixture or a remote too old to report it -- stays *unknown* and is
    never flagged. Any past turns or a live mux session count as ownership, and
    daemon-owned ``system``/``bridge`` kinds have their own bucket.
    """
    sc = w.get("session_count")
    if sc is None or sc > 0:
        return False
    if (w.get("kind") or "session") in ("system", "bridge"):
        return False
    if (w.get("turn_count", 0) > 0 or w.get("mux_session")
            or w.get("mux_attached") or w.get("session_ahp_live")
            or w.get("execution_leg_live")):
        return False
    return True


def norm(
    w,
    machine,
    env,
    *,
    source_kind=source_identity.MACHINE_SSH_KIND,
    source_id=None,
    source_label=None,
    source_metadata=None,
    source_capabilities=None,
):
    """Normalize one raw worktree dict into the engine's record shape."""
    source_id = source_identity.resolve_id(
        source_kind, source_id, machine=str(machine), env=str(env)
    )
    source_label = source_label or f"{machine} / {env}"
    kind = w.get("kind") or "session"
    title = (w.get("title") or "").strip() or "(untitled)"
    # Type marker (#2668). Prefer the two-axis interface/origin marks the
    # ``list`` JSON now emits so an operator can tell an ACP (Neuron Forge)
    # session, a delegate, and a system worktree apart at a glance; fall back to
    # the legacy kind label for an older data source that predates the marks.
    _iface = w.get("interface")
    _origin = w.get("origin")
    _tag = None
    if _origin in ("system", "delegate"):
        _tag = _origin
    elif _iface == "acp":
        _tag = "acp"
    elif _iface != "cli" and kind in ("system", "bridge"):
        _tag = kind
    if _tag:
        title = f"[{_tag}] {title}"
    # worktree-status-core: the agent-asserted disposition overlay. A flagged
    # worktree gets a follow-up glyph prefixed on its title (scannable
    # regardless of the narrow state column), and its one-line summary rides
    # after the title. ``state`` stays pure (bucket()/prune key off it); the
    # not-auto-prune-SAFE behavior comes from the ``follow-up`` cleanup bucket.
    follow_up = bool(w.get("follow_up"))
    # #93: a bare (un-muxed) bound Copilot -- invisible to the mux fleet view.
    bare_orphan = bool(w.get("session_bare_orphan"))
    # copilot-extensions#228: the graded rest state's standout value -- the
    # session is parked waiting on a human ("this needs me"). Surfaces both a
    # scannable title marker (below) and an amber sub-line glyph (engine).
    awaiting_operator = (w.get("live_rest") or "").strip() == "awaiting-operator"
    # citadel paired -harness/-knowledge lifecycle (#957): this worktree is one
    # half of a carved pair. A scannable link glyph rides on the title so the
    # operator sees the two rows belong together, and the pair fields ride on the
    # normalized record for filtering / navigation / status aggregation.
    pair_id = w.get("pair_id")
    pair_role = w.get("pair_role")
    pair_kind = w.get("pair_kind")
    is_paired = bool(pair_id)
    summary = (w.get("summary") or "").strip()
    disp_title = title
    if summary:
        disp_title = (summary if title == "(untitled)"
                      else f"{title} — {summary}")
    if follow_up:
        disp_title = f"✚ {disp_title}"
    # citadel pair marker: a link glyph (inner of the urgent ⚠/✚ markers) so a
    # paired row is scannable without widening the state column. Gated on the
    # pair id, so an unpaired worktree's title is untouched.
    if is_paired:
        disp_title = f"⚭ {disp_title}"
    # copilot-extensions#228: the "needs me" marker rides just inside the orphan
    # marker -- a live session parked on the operator is an act-now signal, more
    # urgent than a paired/follow-up cue but not the structural orphan hazard.
    if awaiting_operator:
        disp_title = f"⏳ {disp_title}"
    # #93: the orphan marker rides outermost (leftmost) -- most scannable, and
    # a bound-but-un-muxed Copilot is the more urgent signal than a follow-up.
    if bare_orphan:
        disp_title = f"⚠ {disp_title}"
    source = source_identity.metadata(source_kind, source_id, source_label)
    source.update(source_metadata or {})
    capabilities = dict(source_capabilities or {})
    id4 = w["id"][-4:]
    reciprocal_relation = reciprocal.normalize(
        w.get("reciprocal_relation"),
        has_bound_session=bool(w.get("last_session_id")),
        has_controllers=bool(w.get("controllers") or w.get("controller_revision")),
    )
    return {
        "id4": id4,
        "selection_id": f"{source_id}\x1f{w['id']}",
        "machine": machine,
        "env": env,
        "machine_env": (
            f"{machine} {env}".strip()
            if source_kind == source_identity.MACHINE_SSH_KIND
            else source_label
        ),
        "source_kind": source_kind,
        "source_id": source_id,
        "source_label": source_label,
        "source": source,
        "source_capabilities": capabilities,
        "title": disp_title,
        "follow_up": follow_up,
        "summary": summary,
        # worktree-status-core live pulse: the derived agent-intent line + its
        # freshness ('awaiting'/'fresh'/'stale'/None). Rendered dim by the
        # engine; never the durable disposition. copilot-extensions#228: the
        # line no longer expires -- ``live_rest`` grades its colour (amber
        # awaiting-operator, dim busy, grey idle) but never drops it.
        "live_intent": (w.get("live_intent") or "").strip(),
        "live_pulse": _pulse_level(w),
        "live_rest": (w.get("live_rest") or "").strip(),
        "awaiting_operator": awaiting_operator,
        "kind": kind,
        "tracking": w.get("status", ""),
        "state": _state(w),
        "state_style": _state_style(w),
        "status_markers": _status_markers(w),
        "asset_hints": _asset_hints(w),
        "relation": reciprocal.short_label(reciprocal_relation),
        "reciprocal_relation": reciprocal_relation,
        "age": _age(
            w.get("completed_at") if w.get("status") == "finalized"
            else w.get("started_at")
        ),
        "age_secs": _age_secs(w),
        "sess": _sess(w),
        "turns": w.get("turn_count", 0),
        "session_count": w.get("session_count"),
        "sessionless": _sessionless(w),
        "pr": _pr(w),
        "cleanup_bucket": _bucket_from_raw(w),
        "ff_eligible": _ff_from_raw(w),
        "attached": bool(w.get("mux_attached")),
        "mux_live": bool(w.get("mux_session") or w.get("mux_attached")),
        "active": w.get("status") == "active",
        # two-step-restore: the most-recent session id (shown in the row
        # sub-menu so the operator can ``/resume`` it), and whether a live
        # ``inuse.<pid>.lock`` binds a Copilot process right now (gates Reclaim).
        "last_session_id": w.get("last_session_id"),
        "session_lock_live": bool(w.get("session_lock_live")),
        # Stale-lock residue: an ``inuse.<pid>.lock`` file whose pid is no longer
        # a live Copilot (crashed/killed without cleanup). NOT a live binding, so
        # it never reads ACTIVE -- but it gates Reclaim (file-only cleanup) so a
        # no-mux/no-live-lock worktree with residue can be cleared to zero.
        "session_lock_stale": bool(w.get("session_lock_stale")),
        "stale_lock_pids": list(w.get("stale_lock_pids") or []),
        # #4057/#1416: worktree hosts a live bound Copilot per the OFF-hot-path
        # reconcile (mux OR bare) -- the cached signal that surfaces a
        # bare-resumed session (cwd=home) in the Active section. Distinct from
        # mux_live; drives the classification-absent fast-pass ACTIVE.
        "session_bound_live": bool(w.get("session_bound_live")),
        "session_ahp_live": bool(w.get("session_ahp_live")),
        "execution_leg_live": bool(w.get("execution_leg_live")),
        # Controller metadata is passed through for future presentation and
        # recovery actions. It is deliberately absent from state/active/resume
        # derivation: control is not binding.
        "controllers": list(w.get("controllers") or []),
        "controller_findings": list(w.get("controller_findings") or []),
        # #4272 bridge-lock: worktree hosts a live bridge-owned Copilot per the
        # file-first bridge.lock read. Distinct from mux_live/bound_live; drives
        # the classification-absent fast-pass ACTIVE for a bare/bridge session.
        "session_bridge_live": bool(w.get("session_bridge_live")),
        # #93: worktree hosts a bare (un-muxed) bound Copilot -> orphan marker.
        "session_bare_orphan": bare_orphan,
        # citadel paired -harness/-knowledge lifecycle (#957): the pair linkage,
        # surfaced on the normalized row so the Picker can group/aggregate the
        # two rows and offer "jump to paired worktree". All None/False for an
        # unpaired worktree (the common case).
        "is_paired": is_paired,
        "pair_id": pair_id,
        "pair_role": pair_role,
        "pair_kind": pair_kind,
        # Picker default-visibility. Keys on the origin-based ``picker_hidden``
        # mark the ``list`` JSON now emits (origin in {system, delegate}) so an
        # operator-owned bridge/ACP worktree -- a Neuron Forge session -- is
        # SHOWN by default, symmetric with the NF cockpit (#2668). Falls back to
        # the legacy kind test for an older data source (a remote/runtime that
        # predates the mark) so nothing regresses. Note: this is *visibility*,
        # decoupled from *lifecycle* -- a shown bridge worktree stays
        # cleanup-exempt (that keys on kind via MANAGED_KINDS).
        "hidden": bool(w.get("picker_hidden", kind in ("system", "bridge"))),
        "raw": w,
    }


def for_machine(wts, machine, env):
    here = [w for w in wts if w["machine"] == machine and w["env"] == env]
    annotate_pairs(here)
    return bucket(here)


def for_source(wts, source_id):
    """Return rows owned by one canonical source."""
    here = [w for w in wts if w.get("source_id") == source_id]
    annotate_pairs(here)
    return bucket(here)


# citadel paired -harness/-knowledge lifecycle (#957): the "aggregated dual-status"
# data layer. Given the full row set, cross-reference the two halves of each pair
# so a renderer can present them as a unit (sibling summary + an aggregate
# attention flag). Kept pure + additive; the visual indented sub-row nesting is a
# separate engine change.
_PAIR_ATTENTION_STATES = frozenset({"WIP", "DIRTY", "ORPHAN"})


def _pair_wants_attention(row) -> bool:
    """True when a row has un-landed work (follow-up flag or a WIP/dirty state)."""
    return bool(row.get("follow_up")) or row.get("state") in _PAIR_ATTENTION_STATES


def annotate_pairs(rows):
    """Attach each paired row's SIBLING summary + an aggregate attention flag.

    For every row carrying a ``pair_id`` (see :func:`norm`), match it against the
    other half of the pair within ``rows`` and attach:

    * ``pair_sibling`` -- a compact ``{role, state, tracking, follow_up}`` of the
      OTHER half, or ``None`` when the sibling is not in this set (e.g. it lives
      in a different section/machine and wasn't passed in); and
    * ``pair_attention`` -- ``True`` when EITHER half has un-landed work
      (a follow-up flag or a WIP/DIRTY/ORPHAN state), so a future renderer can
      flag the whole pair as needing a look before it is cleaned up.

    Pure + in-place: mutates and returns the same ``rows`` (unpaired rows
    untouched). Idempotent. Pairs are carved on one machine, so callers annotate
    a per-machine slice (see :func:`for_machine`).
    """
    by_pair: dict[str, list] = {}
    for r in rows:
        pid = r.get("pair_id")
        if pid:
            by_pair.setdefault(pid, []).append(r)
    for r in rows:
        pid = r.get("pair_id")
        if not pid:
            continue
        siblings = [s for s in by_pair.get(pid, ()) if s is not r]
        sib = siblings[0] if siblings else None
        r["pair_sibling"] = None if sib is None else {
            "role": sib.get("pair_role"),
            "state": sib.get("state"),
            "tracking": sib.get("tracking"),
            "follow_up": bool(sib.get("follow_up")),
        }
        r["pair_attention"] = _pair_wants_attention(r) or (
            sib is not None and _pair_wants_attention(sib)
        )
    return rows


def bucket(wts):
    """Split into (active, recent, completed), each most-recent-first.

    Sections key off the canonical *state*, not the tracking status:

    * **active**    -- in session (state ``ACTIVE``: a live Copilot/mux session
      owns the worktree). NOT merely "status active / not finalized".
    * **completed** -- finalized or merged (state ``FINAL`` or ``MERGED`` --
      see ``_state``'s Phase 5 closure-descriptor split), regardless of age.
    * **recent**    -- everything else (WIP / UNUSED / CONVO / DIRTY / ORPHAN /
      GONE): not in session and not final.
    """
    active = sorted((w for w in wts if w["state"] == "ACTIVE"),
                    key=lambda w: w["age_secs"])
    completed = sorted((w for w in wts if w["state"] in ("FINAL", "MERGED")),
                       key=lambda w: w["age_secs"])
    recent = sorted(
        (w for w in wts if w["state"] not in ("ACTIVE", "FINAL", "MERGED")),
        key=lambda w: w["age_secs"])
    return active, recent, completed
