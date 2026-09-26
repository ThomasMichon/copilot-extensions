"""Cross-surface closure-descriptor parity (worktree-finality-and-obligations
Phase 1): ONE real fixture -- a genuine ``WorktreeRecord`` plus a genuine
``WorktreeStateInfo`` -- run through agent-worktrees' own list-JSON row
builder (``_worktree_to_dict``), its mux status segment
(``cmd_status_segment`` / ``_render_status_segment``), and the production
Picker's ``derive.norm`` (fed the exact ``closure`` payload list JSON would
serialize), must all report the identical label/compact/marker facts.

Every existing per-surface test (``test_closure_descriptor_wiring.py``,
``test_status_segment.py``, ``test_status_markers.py``,
``test_prune_shim.py``) proves its own surface is individually well-formed
against either a real fixture or a hand-typed payload -- none of them prove
that TWO surfaces, given the SAME underlying facts, actually agree. That is
the specific gap this Phase 1 Plan bullet ("assert the same expected compact
token, semantic style, blocker counts, and prune verdict across list JSON,
mux rendering, and Picker derivation") calls out. This lives under
worktree-manager's test suite (not agent-worktrees' own) because only this
package's conftest (``ensure_engine_runtime``) puts a real ``agent_worktrees``
on ``sys.path`` for tests to import both sides.
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

from agent_worktrees import __main__ as m
from agent_worktrees import git_ops, sessions, tracking

from worktree_manager.production_picker.picker_tui import derive


def _rec(**kw):
    base = {
        "worktree_id": "wt-parity", "branch": "worktree/wt-parity",
        "worktree_path": str(Path("wt-parity").resolve()), "repo": "owner/repo",
        "machine": "host", "platform": "wsl", "started_at": "2026-06-01T10:00:00",
        "last_resumed_at": "2026-06-01T10:00:00", "resume_count": 0, "title": None,
        "status": "finalized", "completed_at": None,
    }
    base.update(kw)
    return tracking.WorktreeRecord(**base)


def _wire_mux(monkeypatch, rec, *, state, fetch_requested):
    """Mirror ``test_status_segment.py``'s ``_wire`` helper so the mux
    surface is driven through its own real, unmodified code path (not a
    re-derivation) against the SAME record."""
    target = rec.worktree_path
    info = git_ops.WorktreeStateInfo(state=state)
    monkeypatch.setattr(m, "_detect_upstream_branch", lambda *a, **k: "main")
    monkeypatch.setattr(m, "_find_record_for_path", lambda _p: rec)
    monkeypatch.setattr(
        m.git_ops, "classify_worktree",
        lambda *a, fetch=False, **k: dataclasses.replace(
            info, fetch_requested=fetch,
        ),
    )
    monkeypatch.setattr(m, "_apply_tracking_override", lambda r, i: i)
    monkeypatch.setattr(m.sessions, "scan_sessions_fast",
                        lambda recs: sessions.SessionContext())
    return argparse.Namespace(
        path=target, fetch=fetch_requested, plain=True, no_title=True)


def _picker_row_for(closure, worktree_id="wt-parity"):
    return derive.norm(
        {"id": worktree_id, "status": "finalized", "state": "completed",
         "closure": closure},
        "host", "wsl",
    )


def test_clean_completed_record_reports_final_on_every_surface(
    monkeypatch, capsys,
):
    rec = _rec()
    info = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED, fetch_requested=True)

    # Surface 1: list JSON.
    row = m._worktree_to_dict(rec, state_info=info)
    closure = row["closure"]
    assert closure["label"] == "FINAL"
    assert closure["compact"] == "FINAL"

    # Surface 2: the mux status segment, driven through its own real,
    # unmodified rendering path against the identical record.
    ns = _wire_mux(monkeypatch, rec, state=git_ops.WorktreeState.COMPLETED,
                   fetch_requested=True)
    rc = m.cmd_status_segment(ns)
    assert rc == 0
    mux_out = capsys.readouterr().out
    assert "FINAL" in mux_out
    assert "MERGED" not in mux_out

    # Surface 3: the production Picker, fed the EXACT closure payload list
    # JSON serialized above (the shape agent-bridge's crawl actually
    # carries cross-machine).
    picker_row = _picker_row_for(closure)
    assert picker_row["state"] == "FINAL"
    assert picker_row["status_markers"] == ""


def test_held_claim_and_open_follow_up_agree_on_merged_with_markers(
    monkeypatch, capsys,
):
    rec = _rec(
        resources=[
            tracking.ResourceClaim(kind="codespace", ref="cs-1", state="active"),
        ],
        follow_up=True,
    )
    info = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED, fetch_requested=True)

    # Surface 1: list JSON.
    row = m._worktree_to_dict(rec, state_info=info)
    closure = row["closure"]
    assert closure["label"] == "MERGED"
    assert closure["claims"] == {"held": 1}
    assert closure["follow_ups"] == {"open": 1}
    assert {"code": "held-claims", "count": 1} in closure["blockers"]

    # Surface 2: the mux status segment -- same held-claim/follow-up markers
    # must appear in its rendered text, not just list JSON's structured
    # fields.
    ns = _wire_mux(monkeypatch, rec, state=git_ops.WorktreeState.COMPLETED,
                   fetch_requested=True)
    rc = m.cmd_status_segment(ns)
    assert rc == 0
    mux_out = capsys.readouterr().out
    assert "MERGED" in mux_out
    assert "C1" in mux_out
    assert "F1" in mux_out
    assert "FINAL" not in mux_out

    # Surface 3: the production Picker, fed list JSON's own closure payload.
    picker_row = _picker_row_for(closure)
    assert picker_row["state"] == "MERGED"
    assert picker_row["status_markers"] == "C1 F1"


def test_style_metadata_agrees_between_list_json_and_picker(monkeypatch, capsys):
    """Validation Plan "Parity": beyond the label/marker checks above, the
    descriptor's semantic ``style`` (list JSON's `closure["style"]`) must
    match the Picker's own `state_style` field for the SAME fixture -- not
    just an equivalent-looking label."""
    rec = _rec(
        resources=[
            tracking.ResourceClaim(kind="codespace", ref="cs-1", state="active"),
        ],
    )
    info = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED, fetch_requested=True)
    row = m._worktree_to_dict(rec, state_info=info)
    closure = row["closure"]
    assert closure["label"] == "MERGED"
    assert closure["style"] == "merged-blocked"

    picker_row = _picker_row_for(closure)
    assert picker_row["state"] == closure["label"]
    assert picker_row["state_style"] == closure["style"]

    # And the clean/FINAL fixture from the first test above: style "final"
    # agrees too.
    clean_rec = _rec()
    clean_info = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState.COMPLETED, fetch_requested=True)
    clean_row = m._worktree_to_dict(clean_rec, state_info=clean_info)
    clean_closure = clean_row["closure"]
    assert clean_closure["style"] == "final"
    clean_picker_row = _picker_row_for(clean_closure)
    assert clean_picker_row["state_style"] == clean_closure["style"]


def test_cached_evidence_never_upgrades_to_final_on_any_surface(
    monkeypatch, capsys,
):
    """design.md's destructive-freshness rule: a fetch-free (cached) poll of
    a COMPLETED worktree must render MERGED, never FINAL, on every surface --
    not just list JSON, which is the only one most existing tests check."""
    rec = _rec()
    info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)

    row = m._worktree_to_dict(rec, state_info=info)
    closure = row["closure"]
    assert closure["label"] == "MERGED"

    ns = _wire_mux(monkeypatch, rec, state=git_ops.WorktreeState.COMPLETED,
                   fetch_requested=False)
    rc = m.cmd_status_segment(ns)
    assert rc == 0
    mux_out = capsys.readouterr().out
    assert "MERGED" in mux_out
    assert "FINAL" not in mux_out

    picker_row = _picker_row_for(closure)
    assert picker_row["state"] == "MERGED"
