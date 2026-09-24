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

_MARKERS = (".agent-worktrees", os.path.join(".copilot-extensions", "agent-worktrees"))


def machine_config_root(base: str | None) -> str | None:
    """The machine-local project root when it carries agent config, else ``None``."""
    try:
        root = str(cfg.project_dir())
    except Exception:  # noqa: BLE001 -- an unresolvable project has no overlay
        return None
    if base and os.path.abspath(root) == os.path.abspath(base):
        return None
    return root if any(os.path.isdir(os.path.join(root, m)) for m in _MARKERS) else None
