"""The machine-local project root as an ``.agent-*`` config overlay source.

A stateless harness keeps machine-specific entries out of its tree: its setup
writes them under the machine-local project root (``agent-worktrees get
config-dir``, e.g. ``~/.<project>/.agent-worktrees/related.yaml``). That root
joins :func:`state_root.config_source_anchors` between the launch repo and the
knowledge overlay, so a session in any checkout of the project resolves them.
"""
from __future__ import annotations

import os

from . import config as cfg

_MARKERS = (
    ".agent-worktrees",  # marketplace-isolation: allow legacy-compatibility
    os.path.join(".copilot-extensions", "agent-worktrees"),
)


def machine_config_root(base: str | None, *, project: str | None = None) -> str | None:
    """The selected project's local config root; omitted project retains ambient lookup."""
    try:
        root = str(cfg.project_dir(project))
    except Exception:  # noqa: BLE001 -- an unresolvable project has no overlay
        return None
    return config_overlay_root(root, base)


def config_overlay_root(root: str, base: str | None = None) -> str | None:
    """Classify an already-resolved project root using the normal overlay markers."""
    if base and os.path.abspath(root) == os.path.abspath(base):
        return None
    return root if any(os.path.isdir(os.path.join(root, m)) for m in _MARKERS) else None
