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


def norm(w, machine, env):
    """Normalize one raw worktree dict into the engine's record shape."""
    return {
        "id4": w["id"][-4:],
        "machine": machine,
        "env": env,
        "machine_env": f"{machine} {env}",
        "title": (w.get("title") or "").strip() or "(untitled)",
        "tracking": w.get("status", ""),
        "state": _state(w),
        "age": _age(
            w.get("completed_at") if w.get("status") == "finalized"
            else w.get("started_at")
        ),
        "age_secs": _age_secs(w),
        "sess": _sess(w),
        "turns": w.get("turn_count", 0),
        "pr": _pr(w),
        "attached": bool(w.get("mux_attached")),
        "active": w.get("status") == "active",
        "raw": w,
    }


def for_machine(wts, machine, env):
    here = [w for w in wts if w["machine"] == machine and w["env"] == env]
    return bucket(here)


def bucket(wts):
    """Split into (active, recent, completed), each most-recent-first."""
    active = sorted((w for w in wts if w["active"]), key=lambda w: w["age_secs"])
    recent = sorted(
        (w for w in wts if not w["active"] and w["age"].endswith(("m", "h"))),
        key=lambda w: w["age_secs"])
    completed = sorted(
        (w for w in wts if not w["active"] and w["age"].endswith("d")),
        key=lambda w: w["age_secs"])
    return active, recent, completed
