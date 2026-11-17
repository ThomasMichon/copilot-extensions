"""Read-only, state-derived guidance for a bare Manager invocation."""

from __future__ import annotations

from . import __version__
from .core_install import core_status
from .engine_client import engine_available
from .harness_state import build_projects
from .prereqs import detect_baseline, missing
from .self_install import status as self_status


def print_startup_status() -> None:
    manager = self_status()
    core = core_status()
    gaps = missing(detect_baseline())
    engine_present = engine_available()
    projects = build_projects()

    print()
    print("  copilot-extensions Worktree Manager")
    print(f"  Running version: {__version__}")
    print(f"  Installed Manager version: {manager.installed_version or '(not observed)'}")
    print(f"  Agent-worktrees core: {core.state}")
    print(f"  Engine command: {'available' if engine_present else 'unavailable'}")
    print(f"  Registered projects reported: {len(projects)}")
    if gaps:
        print("  Baseline prerequisite gaps: " + ", ".join(gap.name for gap in gaps))
    else:
        print("  No baseline prerequisite gaps were reported.")

    print()
    if not manager.installed:
        print("  The installed Manager slot/binstub is incomplete or absent.")
        print("  Run `worktree-manager self-install --apply` to materialize this app.")
    if gaps or not core.installed:
        print("  Run `worktree-manager setup` to review the remaining install plan;")
        print("  add --apply only after reviewing its proposed changes.")
    elif not engine_present:
        print("  Core files are present, but no usable engine command was resolved.")
        print("  Run `worktree-manager doctor` to investigate before launching.")
    elif projects:
        print("  Use a registered project's binstub, or")
        print("  `worktree-manager picker <project>`, to continue.")
    else:
        print("  No registered projects were reported; inspect")
        print("  `worktree-manager projects` and `worktree-manager repos` before adoption.")
    print("  This is an installation snapshot, not a full health or authentication check.")
    print()
