"""Warm the Picker's pre-app import path without loading data or starting UI."""
from __future__ import annotations

import importlib

PICKER_PREWARM_MODULES = (
    "worktree_manager.production_picker.picker_tui.engine",
    "worktree_manager.production_picker.picker_tui.data_ssh",
    "worktree_manager.production_picker.picker_tui.frame_health",
)


def main() -> None:
    """Import modules used before the Picker reaches its first app checkpoint."""
    for module_name in PICKER_PREWARM_MODULES:
        importlib.import_module(module_name)


if __name__ == "__main__":
    main()
