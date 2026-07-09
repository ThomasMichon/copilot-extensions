#!/usr/bin/env python3
"""Display-field derivation for the Worktree Picker TUI.

Turns a raw ``list --json`` worktree dict (``_worktree_to_dict`` shape, with the
canonical ``state`` from ``--classify``) into the normalized record the engine
renders. The state-label vocabulary mirrors the PSMux/TMux status segment's
``_SEGMENT_STYLE`` so the picker and the status bar never disagree.

Ported from the aperture-labs prototype's ``mockdata`` derivation; the
fixture-loading half is replaced by real data sources (``data_local`` / SSH).
"""
from __future__ import annotations

import datetime as _dt

# The "now" derived ages are measured against. Data sources refresh this to the
# real clock before normalizing a batch (see ``data_local``).
NOW = _dt.datetime.now()

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
    st = (w.get("state") or "").lower()
    pr = (w.get("pr") or {})
    prst = pr.get("state")
    # No git classification (old remote): trust tracking status + PR only.
    if not st:
        status = (w.get("status") or "").lower()
        if prst == "merged" or status in ("finalized", "pushed"):
            return "clean"
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
        return "clean"
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
    if w.get("turn_count", 0) > 0 or w.get("mux_session") or w.get("mux_attached"):
        return False
    return True


def norm(w, machine, env):
    """Normalize one raw worktree dict into the engine's record shape."""
    kind = w.get("kind") or "session"
    title = (w.get("title") or "").strip() or "(untitled)"
    if kind in ("system", "bridge"):
        # Mark managed worktrees distinctly so bridge != system at a glance.
        title = f"[{kind}] {title}"
    return {
        "id4": w["id"][-4:],
        "machine": machine,
        "env": env,
        "machine_env": f"{machine} {env}",
        "title": title,
        "kind": kind,
        "tracking": w.get("status", ""),
        "state": _state(w),
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
        "hidden": bool(kind in ("system", "bridge")),
        "raw": w,
    }


def for_machine(wts, machine, env):
    here = [w for w in wts if w["machine"] == machine and w["env"] == env]
    return bucket(here)


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
