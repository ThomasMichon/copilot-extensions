"""Historical command retirement under the current Manager writer custody."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from . import self_install


def historical_command_binding(root: Path, bin_dir: Path) -> str | None:
    """Bind fixed archived execution targets to the actual current writer scope.

    Neither the supplied root nor a report can redirect the prototype's literal
    HOME/USERPROFILE target. Unknown or split execution homes are not adoption.
    """
    homes = []
    for key in ("HOME", "USERPROFILE"):
        raw = os.environ.get(key)
        if not raw or any(char in raw for char in "\r\n\0"):
            return "archive-execution-home-unresolved"
        home = Path(raw)
        if not home.is_absolute() or ".." in home.parts:
            return "archive-execution-home-unresolved"
        homes.append(home)
    def same(left: Path, right: Path) -> bool:
        return os.path.normcase(str(left)) == os.path.normcase(str(right))

    if not same(homes[0], homes[1]):
        return "archive-execution-homes-contradictory"
    home = homes[0]
    if not same(Path.home(), home):
        return "archive-execution-home-unresolved"
    target = home / ".worktree-manager"
    if not same(root, target):
        return "archive-target-root-mismatch"
    if not same(self_install.default_root(), target):
        return "manager-writer-root-differs-from-archive-target"
    expected_bin = home / ".local" / "bin"
    if not same(bin_dir, expected_bin) or not same(self_install.local_bin(), expected_bin):
        return "archive-command-directory-mismatch"
    return None


def historical_command_selection(root: Path) -> str | None:
    """Preserve every selected/recovery route and unresolved prototype layout.

    This is deliberately stricter than current_version(), which maps unreadable
    or invalid selection to absence. It grants no cleanup of the legacy layout.
    """
    runtime = self_install._runtime_installer().runtime
    for name in (runtime.CURRENT_VERSION_FILE, runtime.LAST_KNOWN_GOOD_FILE,
                 runtime.CURRENT_LINK, runtime.DEV_CLAIM_FILE, ".venv", "lib"):
        try:
            (root / name).lstat()
        except FileNotFoundError:
            continue
        return "manager-selected-or-recovery-binding"
    return None


@contextmanager
def historical_command_custody(root: Path, targets: list[Path]):
    """Use activate()'s selector-then-frontdoor publication lease ordering."""
    if not targets or any(path.parent != targets[0].parent for path in targets):
        raise ValueError("Historical command custody requires one explicit command scope")
    problem = historical_command_binding(root, targets[0].parent)
    if problem:
        raise ValueError(problem)
    runtime = self_install._runtime_installer().runtime
    publication = runtime._runtime_component("versioned_runtime_publication")
    with runtime._publication_lease(root, timeout=0), publication.target_leases(root, targets):
        problem = historical_command_binding(root, targets[0].parent)
        if problem:
            raise ValueError(problem)
        yield publication
