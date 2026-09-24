#!/usr/bin/env python3
"""Phase-2 spike: capture the v1 (OptionList) Worktrees pivot and the v2
(ListView) prototype against the SAME derived data, for a direct comparison.

Not a shipped tool -- a one-off spike script for the worktrees-pivot-ux-overhaul
effort's Phase 2 decision. Writes `<out-dir>/v1.{txt,svg}` and
`<out-dir>/v2.{txt,svg}`.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MANAGER_SRC = os.path.join(_ROOT, "worktree-manager", "src")
_ENGINE_SRC = os.path.join(_ROOT, "plugins", "agent-worktrees", "src")
for _source in (_MANAGER_SRC, _ENGINE_SRC):
    if os.path.isdir(_source) and _source not in sys.path:
        sys.path.insert(0, _source)
os.environ.setdefault("WORKTREE_MANAGER_AGENT_WORKTREES_SRC", _ENGINE_SRC)
os.environ.setdefault("WORKTREE_MANAGER_PICKER_NO_PIVOT_MATERIALIZE", "1")
os.environ.setdefault("AGENT_WORKTREES_PIVOTS_DIR", os.path.join(_ROOT, "_nonexistent_pivots"))

from worktree_manager import demo  # noqa: E402
from worktree_manager.production_picker.picker_tui import capture as pcap  # noqa: E402
from worktree_manager.production_picker.picker_tui import derive  # noqa: E402
from worktree_manager.production_picker.picker_tui.engine_helpers import LIST_SPECS  # noqa: E402
from worktree_manager.production_picker.picker_tui.listview_proto import (  # noqa: E402
    ListViewPivotProto,
)

SIZE = (118, 44)


def _fixture_source():
    """The full Aperture Labs roster (demo.py) as a picker fixture source --
    real-shaped, richer than the 2-row unit-test fixture, so this spike's
    v1/v2 comparison exercises Active/Recent/Completed all at once."""
    local = (demo._MACHINE, "Win")
    raws = list(demo._ROWS) + demo._memo_rows()
    src = types.SimpleNamespace()
    src.LOCAL = local
    src.LOCAL_LABEL = f"{local[0]} \u00b7 win"
    src.machines = lambda: [(f"{local[0]} Win", local[0], "Win", True)]
    src.bucket = derive.bucket
    src.for_machine = derive.for_machine
    src.load = lambda: [derive.norm(w, *local) for w in raws]
    return src


def _sections_for(source):
    a, r, c = source.bucket(source.load())
    unowned = [w for w in r if w.get("sessionless")]
    if unowned:
        r = [w for w in r if not w.get("sessionless")]
        return [("Active", a), ("Recent", r), ("Completed", c),
                ("Unowned", unowned)]
    return [("Active", a), ("Recent", r), ("Completed", c)]


async def _capture_v2(sections, width: int) -> dict[str, str]:
    app = ListViewPivotProto(LIST_SPECS, sections, width=width)
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause()
        return pcap.capture_screen(app.screen, title="Worktrees v2 (ListView spike)")


def main() -> int:
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        _ROOT, "_listview_proto_out")
    os.makedirs(out_dir, exist_ok=True)

    source = _fixture_source()
    v1 = pcap.capture(source, live=False, size=SIZE)
    sections = _sections_for(source)
    v2 = asyncio.run(_capture_v2(sections, SIZE[0]))

    for name, caps in (("v1", v1), ("v2", v2)):
        with open(os.path.join(out_dir, f"{name}.txt"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(caps["text"])
        with open(os.path.join(out_dir, f"{name}.svg"), "w",
                  encoding="utf-8") as fh:
            fh.write(caps["svg"])

    print(f"Wrote v1/v2 .txt + .svg to {out_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
