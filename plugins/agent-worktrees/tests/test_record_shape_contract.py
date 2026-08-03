"""Cross-effort record-shape contract for the picker's normalized row.

Two efforts share ONE normalized picker row (``picker_tui.derive.norm``) and one
row-builder (each view's ``build_data`` -> ``_build_data_vrows`` -> BOTH the
text-line and native ``OptionList`` bodies, which are golden grid-parity-tested):

* ``picker-session-state-signals`` WRITES the liveness signals onto the record --
  ``mux_live`` / ``attached`` (mux presence), ``session_lock_live`` /
  ``session_bare_orphan`` / ``last_session_id`` (the bound-Copilot binding), and
  the ``live_intent`` / ``live_pulse`` activity pulse.
* ``picker-list-interaction-layer`` READS + transforms the record for
  filter / sort / multi-select over ``id4`` / ``title`` / ``cleanup_bucket`` /
  ``ff_eligible`` / machine+env.

The two efforts must not fight over the shape: any list-interaction transform
(filter/sort/select -- the effort's Phase 4) operates over, and must PRESERVE,
the SAME record, and a refactor of ``norm`` must keep every field both efforts
depend on. The golden grid-parity tests already guarantee the two BODIES render
identically -- but a *symmetric* drop of a field would pass parity while silently
losing the signal, so this contract test pins field PRESENCE on the single
normalized record, independent of rendering. A failure here means the two efforts
have drifted apart over the record shape (see
``efforts/active/picker-session-state-signals`` and
``efforts/active/picker-list-interaction-layer``).
"""

from __future__ import annotations

import datetime as _dt

from agent_worktrees.picker_tui import derive

# Fields each effort depends on, asserted present on the single shared record.
_SESSION_STATE_SIGNAL_KEYS = (
    "mux_live", "attached", "session_lock_live", "session_bare_orphan",
    "last_session_id", "live_intent", "live_pulse",
)
_INTERACTION_LAYER_KEYS = (
    "id4", "title", "machine", "env", "machine_env",
    "cleanup_bucket", "ff_eligible", "state",
)


def _raw(**kw):
    base = dict(
        id="lambda-core-win-20260803-0000-abcd",
        machine="lambda-core", title="Chamber work", status="active",
        started_at="2026-08-03T10:00:00",
    )
    base.update(kw)
    return base


def test_norm_carries_both_efforts_signals():
    """The single normalized record carries EVERY field both efforts depend on
    -- the shared-shape contract. A dropped key here is a cross-effort drift."""
    fresh_at = (derive.NOW - _dt.timedelta(seconds=10)).isoformat()
    rec = derive.norm(
        _raw(
            # picker-session-state-signals writes:
            mux_session=True, mux_attached=True,
            session_lock_live=True, session_bare_orphan=True,
            last_session_id="sid-abcd",
            live_intent="Reticulating splines", live_intent_at=fresh_at,
        ),
        "lambda-core", "win",
    )
    for k in _SESSION_STATE_SIGNAL_KEYS + _INTERACTION_LAYER_KEYS:
        assert k in rec, f"normalized record dropped '{k}' (cross-effort contract)"
    # Values round-trip, not just the keys.
    assert rec["mux_live"] is True
    assert rec["attached"] is True
    assert rec["session_lock_live"] is True
    assert rec["session_bare_orphan"] is True
    assert rec["last_session_id"] == "sid-abcd"
    assert rec["live_intent"] == "Reticulating splines"
    assert rec["live_pulse"] == "fresh"


def test_liveness_markers_baked_into_title():
    """The liveness markers the operator scans by (orphan + follow-up) are baked
    into ``title`` by ``norm``, so BOTH bodies render them identically without
    re-reading the raw signal -- the mechanism that keeps the two efforts from
    diverging on how the signal surfaces. Glyphs are derived from ``norm`` itself
    (no hardcoded non-ASCII literal)."""
    orphan_glyph = derive.norm(
        _raw(session_bare_orphan=True), "m", "e")["title"][0]
    fu_glyph = derive.norm(_raw(follow_up=True), "m", "e")["title"][0]

    rec = derive.norm(
        _raw(session_bare_orphan=True, follow_up=True, title="Wedged"),
        "lambda-core", "win")
    assert rec["session_bare_orphan"] is True and rec["follow_up"] is True
    # Orphan marker is outermost (leftmost); the follow-up marker is also present.
    assert rec["title"].startswith(orphan_glyph)
    assert fu_glyph in rec["title"]

    clean = derive.norm(_raw(title="Fine"), "lambda-core", "win")
    assert not clean["title"].startswith(orphan_glyph)


def test_signals_absent_default_off_not_missing():
    """A raw dict WITHOUT the signals still yields the keys (defaulted off), so a
    filter/sort/select transform can rely on them existing on EVERY row rather
    than guarding for absence."""
    rec = derive.norm(_raw(), "lambda-core", "win")
    for k in _SESSION_STATE_SIGNAL_KEYS:
        assert k in rec
    assert rec["mux_live"] is False
    assert rec["session_lock_live"] is False
    assert rec["session_bare_orphan"] is False
    assert rec["last_session_id"] is None
    assert rec["live_pulse"] is None
