#!/usr/bin/env python3
"""Picker snapshot render -- the standing demo / A-B render flow (agent-worktrees
#88 NF).

Captures a deterministic picker state to SVG via the ``picker_tui.capture`` seam
(vision item A -- auditable-testable-rendering), then rasterizes it to PNG with
the SVG's OWN Fira Code font via ``svg2png.mjs``. It never substitutes the font:
Rich lays out the SVG's glyph x-positions on Fira Code's metric grid, so a
different monospace breaks the box-drawing tiling and leaves choppy borders.

Prereq (once):  npm install    # in this directory (installs @resvg/resvg-js)
Also requires node + a Python env with textual/rich/pyyaml importable.

Usage:
    python render.py out.png                 # picker home screen
    python render.py out.png --modal cfg     # with the Configuration modal open
    python render.py out.png --zoom 4        # crisper (bigger file)
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "..", "src"))

import datetime  # noqa: E402
import types  # noqa: E402

from agent_worktrees.picker_tui import capture as pcap  # noqa: E402
from agent_worktrees.picker_tui import derive  # noqa: E402


def _demo_source():
    """A hermetic demo fleet (frozen clock; no git/SSH/subprocess)."""
    derive.NOW = datetime.datetime(2026, 6, 27, 18, 0, 0)
    local = ("lambda-core", "Win")
    raws = [
        {"id": "lambda-core-win-20260627-aaaa", "title": "Fix the parser bug",
         "status": "active", "started_at": "2026-06-27T17:00:00",
         "turn_count": 12, "state": "wip", "ahead": 2, "behind": 1,
         "session_count": 1, "mux_session": True, "mux_attached": True,
         "mux_clients": 1},
        {"id": "lambda-core-win-20260627-bbbb",
         "title": "Add SelectionList to picker", "status": "active",
         "started_at": "2026-06-27T14:30:00", "turn_count": 5, "state": "active"},
        {"id": "lambda-core-win-20260620-cccc", "title": "Old idle experiment",
         "status": "active", "started_at": "2026-06-20T10:00:00",
         "turn_count": 0, "state": "unused"},
    ]
    s = types.SimpleNamespace()
    s.LOCAL = local
    s.LOCAL_LABEL = "lambda-core \u00b7 win"
    s.machines = lambda: [("lambda-core Win", "lambda-core", "Win", True)]
    s.bucket = derive.bucket
    s.for_machine = derive.for_machine
    s.load = lambda: [derive.norm(w, *local) for w in raws]
    return s


async def _open_cfg(scr, pilot):
    scr.sel = ("CFG", 0)
    scr._activate()
    await pilot.pause()


async def _open_clean(scr, pilot):
    scr.sel = ("BTN", 0)
    scr.btn_idx = scr.button_set().index("K")
    scr._activate()
    await pilot.pause()


async def _open_new(scr, pilot):
    scr.htab = 0
    scr.btn_idx = 0
    scr.sel = ("BTN", 0)
    scr._activate()
    await pilot.pause()


async def _open_submenu(scr, pilot):
    scr.machine_idx = scr.local_index()
    # Prefer a worktree that offers Open, so the demo shows the native No Mux
    # checkbox (the NF1 element this render is meant to exercise).
    recs = scr.list_records()
    idx = next((i for i, r in enumerate(recs)
                if r.get("sessionless") or r.get("mux_live")), 0)
    scr.sel = ("L", idx)
    scr._open_submenu()
    await pilot.pause()


async def _open_maint(scr, pilot):
    scr.machine_idx = scr.local_index()
    recs = scr.maint_records()
    ids = {r["id4"] for r in recs
           if scr._cleanable(r) or r.get("ff_eligible")}
    if ids:
        scr.maint_sel.replace(ids)
    scr._open_maint_menu()
    await pilot.pause()


async def _open_quit(scr, pilot):
    # Esc on a top-level view opens the native quit-confirm ModalScreen.
    await pilot.press("escape")
    await pilot.pause()


async def _open_prof(scr, pilot):
    # Push the Profiles-Apply confirm directly with a synthetic add/remove diff
    # (avoids driving the whole profiles grid just to render the confirm).
    from agent_worktrees.picker_tui.engine import ProfConfirmScreen

    def _sel(machine, env, kind):
        return types.SimpleNamespace(machine=machine, env=env, kind=kind)

    host_cols = [
        ("Lambda-Core·Win", "Lambda-Core", "Win"),
        ("Borealis·Win", "Borealis", "Win"),
    ]
    cf = {
        "changed": [1],
        "diffs": {
            1: (
                [_sel("Lambda-Core", "Win", "worktree"),
                 _sel("Wheatley", "WSL", "bridge")],
                [_sel("Borealis", "Win", "stale")],
            ),
        },
    }
    scr.app.push_screen(ProfConfirmScreen(cf, host_cols))
    await pilot.pause()


def main() -> int:
    ap = argparse.ArgumentParser(description="Render a picker snapshot to PNG.")
    ap.add_argument("out", help="output PNG path")
    ap.add_argument(
        "--modal",
        choices=["cfg", "clean", "new", "submenu", "maint", "quit", "prof"],
        help="open a native modal before capturing (composited app)")
    ap.add_argument("--zoom", default="3", help="rasterizer zoom (default 3)")
    args = ap.parse_args()

    src = _demo_source()
    if args.modal == "cfg":
        svg = pcap.capture_modal(src, _open_cfg, title="Configuration menu")
    elif args.modal == "clean":
        svg = pcap.capture_modal(src, _open_clean, title="Clean scope dialog")
    elif args.modal == "new":
        svg = pcap.capture_modal(src, _open_new, title="New-worktree options")
    elif args.modal == "submenu":
        svg = pcap.capture_modal(src, _open_submenu, title="Worktree action menu")
    elif args.modal == "maint":
        svg = pcap.capture_modal(src, _open_maint, title="Maintenance menu")
    elif args.modal == "quit":
        svg = pcap.capture_modal(src, _open_quit, title="Quit confirm")
    elif args.modal == "prof":
        svg = pcap.capture_modal(src, _open_prof, title="Profiles apply confirm")
    else:
        svg = pcap.capture(src, update_state="current")["svg"]

    with tempfile.NamedTemporaryFile(
            "w", suffix=".svg", delete=False, encoding="utf-8") as fh:
        fh.write(svg)
        svg_path = fh.name
    try:
        subprocess.run(
            ["node", os.path.join(_HERE, "svg2png.mjs"), svg_path, args.out,
             args.zoom],
            check=True,
        )
    finally:
        os.unlink(svg_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
