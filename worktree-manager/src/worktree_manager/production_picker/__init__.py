"""Production Picker transplanted from agent-worktrees.

The presentation package is owned by Worktree Manager. During the migration,
its existing data/action imports are bridged to the active agent-worktrees
runtime by the sibling proxy modules. Those proxies are temporary and are
replaced by process/service adapters without changing the Picker UX.
"""

from .picker_tui import run_tui_picker

__all__ = ["run_tui_picker"]
