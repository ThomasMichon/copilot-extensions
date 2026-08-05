"""Resolve the **state root** -- the repo checkout where an effort/vision/log
plugin should write personal state.

This is the resolver behind ``agent-worktrees state-root``. It exists for the
**stateless harness** split (the ``stateless-harness`` vision /
``citadel-harness-split`` effort): a shareable control-plane harness carries the
intelligence but *no* personal state, so plugins like ``efforts``, ``visions``,
and ``agent-logger`` must not assume the launch repo is where their writes land.

Resolution rules (highest precedence first):

1. **Explicit override** (``--repo NAME``): resolve that registered repo's local
   checkout. Lets a caller deliberately target the harness itself or a product
   repo, regardless of the stateless binding.
2. **Stateless harness**: when the launch repo declares ``stateless: true``,
   route to the bound **knowledge repo** (top-level ``knowledge_repo`` in the
   machine-local config), resolved to a checkout via the repos registry. If no
   knowledge repo is bound -- or the bound name is not a registered checkout --
   resolution **fails** (no fallback): the resolver refuses to silently write
   personal state into the shareable harness tree.
3. **Non-stateless (backward-compatible default)**: the launch repo *is* the
   state home. Prefer the current git worktree root (so state lands in the tree
   being edited); fall back to the repo's anchor.

The resolver never hardcodes a repo name or path -- everything comes from the
layered config + the repos registry.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

from . import config as cfg
from . import repos as repos_mod


@dataclass(frozen=True)
class StateRoot:
    """The resolved (or unresolved) state root."""

    path: str | None
    """Absolute path to the checkout where state should be written, or ``None``
    when resolution failed (see :attr:`error`)."""
    source: str
    """Where the root came from: ``"knowledge_repo"``, ``"launch_repo"``, or
    ``"explicit"``."""
    repo: str
    """Name of the repo providing the root (knowledge repo name, launch repo
    name, or the explicit override)."""
    stateless: bool
    """Whether the launch repo declared itself a stateless harness."""
    bound: bool
    """True when a usable path was resolved."""
    error: str | None = None
    """Human-readable reason resolution failed (``None`` on success)."""

    def as_dict(self) -> dict:
        return {
            "state_root": self.path,
            "source": self.source,
            "repo": self.repo,
            "stateless": self.stateless,
            "bound": self.bound,
            "error": self.error,
        }


def _git_toplevel(cwd: str | None) -> str | None:
    """Return the git worktree root of ``cwd`` (or the process cwd), or None."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd or None,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    root = (proc.stdout or "").strip()
    return root or None


def _checkout_path(name: str) -> str | None:
    """Resolve a registered repo name to its local checkout path, or None.

    Uses :func:`repos.resolve_path`, matching ``agent-worktrees repos find``:
    the registry is consulted first, then a ``srcroot/name`` fallback -- so a
    knowledge repo that lives under the machine's source root resolves even
    without an explicit registry entry. The path must be an existing directory.
    """
    path = repos_mod.resolve_path(name)
    if not path or not os.path.isdir(path):
        return None
    return path


def resolve_state_root(
    config: cfg.Config,
    *,
    repo_override: str | None = None,
    cwd: str | None = None,
) -> StateRoot:
    """Resolve the state root for the given loaded config.

    Args:
        config: The layered project config (``cfg.load_config()``).
        repo_override: Explicit registered-repo name to target instead of the
            binding-driven default.
        cwd: Directory used for the non-stateless git-toplevel probe (defaults
            to the process cwd).

    Returns:
        A :class:`StateRoot`. On failure ``path`` is ``None`` and ``error`` is
        set; callers should treat that as "do not write" rather than falling
        back to the launch repo.
    """
    try:
        repo_cfg = config.default_repo
    except KeyError:
        repo_cfg = None
    launch_repo = config.repo_name or (repo_cfg and repo_cfg.anchor) or "?"
    stateless = bool(getattr(repo_cfg, "stateless", False))

    # 1. Explicit override -- resolve any registered repo by name.
    if repo_override:
        path = _checkout_path(repo_override)
        if not path:
            return StateRoot(
                None, "explicit", repo_override, stateless, False,
                error=(
                    f"repo '{repo_override}' is not a registered repo with a "
                    f"local checkout on this machine (agent-worktrees repos add …)"
                ),
            )
        return StateRoot(path, "explicit", repo_override, stateless, True)

    # 2. Stateless harness -> the bound knowledge repo (no fallback).
    if stateless:
        kr = (config.knowledge_repo or "").strip()
        if not kr:
            return StateRoot(
                None, "knowledge_repo", "", True, False,
                error=(
                    f"launch repo '{launch_repo}' is a stateless harness but no "
                    f"knowledge_repo is bound on this machine. Set "
                    f"'knowledge_repo: <name>' in ~/.{launch_repo}/config.yaml "
                    f"(or run the harness-knowledge setup) before writing "
                    f"efforts/logs/visions. Refusing to write state into the "
                    f"harness tree."
                ),
            )
        path = _checkout_path(kr)
        if not path:
            return StateRoot(
                None, "knowledge_repo", kr, True, False,
                error=(
                    f"knowledge_repo '{kr}' is not a registered repo with a "
                    f"local checkout on this machine. Register it "
                    f"(agent-worktrees repos add {kr} …) or fix the pointer in "
                    f"~/.{launch_repo}/config.yaml."
                ),
            )
        return StateRoot(path, "knowledge_repo", kr, True, True)

    # 3. Non-stateless -> the launch repo is the state home (backward-compatible).
    #    Prefer the current git worktree root so state lands in the tree being
    #    edited; fall back to the repo's anchor.
    root = _git_toplevel(cwd)
    if root:
        return StateRoot(root, "launch_repo", launch_repo, False, True)
    anchor = repo_cfg.anchor if repo_cfg else None
    if anchor and os.path.isdir(anchor):
        return StateRoot(anchor, "launch_repo", launch_repo, False, True)
    return StateRoot(
        None, "launch_repo", launch_repo, False, False,
        error=(
            f"could not resolve a state root for '{launch_repo}': no git "
            f"worktree at the current directory and no usable anchor."
        ),
    )
