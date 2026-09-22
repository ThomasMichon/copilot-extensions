#!/usr/bin/env python3
"""Render before/after preview screenshots for the **picker-venue-pivots**
effort (``efforts/active/picker-venue-pivots``): the Codespaces and Containers
pivot overhaul.

Like ``../tasks-preview/render_tasks_preview.py``, this drives the REAL
Textual ``PickerApp`` (same engine as production) against a hermetic demo
Worktrees source, but with **two manifest variants per pivot**:

- ``agent-codespaces.current.json`` / ``agent-containers.current.json`` --
  byte-for-byte copies of the REAL installed manifests
  (``plugins/agent-codespaces/pivots/agent-codespaces.json`` and
  ``plugins/agent-containers/pivots/agent-containers.json``), proving today's
  actual shape (Codespaces' dropped subtitle; Containers' bare badge list).
- ``agent-codespaces.proposed.json`` / ``agent-containers.proposed.json`` --
  this effort's proposed manifests, rendering the SAME underlying fixture
  data (``fake_pool.py``/``fake_fleet.py``, shaped like a Phase 1/2
  ``pool.py``/``_cmd_fleet`` would compute it) through the row grammar,
  driving-worktree mark, claims-list, and Open/navigation actions the vision
  describes.

Nothing here talks to a live CodeSpace, Docker daemon, or agent-bridge, and
nothing mutates anything.

Usage (from this directory, after building the two plugin venvs -- see
``../tasks-preview/README.md`` for how):
    ../../../worktree-manager/.venv/Scripts/python.exe render_venue_preview.py --out-dir ./out
"""
from __future__ import annotations

import argparse
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

_BIN_DIR = os.path.join(_HERE, "bin")

# (pivot label, manifest basename, sandbox filename, output prefix)
_VARIANTS = [
    ("CodeSpaces", "agent-codespaces.current.json",
     "agent-codespaces-preview.json", "codespaces-before"),
    ("CodeSpaces", "agent-codespaces.proposed.json",
     "agent-codespaces-preview.json", "codespaces-after"),
    ("Containers", "agent-containers.current.json",
     "agent-containers-preview.json", "containers-before"),
    ("Containers", "agent-containers.proposed.json",
     "agent-containers-preview.json", "containers-after"),
]


def _demo_worktrees_source():
    """A hermetic demo worktree fleet (frozen clock; no git/SSH/subprocess).

    ``a1c4``'s title deliberately DIFFERS from its CodeSpace/container's own
    declared checkout intent (demonstrating the vision's "intent overrides
    worktree task title" fallback rung); ``88de``'s title is reused verbatim
    as its venue's durable title (demonstrating "no explicit intent -> falls
    back to the worktree's own task title"). ``c72e`` is deliberately ABSENT
    -- its venue's worktree cross-link is orphaned (holder worktree gone),
    so the ``task`` column and the worktree-title fallback both come up
    empty, leaving only the fixture's own baked orphaned-lock subtitle text.
    """
    from worktree_manager.production_picker.picker_tui import derive

    derive.NOW = datetime.datetime(2026, 9, 21, 23, 0, 0)
    local = ("build-host-1", "Win")
    raws = [
        {"id": "build-host-1-20260916-140200-a1c4",
         "title": "Review draft: harden the relay reconnect path",
         "status": "active", "started_at": "2026-09-16T14:02:00",
         "turn_count": 18, "state": "dirty"},
        {"id": "build-host-1-20260916-090000-88de",
         "title": "Fix agent-mcp decorator ordering regression",
         "status": "active", "started_at": "2026-09-16T09:00:00",
         "turn_count": 34, "state": "wip"},
    ]
    s = types.SimpleNamespace()
    s.LOCAL = local
    s.LOCAL_LABEL = "build-host-1 \u00b7 win"
    s.machines = lambda: [("build-host-1 Win", "build-host-1", "Win", True)]
    s.bucket = derive.bucket
    s.for_machine = derive.for_machine
    s.load = lambda: [derive.norm(w, *local) for w in raws]
    return s


def _install_sandbox_pivot(manifest_basename: str, sandbox_name: str) -> None:
    tmp_root = tempfile.mkdtemp(prefix="venue-preview-pivots-")
    pivots_dir = os.path.join(tmp_root, ".agent-worktrees", "pivots")
    os.makedirs(pivots_dir, exist_ok=True)
    # A distinct sandbox filename (not the real installed plugin's own
    # `pivots/agent-codespaces.json`/`agent-containers.json`) is treated as an
    # independent, unmanaged static pivot by the discovery pipeline's
    # `_KNOWN_LEGACY_PIVOTS` identity check -- same precedent as
    # ../tasks-preview's `agent-dispatch-preview.json`.
    shutil.copy(os.path.join(_HERE, manifest_basename),
                os.path.join(pivots_dir, sandbox_name))
    os.environ["AGENT_WORKTREES_PIVOTS_DIR"] = pivots_dir
    os.environ["WORKTREE_MANAGER_PICKER_NO_PIVOT_MATERIALIZE"] = "1"
    # Make the fake `agent-codespaces`/`agent-containers` resolvable via
    # shutil.which() ahead of any real installed binary of the same name.
    os.environ["PATH"] = _BIN_DIR + os.pathsep + os.environ.get("PATH", "")


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


async def _select_row_by_id(scr, pilot, row_id: str) -> int:
    """Focus the ('T', i) row whose entry id matches ``row_id``; waits for the
    pivot's background list to finish loading first (mirrors
    ../tasks-preview's ``_select_task_row``)."""
    import asyncio

    deadline = asyncio.get_event_loop().time() + 20
    while asyncio.get_event_loop().time() < deadline:
        state = scr._task_state()[0]
        if state not in ("loading", "idle"):
            break
        await asyncio.sleep(0.2)
        await pilot.pause()
    rows = scr._task_rows()
    idx = next((i for i, r in enumerate(rows) if r.get("id") == row_id), 0)
    scr.sel = ("T", idx)
    await pilot.pause()
    return idx


async def _open_menu_for(scr, pilot, row_id: str):
    await _select_row_by_id(scr, pilot, row_id)
    scr._open_task_menu()
    await pilot.pause()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default=os.path.join(_HERE, "out"))
    ap.add_argument("--zoom", default="3")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    from worktree_manager.production_picker.picker_tui import capture as pcap

    src = _demo_worktrees_source()

    for label, manifest_basename, sandbox_name, prefix in _VARIANTS:
        print(f"Capturing {prefix}.png ...", file=sys.stderr)
        _install_sandbox_pivot(manifest_basename, sandbox_name)
        caps = pcap.capture(src, pivot=label, wait_pivot=15,
                             update_state="current")
        _svg_to_png(caps["svg"], os.path.join(args.out_dir, f"{prefix}.png"),
                    args.zoom)

    # Menu screenshots: only the PROPOSED manifests carry the new Open /
    # driving-worktree-navigation / claims actions worth showing.
    menu_targets = [
        ("CodeSpaces", "agent-codespaces.proposed.json",
         "agent-codespaces-preview.json", "cs-a1c4-relay",
         "codespaces-menu-driven-live.png",
         "CodeSpace menu \u2014 driven + live session (cs-a1c4-relay)"),
        ("Containers", "agent-containers.proposed.json",
         "agent-containers-preview.json", "sample-repo-1",
         "containers-menu-driven-live.png",
         "Container menu \u2014 driven + live session (sample-repo-1)"),
    ]
    for label, manifest_basename, sandbox_name, row_id, filename, title in menu_targets:
        print(f"Capturing {filename} ...", file=sys.stderr)
        _install_sandbox_pivot(manifest_basename, sandbox_name)

        async def _prepare(scr, pilot, _label=label, _row_id=row_id):
            labels = [str(x).lower() for x in scr.htabs]
            scr.htab = labels.index(_label.lower())
            scr.sel = scr.default_sel()
            await pilot.pause()
            await _open_menu_for(scr, pilot, _row_id)

        svg = pcap.capture_modal(src, _prepare, title=title)
        _svg_to_png(svg, os.path.join(args.out_dir, filename), args.zoom)

    print(f"Wrote previews to {args.out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
