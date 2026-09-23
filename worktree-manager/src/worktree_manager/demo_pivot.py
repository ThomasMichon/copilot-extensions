"""A fake **contributed pivot** provider for preview mode.

Run as ``python -m worktree_manager.demo_pivot list``. Prints a JSON array of
synthetic entries in the shape the generic pivot renderer
(``production_picker.picker_tui``) expects from any manifest-declared ``list``
command -- the same contract a real plugin (e.g. ``agent-containers fleet
--json``) fulfills. Themed to match ``demo.py``'s Aperture Labs fixture, so a
preview screenshot showing this pivot alongside the (also faked) Worktrees
pivot is obviously synthetic.

This module has no dependency on the real pivot-manifest scanning code --
``preview.py`` is what wires it into the registry, by injecting a plain
manifest (see its own module docstring) that names this module's ``list``
invocation as the pivot's data source. Kept here, not in ``preview.py``,
because it must be import-light and independently invocable as a subprocess
argv target (the pivot contract expects a subprocess, never an in-process
call).
"""

from __future__ import annotations

import json
import sys

#: Matches ``demo.py``'s roster in spirit -- a handful of synthetic "test
#: requests" a contributed pivot (e.g. a task queue, a fleet) might show.
#: ``claims_summary`` previews the cross-pivot CLAIMS column convention
#: (see the ``worktrees-pivot-ux-overhaul`` effort's Phase 4).
_ROWS = [
    {
        "id": "chamber-request-0091",
        "title": "Recalibrate the Aperture Science Handheld Portal Device",
        "state": "running",
        "owner": "GLaDOS",
        "claims_summary": "PR #91 (open)",
    },
    {
        "id": "chamber-request-0114",
        "title": "Replace the Weighted Companion Cube (again)",
        "state": "queued",
        "owner": "Cave Johnson",
        "claims_summary": "",
    },
    {
        "id": "chamber-request-0203",
        "title": "Audit the neurotoxin generator maintenance schedule",
        "state": "done",
        "owner": "GLaDOS",
        "claims_summary": "issue #203 (closed)",
    },
]


def entries() -> list[dict]:
    """The demo pivot's roster (the pivot ``list`` contract's row shape)."""
    return [dict(r) for r in _ROWS]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    verb = next((a for a in args if not a.startswith("-")), None)
    if verb == "list":
        json.dump(entries(), sys.stdout)
        sys.stdout.write("\n")
        return 0
    json.dump({"error": f"demo pivot has no verb {verb!r}"}, sys.stdout)
    sys.stdout.write("\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
