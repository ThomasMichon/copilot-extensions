"""Preview mode: render the REAL production Picker against deterministic,
mock, no-live-engine-required data.

Two independent injections compose to make this work, both riding seams the
real architecture already exposes rather than adding any Picker-specific
mock branch:

1. **Worktree data** -- ``engine_client.set_engine_command`` (the same
   mechanism ``picker_app``'s older demo mode used) is pointed at
   ``demo_engine``, the existing fake-engine subprocess that emits the
   Aperture Labs fixture (``demo.py``) in the real ``list --json`` contract
   shape. Every consumer that shells out through ``engine_client`` --
   including ``production_picker``'s own ``data_local``/``data_ssh`` --
   transparently receives mock rows instead of running the real engine.
2. **A contributed pivot** -- the Picker's cross-plugin pivot registry
   (``picker_tui.pivot_manifest``/``pivot_registry_scan``) already lets any
   plugin contribute a pivot via a filesystem manifest, entirely decoupled
   from engine code (see that module's own docstring). This function
   materializes one plain, schema-less ("operator"-class, always active, no
   plugin/root attribution needed) manifest into an isolated temp directory
   naming ``demo_pivot``'s ``list`` command, and points
   ``AGENT_WORKTREES_PIVOTS_DIR`` at it -- with real-plugin materialization
   disabled, so the *only* pivot the registry scan finds is this one. This
   previews the generic multi-pivot rendering path (columns, claims-summary
   convention, etc.) the same way a real contributed pivot would, using
   synthetic data.

Because both injections are pure environment/subprocess-command overrides,
the actual ``production_picker`` app, screen, and rendering code run
completely unmodified -- a preview capture is the same code path a real
capture takes, just fed fixture data. Call :func:`enable_preview_mode` once,
before constructing/running/capturing the Picker; it is idempotent and
process-local (never touches another process's environment).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from . import demo
from .engine_client import set_engine_command
from .production_picker.picker_tui.pivot_manifest import PIVOTS_DIR_ENV

#: Same override ``picker-shot.py`` and the pivot registry's own materializer
#: already recognize: skip republishing real installed plugins' pivots.
_NO_MATERIALIZE_ENV = "WORKTREE_MANAGER_PICKER_NO_PIVOT_MATERIALIZE"

#: The demo pivot's manifest -- schema-less ("operator" class; see
#: ``pivot_registry_scan``'s ``classify()``: an entry with no
#: ``schema_version`` is always active, needs no plugin/root attribution,
#: and is never flagged advisory). Deliberately placed right after the
#: (also-mocked) Worktrees pivot.
_DEMO_PIVOT_MANIFEST: dict[str, object] = {
    "label": "Demo Queue",
    "after": "Worktrees",
    "list": [sys.executable, "-m", "worktree_manager.demo_pivot", "list"],
    "entry": {"id": "id", "title": "title"},
    "columns": [
        {"key": "id", "header": "id", "width": 22},
        {"key": "title", "header": "request", "priority": 1},
        {"key": "state", "header": "state", "width": 8, "palette": "state"},
        {"key": "owner", "header": "owner", "width": 14},
        {"key": "claims_summary", "header": "claims", "width": 18},
    ],
    "empty_hint": "No demo requests.",
    "actions": [],
}

_active_tmp_dir: tempfile.TemporaryDirectory | None = None


def _materialize_demo_pivot() -> Path:
    """Write the demo pivot manifest into a fresh, isolated temp directory."""
    global _active_tmp_dir
    if _active_tmp_dir is None:
        _active_tmp_dir = tempfile.TemporaryDirectory(prefix="wtm-preview-pivots-")
    directory = Path(_active_tmp_dir.name)
    (directory / "demo-queue.json").write_text(
        json.dumps(_DEMO_PIVOT_MANIFEST, indent=2), encoding="utf-8"
    )
    return directory


def enable_preview_mode() -> None:
    """Point the Picker's data and pivot registry at deterministic fixtures.

    Safe to call more than once (idempotent); affects only this process's
    environment and ``engine_client``'s module-level override, never a
    persisted file outside the temp directory this function owns.
    """
    set_engine_command([sys.executable, "-m", "worktree_manager.demo_engine"])
    pivots_dir = _materialize_demo_pivot()
    os.environ[PIVOTS_DIR_ENV] = str(pivots_dir)
    os.environ[_NO_MATERIALIZE_ENV] = "1"


#: Re-exported for callers that want the demo project name without a second
#: import of ``demo`` (e.g. ``__main__``'s picker command).
DEMO_PROJECT = demo.DEMO_PROJECT
