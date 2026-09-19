#!/usr/bin/env python3
"""Render preview screenshots for the agent-dispatch **Tasks pane UX overhaul**
(``efforts/active/agent-dispatch-tasks-pane-ux-overhaul``).

This is a maintainer/review tool, not shipped runtime: it drives the REAL
Textual ``PickerApp`` (same engine as production) against a hermetic demo
Worktrees source plus a **proposed** Tasks pivot manifest
(``agent-dispatch.proposed.json``) whose ``list`` points at a fixed,
deterministic stand-in (``fake_board.py``) instead of the real
``agent-dispatch-board``. Nothing here talks to a live coordinator or mutates
anything. It captures:

  1. tasks-list.png        -- the redesigned Tasks table (phase colour,
                               repo column, WT id4, artifacts, turns/live)
  2. task-menu-blocked.png  -- the action menu for an embodied+blocked task
                               (steer, charter, worktree status, pause,
                               force-stop, reset-to-proposed, abandon)
  3. task-menu-started.png  -- the action menu for an embodied+started task
  4. task-charter.png       -- the read-only charter card
  5. task-worktree-status.png -- the new Worktree Status card
  6. task-steer.png         -- the existing steer modal, reusing this task's
                               own review card (unchanged mechanism)

Usage (from this directory, after building the two plugin venvs -- see
``../../preview-picker.ps1`` docstring for how):
    ../../../worktree-manager/.venv/Scripts/python.exe render_tasks_preview.py --out-dir ./out
"""
from __future__ import annotations

import argparse
import asyncio
import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SNAPSHOT_DIR = os.path.dirname(_HERE)          # .../picker-snapshot
_SCRIPTS_DIR = os.path.dirname(_SNAPSHOT_DIR)   # .../worktree-manager/scripts
_MANAGER_DIR = os.path.dirname(_SCRIPTS_DIR)    # .../worktree-manager
_REPO_ROOT = os.path.dirname(_MANAGER_DIR)      # copilot-extensions checkout
_MANAGER_SRC = os.path.join(_MANAGER_DIR, "src")
_ENGINE_SRC = os.path.join(_REPO_ROOT, "plugins", "agent-worktrees", "src")
for _source in (_MANAGER_SRC, _ENGINE_SRC):
    if os.path.isdir(_source) and _source not in sys.path:
        sys.path.insert(0, _source)
os.environ.setdefault("WORKTREE_MANAGER_AGENT_WORKTREES_SRC", _ENGINE_SRC)

_MANIFEST_SRC = os.path.join(_HERE, "agent-dispatch.proposed.json")
_BIN_DIR = os.path.join(_HERE, "bin")


def _demo_worktrees_source():
    """A hermetic demo worktree fleet (frozen clock; no git/SSH/subprocess) --
    the id4s (a1c4/88de/c72e) match ``fake_board.py``'s ``target_worktree``
    values so a later worktree-title join (if the manifest adds one) resolves."""
    from worktree_manager.production_picker.picker_tui import derive

    derive.NOW = datetime.datetime(2026, 9, 16, 23, 0, 0)
    local = ("build-host-1", "Win")
    raws = [
        {"id": "build-host-1-20260916-140200-a1c4",
         "title": "Review draft: harden the relay reconnect path",
         "status": "active", "started_at": "2026-09-16T14:02:00",
         "turn_count": 18, "state": "dirty"},
        {"id": "build-host-1-20260916-090000-88de",
         "title": "Fix agent-mcp decorator ordering regression",
         "status": "active", "started_at": "2026-09-16T09:00:00",
         "turn_count": 34, "state": "wip",
         "mux_session": True, "mux_attached": True, "mux_clients": 1},
        {"id": "build-host-1-20260915-100000-c72e",
         "title": "Investigate augloop-workflows flaky scenario runner",
         "status": "active", "started_at": "2026-09-15T10:00:00",
         "turn_count": 9, "state": "wip"},
    ]
    s = types.SimpleNamespace()
    s.LOCAL = local
    s.LOCAL_LABEL = "build-host-1 \u00b7 win"
    s.machines = lambda: [("build-host-1 Win", "build-host-1", "Win", True)]
    s.bucket = derive.bucket
    s.for_machine = derive.for_machine
    s.load = lambda: [derive.norm(w, *local) for w in raws]
    return s


def _install_sandbox_pivot(tmp_root: str) -> None:
    pivots_dir = os.path.join(tmp_root, ".agent-worktrees", "pivots")
    os.makedirs(pivots_dir, exist_ok=True)
    # NOT named "agent-dispatch.json": that filename is in pivots.py's
    # `_KNOWN_LEGACY_PIVOTS`, so the discovery pipeline identity-checks it
    # against the REAL installed agent-dispatch plugin's own template and
    # drops any content that doesn't match byte-for-byte. A distinct filename
    # is treated as an independent (unmanaged) static pivot, exactly what a
    # design-preview mockup needs.
    shutil.copy(_MANIFEST_SRC,
                os.path.join(pivots_dir, "agent-dispatch-preview.json"))
    os.environ["AGENT_WORKTREES_PIVOTS_DIR"] = pivots_dir
    os.environ["WORKTREE_MANAGER_PICKER_NO_PIVOT_MATERIALIZE"] = "1"
    # Make the fake `agent-dispatch-board` resolvable via shutil.which().
    os.environ["PATH"] = _BIN_DIR + os.pathsep + os.environ.get("PATH", "")


async def _select_task_row(scr, pilot, task_id: str) -> int:
    """Focus the ('T', i) row whose entry id matches ``task_id``; waits for the
    pivot's background list to finish loading first."""
    deadline = asyncio.get_event_loop().time() + 20
    while asyncio.get_event_loop().time() < deadline:
        state = scr._task_state()[0]
        if state not in ("loading", "idle"):
            break
        await asyncio.sleep(0.2)
        await pilot.pause()
    rows = scr._task_rows()
    idx = next((i for i, r in enumerate(rows) if r.get("id") == task_id), 0)
    scr.sel = ("T", idx)
    await pilot.pause()
    return idx


async def _open_task_menu_for(scr, pilot, task_id: str):
    await _select_task_row(scr, pilot, task_id)
    scr._open_task_menu()
    await pilot.pause()


async def _open_task_card_for(scr, pilot, task_id: str, action_key: str):
    """Bypass the menu and open a declared ``kind:"card"`` action directly
    (mirrors ``render.py``'s existing precedent of driving modals straight
    from the demo script rather than only through keyboard navigation)."""
    await _select_task_row(scr, pilot, task_id)
    reg = scr._reg_pivot()
    rec = scr._selected_task()
    action = next(a for a in reg.actions if a.key == action_key)
    scr._open_pivot_card(reg, action, rec)
    await pilot.pause()


async def _open_steer_for(scr, pilot, task_id: str):
    from worktree_manager.production_picker.picker_tui.engine import PivotFormScreen

    await _select_task_row(scr, pilot, task_id)
    rec = scr._selected_task()
    card = rec.get("card") or {}
    fields = card.get("request_input") or []
    modal = PivotFormScreen(card, fields, "Steer", task_id=task_id)
    scr.app.push_screen(modal)
    await pilot.pause()


def _svg_to_png(svg: str, out_png: str, zoom: str = "3") -> None:
    with tempfile.NamedTemporaryFile(
            "w", suffix=".svg", delete=False, encoding="utf-8") as fh:
        fh.write(svg)
        svg_path = fh.name
    try:
        subprocess.run(
            ["node", os.path.join(_SNAPSHOT_DIR, "svg2png.mjs"), svg_path,
             out_png, zoom],
            check=True,
        )
    finally:
        os.unlink(svg_path)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=os.path.join(_HERE, "out"))
    ap.add_argument("--zoom", default="3")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="tasks-preview-") as tmp:
        _install_sandbox_pivot(tmp)

        # Import AFTER env vars are set so pivot discovery honours the sandbox.
        from worktree_manager.production_picker.picker_tui import capture as pcap

        src = _demo_worktrees_source()

        print("Capturing tasks-list.png ...", file=sys.stderr)
        caps = pcap.capture(src, pivot="Tasks", wait_pivot=15,
                             update_state="current")
        _svg_to_png(caps["svg"], os.path.join(args.out_dir, "tasks-list.png"),
                    args.zoom)

        print("Capturing tasks-list-wide.png ...", file=sys.stderr)
        # At the canonical 118-col capture width, the column-fit algorithm
        # (`TasksView._fitted_columns`) correctly drops the lowest-priority
        # ARTIFACTS column to keep every row on one line -- graceful
        # degradation, not a bug. A wider terminal (a realistic width for
        # this many columns) keeps every column; capture that too so the
        # full design intent (including artifacts) is visible somewhere.
        caps_wide = pcap.capture(src, pivot="Tasks", wait_pivot=15,
                                  size=(160, 40), update_state="current")
        _svg_to_png(caps_wide["svg"],
                    os.path.join(args.out_dir, "tasks-list-wide.png"),
                    args.zoom)

        async def _menu_blocked(scr, pilot):
            await _open_task_menu_for(scr, pilot, "task-9f21")

        async def _menu_started(scr, pilot):
            await _open_task_menu_for(scr, pilot, "task-7b03")

        async def _menu_suspended(scr, pilot):
            await _open_task_menu_for(scr, pilot, "task-6650")

        async def _menu_queued(scr, pilot):
            await _open_task_menu_for(scr, pilot, "task-4410")

        async def _menu_proposed(scr, pilot):
            await _open_task_menu_for(scr, pilot, "task-2201")

        async def _charter(scr, pilot):
            await _open_task_card_for(scr, pilot, "task-9f21", "charter")

        async def _wt_status(scr, pilot):
            await _open_task_card_for(scr, pilot, "task-9f21", "worktree-status")

        async def _steer(scr, pilot):
            await _open_steer_for(scr, pilot, "task-9f21")

        modals = [
            ("task-menu-blocked.png", _menu_blocked, "Task menu \u2014 Blocked (task-9f21)"),
            ("task-menu-started.png", _menu_started, "Task menu \u2014 Started (task-7b03)"),
            ("task-menu-suspended.png", _menu_suspended, "Task menu \u2014 Suspended (task-6650)"),
            ("task-menu-queued.png", _menu_queued, "Task menu \u2014 Queued/pooled (task-4410)"),
            ("task-menu-proposed.png", _menu_proposed, "Task menu \u2014 Proposed (task-2201)"),
            ("task-charter.png", _charter, "Charter viewer (task-9f21)"),
            ("task-worktree-status.png", _wt_status, "Worktree Status card (task-9f21)"),
            ("task-steer.png", _steer, "Steer card (task-9f21)"),
        ]
        for filename, opener, title in modals:
            print(f"Capturing {filename} ...", file=sys.stderr)

            async def _prepare(scr, pilot, _opener=opener):
                # Land on Tasks + let the background list finish before driving
                # the requested modal (mirrors `_select_pivot`'s wait_pivot).
                labels = [str(x).lower() for x in scr.htabs]
                scr.htab = labels.index("tasks")
                scr.sel = scr.default_sel()
                await pilot.pause()
                await _opener(scr, pilot)

            svg = pcap.capture_modal(src, _prepare, title=title)
            _svg_to_png(svg, os.path.join(args.out_dir, filename), args.zoom)

    print(f"Wrote previews to {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
