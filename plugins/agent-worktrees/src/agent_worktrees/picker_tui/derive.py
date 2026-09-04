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
    'stale'    -- the intent has aged, or its session is idle/at-rest (greyed).
    None       -- no intent text to show, OR (absent any graded ``live_rest``)
                  an unparseable/missing timestamp leaves freshness unknown.

    copilot-extensions#228: the line does NOT expire on AGE -- a worktree that
    ever reported an intent keeps showing its last one (greyed) whenever the
    freshness is knowable, so the graded ``live_rest`` and the age boundary only
    pick fresh vs. stale, never None-on-age. The crisp ``live_rest``
    (busy/idle/awaiting-operator) is preferred for the colour and, when present,
    always yields a level; the intent's own age + idle flag are the coarse
    fallback when no graded rest is present (and only there can a bad timestamp
    still drop the line).

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
        return None
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
            or w.get("session_bare_orphan")):
        return "ACTIVE"
    st = (w.get("state") or "").lower()
    if st:
        # Conversation-only refinement: an UNUSED worktree whose session held
        # turns is not idle -- it's CONVO.
        if st == "unused" and w.get("turn_count", 0) > 0:
            return "CONVO"
        return _STATE_LABEL.get(st, st.upper()[:6])
    pr = w.get("pr") or {}
    status = w.get("status")
    if pr.get("state") == "merged":
        return "FINAL"
    if status == "finalized":
        return "FINAL"
    if status == "active":
        return "WIP" if w.get("turn_count", 0) > 0 else "UNUSED"
    return (status or "?").upper()[:6]


def _sess(w):
    if w.get("mux_attached"):
        return f"●{w.get('mux_clients', 1)}"
    if w.get("mux_session"):
        return "○"
    if (w.get("session_lock_live") or w.get("session_bound_live")
            or w.get("session_bridge_live") or w.get("session_ahp_live")
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
            or w.get("mux_attached") or w.get("session_ahp_live")):
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
    * **completed** -- finalized / merged (state ``FINAL``), regardless of age.
    * **recent**    -- everything else (WIP / UNUSED / CONVO / DIRTY / ORPHAN /
      GONE): not in session and not final.
    """
    active = sorted((w for w in wts if w["state"] == "ACTIVE"),
                    key=lambda w: w["age_secs"])
    completed = sorted((w for w in wts if w["state"] == "FINAL"),
                       key=lambda w: w["age_secs"])
    recent = sorted(
        (w for w in wts if w["state"] not in ("ACTIVE", "FINAL")),
        key=lambda w: w["age_secs"])
    return active, recent, completed
