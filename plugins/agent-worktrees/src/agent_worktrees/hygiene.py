"""Detect (and optionally clean) stale global-Python editable installs.

A developer running ``pip install -e .`` by hand inside a worktree checkout
(rather than the plugin's own installer, which always builds an isolated
per-tool versioned venv under e.g. ``~/.agent-worktrees/versions/<v>``) leaves
a ``__editable__.<dist>-<version>.pth`` file in the **global** interpreter's
``site-packages``, permanently pointing at that one worktree's ``src`` tree --
plus a shadow console-script ``.exe``/entry point in the interpreter's
``Scripts``/``bin`` directory that can shadow the real installed binstub on
``PATH``.

Neither of those self-heals: the worktree can be finalized/deleted (breaking
the import with no clear error) or simply drift out of date against whatever
runtime version is actually deployed, while the global interpreter keeps
silently importing it. This showed up concretely in
https://github.com/ThomasMichon/copilot-extensions/issues/2726 -- a stale
``agent_worktrees`` editable install caused ``pytest`` to exercise the wrong
worktree's code with no error at all.

This module scans the global interpreter's ``site-packages`` for editable
installs whose recorded source path looks like a worktree checkout (contains
a ``.worktrees`` path segment -- never legitimate for an installed plugin,
which is always deployed from a versioned, non-worktree runtime root) and can
remove them via ``pip uninstall`` (falling back to deleting the ``.pth`` file
directly if ``pip`` can't or won't).
"""

from __future__ import annotations

import re
import subprocess
import sys
import sysconfig
from dataclasses import dataclass
from pathlib import Path

_EDITABLE_PTH_RE = re.compile(r"^__editable__\.(?P<dist>.+?)-(?P<version>[^-]+)\.pth$")
# A worktree checkout path always contains a "<repo>.worktrees" segment
# (Windows or POSIX separators); a normal source/install root never does.
_WORKTREE_SEGMENT_RE = re.compile(r"\.worktrees[/\\]")


@dataclass(frozen=True)
class StaleEditableInstall:
    """One global editable install whose source resolves into a worktree."""

    dist_name: str
    version: str
    pth_path: Path
    target_path: str

    @property
    def pip_name(self) -> str:
        """The PyPI/pip-facing distribution name (dashes, not underscores)."""
        return self.dist_name.replace("_", "-")


def _site_packages_dirs() -> list[Path]:
    dirs: list[Path] = []
    seen: set[Path] = set()
    for scheme in ("purelib", "platlib"):
        try:
            candidate = Path(sysconfig.get_path(scheme))
        except Exception:
            continue
        if candidate.exists() and candidate not in seen:
            seen.add(candidate)
            dirs.append(candidate)
    return dirs


def find_stale_editable_installs(
    site_packages_dirs: list[Path] | None = None,
) -> list[StaleEditableInstall]:
    """Return every global editable install pointing at a worktree checkout.

    ``site_packages_dirs`` is an injection seam for tests; production callers
    should omit it (defaults to the running interpreter's own site-packages).
    """
    dirs = _site_packages_dirs() if site_packages_dirs is None else site_packages_dirs
    found: list[StaleEditableInstall] = []
    for directory in dirs:
        try:
            candidates = sorted(directory.glob("__editable__.*.pth"))
        except OSError:
            continue
        for pth in candidates:
            match = _EDITABLE_PTH_RE.match(pth.name)
            if not match:
                continue
            try:
                lines = pth.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            target = next((line.strip() for line in lines if line.strip()), "")
            if not target or not _WORKTREE_SEGMENT_RE.search(target):
                continue
            found.append(
                StaleEditableInstall(
                    dist_name=match.group("dist"),
                    version=match.group("version"),
                    pth_path=pth,
                    target_path=target,
                )
            )
    return found


def remove_stale_editable_install(
    entry: StaleEditableInstall, *, python: str | None = None
) -> str:
    """Remove one stale editable install; returns a short status string.

    Prefers ``pip uninstall`` (also removes the ``.dist-info`` and any
    console-script entry points it registered); falls back to deleting the
    ``.pth`` file directly if ``pip`` is unavailable or declines.
    """
    exe = python or sys.executable
    try:
        result = subprocess.run(
            [exe, "-m", "pip", "uninstall", "-y", entry.pip_name],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0:
            return "uninstalled"
    except (OSError, subprocess.SubprocessError):
        pass
    # pip didn't remove it (offline, distribution not found under this
    # normalized name, etc.) -- fall back to deleting the .pth by hand so the
    # stale import path is at least broken; a manual `pip uninstall` can
    # still clean up any leftover .dist-info afterward.
    try:
        entry.pth_path.unlink(missing_ok=True)
        return "pth-removed"
    except OSError as exc:
        return f"failed: {exc}"


def scan_and_clean(*, fix: bool) -> dict:
    """Read-only report by default; removes each finding when ``fix`` is set.

    Returns a JSON-serializable dict:
    ``{"found": [...], "removed": [...] }`` where each entry under ``found``
    also carries a ``"status"`` key ("would-remove" | the removal result)
    once ``fix`` has been applied.
    """
    findings = find_stale_editable_installs()
    report: list[dict] = []
    for entry in findings:
        status = remove_stale_editable_install(entry) if fix else "would-remove"
        report.append(
            {
                "distribution": entry.dist_name,
                "version": entry.version,
                "pth": str(entry.pth_path),
                "target": entry.target_path,
                "status": status,
            }
        )
    return {"action": "cleaned" if fix else "report", "findings": report}
